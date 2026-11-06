"""Backend for the Croma assistant UI.

Streams one chat turn to the browser as server-sent events, merging two sources:
  * the CX agent's answer, streamed from Foundry as it is written
  * the hub's live event log (agent calls, tool calls, SQL), polled while the turn runs, so the admin view can light up each agent
It also keeps the conversation history (SQLite) and exposes the user's Foundry memory (preferences) for viewing and editing.

Run from the agentic-engine folder:   uvicorn ui.backend.main:app --port 8000
Signs in to Foundry with your az login (the managed identity when deployed); reads HUB_URL and HUB_API_KEY from ../../.env or the environment.

Behind an API gateway (set GATEWAY_KEY) the app is multi-user: the gateway says who is calling, and conversations and memory are kept per user
(see identity.py). Without GATEWAY_KEY it behaves as a single-user app, as before.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.deploy_agents import load_env, project
from agents.definitions import NAMES
from agents.memory_store import MEMORY_SCOPE, MEMORY_STORE
from ui.backend import diagnostics, store
from ui.backend.identity import CorrelationId, Identity, gateway_key, memory_scope, require_identity

DIST = Path(__file__).parent.parent / "frontend" / "dist"
CX_AGENT = os.getenv("CX_AGENT", NAMES["cx"])
MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
POLL_SECONDS = 0.5
# When a dropped agent stream is picked up again, fail fast and try again instead of waiting on a dead connection (the client's default is ten minutes).
RESUME_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=30.0, pool=15.0)
MAX_RECONNECTS = 40
# Idle connections are cut after about four minutes (Azure load balancers, the Container Apps ingress), so behind a gateway a long turn needs a signal
# now and then. A comment line is invisible to the browser's event parser. 0 (the default) sends none, so the stream is unchanged for local use.
HEARTBEAT_SECONDS = float(os.getenv("SSE_HEARTBEAT_S", "0"))
WORD_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def register_media_types() -> None:
    """The container image has no system table of media types, so the sample report's Word file would be served as an untyped download."""
    mimetypes.add_type(WORD_TYPE, ".docx")


register_media_types()

# Every API route needs an identity (a no-op without GATEWAY_KEY). Behind the gateway the interactive API docs are not served.
app = FastAPI(title="Croma assistant", dependencies=[Depends(require_identity)],
              **({"docs_url": None, "redoc_url": None, "openapi_url": None} if gateway_key() else {}))
app.add_middleware(CorrelationId)
app.include_router(diagnostics.router)


@app.on_event("startup")
def _startup() -> None:
    store.mark_interrupted()


@lru_cache(maxsize=1)
def _env() -> dict[str, str]:
    return load_env()


@lru_cache(maxsize=1)
def _project():
    return project()


@lru_cache(maxsize=1)
def _openai():
    return _project().get_openai_client()


def _hub_headers() -> dict[str, str]:
    return {"x-api-key": _env()["HUB_API_KEY"]}


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"


def _now_ms() -> int:
    return round(time.time() * 1000)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class RenameRequest(BaseModel):
    title: str


class MemoryRequest(BaseModel):
    content: str


# ---------------------------------------------------------------- status and cases

@app.get("/api/health")
async def health(who: Identity = Depends(require_identity)):
    async with httpx.AsyncClient(timeout=10) as http:
        genie_backend = "unknown"
        sharepoint_backend = "off"
        try:
            r = await http.get(f"{_env()['HUB_URL']}/healthz")
            hub = r.json().get("status") == "ok"
            genie_backend = r.json().get("genie_backend", "mock")
            sharepoint_backend = r.json().get("sharepoint_backend", "off")
        except Exception:
            hub = False
    return {"hub": hub, "hub_url": _env()["HUB_URL"], "project": _env()["FOUNDRY_PROJECT_ENDPOINT"].rsplit("/", 1)[-1], "cx_agent": CX_AGENT,
            "memory_store": MEMORY_STORE, "memory_scope": memory_scope(who, MEMORY_SCOPE), "genie_backend": genie_backend, "sharepoint_backend": sharepoint_backend}


@app.get("/api/me")
def me(who: Identity = Depends(require_identity)):
    """Who the API believes is calling: the signed-in user when behind the gateway, otherwise the default single user (user_id null)."""
    return {"user_id": who.user_id or None, "name": who.name or None, "authenticated": who.is_user, "gateway": bool(gateway_key())}


@app.get("/api/case/{case_id}")
async def case(case_id: str):
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{_env()['HUB_URL']}/case/{case_id}", headers=_hub_headers())
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Case not found")
    return r.json()


