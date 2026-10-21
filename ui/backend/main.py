"""Backend for the Croma assistant UI.

Streams one chat turn to the browser as server-sent events, merging two sources:
  * the CX agent's answer, streamed from Foundry as it is written
  * the hub's live event log (agent calls, tool calls, SQL), polled while the turn runs, so the admin view can light up each agent
It also keeps the conversation history (SQLite) and exposes the user's Foundry memory (preferences) for viewing and editing.

Run from the agentic-engine folder:   uvicorn ui.backend.main:app --port 8000
Signs in to Foundry with your az login; reads HUB_URL and HUB_API_KEY from ../../.env.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.deploy_agents import load_env, project
from agents.memory_store import MEMORY_SCOPE, MEMORY_STORE
from ui.backend import store

DIST = Path(__file__).parent.parent / "frontend" / "dist"
CX_AGENT = "croma-cx"
MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
POLL_SECONDS = 0.5

app = FastAPI(title="Croma assistant")


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
async def health():
    async with httpx.AsyncClient(timeout=10) as http:
        genie_backend = "unknown"
        try:
            r = await http.get(f"{_env()['HUB_URL']}/healthz")
            hub = r.json().get("status") == "ok"
            genie_backend = r.json().get("genie_backend", "mock")
        except Exception:
            hub = False
    return {"hub": hub, "hub_url": _env()["HUB_URL"], "project": _env()["FOUNDRY_PROJECT_ENDPOINT"].rsplit("/", 1)[-1], "cx_agent": CX_AGENT,
            "memory_store": MEMORY_STORE, "memory_scope": MEMORY_SCOPE, "genie_backend": genie_backend}


@app.get("/api/case/{case_id}")
async def case(case_id: str):
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(f"{_env()['HUB_URL']}/case/{case_id}", headers=_hub_headers())
    if r.status_code != 200:
        raise HTTPException(r.status_code, "Case not found")
    return r.json()


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
def conversations():
    return store.list_conversations()


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str):
    found = store.get_conversation(conversation_id)
    if not found:
        raise HTTPException(404, "Conversation not found")
    return found


@app.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, body: RenameRequest):
    if not store.rename(conversation_id, body.title):
        raise HTTPException(404, "Conversation not found")
    return {"ok": True}


@app.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    if not store.delete(conversation_id):
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
async def memory():
    def load():
        items = list(_project().beta.memory_stores.list_memories(MEMORY_STORE, scope=MEMORY_SCOPE, limit=100))
        return [_memory_item(m) for m in items]
    try:
        return await asyncio.to_thread(load)
    except Exception as exc:
        raise HTTPException(502, f"Could not read memory: {type(exc).__name__}")


@app.post("/api/memory")
async def add_memory(body: MemoryRequest):
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "Empty preference")
    created = await asyncio.to_thread(lambda: _project().beta.memory_stores.create_memory(MEMORY_STORE, scope=MEMORY_SCOPE, content=text, kind="user_profile"))
    return _memory_item(created)


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str):
    await asyncio.to_thread(lambda: _project().beta.memory_stores.delete_memory(MEMORY_STORE, memory_id))
    return {"ok": True}


@app.delete("/api/memory")
async def clear_memory():
    await asyncio.to_thread(lambda: _project().beta.memory_stores.delete_scope(MEMORY_STORE, scope=MEMORY_SCOPE))
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
async def chat(req: ChatRequest):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    turn_id = uuid.uuid4().hex[:8]

    def put(kind: str, data: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

    def run_title(conversation_id: str) -> None:
        title = _llm_title(req.message)
        if title and store.rename(conversation_id, title):
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
                    if item_type == "memory_search_call":
                        item_id = getattr(item, "id", "")
                        base = {"id": item_id, "server": "memory", "tool": "search_memories", "caller": "cx", "ts": _now_ms(), "seq": -3}
                        if kind.endswith("added"):
                            memory_started[item_id] = time.time()
                            put("hub", {**base, "type": "tool_start", "args": {"scope": MEMORY_SCOPE}})
                        else:
                            tokens = getattr(getattr(item, "usage", None), "embedding_tokens", None)
                            put("hub", {**base, "type": "tool_end", "ok": True, "summary": "searched your saved preferences",
                                        "seconds": round(time.time() - memory_started.get(item_id, time.time()), 2),
                                        "detail": {"search_id": getattr(item, "search_id", None), "memory_update": getattr(item, "update_id", None), "embedding_tokens": tokens}})
                    elif item_type == "mcp_call" and kind.endswith("added"):
                        put("log", {"level": "debug", "msg": f"croma-cx → {getattr(item, 'server_label', '')}.{getattr(item, 'name', '')}"})
                    elif item_type == "mcp_list_tools" and kind.endswith("added"):
                        put("log", {"level": "debug", "msg": f"croma-cx loaded tools from {getattr(item, 'server_label', '')}"})
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
            stream = client.responses.create(conversation=conversation, input=req.message, stream=True, background=True,
                                             extra_body={"agent_reference": {"name": CX_AGENT, "type": "agent_reference"}})
            reconnects = 0
            while not state["done"]:
                try:
                    for event in stream:
                        handle(event)
                except RuntimeError:
                    raise
                except Exception as exc:
                    if not state["id"] or reconnects >= 40:
                        raise
                    reconnects += 1
                    put("log", {"level": "warn", "msg": f"Connection to the agent dropped ({type(exc).__name__}); resuming ({reconnects})"})
                    time.sleep(3)
                    stream = client.responses.retrieve(state["id"], stream=True, starting_after=state["seq"])
                    continue
                if not state["done"]:  # the stream closed cleanly before the response finished: pick it up again
                    if not state["id"] or reconnects >= 40:
                        raise RuntimeError("The agent stream ended before the answer was complete.")
                    reconnects += 1
                    time.sleep(3)
                    stream = client.responses.retrieve(state["id"], stream=True, starting_after=state["seq"])
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

        def out(kind: str, data: dict) -> str:
            if kind == "hub":
                events_acc.append(data)
            elif kind == "log":
                logs_acc.append({**data, "ts": _now_ms()})
            return _sse(kind, data)

        async with httpx.AsyncClient(timeout=10) as http:
            baseline = await _hub_events(http, 10**9)
            since = baseline["latest"] if baseline else 0
            started_ms = _now_ms()
            cx_id = f"cx-{turn_id}"
            yield out("hub", {"seq": -1, "ts": started_ms, "type": "agent_start", "id": cx_id, "agent": "cx", "caller": "user", "ask": req.message[:600]})
            threading.Thread(target=run_agent, daemon=True).start()
            finished, last_poll, usage, ok = False, 0.0, {}, True
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    kind, data = None, None
                if kind == "turn":
                    conversation_id = data["conversation_id"]
                    created = store.ensure_conversation(conversation_id, _heuristic_title(req.message))
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
                    batch = await _hub_events(http, since)
                    if batch:
                        since = batch["latest"]
                        for ev in batch["events"]:
                            yield out("hub", ev)
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
