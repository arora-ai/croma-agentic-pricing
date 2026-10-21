"""Number and cell formatting shared by the DOCX and HTML renderers."""
from __future__ import annotations

from typing import Any

from studio.ledger import to_number

RUPEE = "\u20b9"


def _num(v: Any):
    try:
        return to_number(v)
    except (ValueError, TypeError):
        return None


def fmt(value: Any, kind: str = "text") -> str:
    """kind: text | int | num1 | num2 | pct (value already in %) | ratio_pct (0.51 -> 51%) | spct (signed %) | pp (signed percentage points)
    | cr (rupee crore, 1 dp) | cr2 | inr | x (multiple) | date"""
    if value is None or value == "":
        return "\u2014"
    if kind in ("text", "date"):
        return str(value)
    n = _num(value)
    if n is None:
        return str(value)
    if kind == "int":
        return f"{n:,.0f}"
    if kind == "num1":
        return f"{n:,.1f}"
    if kind == "num2":
        return f"{n:,.2f}"
    if kind == "pct":
        return f"{n:,.1f}%"
    if kind == "ratio_pct":
        return f"{n * 100:,.1f}%"
    if kind == "spct":
        return f"{n:+,.1f}%"
    if kind == "pp":
        return f"{n:+,.1f} pp"
    if kind == "cr":
        return f"{RUPEE}{n:,.1f} Cr"
    if kind == "cr2":
        return f"{RUPEE}{n:,.2f} Cr"
    if kind == "inr":
        return f"{RUPEE}{n:,.0f}"
    if kind == "x":
        return f"{n:,.2f}x"
    return str(value)


def tone_for(value: Any, heat: str) -> str:
    """good | bad | warn | '' for conditional colouring. achievement: 100% and above is good, below 90% is bad. growth/delta: sign."""
    n = _num(value)
    if n is None or heat in ("", "none"):
        return ""
    if heat == "achievement":
        return "good" if n >= 100 else ("warn" if n >= 90 else "bad")
    if heat in ("growth", "delta"):
        return "good" if n > 0 else ("bad" if n < 0 else "")
    return ""
