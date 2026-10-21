"""The hub's data tool (the 'genie' MCP server). The Data agent calls one tool; the hub hands the question to the configured backend (see backends/) and
returns the SQL and rows. Every successful answer is also stored in the evidence ledger under a result id, which the report team cites.

The tool name is part of the agent contract (agents/definitions.py allows it, agents/prompts/data.md names it): keep the two in step if you rename it."""
from __future__ import annotations

import json
from collections import defaultdict

from mcp.server.fastmcp import FastMCP

from backends import get_ask
from events import traced
from settings import GENIE_BACKEND, GENIE_TOOL_DESCRIPTION, MAX_QUESTIONS_PER_CASE, MAX_ROWS
from studio import ledger

TOOL_NAME = "query_space_croma_pricing"
DEFAULT_DESCRIPTION = (
    "Ask the connected data space a business question in natural language, for example 'NMV and merchandise margin % by category for stores in "
    "September 2026'. Returns the SQL that was run and the resulting rows. Give the period, scope and measure in each question, and write each question "
    "so it stands on its own."
)

mcp = FastMCP("genie", host="0.0.0.0", stateless_http=True, json_response=True)
_asked: dict[str, int] = defaultdict(int)


@mcp.tool(name=TOOL_NAME, description=(GENIE_TOOL_DESCRIPTION.strip() or DEFAULT_DESCRIPTION))
@traced("genie", name=TOOL_NAME)
async def query_space(query: str, conversation_id: str = "") -> str:
    cid = conversation_id or "default"
    if _asked[cid] >= MAX_QUESTIONS_PER_CASE:
        return json.dumps({"error": f"Question limit of {MAX_QUESTIONS_PER_CASE} per conversation reached."})
    _asked[cid] += 1
    payload = await get_ask()(query, cid)
    rows = payload.get("rows")
    if isinstance(rows, list) and len(rows) > MAX_ROWS:
        payload["rows"], payload["truncated_at_max_rows"] = rows[:MAX_ROWS], True
        payload["row_count"], payload["max_rows"] = MAX_ROWS, MAX_ROWS
    rid = ledger.register_genie(cid, payload)
    if rid:
        payload["result_id"] = rid
    payload.setdefault("backend", GENIE_BACKEND)
    return json.dumps(payload, default=str)
