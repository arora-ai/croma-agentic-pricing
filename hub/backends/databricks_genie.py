"""Databricks Genie backend: a question in, the SQL and the rows out.

Configure with environment variables (see .env.example and integrations/genie/README.md):
    DATABRICKS_HOST   https://adb-<workspace-id>.<n>.azuredatabricks.net
    GENIE_SPACE_ID    the id of the Genie space (the string in the space's URL)
    DATABRICKS_AUTH   managed_identity (default) | token

managed_identity: the hub signs in with an Entra token for Azure Databricks. In Azure that is the Container App's managed identity; locally it is your
az login; with AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET set it is that Entra service principal. Whichever identity it is must be a user or
service principal in the workspace, with CAN RUN on the Genie space, CAN USE on the SQL warehouse and SELECT on the tables the space uses.
token: a Databricks personal access token or OAuth token in DATABRICKS_TOKEN. Handy for a quick test; prefer managed_identity for anything shared.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from backends.base import failure
from settings import DATABRICKS_AUTH, DATABRICKS_HOST, DATABRICKS_TOKEN, GENIE_SPACE_ID, GENIE_TIMEOUT_S, MAX_ROWS

DATABRICKS_RESOURCE = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"  # the Azure Databricks first-party application (a public, well-known id)
POLL_SECONDS = 1.5
NUMERIC = {"BYTE", "SHORT", "INT", "LONG", "FLOAT", "DOUBLE", "DECIMAL"}
NAME = "databricks-genie"


def _token() -> str:
    if DATABRICKS_AUTH == "token":
        if not DATABRICKS_TOKEN:
            raise RuntimeError("DATABRICKS_AUTH=token but DATABRICKS_TOKEN is not set")
        return DATABRICKS_TOKEN
    from azure_clients import credential

    return credential().get_token(f"{DATABRICKS_RESOURCE}/.default").token


def _value(raw, type_name: str):
    if raw is None:
        return None
    if type_name in NUMERIC:
        try:
            number = float(raw)
            return int(number) if number.is_integer() and type_name not in ("FLOAT", "DOUBLE", "DECIMAL") else number
        except ValueError:
            return raw
    return raw


async def ask(question: str, conversation_id: str = "") -> dict:
    if not DATABRICKS_HOST or not GENIE_SPACE_ID:
        return failure(question, "The Databricks backend is not configured: set DATABRICKS_HOST and GENIE_SPACE_ID.", NAME)
    started = time.time()
    base = f"{DATABRICKS_HOST}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"
    try:
        token = await asyncio.to_thread(_token)
    except Exception as exc:
        return failure(question, f"Could not get a Databricks token ({type(exc).__name__}: {str(exc)[:160]}).", NAME)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=60, headers=headers) as http:
        # Each question is a fresh Genie conversation: the Data agent writes self-contained questions, and independent questions can then run in
        # parallel without queueing behind each other in one conversation.
        r = await http.post(f"{base}/start-conversation", json={"content": question})
        if r.status_code >= 400:
            return failure(question, f"Genie request failed ({r.status_code}): {r.text[:300]}", NAME)
        started_msg = r.json()
        genie_conversation = started_msg.get("conversation_id")
        message_id = started_msg.get("message_id") or started_msg.get("id")
        url = f"{base}/conversations/{genie_conversation}/messages/{message_id}"

        message = {}
        while time.time() - started < GENIE_TIMEOUT_S:
            m = await http.get(url)
            if m.status_code >= 400:
                return failure(question, f"Genie status check failed ({m.status_code})", NAME)
            message = m.json()
            if message.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
                break
            await asyncio.sleep(POLL_SECONDS)
        status = message.get("status")
        if status != "COMPLETED":
            detail = (message.get("error") or {}).get("error") or status or "timed out"
            return failure(question, f"Genie could not answer within {int(GENIE_TIMEOUT_S)} seconds ({detail}). The warehouse may be starting; ask again, "
                                     "or ask a simpler question.", NAME)

        attachments = message.get("attachments", [])
        query = next((a for a in attachments if a.get("query")), None)
        notes = " ".join(a["text"]["content"] for a in attachments if a.get("text", {}).get("content"))
        if not query:
            return failure(question, notes or "Genie did not produce a query for this question. Rephrase it with the period, scope and measure spelled out.", NAME)

        result = await http.get(f"{url}/attachments/{query['attachment_id']}/query-result")
        if result.status_code >= 400:
            return {**failure(question, f"Genie ran the query but the result could not be read ({result.status_code})", NAME), "sql": query["query"].get("query")}
        statement = result.json().get("statement_response", {})

    columns = statement.get("manifest", {}).get("schema", {}).get("columns", [])
    raw_rows = statement.get("result", {}).get("data_array", []) or []
    rows = [[_value(v, columns[i]["type_name"]) for i, v in enumerate(row)] for row in raw_rows[:MAX_ROWS]]
    total = statement.get("manifest", {}).get("total_row_count", len(raw_rows))
    return {
        "question": question, "description": query["query"].get("description", ""), "sql": query["query"].get("query"),
        "columns": [c["name"] for c in columns], "rows": rows, "row_count": len(rows),
        "truncated_at_max_rows": total > len(rows) or bool(statement.get("manifest", {}).get("truncated")), "max_rows": MAX_ROWS,
        "elapsed_s": round(time.time() - started, 1), "backend": NAME, "genie_conversation_id": genie_conversation,
        **({"notes": notes} if notes else {}),
    }
