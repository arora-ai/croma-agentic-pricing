"""Keeping the unit an analysis runs in the same as the unit the user is shown.

The data space does not always honour "in rupees crore": its SQL may return rupees while the Data agent, which writes the answer, converts them for the reader.
The harness computes from the ledger (the SQL's own rows), so before it computes it compares each ledger result with the table the Data agent showed. When a
column differs by a power of ten (rupees against crore, a fraction against a percentage) the analysis runs on a rescaled copy, so every figure it produces is in the
unit the answer used. The raw result is never changed: it stays the evidence, with the SQL behind it."""
from __future__ import annotations

import math
import re
import statistics

from studio import ledger

NUMBER = re.compile(r"([-+]?)\s*[\u20b9$]?\s*(\d[\d,]*(?:\.\d+)?)")
UNIT_NOTE = re.compile(r"\s*[\(\[].*?[\)\]]")  # "cur_nmv (₹ Cr)" is the column cur_nmv
SEPARATOR = re.compile(r"-+")
MIN_POWER, MAX_POWER = -2, 9  # 0.01 (a fraction shown as a percentage) up to 10^9
TOLERANCE = 0.04  # in log10 (about 10%): a figure rounded for display is still this close to a power of ten of the real one
ROWS_USED = 8


def markdown_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """The markdown tables of a Data agent answer, as (header cells, rows of cells)."""
    found, block = [], []
    for line in text.splitlines() + [""]:
        stripped = line.strip()
        if stripped.startswith("|"):
            block.append(stripped)
            continue
        if len(block) >= 3:
            cells = [[c.strip() for c in b.strip("|").split("|")] for b in block]
            if all(SEPARATOR.fullmatch(c.replace(":", "").replace(" ", "")) for c in cells[1] if c):
                found.append((cells[0], [r for r in cells[2:] if len(r) == len(cells[0])]))
        block = []
    return found


def displayed_number(cell: str) -> float | None:
    """The number a table cell shows: '2,205', '7.68', '-\u20b916.56 Cr', '11.92%'; None when there is none ('n/a', a label)."""
    m = NUMBER.search(cell.replace("\u2212", "-"))
    if not m:
        return None
    value = float(m.group(2).replace(",", ""))
    return -value if m.group(1) == "-" else value


def _number(v) -> float | None:
    try:
        return ledger.to_number(v)
    except (ValueError, TypeError):
        return None


def power_of_ten(pairs: list[tuple[float, float]]) -> int:
    """k when the ledger values are 10^k times the displayed ones (the same k on the largest rows), else 0."""
    usable = [(a, b) for a, b in pairs if a and b]
    if not usable:
        return 0
    usable.sort(key=lambda p: -abs(p[1]))  # the largest displayed figures carry the least rounding
    logs = [math.log10(abs(a / b)) for a, b in usable[:ROWS_USED]]
    k = round(statistics.median(logs))
    if k == 0 or not MIN_POWER <= k <= MAX_POWER:
        return 0
    agreeing = sum(1 for x in logs if abs(x - k) <= TOLERANCE)
    return k if agreeing >= max(1, math.ceil(0.75 * len(logs))) else 0


def _compare(table: ledger.Table, headers: list[str], rows: list[list[str]]) -> tuple[int, dict[str, float]]:
    """(how many ledger columns this displayed table lines up with, {column: factor} for those that differ by a power of ten)."""
    by_name = {ledger.norm(c): i for i, c in enumerate(table.columns)}
    mapping = {j: by_name[n] for j, h in enumerate(headers) if (n := ledger.norm(UNIT_NOTE.sub("", h))) in by_name}
    if len(headers) == len(table.columns):  # the same shape: a column the agent renamed is the one in the same position
        taken = set(mapping.values())
        mapping.update({j: j for j in range(len(headers)) if j not in mapping and j not in taken})
    if not mapping or not table.rows:
        return 0, {}
    # Which displayed row is which ledger row: by the label in the first text column, otherwise by position.
    label_col = next((i for i, v in enumerate(table.rows[0]) if isinstance(v, str)), None)
    shown_label = next((j for j, i in mapping.items() if i == label_col), None)
    if label_col is not None and shown_label is not None:
        index = {ledger.norm(str(r[label_col])): r for r in table.rows}
        aligned = [(index[ledger.norm(row[shown_label])], row) for row in rows if ledger.norm(row[shown_label]) in index]
    else:
        aligned = list(zip(table.rows, rows))
    factors: dict[str, float] = {}
    for j, i in mapping.items():
        if i == label_col:
            continue
        pairs = [(a, b) for led, row in aligned if (a := _number(led[i])) is not None and (b := displayed_number(row[j])) is not None]
        if (k := power_of_ten(pairs)):
            factors[table.columns[i]] = 10.0 ** k
    return len(mapping), factors


def detect(table: ledger.Table, answer: str) -> dict[str, float]:
    """{column: factor} for the ledger columns whose values are `factor` times the figures the answer shows, from the displayed table that fits the result best."""
    best_fit, best = 0, {}
    for headers, rows in markdown_tables(answer):
        fit, factors = _compare(table, headers, rows)
        if fit > best_fit:
            best_fit, best = fit, factors
    return best


def align(case_id: str, result_id: str, answer: str) -> tuple[dict[str, float], str | None]:
    """Compares a ledger result with the Data agent's answer about it. Returns ({column: factor}, the id of the rescaled copy to analyse), or ({}, None) when the
    result is already in the unit the answer shows (or cannot be compared)."""
    factors = detect(ledger.get_table(case_id, result_id), answer)
    return (factors, ledger.rescale_table(case_id, result_id, factors)) if factors else ({}, None)


def describe(factors: dict[str, float], scaled_id: str) -> str:
    """One sentence for the evidence pack, so a reviewer can see the conversion."""
    parts = ", ".join(f"{name} divided by {f:,.0f}" if f >= 1 else f"{name} divided by {f:g}" for name, f in factors.items())
    return f"the data space returned these columns in a different unit from the answer shown to the user; analysed in the answer's unit on {scaled_id} ({parts})"
