"""Stub backend: answers every question with a clear "not configured" message.

It is the default, so the hub starts and the whole agent system can be deployed and exercised before any data source is connected: the Data agent
reports the gap instead of inventing numbers. Choose a real backend with GENIE_BACKEND (databricks or custom)."""
from __future__ import annotations

from backends.base import failure

MESSAGE = ("No data source is connected yet. Set GENIE_BACKEND to 'databricks' (and DATABRICKS_HOST and GENIE_SPACE_ID) or to 'custom' "
           "(and CUSTOM_BACKEND) on the hub. See integrations/genie/README.md.")


async def ask(question: str, conversation_id: str = "") -> dict:
    return failure(question, MESSAGE, "stub")
