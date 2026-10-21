"""Builds a complete sample report from small inline tables (no agents, no data source) to exercise ledger -> calc -> audit -> DOCX/HTML.
The numbers are invented sample values for testing the plumbing.
Run: python tests/make_sample_report.py [out_dir]   (also used by test_studio_server.py)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hub"))

from studio import audit, ledger, render_docx, render_html, resolve, spec  # noqa: E402

CASE = "C-sample"

CHANNELS = {"question": "NMV and units by channel, current year vs last year (sample)", "columns": ["channel", "cy_nmv", "ly_nmv", "cy_qty", "ly_qty"],
            "rows": [["Offline", 121.9, 109.3, 51032, 47810], ["Online", 50.7, 43.3, 22194, 20511], ["Quick Commerce", 1.9, 1.6, 4307, 3790]],
            "sql": "-- sample data, no query was run"}
CATEGORIES = {"question": "NMV by category (top 10), current year vs last year (sample)", "columns": ["category", "cy_nmv", "ly_nmv"],
              "rows": [["Macbook", 27.0, 22.9], ["iPhones", 28.3, 24.4], ["Laptops", 27.6, 24.3], ["TV LCD", 17.9, 15.9], ["Smart Phones", 19.7, 17.8],
                       ["Refrigerators", 9.5, 8.4], ["Air Conditioners", 11.3, 10.3], ["Gaming Laptops", 10.5, 9.8], ["Washing Machines", 5.0, 4.4],
                       ["Extended Warranty", 3.3, 2.9]], "sql": "-- sample data, no query was run"}


def build() -> tuple[dict, list[dict]]:
    ledger._cases.pop(CASE, None)
    ch = ledger.register_genie(CASE, CHANNELS)
    cat = ledger.register_genie(CASE, CATEGORIES)
    g = ledger.calc(CASE, [
        {"label": "Enterprise NMV growth %", "formula": "(cy/ly-1)*100", "inputs": {"cy": f"{ch}:SUM:cy_nmv", "ly": f"{ch}:SUM:ly_nmv"}},
        {"label": "Enterprise NMV added (Cr)", "formula": "cy-ly", "inputs": {"cy": f"{ch}:SUM:cy_nmv", "ly": f"{ch}:SUM:ly_nmv"}},
        {"label": "Unit growth %", "formula": "(cy/ly-1)*100", "inputs": {"cy": f"{ch}:SUM:cy_qty", "ly": f"{ch}:SUM:ly_qty"}},
        {"label": "ASP growth %", "formula": "((cn/cq)/(ln/lq)-1)*100", "inputs": {"cn": f"{ch}:SUM:cy_nmv", "cq": f"{ch}:SUM:cy_qty", "ln": f"{ch}:SUM:ly_nmv", "lq": f"{ch}:SUM:ly_qty"}},
    ])
    assert all("id" in x for x in g), g
    t = ledger.derive_table(CASE, "Category growth", cat, add_columns=[{"name": "growth_pct", "formula": "(cy_nmv/ly_nmv-1)*100"}, {"name": "added", "formula": "cy_nmv-ly_nmv"}],
                            sort_by="added")
    tbl = t["id"]
    total_cy = sum(r[1] for r in ledger.get_table(CASE, ch).rows)
    frame = {"title": "Sample weekly performance", "subtitle": "Current year vs last year, enterprise", "report_type": "weekly_performance",
             "audience": "CEO Office, Profit Planning", "period_label": "1\u201331 Aug", "comparison_label": "Same dates last year", "scope": "All channels, merchandise",
             "measure": "NMV (\u20b9 Cr) and units", "data_as_of": "sample",
             "headline": f"Enterprise NMV grew {g[0]['value']:.1f}% year on year, adding {g[1]['value']:.1f} Cr, with units up {g[2]['value']:.1f}% and ASP up {g[3]['value']:.1f}%.",
             "bottom_line": [f"NMV growth was {g[0]['value']:.1f}%.", f"Units grew {g[2]['value']:.1f}%.", f"ASP grew {g[3]['value']:.1f}%."],
             "section_order": ["s1", "s2"], "caveats": ["Sample data."]}
    sections = [
        {"id": "s1", "title": "Channel performance", "takeaway": "Stores carry the growth.",
         "blocks": [
             {"type": "kpi_tiles", "heading": "Enterprise", "tiles": [{"value": f"\u20b9{total_cy:,.1f} Cr", "label": "NMV", "tone": "good", "delta": f"{g[0]['value']:+.1f}%"}]},
             {"type": "chart", "kind": "bar", "title": "Channel NMV, CY vs LY", "result_id": ch, "x": "channel", "series": [{"col": "ly_nmv", "label": "LY"}, {"col": "cy_nmv", "label": "CY"}]},
             {"type": "table", "title": "By channel", "result_id": ch, "columns": [{"key": "channel", "label": "Channel"}, {"key": "cy_nmv", "label": "CY NMV", "format": "num1"},
                                                                          {"key": "ly_nmv", "label": "LY NMV", "format": "num1"}, {"key": "cy_qty", "label": "CY units", "format": "int"}],
              "total_row": True, "sum_columns": ["cy_nmv", "ly_nmv", "cy_qty"]}]},
        {"id": "s2", "title": "Where the growth came from", "takeaway": "A few categories explain most of it.",
         "blocks": [
             {"type": "chart", "kind": "waterfall", "title": "Category contribution to growth (\u20b9 Cr)", "result_id": tbl, "x": "category", "series": [{"col": "added"}],
              "start_ref": f"{ch}:SUM:ly_nmv", "start_label": "LY", "end_label": "CY", "top_n": 8, "value_format": "num1"},
             {"type": "table", "result_id": tbl, "top_n": 10, "columns": [{"key": "category", "label": "Category"}, {"key": "cy_nmv", "format": "num1", "label": "CY"},
                                                                 {"key": "growth_pct", "format": "spct", "label": "Growth", "heat": "growth"}]},
             {"type": "callout", "tone": "risk", "title": "Watch", "text": f"Enterprise NMV added {g[1]['value']:.1f} Cr."},
             {"type": "actions", "items": [{"priority": "P1", "action": "Protect availability in the top growth categories", "owner": "Category head",
                                            "impact": "Hold +%.1f%% growth" % g[0]["value"], "evidence": [tbl]}]}]},
    ]
    return frame, sections


def main(out: Path):
    frame, sections = build()
    f, e = spec.validate_frame(frame)
    assert f, e
    for s in sections:
        sec, e = spec.validate_section(s)
        assert sec, e
    resolved = resolve.build(CASE, frame, sections, {"verdict": "pass", "summary": "Sample."})
    out.mkdir(parents=True, exist_ok=True)
    (out / "sample.docx").write_bytes(render_docx.render(resolved))
    (out / "sample.html").write_text(render_html.render(resolved), encoding="utf-8")
    print("audit:", resolved["audit"]["traced"], "/", resolved["audit"]["numbers_checked"], "untraced:", resolved["audit"]["untraced_numbers"])
    print("lint:", audit.lint(CASE, frame, sections))
    return resolved


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "tests" / "_out")
