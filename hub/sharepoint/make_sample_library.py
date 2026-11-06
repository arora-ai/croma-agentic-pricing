"""Generates the sample SharePoint library (hub/sharepoint/sample_library): synthetic files of the kind a merchandising team keeps, for trying the connector with
SHAREPOINT_BACKEND=folder. Everything in it is invented; none of it is Croma data. Deterministic, so it can be regenerated and the files compared.

    python hub/sharepoint/make_sample_library.py            writes the files next to this script
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

OUT = Path(__file__).parent / "sample_library"
CATEGORIES = ["iPhones", "Macbook", "Air Conditioners", "Laptops", "Smart Phones", "Mobile Accessories"]
ZONES = ["MMR", "Rest of Maharashtra", "Gujarat 1", "Gujarat 2", "Delhi NCR", "Hyderabad", "Bengaluru", "Chennai", "Kolkata", "RON"]
WEEKS = [dt.date(2026, 7, 27) + dt.timedelta(days=7 * i) for i in range(12)]  # Mondays, 27 Jul to 12 Oct 2026
CAMPAIGN = {  # the campaign that runs in each week (None: nothing running)
    "2026-07-27": "Monsoon Fest", "2026-08-03": "Independence Day Sale (build-up)", "2026-08-10": "Independence Day Sale", "2026-08-17": None, "2026-08-24": None,
    "2026-08-31": "Back to Work Laptop Fest", "2026-09-07": "Back to Work Laptop Fest", "2026-09-14": "Ganesh Chaturthi Gadget Fest", "2026-09-21": None,
    "2026-09-28": "Onam and Navratri Early Access", "2026-10-05": "Festive Preview", "2026-10-12": "Festive Season Launch",
}
DEPTH = {  # planned discount depth, as a share of MRP, by campaign and category (0 where the category is not in the campaign)
    "Monsoon Fest": {"Air Conditioners": 0.10, "Laptops": 0.04, "Smart Phones": 0.03, "Mobile Accessories": 0.15},
    "Independence Day Sale (build-up)": {"iPhones": 0.04, "Macbook": 0.05, "Air Conditioners": 0.12, "Laptops": 0.08, "Smart Phones": 0.07, "Mobile Accessories": 0.20},
    "Independence Day Sale": {"iPhones": 0.06, "Macbook": 0.07, "Air Conditioners": 0.15, "Laptops": 0.12, "Smart Phones": 0.10, "Mobile Accessories": 0.25},
    "Back to Work Laptop Fest": {"Macbook": 0.06, "Laptops": 0.14, "Mobile Accessories": 0.10},
    "Ganesh Chaturthi Gadget Fest": {"iPhones": 0.03, "Smart Phones": 0.08, "Mobile Accessories": 0.18, "Laptops": 0.05},
    "Onam and Navratri Early Access": {"iPhones": 0.04, "Macbook": 0.04, "Air Conditioners": 0.08, "Laptops": 0.08, "Smart Phones": 0.09, "Mobile Accessories": 0.15},
    "Festive Preview": {"iPhones": 0.05, "Macbook": 0.05, "Air Conditioners": 0.10, "Laptops": 0.10, "Smart Phones": 0.10, "Mobile Accessories": 0.20},
    "Festive Season Launch": {"iPhones": 0.06, "Macbook": 0.06, "Air Conditioners": 0.12, "Laptops": 0.12, "Smart Phones": 0.12, "Mobile Accessories": 0.25},
}
HEAD = Font(bold=True, color="FFFFFF")
HEAD_FILL = PatternFill("solid", fgColor="2E2A6E")


def _sheet(ws, title: str, headers: list[str], rows: list[list], widths: list[int], percent: tuple[int, ...] = (), dates: tuple[int, ...] = ()) -> None:
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(headers)
    for c in ws[3]:
        c.font, c.fill, c.alignment = HEAD, HEAD_FILL, Alignment(wrap_text=True, vertical="center")
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    for row in ws.iter_rows(min_row=4):
        for j in percent:
            row[j].number_format = "0%"
        for j in dates:
            row[j].number_format = "dd-mmm-yyyy"


def promo_calendar() -> None:
    wb = Workbook()
    weekly, camp = [], []
    for w in WEEKS:
        name = CAMPAIGN[w.isoformat()]
        for cat in CATEGORIES:
            depth = DEPTH.get(name or "", {}).get(cat, 0.0)
            weekly.append([w, cat, name or "No campaign", depth, ("Bank cashback" if depth >= 0.10 else "None") if name else "None"])
    _sheet(wb.active, "Festive and event promotion plan FY27: weekly plan by category (synthetic sample)", ["Week start", "Category", "Campaign", "Planned discount", "Bank offer"],
           weekly, [14, 20, 34, 18, 16], percent=(3,), dates=(0,))
    wb.active.title = "Weekly plan"
    starts: dict[str, list[dt.date]] = {}
    for w in WEEKS:
        n = CAMPAIGN[w.isoformat()]
        if n:
            starts.setdefault(n, []).append(w)
    for n, ws_ in starts.items():
        camp.append([n, ws_[0], ws_[-1] + dt.timedelta(days=6), "Approved", "Merchandising"])
    ws2 = wb.create_sheet("Campaigns")
    _sheet(ws2, "Campaign list (synthetic sample)", ["Campaign", "Start date", "End date", "Status", "Owner"], camp, [34, 14, 14, 12, 16], dates=(1, 2))
    wb.save(OUT / "Planning" / "Festive_Promo_Calendar_FY27.xlsx")


def competitor_prices() -> None:
    path = OUT / "Pricing" / "Competitor_Price_Index_Sep2026.csv"
    lines = ["Week start,Category,Competitor,Croma price gap %"]
    gap = {"Amazon": {"iPhones": -0.5, "Macbook": 1.0, "Air Conditioners": -2.5, "Laptops": -1.0, "Smart Phones": -1.5, "Mobile Accessories": -3.0},
           "Reliance Digital": {"iPhones": 0.0, "Macbook": 0.5, "Air Conditioners": 0.5, "Laptops": 1.0, "Smart Phones": 0.5, "Mobile Accessories": 1.0}}
    for i, w in enumerate(WEEKS):
        for comp, by_cat in gap.items():
            for cat in CATEGORIES:
                swing = 0.0
                if comp == "Amazon" and cat == "Laptops" and w >= dt.date(2026, 9, 7):
                    swing = 4.5  # Amazon undercuts Croma on laptops from the second week of September
                if comp == "Amazon" and cat == "Smart Phones" and dt.date(2026, 8, 3) <= w <= dt.date(2026, 8, 10):
                    swing = -3.0
                lines.append(f"{w.isoformat()},{cat},{comp},{by_cat[cat] + swing + (i % 3 - 1) * 0.2:.1f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def network_changes() -> None:
    wb = Workbook()
    rows = [
        ["MMR", "Croma Andheri West", "Renovated (reopened)", dt.date(2026, 8, 24), "Large format"],
        ["MMR", "Croma Thane Viviana", "Closed for refit", dt.date(2026, 9, 1), "Large format"],
        ["Rest of Maharashtra", "Croma Pune Camp", "Closed", dt.date(2026, 8, 31), "Medium format"],
        ["Gujarat 2", "Croma Surat Adajan", "Opened", dt.date(2026, 8, 12), "Medium format"],
        ["Delhi NCR", "Croma Noida Sector 18", "Opened", dt.date(2026, 7, 29), "Large format"],
        ["Hyderabad", "Croma Banjara Hills", "Renovated (reopened)", dt.date(2026, 9, 7), "Large format"],
        ["Chennai", "Croma Velachery", "Reduced hours (flooding)", dt.date(2026, 9, 7), "Medium format"],
        ["Chennai", "Croma Anna Nagar", "Reduced hours (flooding)", dt.date(2026, 9, 7), "Medium format"],
        ["Kolkata", "Croma Salt Lake", "Closed for refit", dt.date(2026, 9, 14), "Large format"],
        ["Bengaluru", "Croma Whitefield", "Opened", dt.date(2026, 9, 21), "Large format"],
    ]
    _sheet(wb.active, "Store network changes FY27 (synthetic sample)", ["Zone", "Store", "Change", "Effective date", "Format"], rows, [22, 26, 26, 16, 16], dates=(3,))
    wb.active.title = "Changes"
    wb.save(OUT / "Planning" / "Store_Network_Changes_FY27.xlsx")


def policy() -> None:
    d = Document()
    d.add_heading("Markdown and promotion policy FY27 (synthetic sample)", 0)
    d.add_heading("Approval thresholds", 1)
    d.add_paragraph("A discount of up to 10% of MRP on a category for an event week can be approved by the category head. A discount above 10% and up to 15% needs the business head. "
                    "A discount above 15% needs the CFO's approval and a written case for the margin impact.")
    d.add_heading("Event discount caps by category", 1)
    t = d.add_table(rows=1, cols=3)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text, t.rows[0].cells[2].text = "Category", "Cap on event discount", "Bank offer funding (Croma : bank)"
    for cat, cap, fund in [("iPhones", "5%", "0 : 100"), ("Macbook", "7%", "0 : 100"), ("Air Conditioners", "18%", "70 : 30"), ("Laptops", "14%", "60 : 40"),
                           ("Smart Phones", "12%", "50 : 50"), ("Mobile Accessories", "25%", "100 : 0")]:
        cells = t.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = cat, cap, fund
    d.add_heading("Price matching", 1)
    d.add_paragraph("Store managers may match a competitor's advertised price on a like-for-like model when the gap is within 3% of Croma's price; a larger gap needs the zonal manager's approval. "
                    "Price matching is not allowed on iPhones and Macbooks during the first 30 days after launch.")
    d.add_heading("Calendar discipline", 1)
    d.add_paragraph("Event discounts start on a Monday and end on a Sunday. Any change to an approved week must be re-approved at the threshold of the new depth. "
                    "The weekly plan in the promotion calendar is the record of what was approved, not of what was executed.")
    d.save(OUT / "Policies" / "Markdown_and_Promotion_Policy_FY27.docx")


def zonal_notes() -> None:
    d = Document()
    d.add_heading("Zonal review notes: September 2026 (synthetic sample)", 0)
    notes = [
        ("MMR", "Thane Viviana closed for a refit from 1 September for about six weeks; the zone expects the lost footfall to return after the reopening in mid-October."),
        ("Rest of Maharashtra", "Pune Camp closed on 31 August after the lease ended. Its customers were redirected to the two nearest stores."),
        ("Chennai", "Heavy rain on 7 September cut the opening hours of two stores to four hours a day for the whole week. Online orders from the city were delayed by two days."),
        ("Gujarat 2", "Surat Adajan opened on 12 August and is ramping up faster than plan, with Air Conditioners the best category."),
        ("Delhi NCR", "Noida Sector 18 opened in late July. Stock of Laptops ran short for four days in the first week of September."),
        ("Kolkata", "Salt Lake goes into a refit from 14 September; the zone has asked for a larger share of the Onam and Navratri allocation to compensate."),
    ]
    for zone, text in notes:
        d.add_heading(zone, 1)
        d.add_paragraph(text)
    d.save(OUT / "Notes" / "Zonal_Review_Notes_Sep2026.docx")


def main() -> None:
    for sub in ("Planning", "Pricing", "Policies", "Notes"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    promo_calendar()
    competitor_prices()
    network_changes()
    policy()
    zonal_notes()
    # what a library would show as each file's last modified date (the files' own dates are the day they were generated or copied)
    (OUT / "_modified.json").write_text(json.dumps({
        "Planning/Festive_Promo_Calendar_FY27.xlsx": "2026-07-24", "Planning/Store_Network_Changes_FY27.xlsx": "2026-09-22", "Pricing/Competitor_Price_Index_Sep2026.csv": "2026-10-13",
        "Policies/Markdown_and_Promotion_Policy_FY27.docx": "2026-04-02", "Notes/Zonal_Review_Notes_Sep2026.docx": "2026-10-05"}, indent=2) + "\n", encoding="utf-8")
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"{p.relative_to(OUT).as_posix():60s} {p.stat().st_size:>7} bytes")


if __name__ == "__main__":
    sys.exit(main())
