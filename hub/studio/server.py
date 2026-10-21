"""Report studio MCP server: the tools the report team (planner, insight analyst, strategist, writer, reviewer, editor) work with.

Agents think and write; this server owns the numbers and the layout. Evidence lives in the ledger (R/T/D ids), the working papers (blueprint,
insights, actions, document sections) live in a per-case workspace, and rendering is deterministic: the same saved document always produces the
same DOCX and HTML, and no figure reaches the page that is not in the ledger.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

import case_context
from events import traced
from studio import audit as audit_mod
from studio import cites, ledger, render_docx, render_html, resolve, spec, tidy

mcp = FastMCP("report-studio", host="0.0.0.0", stateless_http=True, json_response=True)
HERE = Path(__file__).parent
_lock = threading.RLock()
_work: dict[str, dict] = {}
_artifacts: dict[tuple[str, int], dict] = {}


def _ws(case_id: str) -> dict:
    with _lock:
        return _work.setdefault(case_id, {"blueprint": None, "insights": [], "actions": [], "frame": None, "sections": {}, "review": None, "reports": 0})


def _json(text: str, what: str):
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as exc:
        return None, f"{what} is not valid JSON ({exc.msg} at line {exc.lineno}, column {exc.colno}). Send a single JSON object with double-quoted keys and no trailing commas."


def _ok(**kw) -> str:
    return json.dumps(kw, default=str)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


def artifact(case_id: str, n: int) -> dict | None:
    return _artifacts.get((case_id, n))


def export_state(case_id: str) -> dict:
    with _lock:
        return {"case_id": case_id, "ledger": ledger.dump(case_id), "workspace": _work.get(case_id), "case": case_context.snapshot(case_id)}


def import_state(data: dict) -> str:
    case_id = data["case_id"]
    with _lock:
        ledger.load(case_id, data["ledger"])
        if data.get("workspace"):
            _work[case_id] = data["workspace"]
        if data.get("case"):
            case_context.restore(data["case"])
    return case_id


# ------------------------------------------------------------------ playbook

@mcp.tool()
@traced("studio")
def get_report_playbook(topic: str = "all") -> str:
    """Read the Croma report playbook: report types and their section catalogues, the quality bar, how to word insights and actions, and the exact
    document-spec JSON with an example of every block type. topic: all | design | spec | writing."""
    text = (HERE / "playbook.md").read_text(encoding="utf-8")
    if topic == "all":
        return text
    parts = {"design": ("## DESIGN", "## SPEC"), "spec": ("## SPEC", "## WRITING"), "writing": ("## WRITING", None)}
    if topic not in parts:
        return _err("topic must be all, design, spec or writing")
    start, end = parts[topic]
    i = text.index(start)
    return text[i: text.index(end) if end else None]


# ------------------------------------------------------------------ evidence

@mcp.tool()
@traced("studio")
def list_evidence(case_id: str) -> str:
    """List every data result on the case (R ids from the Data agent, T ids you derived) with the question each answers, its columns, row count and
    a 3-row preview, plus the calculations made so far (D ids). Start here to see what evidence exists before asking for more."""
    c = ledger.case(case_id)
    return _ok(results=[t.brief(2) for t in c.tables.values()],
               calculations=[{"id": d.id, "label": d.label, "value": round(d.value, 4)} for d in c.calcs.values()])


def _compact(v):
    return round(v, 4) if isinstance(v, float) else v


@mcp.tool()
@traced("studio")
def get_results(case_id: str, result_ids: list[str], limit: int = 12) -> str:
    """Read several results at once (R or T ids): columns, row count, column totals and the first `limit` rows of each (default 12, max 30). One call for
    all the evidence you need is much cheaper than many calls."""
    out = []
    for rid in result_ids[:8]:
        try:
            t = ledger.get_table(case_id, rid)
        except KeyError as exc:
            out.append({"id": rid, "error": str(exc.args[0])})
            continue
        n = min(max(limit, 1), 30)
        out.append({"id": t.id, "question": (t.question or t.label)[:160], "columns": t.columns, "row_count": len(t.rows),
                    "rows": [[_compact(v) for v in r] for r in t.rows[:n]], "more_rows": max(0, len(t.rows) - n), "column_stats": ledger.numeric_summary(t)})
    return _ok(results=out)


@mcp.tool()
@traced("studio")
def get_section_pack(case_id: str, section_id: str) -> str:
    """Writer: everything needed to write ONE section in a single call: its blueprint entry (purpose, question, visuals), the insight cards for it in full, the
    actions, only the calculations those insights cite, and the results they cite (columns, totals and a few rows), plus an index of every other result.
    Use this instead of reading the workspace and evidence piece by piece."""
    ws = _ws(case_id)
    bp = ws["blueprint"] or {}
    entry = next((s for s in bp.get("sections", []) if str(s.get("id")) == section_id), None)
    if not entry:
        return _err(f"The blueprint has no section '{section_id}'. Sections: {', '.join(str(s.get('id')) for s in bp.get('sections', []))}")
    mine = [i for i in ws["insights"] if str(i.get("section_hint", "")).strip().lower() in (section_id.lower(), str(entry.get("title", "")).lower())]
    rest = [i for i in ws["insights"] if i not in mine]
    want = " ".join(json.dumps(i.get("evidence", [])) + " " + str(i.get("finding", "")) + " " + str(i.get("so_what", "")) for i in mine)
    ids = {f"{a}{b}" for a, b in cites.ID.findall(want)}
    c = ledger.case(case_id)
    results = []
    for rid in sorted((i for i in ids if i[0] in "RT"), key=lambda x: (x[0], int(x[1:]))):
        t = c.tables.get(rid)
        if t:
            results.append({"id": t.id, "question": (t.question or t.label)[:140], "columns": t.columns, "row_count": len(t.rows),
                            "rows": [[_compact(v) for v in r] for r in t.rows[:6]], "column_stats": ledger.numeric_summary(t)})
    calcs = [{"id": d.id, "label": d.label, "value": round(d.value, 4)} for did, d in c.calcs.items() if did in ids]
    index = [{"id": t.id, "label": (t.label or t.question)[:80], "columns": t.columns[:14]} for t in c.tables.values() if t.id not in ids]
    actions = ws["actions"] if any(k in (entry.get("title", "") + entry.get("purpose", "")).lower() for k in ("action", "recommend", "next")) else [
        {"id": a.get("id"), "priority": a.get("priority"), "action": a.get("action"), "owner": a.get("owner")} for a in ws["actions"]]
    return _ok(section=entry, report={k: bp.get(k) for k in ("report_type", "title", "audience", "key_question", "windows", "measure")}, insights_for_this_section=mine,
               other_insights=[{"id": i.get("id"), "headline": i.get("headline"), "section_hint": i.get("section_hint")} for i in rest], actions=actions,
               calculations_cited=calcs, results_cited=results, other_results=index,
               how_to_use="Prepare tables with derive_table, quote only cited calculations or new calc results, then save_section once.")


@mcp.tool()
@traced("studio")
def get_result(case_id: str, result_id: str, offset: int = 0, limit: int = 30) -> str:
    """Read the rows of one result (R or T id), with column totals, min and max for numeric columns. Default 30 rows, at most 50 per call (use offset to
    page). Read only the results you need and only the rows you need: use derive_table (sort, limit, add columns) to get a small, focused table first,
    and calc for arithmetic, rather than reading large results in full."""
    try:
        t = ledger.get_table(case_id, result_id)
    except KeyError as exc:
        return _err(str(exc.args[0]))
    limit = min(max(limit, 1), 50)
    rows = [[_compact(v) for v in r] for r in t.rows[offset: offset + limit]]
    return _ok(id=t.id, question=(t.question or t.label)[:200], lineage=t.lineage, columns=t.columns, row_count=len(t.rows), truncated_at_source=t.truncated,
               offset=offset, rows=rows, more_rows=max(0, len(t.rows) - offset - limit), column_stats=ledger.numeric_summary(t))


@mcp.tool()
@traced("studio")
def calc(case_id: str, items: list[dict]) -> str:
    """Do arithmetic and record it, so every derived figure in the report is traceable. Never compute a number in your head: call this.
    items: a list of {label, formula, inputs, unit?, note?}. formula is arithmetic over the input names, for example "(cy/ly-1)*100".
    inputs maps each name to a cell reference or a number: "R3:Stores:cy_nmv" (row by its label), "R3:#0:cy_nmv" (row by position),
    "R3:SUM:cy_nmv" (also AVG, MAX, MIN, COUNT), or "D2" (an earlier calculation). A plain number is recorded as an assumption, so use it only
    for stated assumptions such as a target uplift. Returns a D id and value for each item; use the value when you write, and cite the id."""
    return _ok(results=ledger.calc(case_id, items))


@mcp.tool()
@traced("studio")
def derive_table(case_id: str, label: str, source: str, add_columns: list[dict] | None = None, keep: list[str] | None = None, rename: dict | None = None,
                 sort_by: str = "", descending: bool = True, limit: int = 0, join_with: str = "", join_on: list[str] | None = None, where: str = "") -> str:
    """Make a new table (T id) from results you already have, without retyping any data. source: a result id. join_with + join_on: left-join a second
    result on shared key columns first (for example CY and LY pulled separately, or actuals and targets). add_columns: [{name, formula}] with row-wise
    formulas over column names in lower case with underscores, for example {"name": "growth_pct", "formula": "(cy_nmv/ly_nmv-1)*100"}. where: a row
    condition such as "cy_nmv > 0". keep, rename, sort_by, limit shape the output. Use the T id in a table or chart block."""
    try:
        return _ok(table=ledger.derive_table(case_id, label, source, add_columns, keep, rename, sort_by, descending, limit, join_with, join_on, where))
    except Exception as exc:
        return _err(f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ working papers

def _save_list(case_id: str, key: str, text: str, what: str) -> str:
    data, e = _json(text, what)
    if e:
        return _err(e)
    items = data if isinstance(data, list) else data.get(key) or data.get("items")
    if not isinstance(items, list) or not items:
        return _err(f"{what} must be a JSON list (or an object with a '{key}' list).")
    with _lock:
        _ws(case_id)[key] = items
    return _ok(saved=len(items))


@mcp.tool()
@traced("studio")
def save_blueprint(case_id: str, blueprint_json: str) -> str:
    """Planner: save the report blueprint. JSON object with: report_type (weekly_performance | category_deep_dive | decision_brief | ad_hoc), title,
    audience, key_question, hypothesis (what you expect the story to be), depth (brief | standard | deep), windows {current, comparison},
    measure, sections [{id, title, purpose, question_answered, evidence_needed [data request ids], visuals [..]}], data_requests [{id, question
    (self-contained, with exact dates), why, sections [ids], priority}], risks_to_manage [..]."""
    data, e = _json(blueprint_json, "blueprint")
    if e:
        return _err(e)
    for need in ("report_type", "title", "sections", "data_requests"):
        if need not in data:
            return _err(f"blueprint is missing '{need}'.")
    with _lock:
        _ws(case_id)["blueprint"] = data
    return _ok(saved=True, sections=len(data["sections"]), data_requests=len(data["data_requests"]))


@mcp.tool()
@traced("studio")
def save_insights(case_id: str, insights_json: str) -> str:
    """Insight analyst: save the insight cards. JSON list of {id (I1, I2...), headline (a full sentence with the point), finding (what the evidence shows,
    with figures taken from results or calc), evidence [R/T/D ids], so_what (why the business should care), kind (driver | risk | opportunity |
    anomaly | reconciliation | confirmation), materiality (high | medium | low, and why), confidence (high | medium | low), section_hint}."""
    return _save_list(case_id, "insights", insights_json, "insights")


@mcp.tool()
@traced("studio")
def save_actions(case_id: str, actions_json: str) -> str:
    """Strategist: save the recommended actions. JSON list of {id (A1...), priority (P1 | P2 | P3), action (an imperative sentence), where (zone, channel,
    category, brand or SKUs), owner (a role), expected_impact (sized with calc where the evidence allows; state the assumption), evidence [ids],
    insight_ids [..], confidence, risk_or_guardrail, kpi_to_track, timing}."""
    return _save_list(case_id, "actions", actions_json, "actions")


@mcp.tool()
@traced("studio")
def get_workspace(case_id: str, parts: str = "summary") -> str:
    """Read the working papers for the case. parts: "summary" (default: the request, blueprint outline, insight headlines, action headlines, sections saved,
    latest review) or a comma list of full parts from: blueprint, insights, actions, findings, evidence. Ask for full parts only when you need them."""
    ws = _ws(case_id)
    case = case_context.snapshot(case_id) or {}
    want = {p.strip() for p in parts.split(",")}
    out: dict = {"request": case.get("request"), "frame_saved": bool(ws["frame"]),
                 "sections_saved": [{"id": s["id"], "title": s["title"], "blocks": len(s["blocks"])} for s in ws["sections"].values()], "review": ws["review"]}
    bp = ws["blueprint"] or {}
    if "blueprint" in want:
        out["blueprint"] = bp
    elif bp:
        out["blueprint"] = {k: bp.get(k) for k in ("report_type", "title", "audience", "key_question", "hypothesis", "depth", "windows", "measure")} | {
            "sections": [{"id": s.get("id"), "title": s.get("title"), "purpose": s.get("purpose")} for s in bp.get("sections", [])],
            "data_requests": f"{len(bp.get('data_requests', []))} (use parts=blueprint to read them)", "risks_to_manage": bp.get("risks_to_manage")}
    if "insights" in want:
        out["insights"] = ws["insights"]
    else:
        out["insights"] = [{"id": i.get("id"), "headline": i.get("headline"), "kind": i.get("kind"), "materiality": str(i.get("materiality"))[:60], "evidence": i.get("evidence"),
                            "section_hint": i.get("section_hint")} for i in ws["insights"]]
    if "actions" in want:
        out["actions"] = ws["actions"]
    else:
        out["actions"] = [{"id": a.get("id"), "priority": a.get("priority"), "action": a.get("action"), "owner": a.get("owner"), "expected_impact": a.get("expected_impact")} for a in ws["actions"]]
    if "findings" in want:
        out["findings"] = [{"n": f["n"], "agent": f["agent"], "title": f["title"], "finding": f["finding"]} for f in case.get("findings", [])]
    if "evidence" in want:
        out["evidence"] = [{"id": t.id, "label": t.label or t.question[:100], "rows": len(t.rows)} for t in ledger.case(case_id).tables.values()]
    return _ok(**out)


# ------------------------------------------------------------------ the document

@mcp.tool()
@traced("studio")
def save_frame(case_id: str, frame_json: str) -> str:
    """Writer/editor: save the report frame (title, report_type, audience, period_label, comparison_label, scope, measure, data_as_of, headline, bottom_line [3-5
    points], decisions [asks for the reader], section_order [section ids], caveats). See get_report_playbook('spec') for the exact fields."""
    data, e = _json(frame_json, "frame")
    if e:
        return _err(e)
    frame, e = spec.validate_frame(data)
    if not frame:
        return _err(e)
    with _lock:
        _ws(case_id)["frame"] = frame.model_dump()
    return _ok(saved=True)


@mcp.tool()
@traced("studio")
def save_section(case_id: str, section_json: str) -> str:
    """Writer: save one report section (replaces a section with the same id). JSON {id, title, takeaway, blocks [...]}. Tables and charts name a result
    id and its columns, and the renderer fills in the rows, so never type table data. See get_report_playbook('spec') for every block type. The reply
    lists any numbers in your text that cannot be traced to evidence and any problems with the section, so fix them and save again."""
    data, e = _json(section_json, "section")
    if e:
        return _err(e)
    section, e = spec.validate_section(data)
    if not section:
        return _err(e)
    s = section.model_dump()
    problems = audit_mod.lint(case_id, {"headline": "x", "bottom_line": ["a", "b", "c"]}, [s])
    problems = [p for p in problems if "actions block" not in p and "No frame" not in p]
    with _lock:
        _ws(case_id)["sections"][s["id"]] = s
    _matched, missing = [], []
    for where, text in audit_mod.narrative_items({}, [s]):
        if text:
            m, x = audit_mod.trace(case_id, text)
            missing += [{"where": where, "number": y["token"], "context": y["context"]} for y in x]
    return _ok(saved=s["id"], untraced_numbers=missing[:25], problems=problems,
               note="Fix untraced numbers by deriving them with calc or removing them; fix problems and save the section again." if (missing or problems) else "Clean.")


def _tidy_all(case_id: str) -> int:
    """Round over-precise figures in everything saved so far (idempotent); used before auditing, reading and publishing."""
    ws = _ws(case_id)
    total = 0
    with _lock:
        if ws["frame"]:
            ws["frame"], n = tidy.tidy(ws["frame"])
            total += n
        for sid, s in list(ws["sections"].items()):
            ws["sections"][sid], n = tidy.tidy(s)
            total += n
    return total


def _assemble(case_id: str, draw: bool):
    _tidy_all(case_id)
    ws = _ws(case_id)
    if not ws["frame"]:
        raise ValueError("No frame saved. Call save_frame first.")
    sections = list(ws["sections"].values())
    if not sections:
        raise ValueError("No sections saved. Call save_section for each section.")
    return resolve.build(case_id, ws["frame"], sections, ws["review"], draw_charts=draw, present=draw), ws["frame"], sections


@mcp.tool()
@traced("studio")
def get_document(case_id: str, max_rows: int = 12) -> str:
    """Read the whole saved report as a reader will see it, as plain text with tables and charts shown as their data (first max_rows rows of each table).
    Use it to review or to check that sections agree with each other."""
    try:
        resolved, _f, _s = _assemble(case_id, False)
    except Exception as exc:
        return _err(str(exc))
    return resolve.as_text(resolved, max(3, min(max_rows, 40)))


@mcp.tool()
@traced("studio")
def get_review_pack(case_id: str) -> str:
    """Reviewer: everything needed to review in one call: the request and blueprint outline, insight and action headlines, the audit result and the whole
    report as text (12 rows per table). Use get_result and calc afterwards only to spot-check figures."""
    try:
        resolved, frame, sections = _assemble(case_id, False)
    except Exception as exc:
        return _err(str(exc))
    audit = audit_mod.audit(case_id, frame, sections)
    audit["problems"] = audit_mod.lint(case_id, frame, sections)
    return json.dumps({"workspace": json.loads(get_workspace(case_id)), "audit": audit, "document": resolve.as_text(resolved, 12)}, default=str)


@mcp.tool()
@traced("studio")
def audit_report(case_id: str) -> str:
    """Check the saved report: every number in the narrative is traced to the evidence ledger, and the structure is linted (headline, takeaways, a
    prescriptive actions block, valid result and column references). The report cannot be published until untraced numbers are zero."""
    ws = _ws(case_id)
    _tidy_all(case_id)
    if not ws["frame"]:
        return _err("No frame saved.")
    sections = list(ws["sections"].values())
    result = audit_mod.audit(case_id, ws["frame"], sections)
    result["problems"] = audit_mod.lint(case_id, ws["frame"], sections)
    result["pass"] = result["pass"] and not result["problems"]
    return json.dumps(result, default=str)


@mcp.tool()
@traced("studio")
def save_review(case_id: str, review_json: str) -> str:
    """Reviewer: save the review. JSON {verdict (pass | revise), summary (two sentences), scores {accuracy, insight, actionability, clarity, consistency} 1-5,
    issues [{severity (blocker | major | minor), section, problem, fix}]}."""
    data, e = _json(review_json, "review")
    if e:
        return _err(e)
    if data.get("verdict") not in ("pass", "revise"):
        return _err("verdict must be 'pass' or 'revise'.")
    with _lock:
        _ws(case_id)["review"] = data
    return _ok(saved=True, verdict=data["verdict"], issues=len(data.get("issues", [])))


def _summary_markdown(frame: dict, sections: list[dict]) -> str:
    out = [f"**{frame['headline']}**", ""] + [f"- {x}" for x in frame.get("bottom_line", [])]
    actions = [a for s in sections for b in s["blocks"] if b["type"] == "actions" for a in b["items"]]
    if actions:
        out += ["", "**Top actions**"] + [f"{i}. [{a['priority']}] {a['action']} ({a['owner']})" for i, a in enumerate(actions[:5], 1)]
    return "\n".join(out)


@mcp.tool()
@traced("studio")
async def render_report(case_id: str, force: bool = False) -> str:
    """Publish: render the saved report to DOCX and HTML, with charts, an evidence register, the calculations and the number audit, and attach it to the
    case. Refuses while numbers are untraced or the structure has problems (unless force is true, which the editor may use only after two repair rounds,
    and the appendix then shows the audit as failed)."""
    ws = _ws(case_id)
    _tidy_all(case_id)
    if not ws["frame"]:
        return _err("No frame saved.")
    sections = list(ws["sections"].values())
    a = audit_mod.audit(case_id, ws["frame"], sections)
    problems = audit_mod.lint(case_id, ws["frame"], sections)
    if (not a["pass"] or problems) and not force:
        return json.dumps({"error": "The report is not ready to publish.", "untraced_numbers": a["untraced_numbers"][:25], "problems": problems})

    def work():
        resolved, frame, secs = _assemble(case_id, True)
        return resolved, render_docx.render(resolved), render_html.render(resolved), frame, secs

    try:
        resolved, docx_bytes, html_text, frame, secs = await asyncio.to_thread(work)
    except Exception as exc:
        return _err(f"Rendering failed: {type(exc).__name__}: {exc}")
    with _lock:
        ws["reports"] += 1
        n = ws["reports"]
        _artifacts[(case_id, n)] = {"docx": docx_bytes, "html": html_text, "title": frame["title"], "at": time.time(), "audit": resolved["audit"],
                                    "pages_estimate": max(1, len(docx_bytes) // 40000)}
    case_context.add_report(case_id, frame["title"], _summary_markdown(frame, secs),
                            {"n": n, "docx": f"/report/{case_id}/{n}.docx", "html": f"/report/{case_id}/{n}.html", "audit": resolved["audit"],
                             "sections": len(secs), "evidence": len(resolved["evidence"])})
    return _ok(published=True, report_number=n, title=frame["title"], docx=f"/report/{case_id}/{n}.docx", html=f"/report/{case_id}/{n}.html",
               audit={k: resolved["audit"][k] for k in ("numbers_checked", "traced", "untraced", "pass")}, sections=len(secs), evidence_items=len(resolved["evidence"]))
