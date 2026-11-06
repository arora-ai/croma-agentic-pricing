"""The SharePoint source: file parsing, the ledger join, the two backends, the tools the SharePoint agent calls, and the harnesses planning with both sources.
No Azure or SharePoint access is needed: Graph is tested against a mocked transport, and the harness against a scripted Data agent and SharePoint agent."""
import asyncio
import datetime as dt
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import pytest  # noqa: E402
from docx import Document  # noqa: E402
from openpyxl import Workbook  # noqa: E402

import events  # noqa: E402
import settings  # noqa: E402
import sharepoint  # noqa: E402
import sharepoint_gateway as gateway  # noqa: E402
from harness import execute, runtime, workers  # noqa: E402
from harness.schemas import AnalysisPlan  # noqa: E402
from sharepoint import graph as graph_backend  # noqa: E402
from sharepoint import parse  # noqa: E402
from sharepoint.base import SharePointError  # noqa: E402
from sharepoint.folder import FolderBackend  # noqa: E402
from studio import analyze, ledger  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "hub" / "sharepoint" / "sample_library"


def workbook(rows, title=None, percent_col=None, sheets=None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Weekly plan"
    if title:
        ws.append([title])
        ws.append([])
    for r in rows:
        ws.append(r)
    if percent_col is not None:
        for row in ws.iter_rows(min_row=(4 if title else 2)):
            row[percent_col].number_format = "0%"
    for name, data in (sheets or {}).items():
        w2 = wb.create_sheet(name)
        for r in data:
            w2.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- parsing

def test_a_workbook_becomes_a_table_with_dates_and_percentages_in_the_units_a_reader_sees():
    data = workbook([["Week start", "Category", "Planned discount"], [dt.date(2026, 9, 7), "Laptops", 0.14], [dt.date(2026, 9, 14), "Laptops", 0.05]], title="Plan (sample)", percent_col=2)
    (sheet,) = parse.read_tables("plan.xlsx", data)
    assert sheet.name == "Weekly plan" and sheet.columns == ["Week start", "Category", "Planned discount %"]  # the title row is skipped; % marks the unit
    assert sheet.rows == [["2026-09-07", "Laptops", 14.0], ["2026-09-14", "Laptops", 5.0]]  # 14%, not 0.14


def test_hidden_and_empty_sheets_are_left_out_and_a_csv_reads_numbers():
    wb = Workbook()
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    hidden = wb.create_sheet("secret")
    hidden.append(["x", "y"])
    hidden.append([1, 1])
    hidden.sheet_state = "hidden"
    wb.create_sheet("blank")
    buf = io.BytesIO()
    wb.save(buf)
    assert [s.name for s in parse.read_tables("w.xlsx", buf.getvalue())] == ["Sheet"]
    (csv_sheet,) = parse.read_tables("p.csv", "Name,Value\nA,\"1,250.5\"\nB,3\n".encode())
    assert csv_sheet.rows == [["A", 1250.5], ["B", 3]]
    with pytest.raises(parse.ParseError, match="not a workbook"):
        parse.read_tables("broken.xlsx", b"not a zip")


def test_a_document_becomes_passages_with_their_headings_and_tables():
    d = Document()
    d.add_heading("Approval thresholds", 1)
    d.add_paragraph("Up to 10% can be approved by the category head.")
    d.add_heading("Caps", 1)
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Laptops", "14%"
    buf = io.BytesIO()
    d.save(buf)
    passages = parse.read_passages("policy.docx", buf.getvalue())
    assert passages[0] == ("Approval thresholds", "Up to 10% can be approved by the category head.")
    assert passages[1][0] == "Caps" and "Laptops | 14%" in passages[1][1]
    md = parse.read_passages("notes.md", b"# Chennai\n\nTwo stores had short hours.\n\n# Kolkata\n\nA refit starts on 14 September.")
    assert md == [("Chennai", "Two stores had short hours."), ("Kolkata", "A refit starts on 14 September.")]


def test_the_most_relevant_passages_are_picked_in_reading_order():
    passages = [(f"s{i}", f"text {i} about {'laptops caps' if i in (3, 7) else 'other things'}") for i in range(10)]
    picked = parse.pick_passages(passages, "laptops caps", 2)
    assert [s for s, _t in picked] == ["s3", "s7"]
    assert parse.pick_passages(passages, "", 3) == passages[:3] and parse.pick_passages(passages, "x", 20) == passages


def test_row_filters_select_without_computing_and_say_when_a_column_is_wrong():
    sheet = parse.Sheet("s", ["Week start", "Category", "Gap %"], [["2026-08-03", "Laptops", 1.0], ["2026-08-10", "Laptops", 2.5], ["2026-08-10", "iPhones", 0.5], ["2026-09-07", "Laptops", None]])
    rows, applied = parse.filter_rows(sheet, "category=laptops; week_start>=2026-08-05")
    assert [r[0] for r in rows] == ["2026-08-10", "2026-09-07"] and "2 of 4 rows" in applied
    assert [r[0] for r in parse.filter_rows(sheet, "gap>1")[0]] == ["2026-08-10"]  # a column is found by its name without the unit
    with pytest.raises(parse.ParseError, match="not in the sheet.*Week start"):
        parse.filter_rows(sheet, "colour=red")
    with pytest.raises(parse.ParseError, match="not of the form"):
        parse.filter_rows(sheet, "just words")


# ---------------------------------------------------------------- the ledger

CASE = "T-src"


@pytest.fixture(autouse=True)
def clean():
    ledger._cases.pop(CASE, None)
    runtime._runs.clear()
    runtime._live.clear()
    events.EVENTS.clear()
    sharepoint.set_backend(None)
    gateway._calls.clear()
    yield
    workers.set_invoker(None)
    sharepoint.set_backend(None)


def register(rows, columns, kind="genie", case=CASE):
    if kind == "genie":
        return ledger.register_genie(case, {"columns": columns, "rows": rows, "question": "q"})
    return ledger.register_file(case, {"columns": columns, "rows": rows, "question": "f", "kind": kind, "origin": "plan.xlsx"})


def test_a_join_puts_a_file_beside_a_data_space_result_and_says_what_did_not_match():
    sales = register([["2026-09-07", 10.0], ["2026-09-14", 12.0], ["2026-09-21", 9.0]], ["week_start", "nmv"])
    plan = register([["2026-09-07 00:00:00", "Laptops fest", 14.0], ["2026-09-14", "Ganesh fest", 5.0], ["2026-10-05", "Preview", 10.0]], ["Week start", "Campaign", "Planned discount %"], "sharepoint")
    out = ledger.join_tables(CASE, sales, plan, "week_start", take=["Campaign", "planned_discount"])
    t = ledger.get_table(CASE, out["table"])
    assert t.columns == ["week_start", "nmv", "Campaign", "Planned discount %"] and t.kind == "derived"
    assert t.rows == [["2026-09-07", 10.0, "Laptops fest", 14.0], ["2026-09-14", 12.0, "Ganesh fest", 5.0], ["2026-09-21", 9.0, None, None]]  # a date matches with or without a time
    assert any("1 of 3 row(s) of R1 had no match" in n for n in out["notes"]) and any("matched nothing" in n for n in out["notes"])
    assert "databricks" in t.lineage and "sharepoint" in t.lineage
    inner = ledger.join_tables(CASE, sales, plan, "week_start", how="inner")
    assert len(inner["rows"]) == 2


def test_a_join_refuses_several_rows_per_key_and_keys_that_never_meet():
    sales = register([["Laptops", 10.0]], ["category", "nmv"])
    many = register([["Laptops", 1], ["Laptops", 2]], ["category", "x"], "sharepoint")
    with pytest.raises(ValueError, match="several rows.*Ask for one row per category"):
        ledger.join_tables(CASE, sales, many, "category")
    other = register([["Phones", 1]], ["category", "x"], "sharepoint")
    with pytest.raises(ValueError, match="no row of R1 matches"):
        ledger.join_tables(CASE, sales, other, "category")
    with pytest.raises(KeyError, match="no column 'region'"):
        ledger.join_tables(CASE, sales, other, "region")


def test_numbers_written_in_a_document_are_evidence_and_an_empty_file_is_not_stored():
    ledger.register_file(CASE, {"columns": ["section", "text"], "rows": [["Caps", "Laptops are capped at 14% and need CFO approval above 15%."]], "kind": "document", "origin": "policy.docx"})
    nums = ledger.candidate_numbers(CASE)
    assert 14.0 in nums and 15.0 in nums
    assert ledger.register_file(CASE, {"columns": ["a"], "rows": [], "kind": "sharepoint"}) is None
    assert ledger.register_file(CASE, {"error": "nope"}) is None
    t = ledger.get_table(CASE, "R1")
    assert t.source == "sharepoint" and t.kind == "document" and t.brief()["origin"] == "policy.docx"


def test_the_join_step_runs_through_the_analytics_runner_and_is_listed_for_the_scoper():
    sales = register([["Laptops", 10.0], ["iPhones", 20.0]], ["category", "nmv"])
    caps = register([["Laptops", 14.0], ["iPhones", 5.0]], ["category", "cap_pct"], "sharepoint")
    run = analyze.run_op(CASE, "join", sales, {"on": "category", "with": caps}, "Sales with caps")
    assert run["op"] == "join" and run["columns"] == ["category", "nmv", "cap_pct"] and run["outputs"] == {} and run["row_count"] == 2
    from studio import analytics

    assert "- join:" in analytics.catalog() and "no figures" in analytics.catalog()
    with pytest.raises(analytics.AnalyticsError, match="cannot run on rows alone"):
        analytics.run("join", [], {"on": "a", "with": "b"})


# ---------------------------------------------------------------- the folder backend

def test_the_folder_backend_finds_files_by_name_and_by_what_is_inside_them():
    b = FolderBackend(SAMPLE)
    names = [f["name"] for f in asyncio.run(b.search("festive promotion calendar", 5))]
    assert names[0] == "Festive_Promo_Calendar_FY27.xlsx"
    inside = [f["name"] for f in asyncio.run(b.search("lease", 5))]  # a word that is only in a document's text
    assert inside == ["Zonal_Review_Notes_Sep2026.docx"]
    everything = asyncio.run(b.search("", 20))
    assert {f["kind"] for f in everything} == {"table", "document"} and len(everything) == 5
    entry, data = asyncio.run(b.fetch("Pricing/Competitor_Price_Index_Sep2026.csv"))
    assert entry["name"].endswith(".csv") and data.startswith(b"Week start")
    with pytest.raises(SharePointError, match="no file with id"):
        asyncio.run(b.fetch("../../hub/settings.py"))


# ---------------------------------------------------------------- the Graph backend

def graph_transport(calls, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, dict(request.url.params), request.headers.get("authorization")))
        p = request.url.path
        if status != 200:
            return httpx.Response(status)
        if p.endswith("/sites/croma.sharepoint.com:/sites/merch"):
            return httpx.Response(200, json={"id": "site-1", "displayName": "Merchandising"})
        if p.endswith("/sites/site-1/drive"):
            return httpx.Response(200, json={"id": "drive-1"})
        if "/root/search(" in p:
            return httpx.Response(200, json={"value": [
                {"id": "ITEM0001abc", "name": "Plan.xlsx", "webUrl": "https://x/Plan.xlsx", "size": 2000, "lastModifiedDateTime": "2026-09-02T10:00:00Z", "file": {},
                 "parentReference": {"path": "/drives/drive-1/root:/Planning"}},
                {"id": "FOLDER0001", "name": "Planning", "folder": {}, "size": 0},
                {"id": "ITEM0002abc", "name": "photo.png", "file": {}, "size": 10}]})
        if p.endswith("/items/ITEM0001abc"):
            return httpx.Response(200, json={"id": "ITEM0001abc", "name": "Plan.xlsx", "size": 2000, "lastModifiedDateTime": "2026-09-02T10:00:00Z", "file": {},
                                             "parentReference": {"path": "/drives/drive-1/root:/Planning"}})
        if p.endswith("/items/ITEM0001abc/content"):
            return httpx.Response(200, content=workbook([["a", "b"], [1, 2]]))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def make_graph(monkeypatch, calls, status=200):
    monkeypatch.setattr(graph_backend, "SHAREPOINT_AUTH", "token")
    monkeypatch.setattr(graph_backend, "SHAREPOINT_TOKEN", "t0ken")
    client = httpx.AsyncClient(transport=graph_transport(calls, status))
    return graph_backend.GraphBackend("https://croma.sharepoint.com/sites/merch", "", client)


