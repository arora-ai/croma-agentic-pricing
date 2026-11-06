"""Runs an analytics operation (studio/analytics.py) on a result in the evidence ledger and records what it produced: the detail as a derived table (T id)
and every headline figure as a calculation (D id), so a report or an analysis can cite them and the number audit can trace them."""
from __future__ import annotations

import json
import re

from studio import analytics, ledger


def _rows(table: ledger.Table) -> list[dict]:
    return [{ledger.norm(c): v for c, v in zip(table.columns, r)} for r in table.rows]


def _figure(c: ledger.CaseLedger, value) -> float:
    """A number, a calculation id (D3) or a cell reference (R3:SUM:cy_nmv)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return ledger.resolve_cell(c, str(value))[0]


def _unit_of(c: ledger.CaseLedger, ref) -> str:
    """How a referenced figure is measured (pct, pp, ...), from the calculation it is or the name of the column it sits in; '' when unknown."""
    if not isinstance(ref, str):
        return ""
    ref = ref.strip()
    if re.fullmatch(r"D\d+", ref, re.I):
        d = c.calcs.get(ref.upper())
        return d.unit if d else ""
    column = ref.split(":")[-1].lower()
    return "pct" if column.endswith("_pct") else ("pp" if column.endswith("_pp") else "")


def run_op(case_id: str, op: str, source: str = "", params: dict | None = None, label: str = "", preview_rows: int = 12) -> dict:
    """Raises analytics.AnalyticsError (bad parameters, columns that are not there), KeyError (unknown result) or ValueError with a message that says what to fix."""
    spec = analytics.OPS.get(op)
    if spec is None:
        raise analytics.AnalyticsError(f"unknown op '{op}'. Available: {', '.join(analytics.OPS)}")
    params = dict(params or {})
    if op == "join":
        if not source:
            raise analytics.AnalyticsError("join needs a source result (an R or T id)")
        p = analytics.validate(op, params)
        j = ledger.join_tables(case_id, source, str(p["with"]), str(p["on"]), str(p["right_on"] or ""), p["take"], str(p["how"]), label)
        label = label.strip() or f"{source} with {p['with']}"
        return {"table": j["table"], "label": label, "op": op, "source": source, "columns": j["columns"], "row_count": len(j["rows"]), "rows": j["rows"][:preview_rows],
                "more_rows": max(0, len(j["rows"]) - preview_rows), "outputs": {}, "notes": j["notes"], "checks": {}}
    with ledger._lock:
        c = ledger.case(case_id)
        truncated = False
        if spec.scalar:
            hint = next((h for h in (_unit_of(c, params.get(n)) for n in ("a", "b", "change", "base")) if h), "")
            for name in ("a", "b", "change", "base"):
                if name in params:
                    params[name] = _figure(c, params[name])
            result = analytics.run(op, None, params)
            if hint:  # the compared figures are percentages (or points), not rupees: say so wherever they are shown
                for name in ("first", "second", "change", "base"):
                    if name in result.units:
                        result.units[name] = hint
                if "difference" in result.units:
                    result.units["difference"] = "pp" if hint == "pct" else hint
            origin = "figures"
        else:
            if not source:
                raise analytics.AnalyticsError(f"{op} needs a source result (an R or T id)")
            table = ledger.get_table(case_id, source)
            truncated = table.truncated
            result = analytics.run(op, _rows(table), params)
            origin = table.id
        label = label.strip() or f"{op.replace('_', ' ')} on {origin}"
        tid = c.next_id("T")
        c.tables[tid] = ledger.Table(id=tid, kind="derived", label=label, columns=result.columns, rows=result.rows, question=label,
                                     lineage=f"{op} on {origin} {json.dumps(params, default=str)}", truncated=truncated)
        outputs = {}
        for name, value in result.outputs.items():
            if value is None:
                continue
            did = c.next_id("D")
            c.calcs[did] = ledger.Calc(id=did, label=f"{label}: {name.replace('_', ' ')}", formula=f"{op}({origin}).{name}", value=float(value),
                                       inputs={"rows analysed": {"value": float(len(result.rows)), "source": origin}}, unit=result.units.get(name, ""))
            outputs[name] = {"id": did, "value": value, "unit": result.units.get(name, "")}
    return {"table": tid, "label": label, "op": op, "source": origin, "columns": result.columns, "row_count": len(result.rows),
            "rows": result.rows[:preview_rows], "more_rows": max(0, len(result.rows) - preview_rows), "outputs": outputs, "notes": result.notes, "checks": result.checks}


def output_values(run: dict) -> dict[str, float]:
    """name -> value of the headline figures of a run_op result (what a rule such as 'top3_share_of_gross_pct >= 50' is evaluated on)."""
    return {name: o["value"] for name, o in run.get("outputs", {}).items()}
