"""The hub's SharePoint tools (the 'sharepoint' MCP server). The SharePoint agent calls two tools: search_sharepoint finds files in the connected library, and
read_sharepoint_file reads one. A spreadsheet or CSV comes back as a table and a document as passages of text; either way the result is stored in the evidence ledger
under a result id (R7), exactly as a Databricks answer is, so the analytics engine can compute on it and the number audit can trace figures quoted from it.

The tool names are part of the agent contract (agents/definitions.py allows them, agents/prompts/sharepoint.md names them): keep them in step if you rename them."""
from __future__ import annotations

import json
from collections import defaultdict

from mcp.server.fastmcp import FastMCP

from events import traced
from settings import (SHAREPOINT_BACKEND, SHAREPOINT_MAX_CALLS_PER_CASE, SHAREPOINT_MAX_PASSAGES, SHAREPOINT_MAX_ROWS, SHAREPOINT_TOOL_DESCRIPTION)
from sharepoint import get_backend, parse
from sharepoint.base import SharePointError
from studio import ledger

SEARCH_TOOL = "search_sharepoint"
READ_TOOL = "read_sharepoint_file"
SEARCH_DESCRIPTION = (
    "Find files in the connected SharePoint library: spreadsheets and CSV files (plans, targets, calendars, price lists), and Word, PDF, PowerPoint or text documents "
    "(policies, briefs, notes). Give a few keywords from the topic, for example 'festive promotion calendar September 2026'. An empty query lists what the library holds. "
    "Returns each file's id, name, folder, last modified date and kind (table or document); pass the id to read_sharepoint_file."
)
READ_DESCRIPTION = (
    "Read one file found by search_sharepoint. A spreadsheet or CSV returns its rows as a table: name the sheet in 'sheet' when the workbook has several, and give 'filter' to "
    "keep only the rows you need, as column=value terms joined with ';' (operators = != > >= < <=; for example 'category=Laptops; week_start>=2026-08-03; week_start<=2026-09-27'). "
    "A document returns its passages of text: give a few keywords in 'focus' to get the passages about them. The result is stored as evidence and returns a result_id (for example R7)."
)

mcp = FastMCP("sharepoint", host="0.0.0.0", stateless_http=True, json_response=True)
_calls: dict[str, int] = defaultdict(int)


def _fail(message: str) -> str:
    return json.dumps({"error": message, "backend": SHAREPOINT_BACKEND})


def _budget(cid: str) -> str | None:
    if _calls[cid] >= SHAREPOINT_MAX_CALLS_PER_CASE:
        return _fail(f"SharePoint call limit of {SHAREPOINT_MAX_CALLS_PER_CASE} per conversation reached.")
    _calls[cid] += 1
    return None


@mcp.tool(name=SEARCH_TOOL, description=(SEARCH_DESCRIPTION + " " + SHAREPOINT_TOOL_DESCRIPTION.strip()).strip())
@traced("sharepoint", name=SEARCH_TOOL)
async def search_sharepoint(query: str = "", conversation_id: str = "") -> str:
    cid = conversation_id or "default"
    if (over := _budget(cid)):
        return over
    try:
        files = await get_backend().search(query, 10)
    except SharePointError as exc:
        return _fail(str(exc))
    shown = [{k: f[k] for k in ("id", "name", "path", "site", "modified", "size", "kind")} for f in files]
    return json.dumps({"query": query, "count": len(shown), "files": shown, "backend": SHAREPOINT_BACKEND}, default=str)


def _origin(entry: dict, sheet: str = "") -> str:
    parts = [entry["name"] + (f" (sheet {sheet})" if sheet else "")]
    if entry.get("site"):
        parts.append(entry["site"])
    if entry.get("modified"):
        parts.append(f"last modified {entry['modified']}")
    return " \u00b7 ".join(parts)


@mcp.tool(name=READ_TOOL, description=READ_DESCRIPTION)
@traced("sharepoint", name=READ_TOOL)
async def read_sharepoint_file(file_id: str, sheet: str = "", focus: str = "", filter: str = "", conversation_id: str = "") -> str:
    cid = conversation_id or "default"
    if (over := _budget(cid)):
        return over
    try:
        entry, data = await get_backend().fetch(file_id)
        name = entry["name"]
        if entry["kind"] == "table":
            sheets = parse.read_tables(name, data)
            if not sheets:
                return _fail(f"{name} has no table in it (no header row with data under it)")
            names = [s.name for s in sheets]
            chosen = next((s for s in sheets if s.name.lower() == sheet.strip().lower()), None) if sheet.strip() else sheets[0]
            if chosen is None:
                return _fail(f"{name} has no sheet '{sheet}'. Its sheets are: {', '.join(names)}")
            kept, applied = parse.filter_rows(chosen, filter)
            if not kept:
                return _fail(f"{applied or 'No rows'} No row of sheet '{chosen.name}' of {name} matches it. The sheet has {len(chosen.rows)} rows; its columns are: {', '.join(chosen.columns)}")
            total = len(kept)
            rows = kept[:SHAREPOINT_MAX_ROWS]
            payload = {"question": f"{name} \u00b7 {chosen.name}" + (f" \u00b7 {filter.strip()}" if filter.strip() else ""), "kind": "sharepoint",
                       "description": f"Rows of sheet '{chosen.name}' of {name}. {applied}".strip(), "columns": chosen.columns, "rows": rows,
                       "row_count": len(rows), "truncated_at_max_rows": total > len(rows), "origin": _origin(entry, chosen.name if parse.extension(name) in (".xlsx", ".xlsm") else ""),
                       "sheets": names, "filter": applied, "file": {k: entry[k] for k in ("id", "name", "path", "site", "modified")}}
        else:
            passages = parse.read_passages(name, data)
            if not passages:
                return _fail(f"{name} has no readable text")
            picked = parse.pick_passages(passages, focus, SHAREPOINT_MAX_PASSAGES)
            payload = {"question": f"{name}" + (f" about {focus.strip()}" if focus.strip() else ""), "kind": "document", "description": f"Passages of {name}", "columns": ["section", "text"],
                       "rows": [[s, t] for s, t in picked], "row_count": len(picked), "truncated_at_max_rows": len(picked) < len(passages), "passages_in_file": len(passages),
                       "origin": _origin(entry), "file": {k: entry[k] for k in ("id", "name", "path", "site", "modified")}}
    except (SharePointError, parse.ParseError) as exc:
        return _fail(str(exc))
    rid = ledger.register_file(cid, payload)
    if rid:
        payload["result_id"] = rid
    payload["backend"] = SHAREPOINT_BACKEND
    return json.dumps(payload, default=str)
