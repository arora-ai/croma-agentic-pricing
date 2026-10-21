"""Shared case context (the 'case context' box in the architecture): what was asked, what each agent found, and the finished report.
In-memory for the test (single replica). In the end state this is Foundry thread state plus a durable store."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from events import traced
mcp = FastMCP("case-context", host="0.0.0.0", stateless_http=True, json_response=True)

_cases: dict[str, dict] = {}
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot(case_id: str) -> dict | None:
    with _lock:
        return json.loads(json.dumps(_cases.get(case_id))) if case_id in _cases else None


@mcp.tool()
@traced("context")
def create_case(request_summary: str) -> str:
    """Open a new case once the user's request is confirmed. Returns the case_id to pass to every agent working on it.
    request_summary: the confirmed request in one or two sentences (scope, period, KPI, what the user wants to decide)."""
    case_id = "C-" + uuid.uuid4().hex[:8]
    with _lock:
        _cases[case_id] = {"case_id": case_id, "created_at": _now(), "request": request_summary, "findings": [], "reports": []}
    return json.dumps({"case_id": case_id})


@mcp.tool()
@traced("context")
def save_finding(case_id: str, agent: str, title: str, finding: str) -> str:
    """Record a finding on the case so other agents can build on it. Include the numbers and where they came from (table, period, filters).
    agent: your own name, for example research or analysis."""
    with _lock:
        case = _cases.get(case_id)
        if not case:
            return json.dumps({"error": f"Unknown case_id {case_id}"})
        case["findings"].append({"n": len(case["findings"]) + 1, "at": _now(), "agent": agent, "title": title, "finding": finding})
        return json.dumps({"saved": True, "findings_on_case": len(case["findings"])})


@mcp.tool()
@traced("context")
def get_case(case_id: str) -> str:
    """Read the whole case: the confirmed request, every finding saved so far and any reports."""
    case = snapshot(case_id)
    return json.dumps(case if case else {"error": f"Unknown case_id {case_id}"})


@mcp.tool()
@traced("context")
def save_report(case_id: str, title: str, markdown: str) -> str:
    """Store a finished plain-text report on the case. (The report studio's render_report publishes designed reports; this is for simple text.)"""
    with _lock:
        case = _cases.get(case_id)
        if not case:
            return json.dumps({"error": f"Unknown case_id {case_id}"})
        case["reports"].append({"n": len(case["reports"]) + 1, "at": _now(), "title": title, "markdown": markdown})
        return json.dumps({"saved": True, "report_number": len(case["reports"])})


def restore(case: dict) -> None:
    with _lock:
        _cases[case["case_id"]] = case


def add_report(case_id: str, title: str, markdown: str, artifact: dict) -> None:
    """Attach a published, designed report (DOCX and HTML) to the case; called by the report studio."""
    with _lock:
        case = _cases.setdefault(case_id, {"case_id": case_id, "created_at": _now(), "request": "", "findings": [], "reports": []})
        case["reports"].append({"n": len(case["reports"]) + 1, "at": _now(), "title": title, "markdown": markdown, "artifact": artifact})