def test_the_graph_backend_searches_the_library_and_reads_a_file_with_a_token_it_never_logs(monkeypatch):
    calls = []
    g = make_graph(monkeypatch, calls)
    found = asyncio.run(g.search("plan", 10))
    assert [f["name"] for f in found] == ["Plan.xlsx"]  # folders and files it cannot read are left out
    assert found[0]["path"] == "Planning/Plan.xlsx" and found[0]["modified"] == "2026-09-02" and found[0]["kind"] == "table" and found[0]["site"] == "Merchandising"
    entry, data = asyncio.run(g.fetch("ITEM0001abc"))
    assert entry["name"] == "Plan.xlsx" and parse.read_tables("Plan.xlsx", data)[0].rows == [[1, 2]]
    assert all(c[2] == "Bearer t0ken" for c in calls) and any("search(q='plan')" in c[0] for c in calls)


def test_graph_errors_are_said_in_words_the_agent_can_act_on(monkeypatch):
    with pytest.raises(SharePointError, match="no read access"):
        asyncio.run(make_graph(monkeypatch, [], 403).search("x", 5))
    with pytest.raises(SharePointError, match="not found"):
        asyncio.run(make_graph(monkeypatch, [], 404).search("x", 5))
    with pytest.raises(SharePointError, match="not a file id"):
        asyncio.run(make_graph(monkeypatch, []).fetch("../etc"))
    with pytest.raises(SharePointError, match="SHAREPOINT_SITE_URL"):
        graph_backend.GraphBackend("", "")


