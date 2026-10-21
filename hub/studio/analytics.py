"""Deterministic analytics for the Analysis and Research harnesses and the report studio's `analyze` tool.

The language model chooses WHICH operation to run and on WHICH columns; this module does all the arithmetic, so a driver table is exact and
reproducible. The decompositions are exact: the parts add up to the total change, and every result says so in `checks`.
Rows are dicts keyed by normalised column name (ledger.norm). There is no I/O here; studio/analyze.py binds it to the evidence ledger.

    contribution        what each segment added to the change in a total
    rate_mix            change in a ratio (margin %, markdown %, price) split into a rate effect and a mix effect, per segment
    price_volume_mix    change in a value (sales) split into volume, mix and price effects, per segment
    target_gap          actual against target, per segment
    concentration       how concentrated a total is in a few segments
    trend               direction and size of a series over time
    reconcile           whether two figures that should agree do
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

EPS = 1e-9
CONTROL_LABELS = {"total", "grand total", "overall", "overall total", "all", "all segments"}


class AnalyticsError(ValueError):
    pass


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


@dataclass
class Result:
    op: str
    columns: list[str]
    rows: list[list]
    outputs: dict[str, float | None]
    notes: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)  # output name -> pp | pct | value | count | ratio, a hint for display


# ---------------------------------------------------------------- helpers

def _f(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return None if math.isnan(v) else float(v)
    try:
        return float(str(v).replace(",", "").replace("%", "").replace("\u20b9", "").strip())
    except ValueError:
        return None


def _r(x: float | None, nd: int = 10) -> float | None:
    return None if x is None or math.isnan(x) or math.isinf(x) else round(x, nd)


def _require(rows: list[dict], *cols: str) -> None:
    if not rows:
        raise AnalyticsError("the result has no rows to analyse")
    missing = [c for c in cols if norm(c) not in rows[0]]
    if missing:
        raise AnalyticsError(f"column(s) {missing} not found. The result has: {', '.join(rows[0])}")


def _segments(rows: list[dict], key: str, measures: dict[str, str]) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], int]:
    """Sums each measure by segment label (a result can repeat a label when it was grouped finer). Rows labelled like a total are set aside as control
    totals. Returns (segments, control rows, number of empty cells treated as zero)."""
    k = norm(key)
    segments: dict[str, dict[str, float]] = {}
    controls: dict[str, dict[str, float]] = {}
    empty = 0
    for r in rows:
        raw = r.get(k)
        label = "(blank)" if raw is None or str(raw).strip() == "" else str(raw).strip()
        target = controls if label.lower() in CONTROL_LABELS else segments
        seg = target.setdefault(label, {m: 0.0 for m in measures})
        for m, col in measures.items():
            v = _f(r.get(norm(col)))
            if v is None:
                empty += 1
            else:
                seg[m] += v
    if not segments:
        raise AnalyticsError(f"no segments found in column '{key}'")
    return segments, controls, empty


def _material(effect: float, gross: float, min_share: float, abs_floor: float) -> bool:
    return abs(effect) > EPS and abs(effect) >= max(abs_floor, min_share * gross) - EPS


def _ratio(a: float, b: float) -> float | None:
    return a / b if abs(b) > EPS else None


def _control_note(controls: dict, measure: str, total: float, notes: list[str], checks: dict, name: str) -> None:
    if not controls:
        return
    label, vals = next(iter(controls.items()))
    diff = vals[measure] - total
    checks[f"matches_control_total_{name}"] = abs(diff) <= max(1e-6, 1e-4 * abs(total))
    notes.append(f"The result's '{label}' row was not counted as a segment; it is {'consistent with' if checks[f'matches_control_total_{name}'] else 'DIFFERENT from'} the sum of the segments.")


def _pct(x: float | None) -> float | None:
    return None if x is None else x * 100.0


def _sorted_by_abs(items: list[dict], field_name: str) -> list[dict]:
    return sorted(items, key=lambda it: (-abs(it[field_name]), str(it["label"])))


def _top_share(items: list[dict], field_name: str, n: int, total: float) -> float | None:
    return _ratio(sum(abs(it[field_name]) for it in _sorted_by_abs(items, field_name)[:n]), total)


# ---------------------------------------------------------------- operations

def contribution(rows: list[dict], key: str, cur: str, base: str, min_share: float = 0.05, abs_floor: float = 0.0) -> Result:
    """What each segment added to the change in a total (current minus base). Shares are of the gross movement (sum of absolute changes), which stays
    meaningful when increases and decreases offset each other."""
    _require(rows, key, cur, base)
    segments, controls, empty = _segments(rows, key, {"cur": cur, "base": base})
    total_cur = sum(v["cur"] for v in segments.values())
    total_base = sum(v["base"] for v in segments.values())
    total_change = total_cur - total_base
    items = [{"label": label, "cur": v["cur"], "base": v["base"], "delta": v["cur"] - v["base"]} for label, v in segments.items()]
    gross = sum(abs(it["delta"]) for it in items)
    ordered = _sorted_by_abs(items, "delta")
    out_rows = []
    for it in ordered:
        out_rows.append([it["label"], _r(it["cur"]), _r(it["base"]), _r(it["delta"]), _r(_pct(_ratio(it["delta"], abs(it["base"])))) if it["base"] > EPS else None,
                         _r(_pct(_ratio(it["delta"], total_change))), _r(_pct(_ratio(abs(it["delta"]), gross))),
                         "yes" if _material(it["delta"], gross, min_share, abs_floor) else "no"])
    notes: list[str] = []
    checks: dict[str, Any] = {"parts_sum_to_total": abs(sum(it["delta"] for it in items) - total_change) <= 1e-6 * max(1.0, abs(total_cur))}
    _control_note(controls, "cur", total_cur, notes, checks, "current")
    if empty:
        notes.append(f"{empty} empty cell(s) were counted as zero.")
    up = [it for it in items if it["delta"] > 0]
    down = [it for it in items if it["delta"] < 0]
    outputs = {
        "total_current": total_cur, "total_base": total_base, "total_change": total_change, "total_change_pct": _pct(_ratio(total_change, abs(total_base))),
        "gross_increase": sum(it["delta"] for it in up), "gross_decrease": sum(it["delta"] for it in down),
        "segments_up": float(len(up)), "segments_down": float(len(down)),
        "top1_share_of_gross_pct": _pct(_top_share(items, "delta", 1, gross)), "top3_share_of_gross_pct": _pct(_top_share(items, "delta", 3, gross)),
        "top3_net_share_of_change_pct": _pct(_ratio(sum(it["delta"] for it in ordered[:3]), total_change)),
        "material_segments": float(sum(1 for r in out_rows if r[-1] == "yes")),
    }
    if ordered and gross > EPS:
        notes.append(f"Largest mover: {ordered[0]['label']} ({ordered[0]['delta']:+,.2f}).")
    return Result("contribution", ["segment", "current", "base", "change", "change_pct", "share_of_change_pct", "share_of_gross_pct", "material"], out_rows,
                  {k: _r(v) for k, v in outputs.items()}, notes, checks,
                  {"total_current": "value", "total_base": "value", "total_change": "value", "total_change_pct": "pct", "gross_increase": "value", "gross_decrease": "value",
                   "segments_up": "count", "segments_down": "count", "top1_share_of_gross_pct": "pct", "top3_share_of_gross_pct": "pct",
                   "top3_net_share_of_change_pct": "pct", "material_segments": "count"})


def rate_mix(rows: list[dict], key: str, cur_num: str, cur_den: str, base_num: str, base_den: str, unit: str = "pp",
             min_share: float = 0.05, abs_floor: float = 0.0) -> Result:
    """Change in a ratio R = sum(num) / sum(den) between two periods, per segment i with weight w = den share and rate r = num / den:
        rate effect  = w_base * (r_cur - r_base)         the segment's own rate moved
        mix effect   = (w_cur - w_base) * (r_cur - R_base)   weight moved towards or away from a segment whose rate is above or below the base average
    The two effects over all segments add up exactly to R_cur - R_base. unit "pp" shows rates in % and effects in percentage points."""
    _require(rows, key, cur_num, cur_den, base_num, base_den)
    segments, controls, empty = _segments(rows, key, {"cn": cur_num, "cd": cur_den, "bn": base_num, "bd": base_den})
    scale = 100.0 if unit == "pp" else 1.0
    dc = sum(v["cd"] for v in segments.values())
    db = sum(v["bd"] for v in segments.values())
    if dc <= EPS or db <= EPS:
        raise AnalyticsError("rate_mix needs a positive denominator total in both periods")
    nc = sum(v["cn"] for v in segments.values())
    nb = sum(v["bn"] for v in segments.values())
    rc_total, rb_total = nc / dc, nb / db
    items = []
    for label, v in segments.items():
        wb, wc = v["bd"] / db, v["cd"] / dc
        rb = v["bn"] / v["bd"] if v["bd"] > EPS else None
        rc = v["cn"] / v["cd"] if v["cd"] > EPS else None
        if rb is None and rc is None:
            continue
        rb = rc if rb is None else rb  # a new segment had no base weight, so it has no rate effect, only mix
        rc = rb if rc is None else rc  # a segment that stopped selling has no current weight
        rate = wb * (rc - rb)
        mix = (wc - wb) * (rc - rb_total)
        items.append({"label": label, "wb": wb, "wc": wc, "rb": rb, "rc": rc, "rate": rate, "mix": mix, "total": rate + mix})
    gross = sum(abs(it["total"]) for it in items)
    ordered = _sorted_by_abs(items, "total")
    pp = unit == "pp"
    cols = ["segment", "base_weight_pct", "current_weight_pct", "base_rate_pct" if pp else "base_rate", "current_rate_pct" if pp else "current_rate",
            "rate_effect_pp" if pp else "rate_effect", "mix_effect_pp" if pp else "mix_effect", "total_effect_pp" if pp else "total_effect", "share_of_gross_pct", "material"]
    out_rows = [[it["label"], _r(it["wb"] * 100), _r(it["wc"] * 100), _r(it["rb"] * scale), _r(it["rc"] * scale), _r(it["rate"] * scale), _r(it["mix"] * scale),
                 _r(it["total"] * scale), _r(_pct(_ratio(abs(it["total"]), gross))), "yes" if _material(it["total"], gross, min_share, abs_floor * (1 / scale)) else "no"]
                for it in ordered]
    change = rc_total - rb_total
    rate_total, mix_total = sum(it["rate"] for it in items), sum(it["mix"] for it in items)
    notes: list[str] = []
    checks: dict[str, Any] = {"rate_plus_mix_equals_change": abs(rate_total + mix_total - change) <= 1e-9 * max(1.0, abs(rb_total))}
    _control_note(controls, "cd", dc, notes, checks, "current_denominator")
    if empty:
        notes.append(f"{empty} empty cell(s) were counted as zero.")
    top_rate = max(items, key=lambda it: abs(it["rate"]), default=None)
    top_mix = max(items, key=lambda it: abs(it["mix"]), default=None)
    sfx = "pp" if pp else ""
    if top_rate and abs(top_rate["rate"]) > EPS:
        notes.append(f"Largest rate effect: {top_rate['label']} ({top_rate['rate'] * scale:+,.2f}{sfx and ' ' + sfx}).")
    if top_mix and abs(top_mix["mix"]) > EPS:
        notes.append(f"Largest mix effect: {top_mix['label']} ({top_mix['mix'] * scale:+,.2f}{sfx and ' ' + sfx}).")
    rate_gross, mix_gross = sum(abs(it["rate"]) for it in items), sum(abs(it["mix"]) for it in items)
    outputs = {
        "base_rate": rb_total * scale, "current_rate": rc_total * scale, "rate_change": change * scale,
        "rate_effect_total": rate_total * scale, "mix_effect_total": mix_total * scale,
        "rate_share_of_gross_pct": _pct(_ratio(rate_gross, rate_gross + mix_gross)), "mix_share_of_gross_pct": _pct(_ratio(mix_gross, rate_gross + mix_gross)),
        "top3_share_of_gross_pct": _pct(_top_share([{"label": it["label"], "g": abs(it["rate"]) + abs(it["mix"])} for it in items], "g", 3, rate_gross + mix_gross)),
        "material_segments": float(sum(1 for r in out_rows if r[-1] == "yes")),
    }
    u = "pp" if pp else "value"
    return Result("rate_mix", cols, out_rows, {k: _r(v) for k, v in outputs.items()}, notes, checks,
                  {"base_rate": "pct" if pp else "value", "current_rate": "pct" if pp else "value", "rate_change": u, "rate_effect_total": u, "mix_effect_total": u,
                   "rate_share_of_gross_pct": "pct", "mix_share_of_gross_pct": "pct", "top3_share_of_gross_pct": "pct", "material_segments": "count"})


def price_volume_mix(rows: list[dict], key: str, cur_qty: str, cur_value: str, base_qty: str, base_value: str, min_share: float = 0.05, abs_floor: float = 0.0) -> Result:
    """Change in a value (for example sales) between two periods, per segment, as volume + mix + price. With Q = total quantity, s = a segment's quantity share,
    P = its price (value / quantity):
        volume = (Q_cur - Q_base) * s_base * P_base      more or fewer units overall, at the base mix and prices
        mix    = (s_cur - s_base) * Q_cur * P_base       units moved between segments, at base prices
        price  = (P_cur - P_base) * q_cur                the segment's price moved
    Per segment the three add up to the change in that segment's value, so over all segments they add up exactly to the total change."""
    _require(rows, key, cur_qty, cur_value, base_qty, base_value)
    segments, controls, empty = _segments(rows, key, {"cq": cur_qty, "cv": cur_value, "bq": base_qty, "bv": base_value})
    qc = sum(v["cq"] for v in segments.values())
    ql = sum(v["bq"] for v in segments.values())
    if qc <= EPS and ql <= EPS:
        raise AnalyticsError("price_volume_mix needs a quantity in at least one of the two periods")
    vc = sum(v["cv"] for v in segments.values())
    vl = sum(v["bv"] for v in segments.values())
    items = []
    for label, v in segments.items():
        pl = v["bv"] / v["bq"] if v["bq"] > EPS else None
        pc = v["cv"] / v["cq"] if v["cq"] > EPS else None
        if pl is None and pc is None:
            continue
        pl = pc if pl is None else pl  # a new item has no base price: all of it is mix, none is price
        pc = pl if pc is None else pc
        sl = v["bq"] / ql if ql > EPS else None
        sc = v["cq"] / qc if qc > EPS else None
        sl = sc if sl is None else sl  # no base quantity at all: everything is volume
        sc = sl if sc is None else sc
        volume = (qc - ql) * sl * pl
        mix = (sc - sl) * qc * pl
        price = (pc - pl) * v["cq"]
        items.append({"label": label, "bq": v["bq"], "cq": v["cq"], "pl": pl, "pc": pc, "volume": volume, "mix": mix, "price": price, "total": volume + mix + price})
    gross = sum(abs(it["total"]) for it in items)
    ordered = _sorted_by_abs(items, "total")
    out_rows = [[it["label"], _r(it["bq"]), _r(it["cq"]), _r(it["pl"]), _r(it["pc"]), _r(it["volume"]), _r(it["mix"]), _r(it["price"]), _r(it["total"]),
                 _r(_pct(_ratio(abs(it["total"]), gross))), "yes" if _material(it["total"], gross, min_share, abs_floor) else "no"] for it in ordered]
    change = vc - vl
    vol_t, mix_t, price_t = (sum(it[k] for it in items) for k in ("volume", "mix", "price"))
    notes: list[str] = []
    checks: dict[str, Any] = {"volume_mix_price_equal_change": abs(vol_t + mix_t + price_t - change) <= 1e-6 * max(1.0, abs(vc))}
    _control_note(controls, "cv", vc, notes, checks, "current_value")
    if empty:
        notes.append(f"{empty} empty cell(s) were counted as zero.")
    if ql <= EPS:
        notes.append("No base-period quantity: the whole change is shown as volume.")
    if qc <= EPS:
        notes.append("No current-period quantity: the whole change is shown as volume.")
    gv, gm, gp = (sum(abs(it[k]) for it in items) for k in ("volume", "mix", "price"))
    avg_base, avg_cur = _ratio(vl, ql), _ratio(vc, qc)
    outputs = {
        "total_base_value": vl, "total_current_value": vc, "total_change": change, "total_change_pct": _pct(_ratio(change, abs(vl))),
        "volume_effect_total": vol_t, "mix_effect_total": mix_t, "price_effect_total": price_t,
        "volume_share_of_gross_pct": _pct(_ratio(gv, gv + gm + gp)), "mix_share_of_gross_pct": _pct(_ratio(gm, gv + gm + gp)), "price_share_of_gross_pct": _pct(_ratio(gp, gv + gm + gp)),
        "base_average_price": avg_base, "current_average_price": avg_cur,
        "average_price_change_pct": _pct(_ratio(avg_cur - avg_base, abs(avg_base))) if avg_base is not None and avg_cur is not None else None,
    }
    return Result("price_volume_mix", ["segment", "base_qty", "current_qty", "base_price", "current_price", "volume_effect", "mix_effect", "price_effect", "total_effect",
                                       "share_of_gross_pct", "material"], out_rows, {k: _r(v) for k, v in outputs.items()}, notes, checks,
                  {"total_base_value": "value", "total_current_value": "value", "total_change": "value", "total_change_pct": "pct", "volume_effect_total": "value",
                   "mix_effect_total": "value", "price_effect_total": "value", "volume_share_of_gross_pct": "pct", "mix_share_of_gross_pct": "pct",
                   "price_share_of_gross_pct": "pct", "base_average_price": "value", "current_average_price": "value", "average_price_change_pct": "pct"})


