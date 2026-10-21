"""A synthetic Server-Sent Events stream for checking a gateway's streaming behaviour without involving an agent: one event every `interval` seconds.
Off unless ENABLE_STREAM_CHECK is set, so it does nothing in a normal deployment. infra/apim/test_gateway.ps1 uses it to show that events reach the
client as they are produced instead of all at once when the response ends (response buffering), and that a long silence survives the four-minute idle
timeout of the load balancers when keep-alive comments are sent (the same SSE_HEARTBEAT_S the chat stream uses; `heartbeat` overrides it, 0 = none)."""
from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()


def enabled() -> bool:
    return os.getenv("ENABLE_STREAM_CHECK", "").lower() in ("1", "true", "yes", "on")


@router.get("/api/stream-check")
async def stream_check(count: int = 10, interval: float = 1.0, heartbeat: float | None = None):
    if not enabled():
        raise HTTPException(404, "Not found")
    count, interval = max(1, min(count, 600)), max(0.1, min(interval, 900.0))
    beat = float(os.getenv("SSE_HEARTBEAT_S", "0")) if heartbeat is None else max(0.0, heartbeat)

    async def generate():
        for i in range(count):
            yield f"event: tick\ndata: {json.dumps({'i': i, 'of': count, 't': round(time.time() * 1000)})}\n\n"
            remaining = interval if i < count - 1 else 0.0
            while remaining > 0:
                step = min(remaining, beat) if beat > 0 else remaining
                await asyncio.sleep(step)
                remaining -= step
                if remaining > 0 and beat > 0:
                    yield ": keepalive\n\n"
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