# ---------------------------------------------------------------- the tools

def call(tool, *args, **kw):
    return json.loads(asyncio.run(tool(*args, **kw)))


def test_the_tools_return_tables_and_passages_and_store_them_as_evidence():
    sharepoint.set_backend(FolderBackend(SAMPLE))
    found = call(gateway.search_sharepoint, "festive promotion calendar", CASE)
    assert found["count"] >= 1 and set(found["files"][0]) == {"id", "name", "path", "site", "modified", "size", "kind"}
    plan = call(gateway.read_sharepoint_file, found["files"][0]["id"], filter="category=Laptops; week_start>=2026-08-03; week_start<=2026-09-14", conversation_id=CASE)
    assert plan["result_id"] == "R1" and plan["kind"] == "sharepoint" and plan["row_count"] == 7 and plan["columns"][0] == "Week start"
    assert plan["rows"][1][3] == 12.0 and "Weekly plan" in plan["origin"] and plan["sheets"] == ["Weekly plan", "Campaigns"]
    other_sheet = call(gateway.read_sharepoint_file, found["files"][0]["id"], sheet="Campaigns", conversation_id=CASE)
    assert other_sheet["columns"][0] == "Campaign" and other_sheet["result_id"] == "R2"
    doc_id = call(gateway.search_sharepoint, "markdown policy", CASE)["files"][0]["id"]
    doc = call(gateway.read_sharepoint_file, doc_id, focus="laptops cap", conversation_id=CASE)
    assert doc["kind"] == "document" and doc["columns"] == ["section", "text"] and doc["result_id"] == "R3"
    c = ledger.case(CASE)
    assert {t.id: (t.kind, t.source) for t in c.tables.values()} == {"R1": ("sharepoint", "sharepoint"), "R2": ("sharepoint", "sharepoint"), "R3": ("document", "sharepoint")}


