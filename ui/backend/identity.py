"""Who is calling the chat API, and can that be believed?

Behind the API gateway (Azure API Management) the gateway authenticates the caller, with an Entra ID token or an APIM subscription key, and tells this API
who it is: `x-user-id` (the token's object id, or `sub-<subscription>` for a key) and `x-user-name`. Those headers are believed only when the request also
carries the shared secret in `x-gateway-key`, equal to the GATEWAY_KEY environment variable, so a request that reaches the container directly (not through
the gateway) cannot pretend to be someone.

    GATEWAY_KEY not set   single-user mode, exactly as before the gateway existed: the headers are ignored, there is one default user and one memory scope.
    GATEWAY_KEY set       every /api request must carry the key (401 otherwise) and a well-formed x-user-id (401 if absent, 400 if malformed).
                          Conversation history and the Foundry memory scope are then kept per user.
"""
from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request

USER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,127}")
CORRELATION_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")


@dataclass(frozen=True)
class Identity:
    user_id: str = ""  # "" is the default single user
    name: str = ""

    @property
    def is_user(self) -> bool:
        return bool(self.user_id)


SINGLE_USER = Identity()


def gateway_key() -> str:
    return os.getenv("GATEWAY_KEY", "")


def require_identity(request: Request) -> Identity:
    """FastAPI dependency: the caller's identity, or an HTTP error. Applied to every API route (see main.py)."""
    key = gateway_key()
    if not key:
        return SINGLE_USER
    supplied = request.headers.get("x-gateway-key", "")
    if not hmac.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
        raise HTTPException(401, "This API is reachable only through the gateway.")
    user_id = request.headers.get("x-user-id", "").strip()
    if not user_id:
        raise HTTPException(401, "The gateway did not identify the caller.")
    if not USER_ID.fullmatch(user_id):
        raise HTTPException(400, "Malformed user id.")
    return Identity(user_id=user_id, name=request.headers.get("x-user-name", "").strip()[:120])


def memory_scope(who: Identity, base: str) -> str:
    """The Foundry memory scope: the shared default one, or one per user."""
    return f"{base}-{who.user_id}" if who.user_id else base


class CorrelationId:
    """Echoes a well-formed x-correlation-id request header on the response. Pure ASGI, so a streamed response is not buffered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        value = next((v.decode("latin-1") for k, v in scope["headers"] if k == b"x-correlation-id"), "")
        if not CORRELATION_ID.fullmatch(value):
            await self.app(scope, receive, send)
            return

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"x-correlation-id", value.encode("latin-1"))]
            await send(message)

        await self.app(scope, receive, send_with_id)
