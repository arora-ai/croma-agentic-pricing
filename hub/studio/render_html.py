"""HTML renderer (for the in-app preview and for printing). Same resolved document as the DOCX, so the two always agree."""
from __future__ import annotations

import base64
import html
import re

TYPE_LABEL = {"weekly_performance": "Weekly performance report", "category_deep_dive": "Category deep-dive", "decision_brief": "Decision brief", "ad_hoc": "Business analysis"}

CSS = """
:root{--navy:#1F3A6B;--teal:#0F766E;--green:#2E7D32;--red:#C62828;--amber:#B45309;--grey:#6B7280;--light:#F4F6FA;--line:#E5E7EB}
*{box-sizing:border-box}body{font-family:Calibri,'Segoe UI',system-ui,sans-serif;color:#111827;margin:0;background:#EEF1F6;font-size:14px;line-height:1.5}
.page{max-width:900px;margin:18px auto;background:#fff;padding:42px 52px;box-shadow:0 1px 8px rgba(15,23,42,.12)}
.conf{text-align:right;font-size:10px;letter-spacing:.08em;color:var(--grey);font-weight:700}
.kicker{color:var(--teal);font-weight:700;font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin-top:10px}
h1.title{color:var(--navy);font-size:34px;line-height:1.1;margin:4px 0 4px}.sub{color:var(--grey);font-size:16px;margin-bottom:14px}
.meta{display:grid;grid-template-columns:120px 1fr;gap:3px 12px;margin:10px 0 18px;font-size:13px}.meta b{color:var(--grey);font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding-top:2px}
.box{border-left:5px solid var(--navy);background:#EEF3FB;padding:14px 18px;margin:10px 0;border-radius:0 6px 6px 0}.box.amber{border-color:var(--amber);background:#FEF6E7}
.box .lbl{font-size:11px;letter-spacing:.08em;font-weight:700;color:var(--teal)}.box.amber .lbl{color:var(--amber)}.box .head{font-size:18px;font-weight:700;color:var(--navy);margin:3px 0 6px}
.box ul{margin:4px 0 0 0;padding-left:18px}.box li{margin:3px 0}
.toc{columns:2;font-size:13px;color:var(--navy);margin:6px 0 4px}.toc div{padding:1px 0}
h2.sec{color:var(--navy);font-size:24px;margin:34px 0 8px;padding-bottom:4px;border-bottom:2px solid var(--teal)}
h3.sub{color:var(--navy);font-size:17px;margin:22px 0 6px}
.takeaway{background:var(--light);border-left:5px solid var(--teal);padding:10px 16px;font-weight:700;color:var(--navy);margin:8px 0 14px;border-radius:0 6px 6px 0}
p{margin:6px 0 10px}ul.b{margin:4px 0 12px;padding-left:22px}ul.b li{margin:3px 0}
.tiles{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:6px;margin:8px 0 14px}.tile{background:var(--light);border-top:4px solid var(--navy);text-align:center;padding:12px 6px 10px}
.tile.good{border-color:var(--green)}.tile.bad{border-color:var(--red)}.tile.warn{border-color:var(--amber)}
.tile .v{font-size:26px;font-weight:700;color:var(--navy)}.tile .l{font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#374151}
.tile .d{font-weight:700;margin-top:2px}.tile.good .d{color:var(--green)}.tile.bad .d{color:var(--red)}.tile.warn .d{color:var(--amber)}.tile .s{font-size:11.5px;color:var(--grey)}
.tt{font-weight:700;color:var(--navy);margin:14px 0 4px}.tw{overflow-x:auto}
table.t{border-collapse:collapse;width:100%;font-size:12px}table.t th{background:var(--navy);color:#fff;padding:6px 8px;text-align:left;position:sticky;top:0}
table.t td{padding:5px 8px;border-bottom:1px solid var(--line)}table.t tr:nth-child(even) td{background:#F7F8FB}
table.t .r{text-align:right;font-variant-numeric:tabular-nums}table.t .good{color:var(--green);font-weight:700}table.t .bad{color:var(--red);font-weight:700}table.t .warn{color:var(--amber);font-weight:700}
table.t tr.total td{background:#E6EBF4;font-weight:700;color:var(--navy);border-top:2px solid var(--navy)}table.t.wide{font-size:11px}
.src{font-size:10.5px;color:var(--grey);font-style:italic;margin:3px 0 16px}.note{font-size:12px;color:#374151;font-style:italic;margin-top:4px}
figure{margin:10px 0 4px;text-align:center}figure img{max-width:100%}figcaption{font-size:12px;color:#374151;font-style:italic}
.callout{border-left:5px solid var(--navy);background:#EEF3FB;padding:10px 16px;margin:10px 0 14px;border-radius:0 6px 6px 0}.callout.risk{border-color:var(--red);background:#FDF0EF}
.callout.action{border-color:var(--green);background:#EEF7EF}.callout.note{border-color:var(--grey);background:#F3F4F6}.callout b.t{display:block;margin-bottom:2px}
.callout.insight b.t{color:var(--navy)}.callout.risk b.t{color:var(--red)}.callout.action b.t{color:var(--green)}
.pri{display:inline-block;min-width:30px;text-align:center;color:#fff;font-weight:700;border-radius:4px;padding:2px 4px}.pri.P1{background:var(--red)}.pri.P2{background:var(--amber)}.pri.P3{background:var(--grey)}
.small{font-size:11px;color:var(--grey)}.small.guard{color:#7C2D12;font-size:11.5px}.conf-high{color:var(--green);font-weight:700}.conf-medium{color:var(--amber);font-weight:700}.conf-low{color:var(--red);font-weight:700}
dl.def{display:grid;grid-template-columns:150px 1fr;gap:0;margin:4px 0 14px}dl.def dt{font-weight:700;color:var(--navy);padding:5px 6px;border-bottom:1px solid var(--line)}dl.def dd{margin:0;padding:5px 6px;border-bottom:1px solid var(--line)}
.audit{border-left:5px solid var(--green);background:#EEF7EF;padding:10px 16px;margin:10px 0}.audit.fail{border-color:var(--red);background:#FDF0EF}
pre{white-space:pre-wrap;font-family:Consolas,monospace;font-size:10px;color:#374151;background:#F8F9FB;padding:6px 8px;border-radius:4px;margin:2px 0 8px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none;padding:0}}
"""


