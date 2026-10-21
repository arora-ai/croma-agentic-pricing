"""DOCX renderer: a designed business report from the resolved document."""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

NAVY, TEAL, GREEN, RED, AMBER, GREY, LIGHT, ZEBRA, TOTAL = "1F3A6B", "0F766E", "2E7D32", "C62828", "B45309", "6B7280", "F4F6FA", "F7F8FB", "E6EBF4"
TONE_COLOR = {"good": GREEN, "bad": RED, "warn": AMBER, "neutral": NAVY, "": None}
CALLOUT = {"insight": (NAVY, "EEF3FB"), "risk": (RED, "FDF0EF"), "action": (GREEN, "EEF7EF"), "note": (GREY, "F3F4F6")}
TYPE_LABEL = {"weekly_performance": "WEEKLY PERFORMANCE REPORT", "category_deep_dive": "CATEGORY DEEP-DIVE", "decision_brief": "DECISION BRIEF", "ad_hoc": "BUSINESS ANALYSIS"}
FONT = "Calibri"
PAGE_W = 6.85  # usable width in inches (A4 with 0.7in margins)


def _rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_)


def _shade(cell, fill: str):
    tcPr = cell._tc.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcPr.append(shd)


def _borders(cell, **edges):
    """edges: top/left/bottom/right = (size_eighths, hex) or None for no border."""
    tcPr = cell._tc.get_or_add_tcPr()
    b = tcPr.find(qn("w:tcBorders"))
    if b is None:
        b = OxmlElement("w:tcBorders")
        tcPr.append(b)
    for edge in ("top", "left", "bottom", "right"):
        spec = edges.get(edge, "keep")
        if spec == "keep":
            continue
        el = b.find(qn(f"w:{edge}"))
        if el is None:
            el = OxmlElement(f"w:{edge}")
            b.append(el)
        if spec is None:
            el.set(qn("w:val"), "nil")
        else:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(spec[0]))
            el.set(qn("w:color"), spec[1])
            el.set(qn("w:space"), "0")


def _margins(cell, top=40, bottom=40, left=80, right=80):
    tcPr = cell._tc.get_or_add_tcPr()
    m = OxmlElement("w:tcMar")
    for k, v in (("top", top), ("left", left), ("bottom", bottom), ("right", right)):
        e = OxmlElement(f"w:{k}")
        e.set(qn("w:w"), str(v))
        e.set(qn("w:type"), "dxa")
        m.append(e)
    tcPr.append(m)


def _no_table_borders(table):
    tblPr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement(f"w:{edge}")
        e.set(qn("w:val"), "nil")
        borders.append(e)
    tblPr.append(borders)


def _fixed(table, widths_in: list[float]):
    table.autofit = False
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    for row in table.rows:
        for cell, w in zip(row.cells, widths_in):
            cell.width = Inches(w)


def _runs(par, text: str, size: float = 10, color: str | None = None, bold: bool = False, italic: bool = False):
    """Adds text to a paragraph; **double-star** spans become bold."""
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        r = par.add_run(part)
        r.font.name = FONT
        r._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        r.font.size = Pt(size)
        r.bold = bold or (i % 2 == 1)
        r.italic = italic
        if color:
            r.font.color.rgb = _rgb(color)
    return par


def _para(container, text="", size=10, color=None, bold=False, italic=False, align=None, before=0, after=4, keep_next=False):
    p = container.add_paragraph()
    pf = p.paragraph_format
    pf.space_before, pf.space_after, pf.line_spacing = Pt(before), Pt(after), 1.08
    pf.keep_with_next = keep_next
    if align:
        p.alignment = align
    if text:
        _runs(p, text, size, color, bold, italic)
    return p


def _cell_text(cell, text, size=9, color=None, bold=False, italic=False, align=None, first=True):
    p = cell.paragraphs[0] if first else cell.add_paragraph()
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.line_spacing = 1.0
    if align:
        p.alignment = align
    _runs(p, text, size, color, bold, italic)
    return p


def _field(par, instr: str, size=8, color=GREY):
    r = par.add_run()
    r.font.size = Pt(size)
    r.font.color.rgb = _rgb(color)
    for kind, text in (("begin", None), (None, instr), ("end", None)):
        if kind:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        r._r.append(el)


