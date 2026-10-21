"""Unit tests for the report studio's deterministic parts. Run from the agentic-engine folder: python -m pytest tests -q"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

from studio import audit, ledger  # noqa: E402

CASE = "T-unit"
GENIE = {"question": "Channel NMV Aug 2026 vs 2025", "columns": ["Channel", "cy_nmv", "ly_nmv", "plan_nmv"],
         "rows": [["Stores", 2360.2, 1559.1, 2220.8], ["Online", 110.2, 85.9, 102.2], ["BB", 21.1, 0, 59.3]], "sql": "select 1"}


def setup_module(_m):
    ledger._cases.pop(CASE, None)
    assert ledger.register_genie(CASE, GENIE) == "R1"


def test_calc_cell_refs_and_assumptions():
    out = ledger.calc(CASE, [
        {"label": "Stores growth", "formula": "(cy/ly-1)*100", "inputs": {"cy": "R1:Stores:cy_nmv", "ly": "R1:Stores:ly_nmv"}},
        {"label": "Total CY", "formula": "t", "inputs": {"t": "R1:SUM:cy_nmv"}},
        {"label": "BB growth", "formula": "(cy/ly-1)*100", "inputs": {"cy": "R1:BB:cy_nmv", "ly": "R1:BB:ly_nmv"}},
        {"label": "Recovery", "formula": "base*share", "inputs": {"base": "D2", "share": 0.02}},
    ])
    assert round(out[0]["value"], 1) == 51.4
    assert round(out[1]["value"], 1) == 2491.5
    assert "division by zero" in out[2]["error"]
    assert round(out[3]["value"], 3) == round(2491.5 * 0.02, 3)
    assert ledger.case(CASE).calcs[out[3]["id"]].inputs["share"]["source"] == "assumption"


def test_safe_eval_rejects_code():
    for bad in ("__import__('os')", "open('x')", "a.b", "[1,2]"):
        try:
            ledger.safe_eval(bad, {"a": 1.0})
        except Exception:
            continue
        raise AssertionError(bad)


def test_derive_table_with_formula_and_sort():
    t = ledger.derive_table(CASE, "Growth", "R1", add_columns=[{"name": "ach", "formula": "cy_nmv/plan_nmv*100"}], sort_by="ach")
    assert t["id"] == "T1" and t["columns"][-1] == "ach"
    assert ledger.get_table(CASE, "T1").rows[0][0] == "Online"  # 107.8% is the highest achievement
    assert ledger.get_table(CASE, "T1").rows[-1][0] == "BB"


def test_audit_traces_numbers_and_flags_invented_ones():
    ledger.calc(CASE, [{"label": "ach", "formula": "cy/p*100", "inputs": {"cy": "R1:Stores:cy_nmv", "p": "R1:Stores:plan_nmv"}}])
    ok = "Stores delivered \u20b92,360.2 Cr, up 51.4% on last year, at 106.3% of plan, 94.7% of the mix."
    ledger.calc(CASE, [{"label": "growth", "formula": "(cy/ly-1)*100", "inputs": {"cy": "R1:Stores:cy_nmv", "ly": "R1:Stores:ly_nmv"}}])
    matched, missing = audit.trace(CASE, ok)
    assert [m["token"] for m in missing] == ["94.7"], missing
    bad = "Online grew 73.4% to \u20b9110.2 Cr in the week of 24 Aug 2026 (W2) across 3 zones in section 3.1."
    matched, missing = audit.trace(CASE, bad)
    assert [m["token"] for m in missing] == ["73.4"], (matched, missing)


def test_charts_handle_negative_values():
    from studio import charts, spec

    ledger.register_genie(CASE, {"question": "yoy by category", "columns": ["category", "delta"], "rows": [["iPhones", -1.08], ["Macbook", -1.08], ["Laptops", -0.5], ["Gaming", 0.04]]})
    table = ledger.get_table(CASE, f"R{ledger.case(CASE)._n['R']}")
    for kind in ("hbar", "bar", "line"):
        chart = spec.Chart.model_validate({"type": "chart", "kind": kind, "title": "t", "result_id": table.id, "x": "category", "series": [{"col": "delta"}]})
        png = charts.render(chart, table, CASE)
        assert png[:4] == b"\x89PNG" and len(png) > 5000
        if kind == "hbar":
            (Path(__file__).parent / "_out").mkdir(exist_ok=True)
            (Path(__file__).parent / "_out" / "neg_hbar.png").write_bytes(png)


def test_precision_lint_flags_long_decimals():
    frame = {"headline": "NMV fell to \u20b937.8668 Cr", "bottom_line": ["a", "b", "c"], "decisions": [], "caveats": [], "subtitle": ""}
    problems = audit.lint(CASE, frame, [])
    assert any("37.8668" in p and "4 decimals" in p for p in problems)
    frame["headline"] = "NMV fell to \u20b937.9 Cr"
    assert not any("decimals" in p for p in audit.lint(CASE, frame, []))


def test_tidy_rounds_over_precise_figures_and_audit_still_traces():
    from studio import tidy

    text = "NMV fell \u20b937.8668 Cr (\u20134.5962 on LY), 12.3456% and 0.0157 Cr, see [D1, R2] on 2026-10-05, week 5."
    out, n = tidy.round_text(text)
    assert out == "NMV fell \u20b937.9 Cr (\u20134.60 on LY), 12.3% and 0.02 Cr, see [D1, R2] on 2026-10-05, week 5." and n == 4
    assert tidy.round_text(out)[1] == 0

    ledger.register_genie(CASE, {"question": "q", "columns": ["k", "v"], "rows": [["a", 37.8668]]})
    _m, miss = audit.trace(CASE, "NMV was \u20b937.9 Cr")
    assert not miss


def test_audit_scale_and_rounding():
    ledger.register_genie(CASE, {"question": "q", "columns": ["k", "nmv"], "rows": [["a", 24590000000.0]]})
    _m, miss = audit.trace(CASE, "Net sales were \u20b92,459.0 Cr")
    assert not miss
