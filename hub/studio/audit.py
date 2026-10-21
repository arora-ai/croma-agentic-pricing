"""Number audit and structure lint.

The audit pulls every number out of the narrative (paragraphs, bullets, callouts, tile text, action impacts, headline) and looks for it in the
case ledger: result cells, column totals and derived values, allowing for the rounding shown and for unit changes (rupees to crore, ratio to
percent). A number it cannot trace is reported, and the Writer must either derive it with calc or remove it.
"""
from __future__ import annotations

import math
import re

from studio import ledger

SCALES = (1.0, 1e-7, 1e-5, 100.0, 0.01, 1e-3, 1e-6, 1e-2)
MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
SIMPLE = re.compile(r"(?<![A-Za-z0-9_.\-])([+\-\u2212]?)(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
SKIP_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?(?:\s*[\u2013\-to]+\s*\d{{1,2}}(?:st|nd|rd|th)?)?\s+{MONTHS}\b(?:\s+\d{{4}})?", re.I),
    re.compile(rf"\b{MONTHS}\s+\d{{1,2}}(?:\s*[\u2013\-]\s*\d{{1,2}})?(?:,?\s+\d{{4}})?", re.I),
    re.compile(r"\b(?:section|part|chart|table|figure|appendix|week|wk|w|q|fy|p)\s*\d+(?:\.\d+)*\b", re.I),
    re.compile(r"\b[RTDAI]\d+\b"),
    re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.I),
    re.compile(r"\b(?:19|20)\d{2}(?:\s*[\u2013\-/]\s*\d{2,4})?\b"),
]
UNIT_AFTER = re.compile(r"^\s*(?:%|cr\b|crore|pp\b|x\b|bps|units|lakh|k\b|m\b|bn\b)", re.I)


def _key(value: float, decimals: int) -> int:
    return math.floor(abs(value) * 10 ** decimals + 0.5 + 1e-9)


def _candidate_keys(case_id: str) -> dict[int, set[int]]:
    cands = ledger.candidate_numbers(case_id)
    keys = {d: set() for d in range(0, 5)}
    for c in cands:
        if c is None or math.isnan(c) or math.isinf(c):
            continue
        for s in SCALES:
            v = c * s
            for d in keys:
                keys[d].add(_key(v, d))
                x = abs(v) * 10 ** d
                if abs((x % 1) - 0.5) < 1e-6:  # an exact tie can print either way (20.25 -> 20.2 or 20.3)
                    keys[d].add(math.floor(x))
    return keys


def _strip_skips(text: str) -> str:
    for p in SKIP_PATTERNS:
        text = p.sub(" ", text)
    return text


def extract_numbers(text: str) -> list[dict]:
    clean = _strip_skips(text)
    out = []
    for m in SIMPLE.finditer(clean):
        raw = m.group(2)
        number = float(raw.replace(",", ""))
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        after = clean[m.end(): m.end() + 12]
        before = clean[max(0, m.start() - 2): m.start()]
        has_unit = bool(UNIT_AFTER.match(after)) or "\u20b9" in before
        if decimals == 0 and number <= 10 and not has_unit:
            continue  # counts and ordinals such as "top 5" or "3 zones"
        start = max(0, m.start() - 40)
        out.append({"token": (m.group(1) + raw), "value": number, "decimals": decimals, "context": clean[start: m.end() + 30].strip()})
    return out


def trace(case_id: str, text: str, keys: dict[int, set[int]] | None = None) -> tuple[list[dict], list[dict]]:
    keys = keys or _candidate_keys(case_id)
    matched, missing = [], []
    for n in extract_numbers(text):
        d = min(n["decimals"], 4)
        ok = _key(n["value"], d) in keys[d]
        if not ok and d == 0:  # "about 72%" written from 71.6
            ok = _key(n["value"], 0) in keys[0]
        (matched if ok else missing).append(n)
    return matched, missing


