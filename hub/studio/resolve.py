"""Turns the saved frame and sections into a resolved document: tables filled from the ledger and formatted, charts drawn, and the evidence appendix
built. Both renderers (DOCX, HTML) work from this, so they always agree."""
from __future__ import annotations

from datetime import datetime, timezone
import re

from studio import audit as audit_mod
from studio import charts, cites, ledger, spec
from studio.fmt import fmt, tone_for

TEXT_FORMATS = {"text", "date"}


def _source_line(t: ledger.Table) -> str:
    if t.kind == "derived":
        return f"{t.id}: derived ({t.lineage})"
    q = t.question.strip().replace("\n", " ")
    return f"{t.id}: {q[:150]}{'…' if len(q) > 150 else ''}"


def _table_block(b: dict, c: ledger.CaseLedger) -> dict:
    t = c.tables[b["result_id"].strip().upper()]
    rows = t.rows[: b["top_n"]] if b.get("top_n") else t.rows
    cols = b["columns"]
    idx = [t.col_index(col["key"]) for col in cols]
    headers = [col.get("label") or t.columns[i] for col, i in zip(cols, idx)]
    aligns = [("left" if col["format"] in TEXT_FORMATS else "right") if col.get("align", "auto") == "auto" else col["align"] for col in cols]
    out_rows = []
    for r in rows:
        cells = []
        for col, i in zip(cols, idx):
            v = r[i]
            cells.append({"text": fmt(v, col["format"]), "tone": tone_for(v, col.get("heat", "none"))})
        out_rows.append(cells)
    total = None
    if b.get("total_row") and b.get("sum_columns"):
        sums = {ledger.norm(k) for k in b["sum_columns"]}
        total = []
        for n, (col, i) in enumerate(zip(cols, idx)):
            if ledger.norm(col["key"]) in sums:
                s = 0.0
                for r in rows:
                    try:
                        s += ledger.to_number(r[i])
                    except (ValueError, TypeError):
                        pass
                total.append({"text": fmt(s, col["format"]), "tone": ""})
            else:
                total.append({"text": b.get("total_label", "Total") if n == 0 else "", "tone": ""})
    note = b.get("note", "")
    if t.truncated:
        note = (note + " " if note else "") + "Partial list: the data source returned its maximum number of rows."
    return {"t": "table", "title": b.get("title", ""), "headers": headers, "aligns": aligns, "rows": out_rows, "total": total, "note": note,
            "source": _source_line(t), "wide": len(cols) > 8}


def _chart_block(b: dict, c: ledger.CaseLedger, case_id: str, draw: bool = True) -> dict:
    t = c.tables[b["result_id"].strip().upper()]
    chart = spec.Chart.model_validate(b)
    png = charts.render(chart, t, case_id) if draw else b""
    rows = t.rows[: chart.top_n] if chart.top_n else t.rows
    xi = t.col_index(chart.x)
    points = [[str(r[xi])] + [fmt(r[t.col_index(s.col)], chart.value_format) for s in chart.series] for r in rows[:30]]
    return {"t": "chart", "png": png, "title": b["title"], "caption": b.get("caption", ""), "source": _source_line(t), "kind": chart.kind,
            "series": [s.label or s.col for s in chart.series], "points": points}


