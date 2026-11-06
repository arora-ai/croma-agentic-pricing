"""The analytics engine against hand-computed numbers, and the property that matters most: the decompositions add up exactly."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

import pytest  # noqa: E402

from studio import analytics as an  # noqa: E402
from studio import analyze, audit, ledger  # noqa: E402


def rows(*items):
    return [{an.norm(k): v for k, v in item.items()} for item in items]


def col(result, name):
    return [r[result.columns.index(name)] for r in result.rows]


def approx(x, tol=1e-6):
    return pytest.approx(x, abs=tol)


# ---------------------------------------------------------------- contribution

CONTRIB = rows({"Category": "A", "cy": 120, "ly": 100}, {"Category": "B", "cy": 80, "ly": 100}, {"Category": "C", "cy": 50, "ly": 20})


def test_contribution_hand_computed():
    r = an.contribution(CONTRIB, "category", "cy", "ly")
    assert col(r, "segment") == ["C", "A", "B"]  # by size of change; ties by name
    assert col(r, "change") == [30, 20, -20]
    assert col(r, "change_pct") == [150, 20, -20]
    assert col(r, "share_of_change_pct")[0] == approx(100) and col(r, "share_of_change_pct")[2] == approx(-66.6666667)
    o = r.outputs
    assert (o["total_current"], o["total_base"], o["total_change"]) == (250, 220, 30)
    assert o["total_change_pct"] == approx(13.6363636)
    assert (o["gross_increase"], o["gross_decrease"]) == (50, -20)
    assert o["top1_share_of_gross_pct"] == approx(42.8571429) and o["top3_share_of_gross_pct"] == approx(100)
    assert o["segments_up"] == 2 and o["segments_down"] == 1 and o["material_segments"] == 3
    assert r.checks["parts_sum_to_total"] is True


def test_contribution_sets_aside_a_total_row_and_checks_it():
    data = CONTRIB + rows({"Category": "Total", "cy": 250, "ly": 220})
    r = an.contribution(data, "category", "cy", "ly")
    assert col(r, "segment") == ["C", "A", "B"] and r.outputs["total_current"] == 250
    assert r.checks["matches_control_total_current"] is True
    r = an.contribution(CONTRIB + rows({"Category": "Total", "cy": 999, "ly": 220}), "category", "cy", "ly")
    assert r.checks["matches_control_total_current"] is False and any("DIFFERENT" in n for n in r.notes)


def test_contribution_repeated_labels_are_summed_and_new_segments_have_no_pct():
    r = an.contribution(rows({"c": "A", "cy": 60, "ly": 50}, {"c": "A", "cy": 60, "ly": 50}, {"c": "N", "cy": 10, "ly": 0}), "c", "cy", "ly")
    assert dict(zip(col(r, "segment"), col(r, "current")))["A"] == 120
    assert dict(zip(col(r, "segment"), col(r, "change_pct")))["N"] is None


def test_column_names_are_matched_loosely_and_errors_are_specific():
    r = an.contribution(rows({"Category Name": "A", "CY NMV": 2, "LY NMV": 1}), "category name", "cy_nmv", "LY-NMV")
    assert r.outputs["total_change"] == 1
    with pytest.raises(an.AnalyticsError, match=r"not found.*category_name"):
        an.contribution(rows({"Category Name": "A", "CY NMV": 2, "LY NMV": 1}), "category", "cy_nmv", "ly_nmv")
    with pytest.raises(an.AnalyticsError, match="no rows"):
        an.contribution([], "a", "b", "c")


# ---------------------------------------------------------------- rate and mix

MARGIN = rows({"cat": "A", "cn": 15, "cd": 150, "bn": 10, "bd": 100}, {"cat": "B", "cn": 12.5, "cd": 50, "bn": 20, "bd": 100})


def test_rate_mix_hand_computed():
    r = an.rate_mix(MARGIN, "cat", "cn", "cd", "bn", "bd")
    # base 15.00%, current 13.75%; B's own rate rose 20% -> 25% (+2.5 pp) but sales moved towards the low-rate category A (-1.25 pp, -2.5 pp)
    assert (r.outputs["base_rate"], r.outputs["current_rate"], r.outputs["rate_change"]) == (approx(15), approx(13.75), approx(-1.25))
    assert r.outputs["rate_effect_total"] == approx(2.5) and r.outputs["mix_effect_total"] == approx(-3.75)
    by = {row[0]: row for row in r.rows}
    assert by["A"][r.columns.index("rate_effect_pp")] == approx(0) and by["A"][r.columns.index("mix_effect_pp")] == approx(-1.25)
    assert by["B"][r.columns.index("rate_effect_pp")] == approx(2.5) and by["B"][r.columns.index("mix_effect_pp")] == approx(-2.5)
    assert by["A"][r.columns.index("total_effect_pp")] == approx(-1.25) and by["B"][r.columns.index("total_effect_pp")] == approx(0)
    assert r.outputs["rate_share_of_gross_pct"] == approx(40) and r.outputs["mix_share_of_gross_pct"] == approx(60)
    assert r.checks["rate_plus_mix_equals_change"] is True


def test_rate_mix_new_and_stopped_segments_are_all_mix():
    data = rows({"c": "A", "cn": 10, "cd": 100, "bn": 10, "bd": 100}, {"c": "B", "cn": 0, "cd": 0, "bn": 30, "bd": 100}, {"c": "C", "cn": 20, "cd": 100, "bn": 0, "bd": 0})
    r = an.rate_mix(data, "c", "cn", "cd", "bn", "bd")
    by = {row[0]: row for row in r.rows}
    assert r.outputs["rate_change"] == approx(-5) and r.outputs["rate_effect_total"] == approx(0) and r.outputs["mix_effect_total"] == approx(-5)
    assert by["B"][r.columns.index("mix_effect_pp")] == approx(-5) and by["C"][r.columns.index("mix_effect_pp")] == approx(0)
    assert r.checks["rate_plus_mix_equals_change"] is True


def test_rate_mix_as_values_not_points():
    r = an.rate_mix(rows({"c": "A", "cn": 1100, "cd": 100, "bn": 1000, "bd": 100}), "c", "cn", "cd", "bn", "bd", unit="value")
    assert "rate_effect" in r.columns and r.outputs["base_rate"] == 10 and r.outputs["current_rate"] == 11 and r.outputs["rate_effect_total"] == approx(1)


# ---------------------------------------------------------------- price, volume, mix

PVM = rows({"c": "A", "bq": 100, "bv": 1000, "cq": 150, "cv": 1650}, {"c": "B", "bq": 100, "bv": 3000, "cq": 100, "cv": 3200})


def test_price_volume_mix_hand_computed():
    r = an.price_volume_mix(PVM, "c", "cq", "cv", "bq", "bv")
    o = r.outputs
    assert (o["total_base_value"], o["total_current_value"], o["total_change"]) == (4000, 4850, 850)
    assert (o["volume_effect_total"], o["mix_effect_total"], o["price_effect_total"]) == (approx(1000), approx(-500), approx(350))
    by = {row[0]: row for row in r.rows}
    assert by["A"][r.columns.index("total_effect")] == approx(650) and by["B"][r.columns.index("total_effect")] == approx(200)  # each segment's own change in value
    assert o["base_average_price"] == 20 and o["current_average_price"] == approx(19.4) and o["average_price_change_pct"] == approx(-3)
    assert r.checks["volume_mix_price_equal_change"] is True


def test_price_volume_mix_new_and_dropped_items():
    data = rows({"c": "A", "bq": 100, "bv": 1000, "cq": 100, "cv": 1000}, {"c": "B", "bq": 100, "bv": 3000, "cq": 0, "cv": 0}, {"c": "C", "bq": 0, "bv": 0, "cq": 100, "cv": 2000})
    r = an.price_volume_mix(data, "c", "cq", "cv", "bq", "bv")
    by = {row[0]: row for row in r.rows}
    assert r.outputs["total_change"] == -1000 and r.outputs["price_effect_total"] == approx(0) and r.outputs["volume_effect_total"] == approx(0)
    assert by["B"][r.columns.index("mix_effect")] == approx(-3000) and by["C"][r.columns.index("mix_effect")] == approx(2000)
    assert r.checks["volume_mix_price_equal_change"] is True


def test_price_volume_mix_without_base_quantity_is_all_volume():
    r = an.price_volume_mix(rows({"c": "A", "bq": 0, "bv": 0, "cq": 10, "cv": 50}), "c", "cq", "cv", "bq", "bv")
    assert r.outputs["volume_effect_total"] == approx(50) and r.outputs["mix_effect_total"] == approx(0) and any("No base-period" in n for n in r.notes)


@pytest.mark.parametrize("seed", range(40))
def test_decompositions_always_add_up_exactly(seed):
    rnd = random.Random(seed)
    segs = [f"s{i}" for i in range(rnd.randint(1, 9))]

    def maybe_zero(x):
        return 0.0 if rnd.random() < 0.15 else x

    data = rows(*[{"k": s, "bq": maybe_zero(rnd.uniform(1, 500)), "cq": maybe_zero(rnd.uniform(1, 500)), "bp": rnd.uniform(1, 90), "cp": rnd.uniform(1, 90)} for s in segs])
    for d in data:
        d["bv"], d["cv"] = d["bq"] * d["bp"], d["cq"] * d["cp"]
    if sum(d["bq"] for d in data) + sum(d["cq"] for d in data) <= 0:
        return
    pvm = an.price_volume_mix(data, "k", "cq", "cv", "bq", "bv")
    assert pvm.checks["volume_mix_price_equal_change"], pvm.outputs
    for row in pvm.rows:
        d = next(x for x in data if x["k"] == row[0])
        assert row[pvm.columns.index("total_effect")] == approx(d["cv"] - d["bv"], 1e-6)  # per segment as well
    if sum(d["bq"] for d in data) > 0 and sum(d["cq"] for d in data) > 0:
        for d in data:  # a margin-like ratio: numerator is a fraction of the value
            d["bn"], d["cn"] = d["bv"] * rnd.uniform(0.02, 0.4), d["cv"] * rnd.uniform(0.02, 0.4)
        rm = an.rate_mix(data, "k", "cn", "cv", "bn", "bv")
        assert rm.checks["rate_plus_mix_equals_change"], rm.outputs


# ---------------------------------------------------------------- the rest

def test_target_gap():
    r = an.target_gap(rows({"c": "A", "a": 90, "t": 100}, {"c": "B", "a": 120, "t": 100}, {"c": "C", "a": 40, "t": 80}), "c", "a", "t")
    assert col(r, "segment") == ["C", "A", "B"]  # worst first
    assert col(r, "share_of_shortfall_pct")[:2] == [approx(80), approx(20)] and col(r, "share_of_shortfall_pct")[2] is None
    o = r.outputs
    assert (o["total_actual"], o["total_target"], o["total_gap"], o["shortfall_total"]) == (250, 280, -30, -50)
    assert o["total_achievement_pct"] == approx(89.2857143) and o["segments_below_target"] == 2 and o["top3_shortfall_share_pct"] == approx(100)


def test_concentration():
    r = an.concentration(rows({"s": "A", "v": 50}, {"s": "B", "v": 30}, {"s": "C", "v": 15}, {"s": "D", "v": 5}), "s", "v")
    assert col(r, "cumulative_share_pct") == [50, 80, 95, 100] and r.outputs["top3_share_pct"] == 95
    assert r.outputs["segments_for_80pct"] == 2 and r.outputs["hhi"] == approx(3650)
    assert an.concentration(rows({"s": "A", "v": 50}, {"s": "B", "v": 50}), "s", "v", top_n=1).outputs["top1_share_pct"] == 50


def test_trend_sorts_by_time_and_fits_a_slope():
    r = an.trend(rows({"m": "2026-04", "v": 121}, {"m": "2026-03", "v": 110}, {"m": "2026-02", "v": 100}, {"m": "2026-05", "v": 133.1}), "m", "v")
    assert col(r, "period") == ["2026-02", "2026-03", "2026-04", "2026-05"] and col(r, "change")[1:] == [10, 11, approx(12.1)]
    o = r.outputs
    assert o["total_change"] == approx(33.1) and o["total_change_pct"] == approx(33.1) and o["slope_per_period"] == approx(11.03)
    assert o["latest_change_pct"] == approx(10) and o["direction"] == 1 and o["peak_value"] == 133.1 and o["trough_value"] == 100
    assert an.trend(rows({"m": 1, "v": 100}, {"m": 2, "v": 100.5}), "m", "v").outputs["direction"] == 0  # within 1%: flat
    with pytest.raises(an.AnalyticsError, match="two periods"):
        an.trend(rows({"m": 1, "v": 100}), "m", "v")


def test_reconcile_and_materiality():
    assert an.reconcile(100.2, 100).outputs["within_tolerance"] == 1 and an.reconcile(105, 100).outputs["within_tolerance"] == 0
    assert an.materiality(0.05, 100).outputs["material"] == 0  # a Rs 0.05 Cr rise on Rs 100 Cr is not a driver
    assert an.materiality(5, 100).outputs["material"] == 1 and an.materiality(5, 100, abs_floor=10).outputs["material"] == 0


def test_validate_gives_messages_a_model_can_fix():
    with pytest.raises(an.AnalyticsError, match="unknown op"):
        an.validate("regression", {})
    with pytest.raises(an.AnalyticsError, match=r"missing parameter.*\['base'\]"):
        an.validate("contribution", {"key": "c", "cur": "x"})
    with pytest.raises(an.AnalyticsError, match=r"unknown parameter.*current"):
        an.validate("contribution", {"key": "c", "cur": "x", "base": "y", "current": "z"})
    assert an.validate("contribution", {"key": "c", "cur": "x", "base": "y"})["min_share"] == 0.05


def test_every_documented_output_is_really_produced():
    samples = {
        "contribution": (CONTRIB, {"key": "category", "cur": "cy", "base": "ly"}),
        "rate_mix": (MARGIN, {"key": "cat", "cur_num": "cn", "cur_den": "cd", "base_num": "bn", "base_den": "bd"}),
        "price_volume_mix": (PVM, {"key": "c", "cur_qty": "cq", "cur_value": "cv", "base_qty": "bq", "base_value": "bv"}),
        "target_gap": (rows({"c": "A", "a": 90, "t": 100}, {"c": "B", "a": 120, "t": 100}), {"key": "c", "actual": "a", "target": "t"}),
        "concentration": (rows({"s": "A", "v": 50}, {"s": "B", "v": 50}), {"key": "s", "value": "v"}),
        "trend": (rows({"m": 1, "v": 100}, {"m": 2, "v": 120}, {"m": 3, "v": 130}), {"time": "m", "value": "v"}),
        "reconcile": (None, {"a": 1.0, "b": 1.0}),
        "materiality": (None, {"change": 1.0, "base": 10.0}),
    }
    assert set(samples) == set(an.OPS) - {"join"}  # join works on two ledger results, see tests/test_sources.py
    for op, (data, params) in samples.items():
        result = an.run(op, data, params)
        assert set(an.OPS[op].outputs) <= set(result.outputs), (op, set(an.OPS[op].outputs) - set(result.outputs))
        assert all(name in result.units for name in result.outputs), op
    text = an.catalog()
    assert all(name in text for name in an.OPS) and "rate_effect_total" in text


# ---------------------------------------------------------------- bound to the evidence ledger

CASE = "T-analytics"
GENIE = {"question": "Stores margin by category, Sep vs Aug", "columns": ["category", "cy_profit", "cy_nmv", "ly_profit", "ly_nmv"],
         "rows": [["Phones", 15, 150, 10, 100], ["Laptops", 12.5, 50, 20, 100]], "sql": "select 1"}


def fresh_case():
    ledger._cases.pop(CASE, None)
    assert ledger.register_genie(CASE, GENIE) == "R1"


def test_run_op_records_a_table_and_calculations_the_audit_can_trace():
    fresh_case()
    out = analyze.run_op(CASE, "rate_mix", "R1", {"key": "category", "cur_num": "cy_profit", "cur_den": "cy_nmv", "base_num": "ly_profit", "base_den": "ly_nmv"},
                         "Margin % drivers by category")
    assert out["table"] == "T1" and out["outputs"]["rate_effect_total"]["value"] == approx(2.5) and out["checks"]["rate_plus_mix_equals_change"] is True
    c = ledger.case(CASE)
    assert c.tables["T1"].kind == "derived" and "rate_mix on R1" in c.tables["T1"].lineage
    d = c.calcs[out["outputs"]["mix_effect_total"]["id"]]
    assert d.value == approx(-3.75) and d.formula == "rate_mix(R1).mix_effect_total" and d.inputs["rows analysed"]["source"] == "R1"
    matched, missing = audit.trace(CASE, "The margin fell 1.25 pp: mix cost 3.75 pp and rate gave back 2.5 pp.")
    assert not missing, missing
    assert analyze.output_values(out)["current_rate"] == approx(13.75)
    # the detail table can be used like any other result
    assert ledger.derive_table(CASE, "worst", "T1", sort_by="total_effect_pp", descending=False, limit=1)["row_count"] == 1


def test_run_op_errors_say_what_to_fix():
    fresh_case()
    with pytest.raises(KeyError, match="Unknown result"):
        analyze.run_op(CASE, "contribution", "R9", {"key": "category", "cur": "cy_nmv", "base": "ly_nmv"})
    with pytest.raises(an.AnalyticsError, match="not found"):
        analyze.run_op(CASE, "contribution", "R1", {"key": "category", "cur": "cy_sales", "base": "ly_nmv"})
    with pytest.raises(an.AnalyticsError, match="needs a source"):
        analyze.run_op(CASE, "contribution", "", {"key": "category", "cur": "cy_nmv", "base": "ly_nmv"})


def test_scalar_ops_take_cell_references_and_calculations():
    fresh_case()
    ledger.calc(CASE, [{"label": "x", "formula": "a", "inputs": {"a": "R1:SUM:cy_nmv"}}])
    out = analyze.run_op(CASE, "reconcile", "", {"a": "D1", "b": 200.0})
    assert out["outputs"]["within_tolerance"]["value"] == 1 and out["source"] == "figures"
    out = analyze.run_op(CASE, "materiality", "", {"change": "R1:Phones:cy_profit", "base": "R1:SUM:cy_nmv"})
    assert out["outputs"]["relative_change_pct"]["value"] == approx(7.5)


def test_the_studio_tool_returns_json_for_results_and_for_errors():
    from studio import server

    fresh_case()
    ok = json.loads(server.analyze(CASE, "contribution", "R1", {"key": "category", "cur": "cy_nmv", "base": "ly_nmv"}, "NMV change"))
    assert ok["table"] == "T1" and ok["outputs"]["total_change"]["value"] == 0
    assert "error" in json.loads(server.analyze(CASE, "contribution", "R1", {"key": "category"}))
    assert "error" in json.loads(server.analyze(CASE, "contribution", "R7", {"key": "category", "cur": "cy_nmv", "base": "ly_nmv"}))
    assert "rate_mix" in json.loads(server.analyze(CASE, "help"))["operations"]
