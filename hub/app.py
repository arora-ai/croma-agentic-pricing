"""Tools hub: one container exposing five MCP servers (data tool, SharePoint tools, agent bridge, case context, report studio) behind an API-key check.
    /genie/mcp       the data tool: hands questions to the configured backend (Databricks Genie, your own function, or a stub)
    /sharepoint/mcp  the SharePoint tools: search a document library and read a file as a table or as passages of text (off unless SHAREPOINT_BACKEND is set)
    /agents/mcp      agent-to-agent calls
    /context/mcp     shared case context
    /studio/mcp      the report studio: evidence ledger, calculations, audit, renderer
    /trace         recent agent-to-agent calls (for test runs);  /case/<id>  the case as saved;  /healthz  open
    /runs/<case>   the harness runs (Analysis, Research) on a case, stage by stage
    /report/<case>/<n>.docx|.html  published reports;  /state/<case>  export a case (with its runs), POST /state to restore it
"""
from __future__ import annotations

import contextlib
import hmac
import logging
import re
from urllib.parse import quote

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

import agent_bridge
import case_context
import events as live_events
import genie_gateway
import sharepoint_gateway
from harness import runtime as harness_runtime
from settings import API_KEY, GENIE_BACKEND, HARNESS_MODE, SHAREPOINT_BACKEND
from studio import server as studio_server

logging.basicConfig(level=logging.WARNING)
SERVERS = [genie_gateway.mcp, sharepoint_gateway.mcp, agent_bridge.mcp, case_context.mcp, studio_server.mcp]


@contextlib.asynccontextmanager
async def lifespan(_app):
    async with contextlib.AsyncExitStack() as stack:
        for server in SERVERS:
            await stack.enter_async_context(server.session_manager.run())
        yield


async def healthz(_request: Request):
    return JSONResponse({"status": "ok", "genie_tool": genie_gateway.TOOL_NAME, "genie_backend": GENIE_BACKEND, "sharepoint_backend": SHAREPOINT_BACKEND, "harness_mode": HARNESS_MODE})


async def trace(request: Request):
    case_id = request.query_params.get("case_id")
    rows = [t for t in agent_bridge.trace if not case_id or t["case_id"] == case_id]
    return JSONResponse(rows)


async def events(request: Request):
    return JSONResponse(live_events.since(int(request.query_params.get("since", "0"))))


async def case(request: Request):
    data = case_context.snapshot(request.path_params["case_id"])
    return JSONResponse(data or {"error": "unknown case"}, status_code=200 if data else 404)


async def report_file(request: Request):
    case_id, n, ext = request.path_params["case_id"], int(request.path_params["n"]), request.path_params["ext"]
    art = studio_server.artifact(case_id, n)
    if not art or ext not in ("docx", "html"):
        return JSONResponse({"error": "unknown report"}, status_code=404)
    if ext == "html":
        return Response(art["html"], media_type="text/html; charset=utf-8")
    name = re.sub(r"[^A-Za-z0-9 ._-]+", "-", art["title"])[:60].strip() or "report"
    return Response(art["docx"], media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f"attachment; filename=\"{name}.docx\"; filename*=UTF-8''{quote(art['title'][:80])}.docx"})


async def state_export(request: Request):
    case_id = request.path_params["case_id"]
    return JSONResponse({**studio_server.export_state(case_id), "runs": harness_runtime.dump(case_id)})


async def state_import(request: Request):
    data = await request.json()
    case_id = studio_server.import_state(data)
    harness_runtime.load(data.get("runs", []))
    return JSONResponse({"restored": case_id, "runs": len(data.get("runs", []))})


async def runs(request: Request):
    """The harness runs on a case with their stages; add ?data=1 to include what each stage saved."""
    with_data = request.query_params.get("data") in ("1", "true")
    return JSONResponse([r.to_dict(with_data=with_data) for r in harness_runtime.for_case(request.path_params["case_id"])])


class ApiKey:
    """Rejects any request without the shared key, except the health probe."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/healthz" and not (SIMULATED and scope["path"].startswith("/graph-sim/")):  # the simulator checks its own bearer token
            supplied = dict(scope["headers"]).get(b"x-api-key", b"").decode()
            if not API_KEY or not hmac.compare_digest(supplied, API_KEY):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


SIMULATED = SHAREPOINT_BACKEND == "simulated"
ROUTES = [Route("/healthz", healthz), Route("/trace", trace), Route("/events", events), Route("/case/{case_id}", case), Route("/runs/{case_id}", runs),
          Route("/report/{case_id}/{n:int}.{ext}", report_file),
          Route("/state/{case_id}", state_export),
          Route("/state", state_import, methods=["POST"]),
          Mount("/genie", app=genie_gateway.mcp.streamable_http_app()),
          Mount("/sharepoint", app=sharepoint_gateway.mcp.streamable_http_app()),
          Mount("/agents", app=agent_bridge.mcp.streamable_http_app()),
          Mount("/context", app=case_context.mcp.streamable_http_app()),
          Mount("/studio", app=studio_server.mcp.streamable_http_app())]
if SIMULATED:
    from sharepoint import mock_graph

    ROUTES.append(Mount(mock_graph.MOUNT, app=mock_graph.app))

app = ApiKey(Starlette(routes=ROUTES, lifespan=lifespan))

if __name__ == "__main__":
    import os
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