def narrative_items(frame: dict, sections: list[dict]) -> list[tuple[str, str]]:
    """(where, text) for every piece of free text the writer produced."""
    items: list[tuple[str, str]] = []
    if frame:
        items.append(("headline", frame.get("headline", "")))
        items += [(f"bottom line {i + 1}", t) for i, t in enumerate(frame.get("bottom_line", []))]
        items += [(f"decision {i + 1}", t) for i, t in enumerate(frame.get("decisions", []))]
        items += [(f"caveat {i + 1}", t) for i, t in enumerate(frame.get("caveats", []))]
        items.append(("subtitle", frame.get("subtitle", "")))
    for s in sections:
        sid = s.get("title", s.get("id", ""))
        items.append((f"{sid} / takeaway", s.get("takeaway", "")))
        for bi, b in enumerate(s.get("blocks", [])):
            t = b.get("type")
            where = f"{sid} / block {bi + 1} ({t})"
            if t == "paragraph":
                items.append((where, b["text"]))
            elif t == "bullets":
                items += [(f"{where} item {i + 1}", x) for i, x in enumerate(b["items"])]
            elif t == "callout":
                items.append((where, f"{b.get('title', '')}. {b['text']}"))
            elif t == "kpi_tiles":
                for ti, tile in enumerate(b["tiles"]):
                    items.append((f"{where} tile {ti + 1}", " ".join([tile["value"], tile.get("sub", ""), tile.get("delta", "")])))
            elif t == "actions":
                for ai, a in enumerate(b["items"]):
                    items.append((f"{where} action {ai + 1}", " ".join([a["action"], a.get("where", ""), a.get("impact", ""), a.get("kpi", "")])))
            elif t in ("table", "chart"):
                items.append((f"{where} note", b.get("note", "") or b.get("caption", "")))
    return items


def audit(case_id: str, frame: dict, sections: list[dict]) -> dict:
    keys = _candidate_keys(case_id)
    matched_total, missing_all = 0, []
    for where, text in narrative_items(frame, sections):
        if not text:
            continue
        matched, missing = trace(case_id, text, keys)
        matched_total += len(matched)
        missing_all += [{"where": where, **m} for m in missing]
    total = matched_total + len(missing_all)
    return {"numbers_checked": total, "traced": matched_total, "untraced": len(missing_all),
            "pass": not missing_all, "untraced_numbers": missing_all[:60]}


def lint(case_id: str, frame: dict, sections: list[dict]) -> list[str]:
    """Structural problems a reader would notice."""
    problems: list[str] = []
    c = ledger.case(case_id)
    if not frame:
        return ["No frame saved. Call save_frame first."]
    if not frame.get("headline"):
        problems.append("Frame has no headline answer.")
    if len(frame.get("bottom_line", [])) < 3:
        problems.append("Bottom line should have 3 to 5 points.")
    ids = {s["id"] for s in sections}
    for sid in frame.get("section_order", []):
        if sid not in ids:
            problems.append(f"section_order lists '{sid}' but no such section was saved.")
    for s in sections:
        if not s.get("takeaway"):
            problems.append(f"Section '{s['title']}' has no takeaway sentence.")
        for b in s.get("blocks", []):
            if b["type"] in ("table", "chart"):
                rid = b["result_id"].strip().upper()
                if rid not in c.tables:
                    problems.append(f"Section '{s['title']}': {b['type']} uses unknown result {b['result_id']}.")
                    continue
                t = c.tables[rid]
                names = [col["key"] for col in b["columns"]] if b["type"] == "table" else [b["x"], *[x["col"] for x in b["series"]]]
                for nme in names:
                    try:
                        t.col_index(nme)
                    except KeyError:
                        problems.append(f"Section '{s['title']}': {rid} has no column '{nme}'. Columns: {', '.join(t.columns)}.")
            if b["type"] == "actions":
                for a in b["items"]:
                    if not a.get("owner"):
                        problems.append(f"Action '{a['action'][:50]}' has no owner.")
    if not any(b["type"] == "actions" for s in sections for b in s.get("blocks", [])):
        problems.append("The report has no actions block. A prescriptive report needs one.")
    for where, text in narrative_items(frame, sections):
        for n in extract_numbers(text or ""):
            if n["decimals"] >= 3:
                problems.append(f"{where}: '{n['token']}' has {n['decimals']} decimals. Round for the reader: \u20b9 Cr to 1 decimal (2 if small), percentages to 1, pp to 1. A value under 0.01 can be written 'under \u20b90.01 Cr'.")
    return problems
