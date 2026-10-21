"""Charts for the report studio (matplotlib, drawn from ledger rows only)."""
from __future__ import annotations

import io
import textwrap
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from studio import ledger  # noqa: E402
from studio.fmt import fmt  # noqa: E402

NAVY, GREY, GREEN, RED, TEAL, AMBER, PURPLE = "#1F3A6B", "#A6A6A6", "#2E7D32", "#C62828", "#0F766E", "#D97706", "#6D28D9"
PALETTE = [NAVY, TEAL, AMBER, PURPLE, "#0369A1", GREEN]
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False, "axes.edgecolor": "#888888",
                     "axes.titleweight": "bold", "axes.titlecolor": NAVY, "axes.titlesize": 12, "font.size": 9})


def _series_values(table: ledger.Table, rows: list[list], col: str) -> list[float]:
    idx = table.col_index(col)
    out = []
    for r in rows:
        try:
            out.append(ledger.to_number(r[idx]))
        except (ValueError, TypeError):
            out.append(float("nan"))
    return out


def _wrap(label: str, width: int = 12) -> str:
    return "\n".join(textwrap.wrap(str(label), width, break_long_words=False)[:3]) or str(label)


def _colors(series, i: int, n: int) -> str:
    if series.color:
        return series.color
    if n == 2:
        return GREY if i == 0 else NAVY  # first series is the comparison year, second is the current year
    return PALETTE[i % len(PALETTE)]