def test_the_tools_say_what_went_wrong_instead_of_raising():
    sharepoint.set_backend(FolderBackend(SAMPLE))
    plan_id = call(gateway.search_sharepoint, "festive promotion calendar", CASE)["files"][0]["id"]
    assert "no sheet 'Nope'" in call(gateway.read_sharepoint_file, plan_id, sheet="Nope", conversation_id=CASE)["error"]
    assert "not in the sheet" in call(gateway.read_sharepoint_file, plan_id, filter="colour=red", conversation_id=CASE)["error"]
    assert "No row" in call(gateway.read_sharepoint_file, plan_id, filter="category=Cameras", conversation_id=CASE)["error"]
    assert "no file with id" in call(gateway.read_sharepoint_file, "nothing.xlsx", conversation_id=CASE)["error"]
    assert call(gateway.search_sharepoint, "zzzqqq", CASE)["count"] == 0


def test_the_tools_report_a_hub_where_sharepoint_is_off_and_cap_the_calls_per_conversation(monkeypatch):
    assert "switched off" in call(gateway.search_sharepoint, "x", CASE)["error"]
    sharepoint.set_backend(FolderBackend(SAMPLE))
    monkeypatch.setattr(gateway, "SHAREPOINT_MAX_CALLS_PER_CASE", 2)
    call(gateway.search_sharepoint, "plan", "T-cap")
    call(gateway.search_sharepoint, "plan", "T-cap")
    assert "call limit of 2" in call(gateway.search_sharepoint, "plan", "T-cap")["error"]