@app.get("/api/runs/{case_id}")
async def runs(case_id: str):
    """The Analysis and Research runs on a case: the finished brief and the stages each run went through (the hub key stays on the server)."""
    if not re.fullmatch(r"[\w-]+", case_id):
        raise HTTPException(404, "Unknown case")
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{_env()['HUB_URL']}/runs/{case_id}", headers=_hub_headers())
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Runs not found")
    return [{"id": x["id"], "harness": x["harness"], "status": x["status"], "error": x["error"], "markdown": (x.get("result") or {}).get("markdown"),
             "stages": [{"name": s["name"], "status": s["status"], "seconds": round((s["ended"] or 0) - (s["started"] or 0), 1) if s["started"] and s["ended"] else None,
                         "summary": s["summary"]} for s in x["stages"]]} for x in r.json()]


@app.get("/api/report/{case_id}/{n}/{ext}")
async def report_file(case_id: str, n: int, ext: str):
    """Streams a published report (DOCX or HTML) from the hub; the hub key stays on the server."""
    if ext not in ("docx", "html") or not re.fullmatch(r"[\w-]+", case_id):
        raise HTTPException(404, "Unknown report")
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.get(f"{_env()['HUB_URL']}/report/{case_id}/{n}.{ext}", headers=_hub_headers())
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Report not found")
    headers = {k: v for k, v in r.headers.items() if k.lower() == "content-disposition"}
    return Response(r.content, media_type=r.headers.get("content-type", "application/octet-stream"), headers=headers)


# ---------------------------------------------------------------- conversation history

@app.get("/api/conversations")
def conversations(who: Identity = Depends(require_identity)):
    return store.list_conversations(who.user_id)


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str, who: Identity = Depends(require_identity)):
    found = store.get_conversation(conversation_id, who.user_id)
    if not found:
        raise HTTPException(404, "Conversation not found")
    return found


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameRequest, who: Identity = Depends(require_identity)):
    if not store.rename(conversation_id, body.title, who.user_id):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, who: Identity = Depends(require_identity)):
    if not store.delete(conversation_id, who.user_id):
        raise HTTPException(404, "Conversation not found")
    try:
        _openai().conversations.delete(conversation_id)
    except Exception:
        pass  # the history is gone either way; Foundry cleans up its own copy eventually
    return {"ok": True}


# ---------------------------------------------------------------- memory (the user's saved preferences)

def _memory_item(m) -> dict:
    return {"id": m.memory_id, "kind": str(getattr(m.kind, "value", m.kind)), "content": m.content,
            "updated_at": m.updated_at.timestamp() if getattr(m, "updated_at", None) else None}


@app.get("/api/memory")
async def memory(who: Identity = Depends(require_identity)):
    scope = memory_scope(who, MEMORY_SCOPE)

    def load():
        items = list(_project().beta.memory_stores.list_memories(MEMORY_STORE, scope=scope, limit=100))
        return [_memory_item(m) for m in items]
    try:
        return await asyncio.to_thread(load)
    except Exception as exc:
        raise HTTPException(502, f"Could not read memory: {type(exc).__name__}")


@app.post("/api/memory")
async def add_memory(body: MemoryRequest, who: Identity = Depends(require_identity)):
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "Empty preference")
    scope = memory_scope(who, MEMORY_SCOPE)
    created = await asyncio.to_thread(lambda: _project().beta.memory_stores.create_memory(MEMORY_STORE, scope=scope, content=text, kind="user_profile"))
    return _memory_item(created)


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str, who: Identity = Depends(require_identity)):
    if who.is_user:
        # Deleting is by memory id only, so with several users first check that the memory is in this user's scope.
        scope = memory_scope(who, MEMORY_SCOPE)
        owned = await asyncio.to_thread(lambda: {m.memory_id for m in _project().beta.memory_stores.list_memories(MEMORY_STORE, scope=scope, limit=100)})
        if memory_id not in owned:
            raise HTTPException(404, "Memory not found")
    await asyncio.to_thread(lambda: _project().beta.memory_stores.delete_memory(MEMORY_STORE, memory_id))
    return {"ok": True}


@app.delete("/api/memory")
async def clear_memory(who: Identity = Depends(require_identity)):
    scope = memory_scope(who, MEMORY_SCOPE)
    await asyncio.to_thread(lambda: _project().beta.memory_stores.delete_scope(MEMORY_STORE, scope=scope))
    return {"ok": True}


# ---------------------------------------------------------------- chat

async def _hub_events(http: httpx.AsyncClient, since: int) -> dict | None:
    """Hub events with timestamps shifted onto this machine's clock, so hub and backend events sort correctly."""
    try:
        r = await http.get(f"{_env()['HUB_URL']}/events", params={"since": since}, headers=_hub_headers())
        if r.status_code != 200:
            return None
        batch = r.json()
        if "now" in batch:
            offset = _now_ms() - batch["now"]
            for ev in batch["events"]:
                ev["ts"] += offset
        return batch
    except Exception:
        return None