def target_gap(rows: list[dict], key: str, actual: str, target: str) -> Result:
    """Actual against target per segment: the gap, the achievement %, and how concentrated the shortfall is."""
    _require(rows, key, actual, target)
    segments, controls, empty = _segments(rows, key, {"a": actual, "t": target})
    items = [{"label": label, "a": v["a"], "t": v["t"], "gap": v["a"] - v["t"]} for label, v in segments.items()]
    total_a, total_t = sum(i["a"] for i in items), sum(i["t"] for i in items)
    shortfall = sum(i["gap"] for i in items if i["gap"] < 0)
    below = sorted((i for i in items if i["gap"] < 0), key=lambda i: i["gap"])
    out_rows = [[i["label"], _r(i["a"]), _r(i["t"]), _r(i["gap"]), _r(_pct(_ratio(i["a"], i["t"]))), _r(_pct(_ratio(i["gap"], shortfall))) if i["gap"] < 0 else None]
                for i in sorted(items, key=lambda i: (i["gap"], i["label"]))]
    notes: list[str] = []
    checks: dict[str, Any] = {}
    _control_note(controls, "a", total_a, notes, checks, "actual")
    if empty:
        notes.append(f"{empty} empty cell(s) were counted as zero.")
    if below:
        notes.append(f"Largest shortfall: {below[0]['label']} ({below[0]['gap']:+,.2f}).")
    outputs = {"total_actual": total_a, "total_target": total_t, "total_gap": total_a - total_t, "total_achievement_pct": _pct(_ratio(total_a, total_t)),
               "segments_below_target": float(len(below)), "segments_at_or_above_target": float(len(items) - len(below)), "shortfall_total": shortfall,
               "top3_shortfall_share_pct": _pct(_ratio(sum(i["gap"] for i in below[:3]), shortfall)) if below else None}
    return Result("target_gap", ["segment", "actual", "target", "gap", "achievement_pct", "share_of_shortfall_pct"], out_rows, {k: _r(v) for k, v in outputs.items()}, notes, checks,
                  {"total_actual": "value", "total_target": "value", "total_gap": "value", "total_achievement_pct": "pct", "segments_below_target": "count",
                   "segments_at_or_above_target": "count", "shortfall_total": "value", "top3_shortfall_share_pct": "pct"})


