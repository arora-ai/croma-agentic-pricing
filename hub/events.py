"""Live event log for the admin view: every agent call and tool call, with start and end, so a UI can light up the agent that is working.
In memory, single replica. In the end state this is OpenTelemetry traces in Application Insights."""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import threading
import time
import uuid
from collections import deque

_lock = threading.Lock()
_seq = 0
EVENTS: deque[dict] = deque(maxlen=3000)
_active: list[tuple[str, str, str]] = []  # (call id, case_id, agent) for agent calls in flight, oldest first

POST_AGENTS = ("lookup", "research", "analysis", "reporting", "planner", "insight", "strategist", "writer", "reviewer")


def emit(type: str, **fields) -> dict:
    global _seq
    with _lock:
        _seq += 1
        event = {"seq": _seq, "ts": round(time.time() * 1000), "type": type, **fields}
        EVENTS.append(event)
        if type == "agent_start":
            _active.append((fields["id"], fields.get("case_id", ""), fields["agent"]))
        elif type == "agent_end":
            _active[:] = [a for a in _active if a[0] != fields["id"]]
        return event


def since(seq: int, limit: int = 500) -> dict:
    with _lock:
        rows = [e for e in EVENTS if e["seq"] > seq][:limit]
    return {"latest": _seq, "now": round(time.time() * 1000), "events": rows}


def infer_caller(case_id: str, candidates: tuple[str, ...] = POST_AGENTS) -> str:
    """Best-effort: the most recently started agent call on this case that could have made the call. Falls back to the CX agent."""
    with _lock:
        for _id, case, agent in reversed(_active):
            if agent in candidates and (not case_id or case == case_id):
                return agent
        for _id, _case, agent in reversed(_active):
            if agent in candidates:
                return agent
    return "cx"


def _clip(value, n: int = 500):
    if isinstance(value, str):
        return value if len(value) <= n else value[:n] + "…"
    if isinstance(value, dict):
        return {k: _clip(v, n) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v, n) for v in value[:8]]
    return value


def _detail(server: str, tool: str, args: dict, result: str) -> tuple[dict, bool, str]:
    """Readable detail, ok flag and one-line summary for a tool call."""
    parsed = None
    try:
        parsed = json.loads(result)
    except Exception:
        pass
    ok = not (isinstance(parsed, dict) and parsed.get("error"))
    if server == "genie" and isinstance(parsed, dict):
        rows = parsed.get("rows") or []
        detail = {"question": args.get("query"), "description": parsed.get("description"), "sql": parsed.get("sql") or parsed.get("last_sql"),
                  "columns": parsed.get("columns"), "rows": _clip(rows[:5]), "row_count": parsed.get("row_count"),
                  "truncated": parsed.get("truncated_at_max_rows"), "error": parsed.get("error"), "backend": parsed.get("backend", "mock"),
                  "statement_id": parsed.get("statement_id")}
        summary = f"{parsed.get('row_count', 0)} rows" if ok else str(parsed.get("error"))[:120]
        return detail, ok, summary
    if server == "sharepoint" and isinstance(parsed, dict):
        if tool == "search_sharepoint":
            detail = {"query": args.get("query"), "files": _clip([f"{f.get('name')} ({f.get('kind')}, {f.get('modified')})" for f in parsed.get("files") or []]), "error": parsed.get("error"),
                      "backend": parsed.get("backend")}
            return detail, ok, (f"{parsed.get('count', 0)} file(s)" if ok else str(parsed.get("error"))[:120])
        rows = parsed.get("rows") or []
        detail = {"file": (parsed.get("file") or {}).get("name"), "origin": parsed.get("origin"), "sheet": args.get("sheet") or None, "focus": args.get("focus") or None,
                  "filter": args.get("filter") or None,
                  "kind": parsed.get("kind"), "columns": parsed.get("columns"), "rows": _clip(rows[:5]), "row_count": parsed.get("row_count"), "truncated": parsed.get("truncated_at_max_rows"),
                  "error": parsed.get("error"), "backend": parsed.get("backend")}
        what = "passages" if parsed.get("kind") == "document" else "rows"
        return detail, ok, (f"{parsed.get('row_count', 0)} {what} from {(parsed.get('file') or {}).get('name', 'file')}" if ok else str(parsed.get("error"))[:120])
    detail = {"args": _clip(args), "result": _clip(parsed if parsed is not None else result)}
    return detail, ok, ("ok" if ok else str(parsed.get("error"))[:120])


def traced(server: str, name: str | None = None):
    """Wrap an MCP tool function so each call emits tool_start and tool_end events."""
    def deco(fn):
        tool = name or fn.__name__
        sig = inspect.signature(fn)

        def begin(args, kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            call_args = dict(bound.arguments)
            case_id = call_args.get("case_id") or call_args.get("conversation_id") or ""
            if server == "genie":
                caller = "data"
            elif server == "sharepoint":
                caller = "sharepoint"
            elif tool == "create_case":
                caller = "cx"
            elif tool in ("save_report", "render_report"):
                caller = "reporting"
            elif tool == "save_finding" and call_args.get("agent") in POST_AGENTS:
                caller = call_args["agent"]
            else:
                caller = infer_caller(case_id, ("cx", *POST_AGENTS)) if tool == "get_case" else infer_caller(case_id)
            call_id = uuid.uuid4().hex[:8]
            emit("tool_start", id=call_id, server=server, tool=tool, caller=caller, case_id=case_id, args=_clip(call_args, 300))
            return call_id, call_args, caller, case_id, time.time()

        def finish(state, result):
            call_id, call_args, caller, case_id, started = state
            result_text = result if isinstance(result, str) else json.dumps(result, default=str)
            detail, ok, summary = _detail(server, tool, call_args, result_text)
            if tool == "create_case":
                try:
                    case_id = json.loads(result_text).get("case_id", case_id)
                except Exception:
                    pass
            emit("tool_end", id=call_id, server=server, tool=tool, caller=caller, case_id=case_id, ok=ok, summary=summary, detail=detail,
                 seconds=round(time.time() - started, 2))

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                state = begin(args, kwargs)
                try:
                    result = await fn(*args, **kwargs)
                except Exception as exc:
                    finish(state, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
                    raise
                finish(state, result)
                return result
        else:
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                state = begin(args, kwargs)
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:
                    finish(state, json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
                    raise
                finish(state, result)
                return result
        return wrapper
    return deco