def render(chart, table: ledger.Table, case_id: str) -> bytes:
    rows = table.rows[: chart.top_n] if chart.top_n else table.rows
    xi = table.col_index(chart.x)
    labels = [str(r[xi]) for r in rows]
    n = len(rows)
    kind = chart.kind
    fig_h = max(3.2, 0.34 * n + 1.4) if kind == "hbar" else 3.6
    fig, ax = plt.subplots(figsize=(7.4, fig_h), dpi=200)
    vf = chart.value_format
    series = chart.series

    if kind == "hbar":
        vals = _series_values(table, rows, series[0].col)
        y = list(range(n))[::-1]
        finite = [v for v in vals if v == v]
        lo, hi = min(finite + [0.0]), max(finite + [0.0])
        span = (hi - lo) or 1.0
        diverging = lo < 0  # a ranking that has losses: green for gains, red for losses
        colors = [(GREEN if v >= 0 else RED) if v == v else GREY for v in vals] if diverging else (series[0].color or NAVY)
        ax.barh(y, [v if v == v else 0 for v in vals], color=colors, height=0.62)
        ax.set_yticks(y, [_wrap(x, 26) for x in labels])
        for yi, v in zip(y, vals):
            if v == v:
                ax.text(v + (span * 0.012 if v >= 0 else -span * 0.012), yi, fmt(v, vf), va="center", ha="left" if v >= 0 else "right", fontsize=8)
        ax.set_xlim(lo - span * 0.16 if diverging else 0, hi + span * 0.16 if hi > 0 else span * 0.05)
        if diverging:
            ax.axvline(0, color="#888888", linewidth=0.8)
        ax.set_xlabel(chart.y_label or series[0].label or series[0].col)
    elif kind in ("bar", "stacked"):
        width = 0.8 / (1 if kind == "stacked" else len(series))
        bottoms = [0.0] * n
        all_vals = []
        for i, s in enumerate(series):
            vals = _series_values(table, rows, s.col)
            all_vals.append(vals)
            xs = [j + (0 if kind == "stacked" else (i - (len(series) - 1) / 2) * width) for j in range(n)]
            ax.bar(xs, [v if v == v else 0 for v in vals], width=width * 0.95, bottom=bottoms if kind == "stacked" else None,
                   label=s.label or s.col, color=_colors(s, i, len(series)))
            if kind == "stacked":
                bottoms = [b + (v if v == v else 0) for b, v in zip(bottoms, vals)]
        ax.set_xticks(range(n), [_wrap(x, 12 if n <= 8 else 9) for x in labels], rotation=0 if n <= 8 else 45, ha="center" if n <= 8 else "right", fontsize=8 if n > 6 else 9)
        flat = [v for vs in all_vals for v in vs if v == v]
        peak = max(bottoms) if kind == "stacked" else max(flat + [0.0]) or 1.0
        floor = min(flat + [0.0]) if kind != "stacked" else 0.0
        if kind == "bar" and len(series) == 2:
            for j in range(n):
                ly, cy = all_vals[0][j], all_vals[1][j]
                if ly == ly and cy == cy and ly > 0:
                    g = (cy / ly - 1) * 100
                    ax.text(j + width / 2, max(cy, 0) + peak * 0.015, fmt(g, "spct"), ha="center", fontsize=8, fontweight="bold", color=GREEN if g >= 0 else RED)
                elif cy == cy and cy > 0:
                    ax.text(j + width / 2, cy + peak * 0.015, "New", ha="center", fontsize=8, fontweight="bold", color=GREEN)
        elif kind == "bar" and len(series) == 1:
            for j, v in enumerate(all_vals[0]):
                if v == v:
                    ax.text(j, v + (peak * 0.015 if v >= 0 else floor * 0.03), fmt(v, vf), ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
        ax.set_ylim(floor * 1.2 if floor < 0 else 0, peak * 1.15)
        if floor < 0:
            ax.axhline(0, color="#888888", linewidth=0.8)
        ax.set_ylabel(chart.y_label)
        if len(series) > 1:
            ax.legend(frameon=False, ncol=len(series), loc="upper right", fontsize=8)
    elif kind == "line":
        for i, s in enumerate(series):
            vals = _series_values(table, rows, s.col)
            ax.plot(range(n), vals, marker="o", markersize=3.5, linewidth=2, label=s.label or s.col, color=_colors(s, i, len(series) if len(series) != 2 else 3))
        ax.set_xticks(range(n), [_wrap(x, 12 if n <= 8 else 9) for x in labels], rotation=0 if n <= 8 else 45, ha="center" if n <= 8 else "right", fontsize=8 if n > 6 else 9)
        ax.set_ylabel(chart.y_label)
        ax.grid(axis="y", alpha=0.25)
        if len(series) > 1:
            ax.legend(frameon=False, ncol=len(series), fontsize=8)
    elif kind == "waterfall":
        deltas = _series_values(table, rows, series[0].col)
        start = ledger.resolve_cell(ledger.case(case_id), chart.start_ref)[0] if chart.start_ref else 0.0
        names = ([chart.start_label or "Start"] if chart.start_ref else []) + labels + [chart.end_label or "End"]
        running, bars = start, []
        if chart.start_ref:
            bars.append((0, start, NAVY, start))
        for d in deltas:
            d = d if d == d else 0
            bars.append((running if d >= 0 else running + d, abs(d), GREEN if d >= 0 else RED, d))
            running += d
        bars.append((0, running, NAVY, running))
        ax.bar(range(len(bars)), [b[1] for b in bars], bottom=[b[0] for b in bars], color=[b[2] for b in bars], width=0.62)
        levels = [b[0] + b[1] for b in bars] + [b[0] for b in bars if b[2] != NAVY]
        lo, hi = min(levels), max(levels)
        span = max(hi - lo, abs(hi) * 0.02, 1e-9)
        floor = max(0.0, lo - span * 1.2)
        ax.set_ylim(floor, hi + span * 0.25)
        for j, (base, h, color, label_value) in enumerate(bars):
            text = fmt(label_value, vf) if color == NAVY else f"{label_value:+,.1f}"
            ax.text(j, base + h + span * 0.03, text, ha="center", fontsize=8)
        ax.set_xticks(range(len(names)), [_wrap(n, 9 if len(names) > 6 else 12) for n in names], fontsize=7.5 if len(names) > 6 else 9)
        ax.set_ylabel(chart.y_label)
        if floor > 0:
            ax.text(0.0, 0.985, "Axis does not start at zero", transform=ax.transAxes, fontsize=6.5, color="#888888", va="top", ha="left")

    ax.set_title(textwrap.fill(chart.title, 64), loc="left", pad=10, fontsize=11)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()