def esc(s) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(str(s)))


def _table(b) -> str:
    th = "".join(f"<th class='{'r' if a != 'left' else ''}'>{esc(h)}</th>" for h, a in zip(b["headers"], b["aligns"]))
    body = ""
    for row in b["rows"]:
        body += "<tr>" + "".join(f"<td class='{'r' if a != 'left' else ''} {c['tone']}'>{esc(c['text'])}</td>" for c, a in zip(row, b["aligns"])) + "</tr>"
    if b["total"]:
        body += "<tr class='total'>" + "".join(f"<td class='{'r' if a != 'left' else ''}'>{esc(c['text'])}</td>" for c, a in zip(b["total"], b["aligns"])) + "</tr>"
    out = (f"<div class='tt'>{esc(b['title'])}</div>" if b["title"] else "")
    out += f"<div class='tw'><table class='t{' wide' if b['wide'] else ''}'><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>"
    if b["note"]:
        out += f"<div class='note'>{esc(b['note'])}</div>"
    return out + f"<div class='src'>Source {esc(b['source'])}</div>"


def _block(b) -> str:
    t = b["t"]
    if t == "para":
        return f"<p>{esc(b['text'])}</p>"
    if t == "bullets":
        return "<ul class='b'>" + "".join(f"<li>{esc(i)}</li>" for i in b["items"]) + "</ul>"
    if t == "tiles":
        tiles = "".join(f"<div class='tile {x.get('tone', '')}'><div class='v'>{esc(x['value'])}</div><div class='l'>{esc(x['label'])}</div>"
                        + (f"<div class='d'>{esc(x['delta'])}</div>" if x.get("delta") else "") + (f"<div class='s'>{esc(x['sub'])}</div>" if x.get("sub") else "") + "</div>"
                        for x in b["tiles"])
        return (f"<div class='tt'>{esc(b['heading'])}</div>" if b.get("heading") else "") + f"<div class='tiles'>{tiles}</div>"
    if t == "table":
        return _table(b)
    if t == "chart":
        img = base64.b64encode(b["png"]).decode()
        return (f"<figure><img alt='{html.escape(b['title'])}' src='data:image/png;base64,{img}'/>"
                + (f"<figcaption>{esc(b['caption'])}</figcaption>" if b["caption"] else "") + f"</figure><div class='src' style='text-align:center'>Source {esc(b['source'])}</div>")
    if t == "callout":
        return f"<div class='callout {b['tone']}'>" + (f"<b class='t'>{esc(b['title'])}</b>" if b["title"] else "") + f"{esc(b['text'])}</div>"
    if t == "actions":
        rows = ""
        for a in b["items"]:
            meta = " · ".join(x for x in (a.get("where"), f"KPI: {a['kpi']}" if a.get("kpi") else "", a.get("timing")) if x)
            ev = f"<div class='small'><i>Evidence: {esc(', '.join(a['evidence']))}</i></div>" if a.get("evidence") else ""
            gr = f"<div class='small guard'>Guardrail: {esc(a['guardrail'])}</div>" if a.get("guardrail") else ""
            rows += (f"<tr><td><span class='pri {a['priority']}'>{a['priority']}</span></td><td>{esc(a['action'])}" + (f"<div class='small'>{esc(meta)}</div>" if meta else "") + gr + ev
                     + f"</td><td>{esc(a['owner'])}</td><td>{esc(a.get('impact', ''))}</td><td class='conf-{a.get('confidence', 'medium')}'>{a.get('confidence', 'medium').capitalize()}</td></tr>")
        title = f"<div class='tt'>{esc(b['title'])}</div>" if b.get("title") else ""
        return title + f"<div class='tw'><table class='t'><thead><tr><th></th><th>Action</th><th>Owner</th><th>Expected impact</th><th>Confidence</th></tr></thead><tbody>{rows}</tbody></table></div><div class='src'></div>"
    if t == "definitions":
        return "<dl class='def'>" + "".join(f"<dt>{esc(i['term'])}</dt><dd>{esc(i['definition'])}</dd>" for i in b["items"]) + "</dl>"
    if t == "break":
        return "<hr/>"
    return ""