def test_tool_calls_are_traced_with_the_file_and_not_the_whole_table():
    sharepoint.set_backend(FolderBackend(SAMPLE))
    fid = call(gateway.search_sharepoint, "festive promotion calendar", CASE)["files"][0]["id"]
    call(gateway.read_sharepoint_file, fid, filter="category=Laptops", conversation_id=CASE)
    ends = [e for e in events.EVENTS if e["type"] == "tool_end" and e["server"] == "sharepoint"]
    assert [e["tool"] for e in ends] == ["search_sharepoint", "read_sharepoint_file"] and all(e["caller"] == "sharepoint" for e in ends)
    assert ends[1]["summary"].startswith("12 rows from Festive_Promo_Calendar_FY27.xlsx") and len(ends[1]["detail"]["rows"]) <= 5 and ends[1]["detail"]["filter"] == "category=Laptops"


# ---------------------------------------------------------------- the harness with two sources

PLAN2 = {
    "restated": "Did the planned promotions explain the movement in Laptops sales between the two months?",
    "kpi": "NMV (Laptops, Stores)", "unit": "\u20b9 Cr", "current_label": "September 2026", "base_label": "August 2026", "basis": ["Stores only", "promotion plan from SharePoint"],
    "questions": [
        {"id": "q1", "purpose": "weekly sales", "ask": "For Stores only, for Laptops: NMV in Rs Crore by week from 2026-08-03 to 2026-09-14, one row per week (week_start = Monday).", "columns": ["week_start", "nmv"]},
        {"id": "q2", "purpose": "planned discount per week", "ask": "From the promotion calendar: Laptops, weekly planned discount, 2026-08-03 to 2026-09-14, one row per week_start.",
         "columns": ["week_start", "planned_discount_pct", "campaign"], "source": "sharepoint"},
        {"id": "q3", "purpose": "what the policy says", "ask": "What does the markdown policy say about the Laptops event discount cap?", "source": "sharepoint", "form": "text"},
    ],
    "steps": [
        {"id": "s1", "label": "Weekly NMV with the planned discount", "op": "join", "source": "q1", "params": {"on": "week_start", "with": "q2", "take": ["planned_discount_pct", "campaign"]}},
        {"id": "s2", "label": "Weekly NMV trend", "op": "trend", "source": "s1", "params": {"time": "week_start", "value": "nmv"}},
    ],
}
WEEKLY = {"columns": ["week_start", "nmv"], "rows": [["2026-08-03", 30.0], ["2026-08-10", 34.0], ["2026-08-17", 25.0], ["2026-08-24", 24.0], ["2026-08-31", 26.0], ["2026-09-07", 28.0], ["2026-09-14", 22.0]], "sql": "select w"}
PLAN_ROWS = {"kind": "sharepoint", "columns": ["Week start", "Planned discount %", "Campaign"], "origin": "Festive_Promo_Calendar_FY27.xlsx (sheet Weekly plan) \u00b7 modified 2026-09-02",
             "rows": [["2026-08-03", 8.0, "Independence Day Sale (build-up)"], ["2026-08-10", 12.0, "Independence Day Sale"], ["2026-08-17", 0, "No campaign"], ["2026-08-24", 0, "No campaign"],
                      ["2026-08-31", 14.0, "Back to Work Laptop Fest"], ["2026-09-07", 14.0, "Back to Work Laptop Fest"], ["2026-09-14", 5.0, "Ganesh Chaturthi Gadget Fest"]]}