def _friendly(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if "429" in text or "rate_limit" in text:
        return "The model is rate limited right now. Wait a minute and try again."
    return text[:400]


def _heuristic_title(message: str) -> str:
    text = re.sub(r"\s+", " ", message).strip()
    return (text[:48].rstrip() + "…") if len(text) > 48 else text or "New chat"


def _llm_title(message: str) -> str | None:
    try:
        r = _openai().responses.create(
            model=MODEL,
            instructions="Write a short title (3 to 6 words, no quotes, no trailing punctuation) for a chat that starts with this message from a retail pricing analyst.",
            input=message[:600],
        )
        title = re.sub(r"[\"'.]+$", "", r.output_text.strip().splitlines()[0]).strip()
        return title[:80] or None
    except Exception:
        return None


@app.post("/api/chat")
async def chat(req: ChatRequest, who: Identity = Depends(require_identity)):
    if who.is_user and req.conversation_id and store.owner_of(req.conversation_id) not in (None, who.user_id):
        raise HTTPException(404, "Conversation not found")  # somebody else's conversation: do not reveal that it exists
    scope = memory_scope(who, MEMORY_SCOPE)
    # The memory search tool resolves its scope from this header when the agent is deployed with scope "{{$userId}}" (see docs/APIM.md); a no-op otherwise.
    agent_headers = {"extra_headers": {"x-memory-user-id": scope}} if who.is_user else {}
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    turn_id = uuid.uuid4().hex[:8]

    def put(kind: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

    def run_title(conversation_id: str) -> None:
        title = _llm_title(req.message)
        if title and store.rename(conversation_id, title, who.user_id):
            put("title", {"conversation_id": conversation_id, "title": title})

    def run_agent() -> None:
        started = time.time()
        memory_started: dict[str, float] = {}
        try:
            client = _openai()
            conversation = req.conversation_id or client.conversations.create().id
            put("turn", {"turn_id": turn_id, "conversation_id": conversation})
            put("log", {"level": "info", "msg": f"Turn {turn_id} started on conversation {conversation[-8:]}"})
            text, usage = [], {}
            state = {"id": None, "seq": -1, "done": False}

            def handle(event) -> None:
                nonlocal usage
                kind = getattr(event, "type", "")
                seq = getattr(event, "sequence_number", None)
                if seq is not None:
                    state["seq"] = max(state["seq"], seq)
                if kind == "response.created":
                    state["id"] = event.response.id
                elif kind == "response.output_text.delta":
                    text.append(event.delta)
                    put("delta", {"text": event.delta})
                elif kind in ("response.output_item.added", "response.output_item.done"):
                    item = getattr(event, "item", None)
                    item_type = getattr(item, "type", "")
                    if item_type == "message" and kind.endswith("added") and text:
                        # A reply that follows tool calls is a new message ("Updated: ... Running it now." and then the answer): keep them apart.
                        text.append("\n\n")
                        put("delta", {"text": "\n\n"})
                    if item_type == "memory_search_call":
                        item_id = getattr(item, "id", "")
                        base = {"id": item_id, "server": "memory", "tool": "search_memories", "caller": "cx", "ts": _now_ms(), "seq": -3}
                        if kind.endswith("added"):
                            memory_started[item_id] = time.time()
                            put("hub", {**base, "type": "tool_start", "args": {"scope": scope}})
                        else:
                            tokens = getattr(getattr(item, "usage", None), "embedding_tokens", None)
                            put("hub", {**base, "type": "tool_end", "ok": True, "summary": "searched your saved preferences",
                                        "seconds": round(time.time() - memory_started.get(item_id, time.time()), 2),
                                        "detail": {"search_id": getattr(item, "search_id", None), "memory_update": getattr(item, "update_id", None), "embedding_tokens": tokens}})
                    elif item_type == "mcp_call" and kind.endswith("added"):
                        put("log", {"level": "debug", "msg": f"{CX_AGENT} → {getattr(item, 'server_label', '')}.{getattr(item, 'name', '')}"})
                    elif item_type == "mcp_list_tools" and kind.endswith("added"):
                        put("log", {"level": "debug", "msg": f"{CX_AGENT} loaded tools from {getattr(item, 'server_label', '')}"})
                elif kind == "response.completed":
                    u = getattr(event.response, "usage", None)
                    usage = {"input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None)}
                    state["done"] = True
                elif kind in ("response.failed", "response.incomplete"):
                    err = getattr(getattr(event, "response", None), "error", None)
                    raise RuntimeError(getattr(err, "message", None) or kind)
                elif kind == "error":
                    raise RuntimeError(getattr(event, "message", "stream error"))

            # A long turn (a full report takes many minutes) outlives a single streamed HTTP connection, which the service cuts at about ten minutes.
            # So the response runs in the background, and if the stream drops it is resumed from the last event received.
            reconnects = 0
            stream = client.responses.create(conversation=conversation, input=req.message, stream=True, background=True,
                                             extra_body={"agent_reference": {"name": CX_AGENT, "type": "agent_reference"}}, **agent_headers)
            while not state["done"]:
                try:
                    if stream is None:
                        # Picking the response up again is a network call too: when it fails (a Wi-Fi blip) that is one more try, not the end of the turn.
                        time.sleep(3)
                        stream = client.responses.retrieve(state["id"], stream=True, starting_after=state["seq"], timeout=RESUME_TIMEOUT)
                    for event in stream:
                        handle(event)
                        reconnects = 0  # events are flowing: only an unbroken run of failures counts against the limit
                except RuntimeError:
                    raise
                except Exception as exc:
                    if not state["id"] or reconnects >= MAX_RECONNECTS:
                        raise
                    reconnects += 1
                    put("log", {"level": "warn", "msg": f"Connection to the agent dropped ({type(exc).__name__}); resuming ({reconnects})"})
                    stream = None
                    continue
                if not state["done"]:  # the stream closed cleanly before the response finished: pick it up again
                    if not state["id"] or reconnects >= MAX_RECONNECTS:
                        raise RuntimeError("The agent stream ended before the answer was complete.")
                    reconnects += 1
                    stream = None
            put("final", {"text": "".join(text), "seconds": round(time.time() - started, 1), **usage})
        except Exception as exc:
            put("error", {"message": _friendly(exc)})
        finally:
            put("_done", {})

    async def generate():
        events_acc: list[dict] = []
        logs_acc: list[dict] = []
        conversation_id, turn_no = req.conversation_id, 0
        final_text, final_seconds, status = "", None, "error"
        share_hub_events = not who.is_user or os.getenv("SHARE_HUB_EVENTS", "").lower() in ("1", "true", "yes", "on")

        def out(kind: str, data: dict) -> str:
            if kind == "hub":
                events_acc.append(data)
            elif kind == "log":
                logs_acc.append({**data, "ts": _now_ms()})
            return _sse(kind, data)

        async def hub_events(http: httpx.AsyncClient, since: int) -> dict | None:
            # The hub's event log is not kept per user, so with several users it would show one person's agent calls and SQL to another.
            # Behind the gateway it is therefore off unless SHARE_HUB_EVENTS is set (a demo with one user at a time, or a private deployment).
            return await _hub_events(http, since) if share_hub_events else None

        async with httpx.AsyncClient(timeout=10) as http:
            baseline = await hub_events(http, 10**9)
            since = baseline["latest"] if baseline else 0
            started_ms = _now_ms()
            cx_id = f"cx-{turn_id}"
            yield out("hub", {"seq": -1, "ts": started_ms, "type": "agent_start", "id": cx_id, "agent": "cx", "caller": "user", "ask": req.message[:600]})
            threading.Thread(target=run_agent, daemon=True).start()
            finished, last_poll, usage, ok = False, 0.0, {}, True
            last_beat = time.time()
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    kind, data = None, None
                if kind == "turn":
                    conversation_id = data["conversation_id"]
                    created = store.ensure_conversation(conversation_id, _heuristic_title(req.message), who.user_id)
                    turn_no = store.next_turn(conversation_id)
                    store.add_turn(conversation_id, turn_no, req.message)
                    yield out("turn", {**data, "turn": turn_no, "title": _heuristic_title(req.message), "new": created})
                    if created:
                        threading.Thread(target=run_title, args=(conversation_id,), daemon=True).start()
                elif kind == "_done":
                    finished = True
                elif kind == "final":
                    usage = {k: data.get(k) for k in ("input_tokens", "output_tokens")}
                    final_text, final_seconds, status = data["text"], data["seconds"], "done"
                    yield out("final", data)
                elif kind == "error":
                    ok = False
                    final_text, status = data["message"], "error"
                    yield out("error", data)
                elif kind:
                    yield out(kind, data)
                if finished or time.time() - last_poll >= POLL_SECONDS:
                    if finished:
                        await asyncio.sleep(0.4)
                    last_poll = time.time()
                    batch = await hub_events(http, since)
                    if batch:
                        since = batch["latest"]
                        for ev in batch["events"]:
                            yield out("hub", ev)
                if HEARTBEAT_SECONDS and not finished and time.time() - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = time.time()
                    yield ": keepalive\n\n"
                if finished:
                    yield out("hub", {"seq": -2, "ts": _now_ms(), "type": "agent_end", "id": cx_id, "agent": "cx", "caller": "user", "ok": ok,
                                      "seconds": round((_now_ms() - started_ms) / 1000, 1), **usage})
                    if conversation_id and turn_no:
                        store.finish_turn(conversation_id, turn_no, final_text, status, final_seconds, events_acc, logs_acc)
                    yield out("end", {"turn_id": turn_id, "conversation_id": conversation_id})
                    return

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")