def concentration(rows: list[dict], key: str, value: str, top_n: int = 3) -> Result:
    """How much of a total sits in the largest segments."""
    _require(rows, key, value)
    segments, controls, empty = _segments(rows, key, {"v": value})
    items = sorted(({"label": label, "v": v["v"]} for label, v in segments.items()), key=lambda i: (-i["v"], i["label"]))
    total = sum(i["v"] for i in items)
    if abs(total) <= EPS:
        raise AnalyticsError("concentration needs a non-zero total")
    cum, out_rows, n80 = 0.0, [], None
    for rank, i in enumerate(items, 1):
        cum += i["v"]
        if n80 is None and cum / total >= 0.8 - EPS:
            n80 = rank
        out_rows.append([i["label"], _r(i["v"]), _r(i["v"] / total * 100), _r(cum / total * 100), rank])
    notes: list[str] = []
    checks: dict[str, Any] = {}
    _control_note(controls, "v", total, notes, checks, "total")
    n = max(1, int(top_n))
    outputs = {"total": total, "segment_count": float(len(items)), f"top{n}_share_pct": sum(i["v"] for i in items[:n]) / total * 100,
               "segments_for_80pct": float(n80 or len(items)), "hhi": sum((i["v"] / total * 100) ** 2 for i in items)}
    return Result("concentration", ["segment", "value", "share_pct", "cumulative_share_pct", "rank"], out_rows, {k: _r(v) for k, v in outputs.items()}, notes, checks,
                  {"total": "value", "segment_count": "count", f"top{n}_share_pct": "pct", "segments_for_80pct": "count", "hhi": "value"})