POLICY = {"kind": "document", "columns": ["section", "text"], "origin": "Markdown_and_Promotion_Policy_FY27.docx \u00b7 modified 2026-08-20",
          "rows": [["Event discount caps by category", "Laptops: cap on event discount 14%; bank offer funded 60 : 40."]]}


class Script:
    def __init__(self, **handlers):
        self.handlers, self.calls = handlers, []

    async def __call__(self, role, message):
        body = json.loads(message)
        self.calls.append((role, body["task"], body))
        handler = self.handlers[body["task"]]
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        reply = handler(body) if callable(handler) else handler
        return (reply if isinstance(reply, str) else json.dumps(reply)), {"input_tokens": 1000, "output_tokens": 400}


def two_source_deps(saved, sharepoint_ok=True, data_ok=True):
    asked = {"databricks": [], "sharepoint": []}

    async def ask_data(question, case_id):
        asked["databricks"].append(question)
        if not data_ok:
            return "ERROR: the data agent could not answer (TimeoutError)."
        rid = ledger.register_genie(case_id, {**WEEKLY, "question": question})
        return f"| week | nmv |\n|---|---|\n| 2026-08-03 | 30 |\n\nSource: Result {rid}; Stores only."

    async def ask_source(source, question, case_id):
        asked[source].append(question)
        if not sharepoint_ok:
            return "ERROR: the sharepoint agent could not answer (TimeoutError)."
        payload = POLICY if "policy" in question else PLAN_ROWS
        rid = ledger.register_file(case_id, {**payload, "question": question})
        return f"Source: Result {rid}; {payload['origin']}."

    deps = runtime.Deps(ask_data=ask_data, ask_source=ask_source, worker=workers.call_worker, save_finding=lambda *a: saved.append(a), get_case=lambda cid: None)
    return deps, asked


def findings(body):
    steps = {s["step"]: s for s in body["evidence"]["steps"]}
    trend = steps["s2"]["figures"]
    return {"findings": {"headline": f"Weekly NMV changed by {trend['total_change']['shown']} over the period.",
                         "drivers": [{"headline": "Sales were highest in the weeks of the largest planned discounts.", "evidence": ["R2", "T2"], "why": "The calendar planned 12% in the week of 10 August.",
                                      "confidence": "medium", "inference": True}],
                         "not_drivers": [], "basis": ["Stores only; planned discounts from Festive_Promo_Calendar_FY27.xlsx, a plan and not a record of what ran."], "caveats": [], "gaps": [], "next_questions": []}}


ACCEPT = {"verdict": "accept", "issues": [], "missing_checks": []}


def run_two(case, script, monkeypatch, **kw):
    monkeypatch.setattr(settings, "SOURCES", ("databricks", "sharepoint"))
    workers.set_invoker(script)
    ledger._cases.pop(case, None)
    saved = []
    deps, asked = two_source_deps(saved, **kw)
    run = asyncio.run(execute("analysis", "Did the promotions explain laptop sales?", case, deps))
    return run, saved, asked


def test_a_plan_can_use_both_sources_and_the_harness_asks_each_and_joins_the_answers(monkeypatch):
    script = Script(analysis_plan=PLAN2, analysis_findings=findings, review_analysis=ACCEPT)
    run, saved, asked = run_two("C-s1", script, monkeypatch)
    assert run.status == "done", run.error
    assert len(asked["databricks"]) == 1 and len(asked["sharepoint"]) == 2  # the data agent once, the SharePoint agent for the table and for the policy
    assert any("Return the relevant passages" in q for q in asked["sharepoint"]) and any("will read these columns" in q for q in asked["sharepoint"])
    assert [s.name for s in run.stages] == ["scope", "gather", "columns", "compute", "conclude", "verify", "challenge", "deliver"]
    assert run.stage("gather").summary.startswith("3 of 3 questions answered (")
    scoper_msg = next(b for r, t, b in script.calls if t == "analysis_plan")
    assert scoper_msg["limits"]["sources"] == ["databricks", "sharepoint"]
    pack = next(b for r, t, b in script.calls if t == "analysis_findings")["evidence"]
    q1, q2, q3 = pack["data"]
    assert q1["source"] == "databricks" and "filters" in q1 and q2["source"] == "sharepoint" and "Weekly plan" in q2["origin"] and q2["table_rows"][1][1] == 12.0
    assert q3["passages"][0]["text"].startswith("Laptops: cap on event discount 14%") and "filters" not in q2
    join = next(s for s in pack["steps"] if s["step"] == "s1")
    assert join["op"] == "join" and join["columns"] == ["week_start", "nmv", "Planned discount %", "Campaign"] and join["figures"] == {}
    trend = next(s for s in pack["steps"] if s["step"] == "s2")
    assert trend["from"] == join["table"] and trend["figures"]["periods"]["shown"]  # the trend read the joined table
    assert "SharePoint files R2, R3" in run.result["markdown"]