def render(resolved: dict) -> str:
    f = resolved["frame"]
    meta = [("Period", f.get("period_label")), ("Compared with", f.get("comparison_label")), ("Scope", f.get("scope")), ("Measure", f.get("measure")),
            ("Prepared for", f.get("audience")), ("Data as of", f.get("data_as_of"))]
    meta_html = "".join(f"<b>{k}</b><span>{esc(v)}</span>" for k, v in meta if v)
    bl = "".join(f"<li>{esc(x)}</li>" for x in f.get("bottom_line", []))
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(f['title'])}</title><style>{CSS}</style></head><body><div class='page'>",
           "<div class='conf'>CONFIDENTIAL \u2014 INTERNAL USE ONLY</div>",
           f"<div class='kicker'>{TYPE_LABEL.get(f.get('report_type', 'ad_hoc'), '')}</div><h1 class='title'>{esc(f['title'])}</h1>",
           f"<div class='sub'>{esc(f.get('subtitle', ''))}</div>" if f.get("subtitle") else "", f"<div class='meta'>{meta_html}</div>",
           f"<div class='box'><div class='lbl'>THE BOTTOM LINE</div><div class='head'>{esc(f['headline'])}</div><ul>{bl}</ul></div>"]
    if f.get("decisions"):
        out.append("<div class='box amber'><div class='lbl'>DECISIONS AND ASKS</div><ul>" + "".join(f"<li>{esc(d)}</li>" for d in f["decisions"]) + "</ul></div>")
    out.append("<div class='toc'>" + "".join(f"<div>{s['number']}. {esc(s['title'])}</div>" for s in resolved["sections"]) + "<div style='color:#6B7280'>Appendix. Evidence and method</div></div>")
    for s in resolved["sections"]:
        out.append(f"<h2 class='sec' id='{html.escape(s['id'])}'>{s['number']}. {esc(s['title'])}</h2>")
        if s["takeaway"]:
            out.append(f"<div class='takeaway'>{esc(s['takeaway'])}</div>")
        out += [_block(b) for b in s["blocks"]]
        if s.get("evidence"):
            out.append(f"<div class='src'>Evidence for this section: {esc(s['evidence'])}</div>")
    if f.get("caveats"):
        out.append("<h3 class='sub'>Caveats and what we could not see</h3><ul class='b'>" + "".join(f"<li>{esc(c)}</li>" for c in f["caveats"]) + "</ul>")
    a = resolved["audit"]
    rv = resolved.get("review") or {}
    out.append("<h2 class='sec'>Appendix: evidence, calculations and method</h2>")
    out.append(f"<div class='audit{'' if a['pass'] else ' fail'}'><b>NUMBER AUDIT</b><br/>{a['traced']} of {a['numbers_checked']} figures in the text were matched to a data result or a recorded calculation."
               + ("" if a["pass"] else f" {a['untraced']} could not be traced.") + (f"<br/><span class='small'>Independent review: {esc(rv.get('verdict', 'n/a'))}. {esc(rv.get('summary', ''))}</span>" if rv else "") + "</div>")
    out.append("<h3 class='sub'>Evidence register</h3><div class='tw'><table class='t'><thead><tr><th>ID</th><th>What it answers</th><th class='r'>Rows</th><th>Used</th></tr></thead><tbody>"
               + "".join(f"<tr><td>{e['id']}</td><td>{esc((e['question'] or e['description'])[:200])}</td><td class='r'>{e['rows']}</td><td>{'yes' if e['used'] else 'context'}</td></tr>" for e in resolved["evidence"]) + "</tbody></table></div>")
    if resolved["calcs"]:
        out.append("<h3 class='sub'>Calculations cited in this report</h3><div class='tw'><table class='t wide'><thead><tr><th>ID</th><th>Measure</th><th>Formula</th><th class='r'>Result</th><th>Inputs</th></tr></thead><tbody>"
                   + "".join(f"<tr><td>{d['id']}</td><td>{esc(d['label'])}</td><td><code>{html.escape(d['formula'])}</code></td><td class='r'>{d['value']:,.4g}</td><td>{esc(d['inputs'])}</td></tr>" for d in resolved["calcs"]) + "</tbody></table></div>")
        if resolved.get("calcs_not_shown"):
            out.append(f"<div class='small'><i>{resolved['calcs_not_shown']} further working calculations are recorded in the case ledger but not cited in the report.</i></div>")
    sqls = [e for e in resolved["evidence"] if e["sql"] and e["used"]]
    if sqls:
        flat = lambda s: html.escape(re.sub(r"\s+", " ", s)[:900])
        out.append("<h3 class='sub'>Queries behind the figures</h3>" + "".join(
            f"<div class='small'><b>{e['id']}</b> \u2014 {esc(e['question'][:140])}</div><pre>{flat(e['sql'])}</pre>" for e in sqls))
    out.append(f"<div class='small' style='margin-top:18px'><i>Generated {resolved['generated_at']} by the Croma profit planning report studio.</i></div></div></body></html>")
    return "".join(out)
