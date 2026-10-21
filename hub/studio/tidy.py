"""Reader-friendly precision. Writers quote what calc returned (for example 37.8668); the report should read 37.9. This rounds any figure in the narrative
that has three or more decimals. The audit still traces the rounded figure, because it matches at the precision shown."""
from __future__ import annotations

import re

from studio.audit import SIMPLE, SKIP_PATTERNS, UNIT_AFTER


def _fmt(raw: str, sign: str, value: float, before: str, after: str) -> str:
    money = "\u20b9" in before or bool(re.match(r"^\s*(?:cr\b|crore)", after, re.I))
    dp = (1 if abs(value) >= 1 else 2) if money else (1 if re.match(r"^\s*(?:%|pp\b)", after, re.I) else 2)
    digits = f"{abs(value):,.{dp}f}" if "," in raw else f"{abs(value):.{dp}f}"
    return sign + digits


def round_text(text: str) -> tuple[str, int]:
    if not isinstance(text, str) or not text:
        return text, 0
    # protect ids, dates and years from rewriting by blanking them while we find the positions of numbers
    masked = text
    for p in SKIP_PATTERNS:
        masked = p.sub(lambda m: " " * len(m.group(0)), masked)
    out, last, changed = [], 0, 0
    for m in SIMPLE.finditer(masked):
        raw = m.group(2)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if decimals < 3:
            continue
        value = float(raw.replace(",", ""))
        replacement = _fmt(raw, m.group(1), value, text[max(0, m.start() - 2): m.start()], text[m.end(): m.end() + 12])
        out.append(text[last: m.start()])
        out.append(replacement)
        last = m.end()
        changed += 1
    out.append(text[last:])
    return "".join(out), changed


def _fix(obj, count: list[int]):
    if isinstance(obj, str):
        new, n = round_text(obj)
        count[0] += n
        return new
    if isinstance(obj, list):
        return [_fix(x, count) for x in obj]
    if isinstance(obj, dict):
        return {k: (v if k in SKIP_KEYS else _fix(v, count)) for k, v in obj.items()}
    return obj


SKIP_KEYS = {"id", "type", "kind", "result_id", "col", "key", "format", "start_ref", "evidence", "x", "sum_columns", "series", "columns", "priority", "tone", "confidence",
             "heat", "align", "section_order", "report_type"}


def tidy(doc: dict) -> tuple[dict, int]:
    """Rounds the narrative fields of a frame or a section. Returns the new document and how many figures were rounded."""
    count = [0]
    return _fix(doc, count), count[0]