def _heading(doc, text: str, level: int, number: str = ""):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.keep_with_next = True
    p.style = doc.styles[f"Heading {level}"]
    pf.space_before, pf.space_after = Pt(16 if level == 1 else 10), Pt(4)
    r = p.add_run(f"{number}  {text}" if number else text)
    r.font.name, r.font.bold = FONT, True
    r.font.size = Pt(17 if level == 1 else 12)
    r.font.color.rgb = _rgb(NAVY)
    if level == 1:
        pPr = p._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("val", "single"), ("sz", "8"), ("space", "2"), ("color", TEAL)):
            bottom.set(qn(f"w:{k}"), v)
        bdr.append(bottom)
        pPr.append(bdr)
    return p


def _box(doc, fill: str, bar: str, bar_size=24):
    t = doc.add_table(rows=1, cols=1)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_table_borders(t)
    _fixed(t, [PAGE_W])
    c = t.rows[0].cells[0]
    _shade(c, fill)
    _borders(c, left=(bar_size, bar), top=None, bottom=None, right=None)
    _margins(c, 90, 90, 160, 140)
    return c


def _spacer(doc, pts=4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after, p.paragraph_format.space_before = Pt(0), Pt(0)
    p.paragraph_format.line_spacing = Pt(pts)


def _tiles(doc, block):
    if block.get("heading"):
        _para(doc, block["heading"].upper(), 8, GREY, bold=True, before=4, after=2, keep_next=True)
    tiles = block["tiles"]
    t = doc.add_table(rows=1, cols=len(tiles))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_table_borders(t)
    _fixed(t, [PAGE_W / len(tiles)] * len(tiles))
    for cell, tile in zip(t.rows[0].cells, tiles):
        tone = TONE_COLOR.get(tile.get("tone", "neutral")) or NAVY
        _shade(cell, LIGHT)
        _borders(cell, top=(36, tone), left=(4, "FFFFFF"), right=(4, "FFFFFF"), bottom=None)
        _margins(cell, 90, 80, 80, 80)
        _cell_text(cell, tile["value"], 17, NAVY, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        _cell_text(cell, tile["label"].upper(), 7.5, "374151", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, first=False)
        if tile.get("delta"):
            _cell_text(cell, tile["delta"], 9.5, tone, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, first=False)
        if tile.get("sub"):
            _cell_text(cell, tile["sub"], 8, GREY, align=WD_ALIGN_PARAGRAPH.CENTER, first=False)
    _spacer(doc, 6)


def _col_widths(headers, rows, total, wide) -> list[float]:
    n = len(headers)
    lens = []
    for j in range(n):
        longest = max([len(str(headers[j])) * 0.9] + [len(r[j]["text"]) for r in rows[:40]] + ([len(total[j]["text"])] if total else []))
        lens.append(min(max(longest, 4), 34))
    scale = PAGE_W / sum(lens)
    widths = [max(0.45, l * scale) for l in lens]
    scale = PAGE_W / sum(widths)
    return [w * scale for w in widths]


def _table(doc, b):
    if b["title"]:
        _para(doc, b["title"], 10, NAVY, bold=True, before=6, after=3, keep_next=True)
    headers, rows, total = b["headers"], b["rows"], b["total"]
    size = 7.5 if b["wide"] else 8.5
    t = doc.add_table(rows=1 + len(rows) + (1 if total else 0), cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_table_borders(t)
    _fixed(t, _col_widths(headers, rows, total, b["wide"]))
    align = {"left": WD_ALIGN_PARAGRAPH.LEFT, "right": WD_ALIGN_PARAGRAPH.RIGHT, "center": WD_ALIGN_PARAGRAPH.CENTER}
    hdr = t.rows[0]
    trPr = hdr._tr.get_or_add_trPr()
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    trPr.append(th)
    for j, h in enumerate(headers):
        c = hdr.cells[j]
        _shade(c, NAVY)
        _margins(c, 50, 50, 70, 70)
        _cell_text(c, h, size, "FFFFFF", bold=True, align=align[b["aligns"][j]] if b["aligns"][j] != "left" else WD_ALIGN_PARAGRAPH.LEFT)
    for i, row in enumerate(rows, 1):
        tr = t.rows[i]
        cant = OxmlElement("w:cantSplit")
        tr._tr.get_or_add_trPr().append(cant)
        for j, cell in enumerate(row):
            c = tr.cells[j]
            if i % 2 == 0:
                _shade(c, ZEBRA)
            _borders(c, bottom=(2, "E5E7EB"))
            _margins(c, 30, 30, 70, 70)
            tone = cell["tone"]
            _cell_text(c, cell["text"], size, TONE_COLOR.get(tone), bold=bool(tone), align=align[b["aligns"][j]])
    if total:
        tr = t.rows[-1]
        for j, cell in enumerate(total):
            c = tr.cells[j]
            _shade(c, TOTAL)
            _borders(c, top=(8, NAVY))
            _margins(c, 40, 40, 70, 70)
            _cell_text(c, cell["text"], size, NAVY, bold=True, align=align[b["aligns"][j]])
    if b["note"]:
        _para(doc, b["note"], 8, "374151", italic=True, before=3, after=0)
    _para(doc, "Source " + b["source"], 7, GREY, italic=True, before=2, after=8)


def _chart(doc, b):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_after = Pt(0)
    p.add_run().add_picture(io.BytesIO(b["png"]), width=Inches(6.6))
    if b["caption"]:
        _para(doc, b["caption"], 8.5, "374151", italic=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=0)
    _para(doc, "Source " + b["source"], 7, GREY, italic=True, align=WD_ALIGN_PARAGRAPH.CENTER, before=2, after=8)


def _callout(doc, b):
    bar, fill = CALLOUT.get(b["tone"], CALLOUT["insight"])
    c = _box(doc, fill, bar, 18)
    first = True
    if b["title"]:
        _cell_text(c, b["title"], 9.5, bar, bold=True)
        first = False
    _cell_text(c, b["text"], 9.5, "1F2937", first=first)
    _spacer(doc, 6)


PRIORITY = {"P1": RED, "P2": AMBER, "P3": GREY}


def _actions(doc, b):
    if b.get("title"):
        _para(doc, b["title"], 10, NAVY, bold=True, before=6, after=3, keep_next=True)
    heads = ["", "Action", "Owner", "Expected impact", "Confidence"]
    widths = [0.4, 3.2, 1.0, 1.6, 0.65]
    t = doc.add_table(rows=1 + len(b["items"]), cols=5)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    _no_table_borders(t)
    _fixed(t, widths)
    for j, h in enumerate(heads):
        c = t.rows[0].cells[j]
        _shade(c, NAVY)
        _margins(c, 50, 50, 70, 70)
        _cell_text(c, h, 8.5, "FFFFFF", bold=True)
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    t.rows[0]._tr.get_or_add_trPr().append(th)
    for i, a in enumerate(b["items"], 1):
        row = t.rows[i]
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for c in row.cells:
            _borders(c, bottom=(2, "E5E7EB"))
            _margins(c, 50, 50, 70, 70)
        pc = row.cells[0]
        _shade(pc, PRIORITY.get(a["priority"], GREY))
        _cell_text(pc, a["priority"], 9, "FFFFFF", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER)
        ac = row.cells[1]
        _cell_text(ac, a["action"], 9, "111827", bold=False)
        meta = " · ".join(x for x in (a.get("where"), f"KPI: {a['kpi']}" if a.get("kpi") else "", a.get("timing")) if x)
        if meta:
            _cell_text(ac, meta, 7.5, GREY, first=False)
        if a.get("guardrail"):
            _cell_text(ac, "Guardrail: " + a["guardrail"], 8, "7C2D12", first=False)
        if a.get("evidence"):
            _cell_text(ac, "Evidence: " + ", ".join(a["evidence"]), 7, GREY, italic=True, first=False)
        _cell_text(row.cells[2], a["owner"], 8.5)
        _cell_text(row.cells[3], a.get("impact", ""), 8.5)
        conf = a.get("confidence", "medium")
        _cell_text(row.cells[4], conf.capitalize(), 8.5, {"high": GREEN, "medium": AMBER, "low": RED}[conf], bold=True)
    _spacer(doc, 8)


def _definitions(doc, b):
    t = doc.add_table(rows=len(b["items"]), cols=2)
    _no_table_borders(t)
    _fixed(t, [1.6, PAGE_W - 1.6])
    for i, it in enumerate(b["items"]):
        for c in t.rows[i].cells:
            _borders(c, bottom=(2, "E5E7EB"))
            _margins(c, 40, 40, 70, 70)
        _cell_text(t.rows[i].cells[0], it["term"], 9, NAVY, bold=True)
        _cell_text(t.rows[i].cells[1], it["definition"], 9)
    _spacer(doc, 8)


def _bullets(doc, items):
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.left_indent = Inches(0.22)
        _runs(p, it, 10)
    _spacer(doc, 4)


def _blocks(doc, blocks):
    for b in blocks:
        t = b["t"]
        if t == "para":
            _para(doc, b["text"], 10, "111827", after=6)
        elif t == "bullets":
            _bullets(doc, b["items"])
        elif t == "tiles":
            _tiles(doc, b)
        elif t == "table":
            _table(doc, b)
        elif t == "chart":
            _chart(doc, b)
        elif t == "callout":
            _callout(doc, b)
        elif t == "actions":
            _actions(doc, b)
        elif t == "definitions":
            _definitions(doc, b)
        elif t == "break":
            doc.add_page_break()


def _simple_table(doc, headers, rows, widths, size=8):
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    _no_table_borders(t)
    _fixed(t, widths)
    for j, h in enumerate(headers):
        c = t.rows[0].cells[j]
        _shade(c, NAVY)
        _margins(c, 40, 40, 60, 60)
        _cell_text(c, h, size, "FFFFFF", bold=True)
    th = OxmlElement("w:tblHeader")
    th.set(qn("w:val"), "true")
    t.rows[0]._tr.get_or_add_trPr().append(th)
    for i, r in enumerate(rows, 1):
        for j, v in enumerate(r):
            c = t.rows[i].cells[j]
            _borders(c, bottom=(2, "E5E7EB"))
            _margins(c, 30, 30, 60, 60)
            _cell_text(c, str(v), size)
    _spacer(doc, 8)


def render(resolved: dict) -> bytes:
    f = resolved["frame"]
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.27), Inches(11.69)
    sec.left_margin = sec.right_margin = Inches(0.7)
    sec.top_margin, sec.bottom_margin = Inches(0.75), Inches(0.7)
    for name in ("Normal", "List Bullet"):
        doc.styles[name].font.name = FONT
        doc.styles[name].font.size = Pt(10)

    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _runs(hp, "CONFIDENTIAL \u2014 INTERNAL USE ONLY", 7.5, GREY, bold=True)
    fp = sec.footer.paragraphs[0]
    _runs(fp, f"Croma \u00b7 Profit Planning \u00b7 {f['title'][:70]}      Page ", 8, GREY)
    _field(fp, "PAGE")
    _runs(fp, " of ", 8, GREY)
    _field(fp, "NUMPAGES")

    # cover block
    _para(doc, TYPE_LABEL.get(f.get("report_type", "ad_hoc"), "BUSINESS ANALYSIS"), 9, TEAL, bold=True, before=0, after=2)
    _para(doc, f["title"], 25, NAVY, bold=True, after=2)
    if f.get("subtitle"):
        _para(doc, f["subtitle"], 12, GREY, after=8)
    meta = [("Period", f.get("period_label")), ("Compared with", f.get("comparison_label")), ("Scope", f.get("scope")), ("Measure", f.get("measure")),
            ("Prepared for", f.get("audience")), ("Data as of", f.get("data_as_of"))]
    meta = [(k, v) for k, v in meta if v]
    if meta:
        t = doc.add_table(rows=len(meta), cols=2)
        _no_table_borders(t)
        _fixed(t, [1.3, PAGE_W - 1.3])
        for i, (k, v) in enumerate(meta):
            _cell_text(t.rows[i].cells[0], k.upper(), 8, GREY, bold=True)
            _cell_text(t.rows[i].cells[1], v, 9.5, "111827")
        _spacer(doc, 10)

    # the bottom line
    c = _box(doc, "EEF3FB", NAVY, 30)
    _cell_text(c, "THE BOTTOM LINE", 8, TEAL, bold=True)
    _cell_text(c, f["headline"], 12.5, NAVY, bold=True, first=False)
    for pt in f.get("bottom_line", []):
        p = c.add_paragraph()
        p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = Inches(0.18), Inches(-0.18)
        p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(3), Pt(0)
        _runs(p, "\u25aa  " + pt, 10, "111827")
    _spacer(doc, 6)
    if f.get("decisions"):
        c = _box(doc, "FEF6E7", AMBER, 30)
        _cell_text(c, "DECISIONS AND ASKS", 8, AMBER, bold=True)
        for d in f["decisions"]:
            p = c.add_paragraph()
            p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = Inches(0.18), Inches(-0.18)
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(3), Pt(0)
            _runs(p, "\u25aa  " + d, 10, "111827")
        _spacer(doc, 6)

    # contents
    _para(doc, "CONTENTS", 8, GREY, bold=True, before=8, after=3)
    for s in resolved["sections"]:
        _para(doc, f"{s['number']}.  {s['title']}", 10, NAVY, after=1)
    _para(doc, "Appendix.  Evidence, calculations and method", 10, GREY, after=1)

    for s in resolved["sections"]:
        if s["number"] == 1 and len(resolved["sections"]) >= 6:
            doc.add_page_break()
        _heading(doc, s["title"], 1, f"{s['number']}.")
        if s["takeaway"]:
            tc = _box(doc, LIGHT, TEAL, 18)
            _cell_text(tc, s["takeaway"], 10.5, NAVY, bold=True)
            for par in tc.paragraphs:
                par.paragraph_format.keep_with_next = True
            sp = doc.add_paragraph()
            sp.paragraph_format.keep_with_next = True
            sp.paragraph_format.space_after, sp.paragraph_format.space_before, sp.paragraph_format.line_spacing = Pt(0), Pt(0), Pt(6)
        _blocks(doc, s["blocks"])
        if s.get("evidence"):
            _para(doc, "Evidence for this section: " + s["evidence"], 7, GREY, italic=True, before=2, after=6)

    if f.get("caveats"):
        _heading(doc, "Caveats and what we could not see", 2)
        _bullets(doc, f["caveats"])

    # appendix, generated from the ledger
    if len(resolved["sections"]) >= 6:
        doc.add_page_break()
    _heading(doc, "Appendix: evidence, calculations and method", 1)
    a = resolved["audit"]
    status = "All figures in the narrative trace to the evidence below." if a["pass"] else f"{a['untraced']} figure(s) could not be traced and are listed in the audit."
    c = _box(doc, "EEF7EF" if a["pass"] else "FDF0EF", GREEN if a["pass"] else RED, 18)
    _cell_text(c, "NUMBER AUDIT", 8, GREEN if a["pass"] else RED, bold=True)
    _cell_text(c, f"{a['traced']} of {a['numbers_checked']} figures in the text were matched to a data result or a recorded calculation. {status}", 9.5, first=False)
    rv = resolved.get("review") or {}
    if rv:
        _cell_text(c, f"Independent review: {rv.get('verdict', 'n/a')}. {rv.get('summary', '')}".strip(), 9, GREY, first=False)
    _spacer(doc, 6)
    _heading(doc, "Evidence register", 2)
    _simple_table(doc, ["ID", "What it answers", "Rows", "Used"],
                  [[e["id"], (e["question"] or e["description"])[:180], e["rows"], "yes" if e["used"] else "context"] for e in resolved["evidence"]],
                  [0.5, 5.2, 0.5, 0.65])
    if resolved["calcs"]:
        _heading(doc, "Calculations cited in this report", 2)
        _simple_table(doc, ["ID", "Measure", "Formula", "Result", "Inputs"],
                      [[d["id"], d["label"][:60], d["formula"], f"{d['value']:,.4g}", d["inputs"][:160]] for d in resolved["calcs"]], [0.4, 1.5, 1.5, 0.7, 2.75], 7.5)
        if resolved.get("calcs_not_shown"):
            _para(doc, f"{resolved['calcs_not_shown']} further working calculations are recorded in the case ledger but not cited in the report.", 8, GREY, italic=True)
    sqls = [e for e in resolved["evidence"] if e["sql"] and e["used"]]
    if sqls:
        _heading(doc, "Queries behind the figures", 2)
        for e in sqls:
            _para(doc, f"{e['id']}  \u2014  {e['question'][:120]}", 8, NAVY, bold=True, before=4, after=1, keep_next=True)
            p = _para(doc, "", after=2)
            r = p.add_run(re.sub(r"\s+", " ", e["sql"])[:900])
            r.font.name, r.font.size = "Consolas", Pt(6.5)
            r.font.color.rgb = _rgb("374151")
    _para(doc, f"Generated {resolved['generated_at']} by the Croma profit planning report studio.", 7.5, GREY, italic=True, before=10)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