def as_text(resolved: dict, max_rows: int = 30) -> str:
    """The report as a reader sees it, in plain text (for the reviewer and the writer's own checks)."""
    f = resolved["frame"]
    out = [f"# {f['title']}", f"Subtitle: {f.get('subtitle', '')}", f"Type: {f.get('report_type')} | Period: {f.get('period_label')} | Compared with: {f.get('comparison_label')} | Scope: {f.get('scope')} | Measure: {f.get('measure')}",
           f"HEADLINE: {f['headline']}", "BOTTOM LINE:"] + [f"  - {x}" for x in f.get("bottom_line", [])]
    if f.get("decisions"):
        out += ["DECISIONS AND ASKS:"] + [f"  - {x}" for x in f["decisions"]]
    for s in resolved["sections"]:
        out += ["", f"## {s['number']}. {s['title']}", f"TAKEAWAY: {s['takeaway']}"]
        for b in s["blocks"]:
            t = b["t"]
            if t == "para":
                out.append(b["text"])
            elif t == "bullets":
                out += [f"  - {i}" for i in b["items"]]
            elif t == "tiles":
                out.append("TILES: " + " | ".join(f"{x['label']}: {x['value']} {x.get('delta', '')} {x.get('sub', '')}".strip() for x in b["tiles"]))
            elif t == "table":
                out.append(f"TABLE {b['title']}: " + " | ".join(b["headers"]))
                out += ["   " + " | ".join(c["text"] for c in r) for r in b["rows"][:max_rows]]
                if b["total"]:
                    out.append("   " + " | ".join(c["text"] for c in b["total"]))
            elif t == "chart":
                out.append(f"CHART ({b['kind']}) {b['title']}: series {', '.join(b['series'])}; " + "; ".join(" ".join(p) for p in b["points"]))
            elif t == "callout":
                out.append(f"CALLOUT[{b['tone']}] {b['title']}: {b['text']}")
            elif t == "actions":
                for a in b["items"]:
                    out.append(f"ACTION {a['priority']} [{a.get('confidence')}] {a['action']} | where: {a.get('where')} | owner: {a['owner']} | impact: {a.get('impact')} | evidence: {', '.join(a.get('evidence', []))}")
            elif t == "definitions":
                out += [f"  {i['term']}: {i['definition']}" for i in b["items"]]
    if f.get("caveats"):
        out += ["CAVEATS:"] + [f"  - {x}" for x in f["caveats"]]
    return "\n".join(out)


def _present(frame: dict, sections: list[dict]) -> tuple[dict, list[dict]]:
    """Display version of the document: evidence ids such as [R2, D43] are taken out of the prose and gathered into one quiet evidence line per section
    (the audit has already traced every figure, and the appendix lists the ids)."""
    def clean(text, bucket: set):
        if not isinstance(text, str):
            return text
        out, ids = cites.strip(text)
        bucket.update(ids)
        return out

    frame_ids: set[str] = set()
    f = dict(frame)
    for key in ("subtitle", "period_label", "comparison_label", "scope", "measure", "headline"):
        f[key] = clean(f.get(key, ""), frame_ids)
    for key in ("bottom_line", "decisions", "caveats"):
        f[key] = [clean(x, frame_ids) for x in f.get(key, [])]
    out = []
    for s in sections:
        ids: set[str] = set()
        blocks = []
        for b in s["blocks"]:
            b = dict(b)
            t = b["t"]
            if t == "para":
                b["text"] = clean(b["text"], ids)
            elif t == "bullets":
                b["items"] = [clean(x, ids) for x in b["items"]]
            elif t == "callout":
                b["title"], b["text"] = clean(b["title"], ids), clean(b["text"], ids)
            elif t == "tiles":
                b["tiles"] = [{**x, **{k: clean(x.get(k, ""), ids) for k in ("value", "label", "sub", "delta")}} for x in b["tiles"]]
            elif t == "actions":
                items = []
                for a in b["items"]:
                    a = dict(a)
                    for k in ("action", "where", "owner", "impact", "guardrail", "kpi", "timing"):
                        a[k] = clean(a.get(k, ""), ids)
                    ids.update(e.strip().upper() for e in a.get("evidence", []))
                    items.append(a)
                b["items"] = items
            elif t == "table":
                b["title"], b["note"] = clean(b["title"], ids), clean(b["note"], ids)
                ids.update(re.findall(r"\b[RT]\d+\b", b["source"]))
            elif t == "chart":
                b["title"], b["caption"] = clean(b["title"], ids), clean(b["caption"], ids)
                ids.update(re.findall(r"\b[RT]\d+\b", b["source"]))
            blocks.append(b)
        ids.update(cites.strip(s["takeaway"])[1])
        out.append({**s, "takeaway": clean(s["takeaway"], set()), "blocks": blocks, "evidence": cites.compact(ids)})
    f["evidence"] = cites.compact(frame_ids)
    return f, out


