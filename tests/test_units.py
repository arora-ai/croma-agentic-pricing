"""Keeping an analysis in the unit the user is shown (harness/units.py): the data space may return rupees while the Data agent's answer shows crore."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

import pytest  # noqa: E402

from harness import units  # noqa: E402
from studio import ledger  # noqa: E402

CRORE = 10_000_000.0
RAW = {"columns": ["category", "cur_units", "cur_nmv", "base_units", "base_nmv"],
       "rows": [["Air Conditioners", 2205, 76796852.64, 2525, 87615436.0], ["Laptops", 2699, 137308000.0, 3813, 189058000.0], ["Cookware", 1539, 2800000.0, 1692, 3000000.0],
                ["iPhones", 2119, 189100000.0, 2374, 209600000.0]], "sql": "SELECT 1"}
SHOWN = ("| category | cur_units | cur_nmv (\u20b9 Cr) | base_units | base_nmv (\u20b9 Cr) |\n|---|---:|---:|---:|---:|\n"
         "| Air Conditioners | 2,205 | 7.68 | 2,525 | 8.76 |\n| Laptops | 2,699 | 13.73 | 3,813 | 18.91 |\n| Cookware | 1,539 | 0.28 | 1,692 | 0.30 |\n"
         "| iPhones | 2,119 | 18.91 | 2,374 | 20.96 |\n\n**Source:** Result R1.")


def result(case, payload):
    ledger._cases.pop(case, None)
    return ledger.get_table(case, ledger.register_genie(case, {**payload, "question": "q"}))


def test_rupees_against_crore_is_found_on_the_money_columns_only():
    table = result("C-u1", RAW)
    assert units.detect(table, SHOWN) == {"cur_nmv": CRORE, "base_nmv": CRORE}  # the unit counts and the labels are left alone


def test_a_result_already_in_the_shown_unit_needs_no_change():
    table = result("C-u2", {**RAW, "rows": [["Air Conditioners", 2205, 7.68, 2525, 8.76], ["Laptops", 2699, 13.73, 3813, 18.91], ["iPhones", 2119, 18.91, 2374, 20.96]]})
    assert units.detect(table, SHOWN) == {}


def test_a_fraction_shown_as_a_percentage_is_found():
    table = result("C-u3", {"columns": ["cur_margin_pct", "base_margin_pct"], "rows": [[0.1192, 0.1088]], "sql": "SELECT 1"})
    shown = "| cur_margin_pct | base_margin_pct |\n|---:|---:|\n| 11.92% | 10.88% |"
    assert units.detect(table, shown) == {"cur_margin_pct": pytest.approx(0.01), "base_margin_pct": pytest.approx(0.01)}


def test_rows_are_matched_by_label_when_the_agent_shows_fewer_and_in_another_order():
    shown = "| category | cur_nmv (\u20b9 Cr) |\n|---|---:|\n| iPhones | 18.91 |\n| Laptops | 13.73 |"
    assert units.detect(result("C-u4", RAW), shown) == {"cur_nmv": CRORE}


def test_columns_the_agent_renamed_are_matched_by_position_when_the_shape_is_the_same():
    shown = SHOWN.replace("cur_nmv", "September NMV").replace("base_nmv", "August NMV")
    assert units.detect(result("C-u5", RAW), shown) == {"cur_nmv": CRORE, "base_nmv": CRORE}


def test_figures_that_are_not_a_power_of_ten_apart_are_left_alone():
    shown = SHOWN.replace("| 7.68 |", "| 5.10 |").replace("| 13.73 |", "| 9.10 |").replace("| 0.28 |", "| 0.19 |").replace("| 18.91 | 2,374", "| 12.50 | 2,374")
    assert units.detect(result("C-u6", RAW), shown) == {"base_nmv": CRORE}  # only the column that really is a power of ten apart


def test_no_table_in_the_answer_means_nothing_to_compare():
    assert units.detect(result("C-u7", RAW), "Stores NMV was \u20b9102.38 Cr in September.") == {}


def test_the_best_fitting_table_is_used_when_the_answer_has_several():
    other = "| zone | share |\n|---|---:|\n| North | 12.5% |\n| South | 14.0% |\n\n"
    assert units.detect(result("C-u8", RAW), other + SHOWN) == {"cur_nmv": CRORE, "base_nmv": CRORE}


def test_cells_are_read_the_way_people_write_them():
    assert [units.displayed_number(c) for c in ("2,205", "7.68", "-\u20b916.56 Cr", "\u2212\u20b91.2 Cr", "11.92%", "+0.35", "n/a", "\u2014")] == [2205.0, 7.68, -16.56, -1.2, 11.92, 0.35, None, None]


def test_the_rescaled_copy_leaves_the_raw_result_untouched_and_says_how_it_was_made():
    table = result("C-u9", RAW)
    factors, scaled = units.align("C-u9", table.id, SHOWN)
    assert factors == {"cur_nmv": CRORE, "base_nmv": CRORE} and scaled == "T1"
    copy = ledger.get_table("C-u9", scaled)
    assert copy.kind == "derived" and copy.columns == table.columns
    assert copy.rows[0][2] == pytest.approx(7.679685264) and copy.rows[0][0] == "Air Conditioners" and copy.rows[0][1] == 2205  # money scaled, counts and labels kept
    assert table.rows[0][2] == 76796852.64 and table.sql == "SELECT 1"
    assert "cur_nmv divided by 10,000,000" in copy.lineage and copy.lineage.startswith("from R1")
    assert units.align("C-u9", table.id, "no table here") == ({}, None)
