"""Calls a harness worker (a Foundry agent with no tools) and returns a validated, typed reply.

The worker is given one JSON message {task, ...inputs} and must answer with one JSON object. The reply is parsed into a pydantic model and, if
it does not fit, the exact problem is sent back once so the worker can correct itself. Each call is counted by the governor against the token
quota and is reported as an agent call, so the admin view shows the workers like any other agent."""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from events import emit
from foundry_runs import invoke, trace
from harness.governor import Governor
from settings import WORKER_PARALLELISM, WORKER_TIMEOUT_S, WORKER_TPM_BUDGET

T = TypeVar("T", bound=BaseModel)
Invoker = Callable[[str, str], Awaitable[tuple[str, dict]]]


class WorkerError(RuntimeError):
    pass


async def _foundry(role: str, text: str) -> tuple[str, dict]:
    return await asyncio.to_thread(invoke, role, text)


_invoker: Invoker = _foundry
_governor = Governor(WORKER_TPM_BUDGET, WORKER_PARALLELISM)


def set_invoker(fn: Invoker | None) -> None:
    """Replaces how a worker is called (tests use a scripted one); None restores Foundry."""
    global _invoker
    _invoker = fn or _foundry


def set_governor(gov: Governor) -> None:
    global _governor
    _governor = gov


def extract_json(text: str) -> Any:
    """The first JSON object in a reply, tolerating a code fence or a sentence around it."""
    s = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.S | re.I)
    if fenced:
        s = fenced.group(1)
    start = s.find("{")
    if start < 0:
        raise ValueError("the reply contains no JSON object")
    try:
        obj, _end = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"the JSON is not valid ({exc.msg} at line {exc.lineno}, column {exc.colno}); it may have been cut off")
    if not isinstance(obj, dict):
        raise ValueError("the reply must be a JSON object")
    return obj


def describe(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for e in exc.errors()[:8]:
            where = ".".join(str(p) for p in e["loc"]) or "reply"
            parts.append(f"{where}: {str(e['msg']).removeprefix('Value error, ')}")
        return "; ".join(parts)
    return str(exc)


async def _once(role: str, task: str, message: str, case_id: str, parent: str, est_output: int) -> tuple[str, dict]:
    call_id = uuid.uuid4().hex[:8]
    started = time.time()
    entry = {"at": time.strftime("%H:%M:%S"), "agent": role, "case_id": case_id, "ask": task}
    emit("agent_start", id=call_id, agent=role, caller=parent, case_id=case_id, ask=task)
    ok, usage, reply = False, {}, ""
    try:
        async with _governor.slot(int(len(message) / 3.2) + est_output) as slot:
            reply, usage = await asyncio.wait_for(_invoker(role, message), WORKER_TIMEOUT_S)
            actual = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
            if actual:
                slot[1] = float(actual)
        ok = True
        entry.update(ok=True, answer=reply[:300], **usage)
        return reply, usage
    except Exception as exc:
        entry.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        raise
    finally:
        entry["seconds"] = round(time.time() - started, 1)
        trace.append(entry)
        emit("agent_end", id=call_id, agent=role, caller=parent, case_id=case_id, ok=ok, seconds=entry["seconds"], answer=reply[:800], **usage)


async def call_worker(role: str, task: str, payload: dict, schema: type[T], *, case_id: str, parent: str, check: Callable[[T], None] | None = None,
                      max_repairs: int = 1, est_output: int = 3500) -> T:
    """check: extra validation beyond the schema; raise ValueError with a message the worker can act on."""
    body: dict[str, Any] = {"task": task, **payload}
    problem, previous = "", ""
    for _attempt in range(max_repairs + 1):
        sent = body if not problem else {**body, "previous_reply": previous[:6000], "problem": problem,
                                         "instruction": "Your previous reply could not be used. Fix exactly the problem described and return the corrected JSON object only."}
        reply, _usage = await _once(role, task, json.dumps(sent, ensure_ascii=False, default=str), case_id, parent, est_output)
        try:
            parsed = schema.model_validate(extract_json(reply))
            if check:
                check(parsed)
            return parsed
        except ValueError as exc:  # pydantic's ValidationError and JSON errors are both ValueErrors
            problem, previous = describe(exc), reply
    raise WorkerError(f"the {role} did not return a usable '{task}' reply: {problem}")
