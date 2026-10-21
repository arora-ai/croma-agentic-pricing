"""Evidence citations in report text, such as [R2, D43] or (R3; D79-D81).

Writers cite ledger ids inline so every claim stays traceable; the renderers move them out of the prose into a quiet evidence line per section, and the
length limits in the spec are measured without them."""
from __future__ import annotations

import re

REF = r"[RTDAI]\d+(?::[\w.]+)?(?:\s*[\u2013\u2014-]\s*[RTDAI]?\d+)?"
CITE = re.compile(rf"\s*[\[(]\s*{REF}(?:\s*[,;]\s*{REF})*\s*[\])]")
ID = re.compile(r"\b([RTDAI])(\d+)\b")


def strip(text: str) -> tuple[str, set[str]]:
    ids: set[str] = set()
    for m in CITE.finditer(text):
        ids.update(f"{a}{b}" for a, b in ID.findall(m.group(0)))
    return CITE.sub("", text).strip(), ids


def plain(text: str) -> str:
    return strip(text)[0]


def compact(ids: set[str], limit: int = 60) -> str:
    order = sorted(ids, key=lambda x: ("RTDAI".index(x[0]), int(x[1:])))
    shown = order[:limit]
    return ", ".join(shown) + (f" and {len(order) - limit} more" if len(order) > limit else "")