def test_a_figure_quoted_from_a_document_is_traced_and_one_invented_is_removed(monkeypatch):
    def findings_with_policy(body):
        f = findings(body)
        f["findings"]["drivers"].append({"headline": "Laptops were capped at 14% for events, and bank funding was 60 : 40.", "evidence": ["R3"], "why": "", "confidence": "high", "inference": False})
        return f

    run, _saved, _asked = run_two("C-s2", Script(analysis_plan=PLAN2, analysis_findings=findings_with_policy, review_analysis=ACCEPT), monkeypatch)
    assert run.status == "done" and "capped at 14%" in run.result["markdown"] and run.stage("verify").summary == "all figures traced"

    def invented(body):
        f = findings(body)
        f["findings"]["drivers"].append({"headline": "Laptops were capped at 17% for events.", "evidence": ["R3"], "why": "", "confidence": "high", "inference": False})
        return f

    run, _saved, _asked = run_two("C-s3", Script(analysis_plan=PLAN2, analysis_findings=invented, review_analysis=ACCEPT), monkeypatch)
    assert "17%" not in run.result["markdown"] and "untraceable" in run.stage("verify").summary


def test_a_plan_that_uses_a_source_that_is_not_connected_goes_back_to_the_scoper():
    plan_off = json.loads(json.dumps(PLAN2))
    ok = {**PLAN2, "questions": [PLAN2["questions"][0]], "steps": [{"id": "s2", "label": "Weekly NMV trend", "op": "trend", "source": "q1", "params": {"time": "week_start", "value": "nmv"}}]}
    script = Script(analysis_plan=[plan_off, ok], analysis_findings=lambda b: {"findings": {"headline": "Weekly NMV changed by %s." % next(s for s in b["evidence"]["steps"])["figures"]["total_change"]["shown"],
                                                                                   "drivers": [], "not_drivers": [], "basis": [], "caveats": [], "gaps": [], "next_questions": []}}, review_analysis=ACCEPT)
    workers.set_invoker(script)
    ledger._cases.pop("C-s4", None)
    assert settings.SOURCES == ("databricks",)
    deps, asked = two_source_deps([])
    run = asyncio.run(execute("analysis", "Did the promotions explain laptop sales?", "C-s4", deps))
    retry = [b for r, t, b in script.calls if t == "analysis_plan"][1]
    assert "not connected" in retry["problem"] and "q2 uses 'sharepoint'" in retry["problem"]
    assert run.status == "done" and asked["sharepoint"] == []  # SharePoint was never asked


def test_the_plan_schema_says_what_to_fix_about_sources_forms_and_joins():
    bad = json.loads(json.dumps(PLAN2))
    bad["steps"][1]["source"] = "q3"
    with pytest.raises(ValueError, match="q3.*text question"):
        AnalysisPlan.model_validate(bad)
    bad = json.loads(json.dumps(PLAN2))
    bad["steps"][0]["params"]["with"] = "q9"
    with pytest.raises(ValueError, match="joins with 'q9'"):
        AnalysisPlan.model_validate(bad)
    bad = json.loads(json.dumps(PLAN2))
    bad["questions"][0]["form"] = "text"
    with pytest.raises(ValueError, match="only a SharePoint question can ask for text"):
        AnalysisPlan.model_validate(bad)
    bad = json.loads(json.dumps(PLAN2))
    bad["questions"][2]["id"] = "s1"
    with pytest.raises(ValueError, match="share an id"):
        AnalysisPlan.model_validate(bad)
    assert AnalysisPlan.model_validate(PLAN2).questions[1].source == "sharepoint"


