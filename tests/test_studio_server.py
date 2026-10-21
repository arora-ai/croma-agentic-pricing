"""Drives the studio tools the way the agents do: ledger -> save_frame/save_section -> audit -> render -> artefacts on the case."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_sample_report as m  # noqa: E402  (adds hub to sys.path)

import case_context  # noqa: E402
from studio import ledger, server  # noqa: E402


def test_tools_end_to_end():
    frame, sections = m.build()
    case = m.CASE
    case_context._cases[case] = {"case_id": case, "created_at": "", "request": "sample", "findings": [], "reports": []}

    ev = json.loads(server.list_evidence(case))
    assert [r["id"] for r in ev["results"]][:2] == ["R1", "R2"] and ev["calculations"]
    got = json.loads(server.get_result(case, "R1"))
    assert got["column_stats"]["cy_nmv"]["sum"] > 0

    bad = json.loads(server.save_frame(case, "{not json"))
    assert "not valid JSON" in bad["error"]
    assert json.loads(server.save_frame(case, json.dumps(frame)))["saved"]

    # an invented number is reported back to the writer, and blocks publishing
    risky = {**sections[0], "blocks": sections[0]["blocks"] + [{"type": "paragraph", "text": "Stores grew 73.4% year on year."}]}
    saved = json.loads(server.save_section(case, json.dumps(risky)))
    assert [x["number"] for x in saved["untraced_numbers"]] == ["73.4"]
    assert json.loads(server.save_section(case, json.dumps({"id": "x", "title": "t", "blocks": [{"type": "chart", "kind": "bar"}]})))["error"].startswith("Invalid spec")
    refused = json.loads(asyncio.run(server.render_report(case)))
    assert "not ready" in refused["error"]

    for s in sections:
        out = json.loads(server.save_section(case, json.dumps(s)))
        assert out["untraced_numbers"] == [], out
    audit = json.loads(server.audit_report(case))
    assert audit["pass"], audit

    doc = server.get_document(case)
    assert "HEADLINE" in doc and "CHART (waterfall)" in doc and "ACTION P1" in doc

    published = json.loads(asyncio.run(server.render_report(case)))
    assert published["published"] and published["audit"]["pass"]
    art = server.artifact(case, published["report_number"])
    assert art["docx"][:2] == b"PK" and "<html" in art["html"][:200].lower()
    snap = case_context.snapshot(case)
    assert snap["reports"][-1]["artifact"]["docx"].endswith(".docx")


def test_playbook_topics():
    assert "## SPEC" in server.get_report_playbook("all")
    assert server.get_report_playbook("spec").startswith("## SPEC")


def test_citations_move_out_of_prose_and_limits_apply():
    from studio import cites, resolve, spec

    clean, ids = cites.strip("Stores fell 12.8% [R2, D43] and RON missed plan (R3; D79-D81), see [T4:growth].")
    assert clean == "Stores fell 12.8% and RON missed plan, see." and ids == {"R2", "D43", "R3", "D79", "D81", "T4"}
    clean, ids = cites.strip("Approve the pilots, see [A1, A2, D192] and (I3; D4-D6).")
    assert clean == "Approve the pilots, see and." and ids == {"A1", "A2", "D192", "I3", "D4", "D6"}
    assert cites.compact({"D2", "R10", "R2", "T1"}) == "R2, R10, T1, D2"

    # the length limit ignores ids; an over-long paragraph is rejected with a message the writer can act on
    ok, err = spec.validate_section({"id": "a", "title": "t", "blocks": [{"type": "paragraph", "text": "x" * 700 + " [R1, R2, R3, D1, D2, D3, D4, D5, D6, D7, D8]"}]})
    assert ok and not err
    bad, err = spec.validate_section({"id": "a", "title": "t", "blocks": [{"type": "paragraph", "text": "x" * 800}]})
    assert bad is None and "limit is 750" in err

    frame, sections = m.build()
    sections[0]["blocks"].append({"type": "paragraph", "text": "Stores lead the change [R1, D1]."})
    resolved = resolve.build(m.CASE, frame, sections)
    assert "[R1" not in resolved["sections"][0]["blocks"][-1]["text"] and "R1" in resolved["sections"][0]["evidence"]
    raw = resolve.build(m.CASE, frame, sections, draw_charts=False, present=False)
    assert "[R1, D1]" in raw["sections"][0]["blocks"][-1]["text"]


def test_state_export_import_round_trip():
    frame, sections = m.build()
    case = m.CASE
    server.save_frame(case, json.dumps(frame))
    for s in sections:
        server.save_section(case, json.dumps(s))
    snapshot = json.loads(json.dumps(server.export_state(case), default=str))
    ledger._cases.pop(case)
    server._work.pop(case)
    server.import_state(snapshot)
    assert json.loads(server.audit_report(case))["pass"]
    assert json.loads(asyncio.run(server.render_report(case)))["published"]