def build(case_id: str, frame: dict, sections: list[dict], review: dict | None = None, draw_charts: bool = True, present: bool = True) -> dict:
    c = ledger.case(case_id)
    frame = spec.Frame.model_validate(frame).model_dump()
    sections = [spec.Section.model_validate(s).model_dump() for s in sections]
    order = frame.get("section_order") or [s["id"] for s in sections]
    by_id = {s["id"]: s for s in sections}
    ordered = [by_id[i] for i in order if i in by_id] + [s for s in sections if s["id"] not in order]
    out_sections = []
    used: set[str] = set()
    for n, s in enumerate(ordered, 1):
        blocks = []
        for b in s["blocks"]:
            t = b["type"]
            if t == "table":
                blocks.append(_table_block(b, c))
                used.add(b["result_id"].strip().upper())
            elif t == "chart":
                blocks.append(_chart_block(b, c, case_id, draw_charts))
                used.add(b["result_id"].strip().upper())
            elif t == "actions":
                for a in b["items"]:
                    used.update(e.strip().upper() for e in a.get("evidence", []))
                blocks.append({"t": "actions", **{k: v for k, v in b.items() if k != "type"}})
            elif t == "kpi_tiles":
                blocks.append({"t": "tiles", "heading": b.get("heading", ""), "tiles": b["tiles"]})
            elif t == "paragraph":
                blocks.append({"t": "para", "text": b["text"]})
            elif t == "bullets":
                blocks.append({"t": "bullets", "items": b["items"]})
            elif t == "callout":
                blocks.append({"t": "callout", "tone": b.get("tone", "insight"), "title": b.get("title", ""), "text": b["text"]})
            elif t == "definitions":
                blocks.append({"t": "definitions", "items": b["items"]})
            elif t == "page_break":
                blocks.append({"t": "break"})
        out_sections.append({"id": s["id"], "number": n, "title": s["title"], "takeaway": s.get("takeaway", ""), "blocks": blocks})

    audit_result = audit_mod.audit(case_id, frame, sections)
    cited = {f"{a}{b}" for _w, text in audit_mod.narrative_items(frame, sections) for a, b in cites.ID.findall(text or "")}
    for s in sections:
        for b in s["blocks"]:
            if b["type"] == "actions":
                cited.update(e.strip().upper() for a in b["items"] for e in a.get("evidence", []))
            if b["type"] in ("table", "chart"):
                t = c.tables.get(b["result_id"].strip().upper())
                if t and t.lineage:
                    cited.update(f"{a}{n}" for a, n in cites.ID.findall(t.lineage))
    frame_out, out_sections = _present(frame, out_sections) if present else (frame, out_sections)
    evidence = [{"id": t.id, "kind": t.kind, "question": t.question or t.label, "description": t.description, "rows": len(t.rows),
                 "columns": t.columns, "sql": t.sql, "lineage": t.lineage, "truncated": t.truncated, "used": t.id in used}
                for t in c.tables.values()]
    calcs = [{"id": d.id, "label": d.label, "formula": d.formula, "value": round(d.value, 4),
              "inputs": "; ".join(f"{k} = {v['value']:,.4g} ({v['source']})" for k, v in d.inputs.items())} for d in c.calcs.values() if d.id in cited]
    calcs_not_shown = len(c.calcs) - len(calcs)
    return {"frame": frame_out, "sections": out_sections, "evidence": evidence, "calcs": calcs, "calcs_not_shown": calcs_not_shown, "audit": {k: v for k, v in audit_result.items() if k != "untraced_numbers"} | {"untraced_numbers": audit_result["untraced_numbers"][:20]},
            "review": review or {}, "generated_at": datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")}