def trend(rows: list[dict], time: str, value: str, flat_pct: float = 1.0) -> Result:
    """A series over time: period-on-period change, overall change, least-squares slope per period, peak, trough and volatility."""
    _require(rows, time, value)
    t, v = norm(time), norm(value)
    points = [(r[t], _f(r.get(v))) for r in rows if _f(r.get(v)) is not None]
    if len(points) < 2:
        raise AnalyticsError("trend needs at least two periods with a value")
    try:
        points.sort(key=lambda p: float(p[0]))
    except (TypeError, ValueError):
        points.sort(key=lambda p: str(p[0]))
    values = [p[1] for p in points]
    n = len(values)
    out_rows, prev = [], None
    for label, val in points:
        out_rows.append([label, _r(val), None if prev is None else _r(val - prev), None if prev is None else _r(_pct(_ratio(val - prev, abs(prev))))])
        prev = val
    mean = sum(values) / n
    xs = list(range(n))
    xm = sum(xs) / n
    denom = sum((x - xm) ** 2 for x in xs)
    slope = sum((x - xm) * (y - mean) for x, y in zip(xs, values)) / denom
    total_change = values[-1] - values[0]
    total_pct = _pct(_ratio(total_change, abs(values[0])))
    peak_i, trough_i = max(range(n), key=lambda i: values[i]), min(range(n), key=lambda i: values[i])
    sd = math.sqrt(sum((y - mean) ** 2 for y in values) / n)
    direction = 0.0 if total_pct is None or abs(total_pct) < flat_pct else (1.0 if total_pct > 0 else -1.0)
    notes = [f"Peak: {points[peak_i][0]} ({values[peak_i]:,.2f}); trough: {points[trough_i][0]} ({values[trough_i]:,.2f})."]
    outputs = {"periods": float(n), "first_value": values[0], "last_value": values[-1], "total_change": total_change, "total_change_pct": total_pct,
               "slope_per_period": slope, "peak_value": values[peak_i], "trough_value": values[trough_i], "volatility_pct": _pct(_ratio(sd, abs(mean))),
               "latest_change_pct": out_rows[-1][3], "direction": direction}
    return Result("trend", ["period", "value", "change", "change_pct"], out_rows, {k: _r(x) for k, x in outputs.items()}, notes, {},
                  {"periods": "count", "first_value": "value", "last_value": "value", "total_change": "value", "total_change_pct": "pct", "slope_per_period": "value",
                   "peak_value": "value", "trough_value": "value", "volatility_pct": "pct", "latest_change_pct": "pct", "direction": "count"})