def test_when_sharepoint_fails_the_run_goes_on_with_the_data_space_and_says_so(monkeypatch):
    plan = {**PLAN2, "steps": [{"id": "s1", "label": "Weekly NMV with the planned discount", "op": "join", "source": "q1", "params": {"on": "week_start", "with": "q2"}},
                               {"id": "s2", "label": "Weekly NMV trend", "op": "trend", "source": "q1", "params": {"time": "week_start", "value": "nmv"}}]}
    script = Script(analysis_plan=plan, analysis_findings=lambda b: {"findings": {"headline": "Weekly NMV changed by %s." % next(s for s in b["evidence"]["steps"] if s["step"] == "s2")["figures"]["total_change"]["shown"],
                                                                               "drivers": [], "not_drivers": [], "basis": [], "caveats": [], "gaps": ["The promotion calendar could not be read."], "next_questions": []}},
                    review_analysis=ACCEPT)
    run, _saved, _asked = run_two("C-s5", script, monkeypatch, sharepoint_ok=False)
    assert run.status == "done", run.error
    pack = next(b for r, t, b in script.calls if t == "analysis_findings")["evidence"]
    assert "problem" in pack["data"][1] and "sharepoint agent could not answer" in pack["data"][1]["problem"]
    join = next(s for s in pack["steps"] if s["step"] == "s1")
    assert "returned no data" in join["problem"]  # the join says why it did not run
    assert "The promotion calendar could not be read." in run.result["markdown"]


def test_when_no_source_answers_the_run_stops_with_a_reason(monkeypatch):
    run, _saved, _asked = run_two("C-s6", Script(analysis_plan=PLAN2), monkeypatch, sharepoint_ok=False, data_ok=False)
    assert run.status == "failed" and "no data source could answer any of the questions" in run.error


def test_a_resumed_run_does_not_ask_either_source_again(monkeypatch):
    script = Script(analysis_plan=PLAN2, analysis_findings=[{"findings": {"headline": "x " * 3}}, findings], review_analysis=ACCEPT)
    monkeypatch.setattr(settings, "SOURCES", ("databricks", "sharepoint"))
    workers.set_invoker(script)
    ledger._cases.pop("C-s7", None)
    deps, asked = two_source_deps([])
    first = asyncio.run(execute("analysis", "Did the promotions explain laptop sales?", "C-s7", deps))
    assert first.status == "done"
    n = (len(asked["databricks"]), len(asked["sharepoint"]))
    first.stage("deliver").status = "pending"
    first.status = "failed"
    again = asyncio.run(execute("analysis", "Did the promotions explain laptop sales?", "C-s7", deps))
    assert again.status == "done" and (len(asked["databricks"]), len(asked["sharepoint"])) == n


# ---------------------------------------------------------------- the prompts

def profile_with(enabled):
    base = json.loads((Path(__file__).resolve().parents[1] / "config" / "data_profile.example.json").read_text(encoding="utf-8"))
    base = {k: ("x" if k not in ("facts", "sharepoint") and not k.startswith("_") else v) for k, v in base.items()}
    base["facts"] = ["a fact"]
    base["sharepoint"] = {"enabled": enabled, "about": "the merchandising library.", "files": [{"name": "Plan.xlsx", "holds": "a plan", "grain": "one row per week", "columns": ["week_start", "x"]}], "rules": ["be careful"]}
    return base


def test_the_scoper_is_told_about_sharepoint_only_when_it_is_enabled_and_the_example_is_a_valid_plan():
    from agents import data_profile

    off = data_profile.render("{{SOURCES}}", profile_with(False))
    assert "Only one data source is connected" in off and "sharepoint" not in off.lower().replace('"databricks"', "")
    on = data_profile.render("{{SOURCES}}\n{{SHAREPOINT_LIBRARY}}", profile_with(True))
    assert "Two data sources are connected" in on and "Plan.xlsx: a plan. one row per week. Columns: week_start, x." in on and "be careful" in on
    fragment = on[on.index("```json") + 7:on.index("```", on.index("```json") + 7)]
    full = json.loads("{" + fragment + ', "restated": "Did promotions explain laptop sales?", "kpi": "NMV", "current_label": "September 2026", "base_label": "August 2026"}')
    assert AnalysisPlan.model_validate(full).steps[0].op == "join"  # the example in the prompt is accepted by the schema that will parse the reply
    assert "{{" not in on