def reconcile(a: float, b: float, tolerance_pct: float = 0.5) -> Result:
    """Whether two figures that should agree do, within a tolerance in % of the second."""
    diff = a - b
    pct = _pct(_ratio(abs(diff), abs(b)))
    ok = (pct is not None and pct <= tolerance_pct) or abs(diff) <= EPS
    outputs = {"first": a, "second": b, "difference": diff, "difference_pct": pct, "within_tolerance": 1.0 if ok else 0.0}
    return Result("reconcile", ["first", "second", "difference", "difference_pct", "within_tolerance"], [[_r(a), _r(b), _r(diff), _r(pct), "yes" if ok else "no"]],
                  {k: _r(v) for k, v in outputs.items()}, [], {"within_tolerance": ok}, {"first": "value", "second": "value", "difference": "value", "difference_pct": "pct", "within_tolerance": "count"})


def materiality(change: float, base: float, min_rel_pct: float = 2.0, abs_floor: float = 0.0) -> Result:
    """Is a change big enough, against the base it applies to, to be called a driver? Both the relative and the absolute floor must be met."""
    rel = _pct(_ratio(change, abs(base)))
    ok = abs(change) > EPS and abs(change) >= abs_floor and rel is not None and abs(rel) >= min_rel_pct
    outputs = {"change": change, "base": base, "relative_change_pct": rel, "material": 1.0 if ok else 0.0}
    return Result("materiality", ["change", "base", "relative_change_pct", "material"], [[_r(change), _r(base), _r(rel), "yes" if ok else "no"]],
                  {k: _r(v) for k, v in outputs.items()}, [], {"material": ok}, {"change": "value", "base": "value", "relative_change_pct": "pct", "material": "count"})


# ---------------------------------------------------------------- registry

@dataclass
class OpSpec:
    name: str
    fn: Callable[..., Result]
    about: str
    required: list[str]
    optional: dict[str, Any]
    outputs: list[str]
    scalar: bool = False  # takes figures, not a result table (reconcile, materiality)


OPS: dict[str, OpSpec] = {s.name: s for s in [
    OpSpec("contribution", contribution, "What each segment (category, zone, store ...) added to the change in a total between two periods. Use for 'where did the change come from'.",
           ["key", "cur", "base"], {"min_share": 0.05, "abs_floor": 0.0},
           ["total_current", "total_base", "total_change", "total_change_pct", "gross_increase", "gross_decrease", "segments_up", "segments_down",
            "top1_share_of_gross_pct", "top3_share_of_gross_pct", "top3_net_share_of_change_pct", "material_segments"]),
    OpSpec("rate_mix", rate_mix, "Change in a RATIO (margin %, markdown %, average price) split, per segment, into a rate effect (the segment's own rate moved) and a mix effect "
           "(sales shifted towards or away from high-rate segments). Needs the numerator and the denominator of the ratio in both periods, for example profit and NMV for margin %. "
           "unit pp (default) shows effects in percentage points.",
           ["key", "cur_num", "cur_den", "base_num", "base_den"], {"unit": "pp", "min_share": 0.05, "abs_floor": 0.0},
           ["base_rate", "current_rate", "rate_change", "rate_effect_total", "mix_effect_total", "rate_share_of_gross_pct", "mix_share_of_gross_pct", "top3_share_of_gross_pct", "material_segments"]),
    OpSpec("price_volume_mix", price_volume_mix, "Change in a VALUE (NMV, sales) split, per segment, into volume (more units), mix (units moved between segments) and price (selling price moved). "
           "Needs quantity and value in both periods.",
           ["key", "cur_qty", "cur_value", "base_qty", "base_value"], {"min_share": 0.05, "abs_floor": 0.0},
           ["total_base_value", "total_current_value", "total_change", "total_change_pct", "volume_effect_total", "mix_effect_total", "price_effect_total",
            "volume_share_of_gross_pct", "mix_share_of_gross_pct", "price_share_of_gross_pct", "base_average_price", "current_average_price", "average_price_change_pct"]),
    OpSpec("target_gap", target_gap, "Actual against target (AOP or SOP) per segment: the gap, the achievement %, and how concentrated the shortfall is.",
           ["key", "actual", "target"], {},
           ["total_actual", "total_target", "total_gap", "total_achievement_pct", "segments_below_target", "segments_at_or_above_target", "shortfall_total", "top3_shortfall_share_pct"]),
    OpSpec("concentration", concentration, "How much of a total sits in the largest few segments (stores, SKUs, categories).",
           ["key", "value"], {"top_n": 3}, ["total", "segment_count", "top3_share_pct", "segments_for_80pct", "hhi"]),
    OpSpec("trend", trend, "A series over time (daily, weekly, monthly): change, slope, peak, trough, volatility and direction (1 up, -1 down, 0 flat).",
           ["time", "value"], {"flat_pct": 1.0},
           ["periods", "first_value", "last_value", "total_change", "total_change_pct", "slope_per_period", "peak_value", "trough_value", "volatility_pct", "latest_change_pct", "direction"]),
    OpSpec("reconcile", reconcile, "Check that two figures that should agree do (for example a total from two different queries). Params a and b are cell references such as R3:SUM:cy_nmv.",
           ["a", "b"], {"tolerance_pct": 0.5}, ["first", "second", "difference", "difference_pct", "within_tolerance"], scalar=True),
    OpSpec("materiality", materiality, "Is a change big enough, against the base it applies to, to be called a driver? Params change and base are cell references or numbers.",
           ["change", "base"], {"min_rel_pct": 2.0, "abs_floor": 0.0}, ["change", "base", "relative_change_pct", "material"], scalar=True),
]}


def validate(op: str, params: dict) -> dict:
    """Checks the parameters of a step before it runs, with a message precise enough for a model to correct itself. Returns the params with defaults filled."""
    spec = OPS.get(op)
    if spec is None:
        raise AnalyticsError(f"unknown op '{op}'. Available: {', '.join(OPS)}")
    params = dict(params or {})
    extra = sorted(set(params) - set(spec.required) - set(spec.optional))
    if extra:
        raise AnalyticsError(f"{op}: unknown parameter(s) {extra}. Required: {spec.required}. Optional: {sorted(spec.optional)}")
    missing = [p for p in spec.required if params.get(p) in (None, "")]
    if missing:
        raise AnalyticsError(f"{op}: missing parameter(s) {missing}. Required: {spec.required}")
    return {**spec.optional, **params}


def run(op: str, rows: list[dict] | None, params: dict) -> Result:
    """Runs one operation. Table operations take the rows of a result; scalar operations (reconcile, materiality) take their figures as params."""
    spec = OPS.get(op)
    if spec is None:
        raise AnalyticsError(f"unknown op '{op}'. Available: {', '.join(OPS)}")
    full = validate(op, params)
    return spec.fn(**full) if spec.scalar else spec.fn(rows or [], **full)


def catalog() -> str:
    """The operations as markdown for an agent prompt (agents/prompts/*.md use {{ANALYTICS_CATALOG}})."""
    lines = []
    for s in OPS.values():
        opt = ", ".join(f"{k} (default {v})" for k, v in s.optional.items())
        lines.append(f"- {s.name}: {s.about}\n  params: {', '.join(s.required)}" + (f"; optional: {opt}" if opt else "") + f"\n  outputs (usable in rules): {', '.join(s.outputs)}")
    return "\n".join(lines)
