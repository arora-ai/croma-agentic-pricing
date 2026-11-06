"""Evidence ledger for the report studio.

Every Genie result is stored under an id (R1, R2, ...) per case, with its question, SQL and rows. Derived tables are T-ids and
single derived numbers are D-ids; both keep their formula and inputs. A report never contains a number that is not in the ledger, so a
reader (or the audit) can trace any figure back to the query that produced it.
"""
from __future__ import annotations

import ast
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

_lock = threading.RLock()


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


@dataclass
class Table:
    id: str
    kind: str  # genie | sharepoint | document | derived
    label: str
    columns: list[str]
    rows: list[list]
    question: str = ""
    description: str = ""
    sql: str = ""
    truncated: bool = False
    lineage: str = ""
    created_at: float = field(default_factory=time.time)
    source: str = "databricks"  # which connected source produced it: databricks | sharepoint (derived tables inherit nothing: see lineage)
    origin: str = ""  # where it came from, in words: the file, its sheet and when it was last modified (SharePoint results)

    def col_index(self, name: str) -> int:
        target = norm(name)
        for i, c in enumerate(self.columns):
            if norm(c) == target:
                return i
        raise KeyError(f"{self.id} has no column '{name}'. Columns: {', '.join(self.columns)}")

    def brief(self, preview_rows: int = 3) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label or self.question[:90], "question": self.question[:220], "columns": self.columns,
                "row_count": len(self.rows), "truncated": self.truncated,
                "preview": [[round(v, 4) if isinstance(v, float) else v for v in r] for r in self.rows[:preview_rows]],
                **({"lineage": self.lineage[:200]} if self.lineage else {}),
                **({"source": self.source, "origin": self.origin} if self.kind in ("sharepoint", "document") else {})}


@dataclass
class Calc:
    id: str
    label: str
    formula: str
    value: float
    inputs: dict[str, dict]
    unit: str = ""
    note: str = ""


class CaseLedger:
    def __init__(self, case_id: str):
        self.case_id = case_id
        self.tables: dict[str, Table] = {}
        self.calcs: dict[str, Calc] = {}
        self._n = {"R": 0, "T": 0, "D": 0}

    def next_id(self, prefix: str) -> str:
        self._n[prefix] += 1
        return f"{prefix}{self._n[prefix]}"


_cases: dict[str, CaseLedger] = {}


def case(case_id: str) -> CaseLedger:
    with _lock:
        if case_id not in _cases:
            _cases[case_id] = CaseLedger(case_id)
        return _cases[case_id]


def known(case_id: str) -> bool:
    return case_id in _cases


def register_genie(case_id: str, payload: dict) -> str | None:
    """Store a successful Genie answer and return its R-id. Errors and empty results are not stored."""
    if payload.get("error") or not payload.get("columns"):
        return None
    with _lock:
        c = case(case_id or "adhoc")
        rid = c.next_id("R")
        c.tables[rid] = Table(id=rid, kind="genie", label="", columns=list(payload["columns"]), rows=[list(r) for r in payload.get("rows", [])],
                              question=payload.get("question", ""), description=payload.get("description", ""), sql=payload.get("sql", "") or "",
                              truncated=bool(payload.get("truncated_at_max_rows")))
        return rid


def register_file(case_id: str, payload: dict) -> str | None:
    """Store what was read from a SharePoint file and return its R-id (the same id space as Databricks results). payload["kind"] is 'sharepoint' for a table
    (columns and rows) or 'document' for text (columns section and text, one row per passage). Errors and empty results are not stored."""
    if payload.get("error") or not payload.get("columns") or not payload.get("rows"):
        return None
    with _lock:
        c = case(case_id or "adhoc")
        rid = c.next_id("R")
        c.tables[rid] = Table(id=rid, kind=payload.get("kind", "sharepoint"), label=payload.get("label", ""), columns=list(payload["columns"]), rows=[list(r) for r in payload["rows"]],
                              question=payload.get("question", ""), description=payload.get("description", ""), truncated=bool(payload.get("truncated_at_max_rows")),
                              source="sharepoint", origin=payload.get("origin", ""))
        return rid


def get_table(case_id: str, table_id: str) -> Table:
    c = case(case_id)
    tid = table_id.strip().upper()
    if tid not in c.tables:
        raise KeyError(f"Unknown result '{table_id}' on case {case_id}. Known: {', '.join(c.tables) or 'none yet'}")
    return c.tables[tid]


def _key(value: Any) -> str:
    """A join key: text compared without case, spacing or punctuation differences, a whole number written as 5 or 5.0 the same way, and a date the same whether or
    not it carries a time (2026-09-07 and 2026-09-07 00:00:00)."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ].*)?$", text)
    return m.group(1) if m else norm(text)


def _find_column(table: Table, name: str) -> int:
    """The column a join means: an exact match ignoring case and punctuation, else the only column that starts or ends with the name (or the name with it)."""
    n = norm(name)
    by_norm = {norm(c): i for i, c in enumerate(table.columns)}
    if n in by_norm:
        return by_norm[n]
    near = [i for k, i in by_norm.items() if n and k and (k.startswith(n) or n.startswith(k) or k.endswith(n) or n.endswith(k))]
    if len(near) == 1:
        return near[0]
    raise KeyError(f"{table.id} has no column '{name}'. Columns: {', '.join(table.columns)}")


def join_tables(case_id: str, left: str, right: str, on: str, right_on: str = "", take: list[str] | None = None, how: str = "left", label: str = "") -> dict:
    """Joins two results (typically one from each source) on a shared key and stores the outcome as a derived table (T id). The left result keeps its rows;
    'take' names the right result's columns to add (default: all except the key). The right result must have one row per key, because a join that silently
    picked one of several rows would put figures in the evidence that nobody asked for. Returns the table id, its columns and rows, and what did not match."""
    if how not in ("left", "inner"):
        raise ValueError("how must be 'left' (keep every row of the left result) or 'inner' (keep only the rows with a match)")
    with _lock:
        c = case(case_id)
        lt, rt = get_table(case_id, left), get_table(case_id, right)
        li, ri = _find_column(lt, on), _find_column(rt, right_on or on)
        wanted = [take] if isinstance(take, str) else list(take or [])
        take_idx = [_find_column(rt, t) for t in wanted] if wanted else [i for i in range(len(rt.columns)) if i != ri]
        index: dict[str, list] = {}
        for row in rt.rows:
            index.setdefault(_key(row[ri]), []).append(row)
        repeated = sorted(k for k, v in index.items() if len(v) > 1)
        if repeated:
            raise ValueError(f"{rt.id} has several rows for {len(repeated)} value(s) of '{rt.columns[ri]}' (for example {repeated[0]}), so it cannot be joined to {lt.id} on it. "
                             f"Ask for one row per {rt.columns[ri]}.")
        taken_names = {norm(x) for x in lt.columns}
        names = []
        for i in take_idx:
            name = rt.columns[i]
            while norm(name) in taken_names:
                name += "_2"
            taken_names.add(norm(name))
            names.append(name)
        rows, unmatched_left, used = [], [], set()
        for row in lt.rows:
            k = _key(row[li])
            hit = index.get(k)
            if hit is None:
                unmatched_left.append(str(row[li]))
                if how == "inner":
                    continue
                rows.append([*row, *([None] * len(take_idx))])
            else:
                used.add(k)
                rows.append([*row, *[hit[0][i] for i in take_idx]])
        unmatched_right = [str(r[ri]) for k, v in index.items() if k not in used for r in v]
        if not used:
            raise ValueError(f"no row of {lt.id} matches a row of {rt.id} on '{lt.columns[li]}' = '{rt.columns[ri]}'. {lt.id} has e.g. {lt.rows[0][li]!r}; {rt.id} has e.g. {rt.rows[0][ri]!r}")
        tid = c.next_id("T")
        text = label.strip() or f"{lt.id} with {rt.id} on {lt.columns[li]}"
        notes = []
        if unmatched_left:
            notes.append(f"{len(unmatched_left)} of {len(lt.rows)} row(s) of {lt.id} had no match in {rt.id} ({', '.join(unmatched_left[:4])}{'...' if len(unmatched_left) > 4 else ''}).")
        if unmatched_right:
            notes.append(f"{len(unmatched_right)} row(s) of {rt.id} matched nothing in {lt.id} ({', '.join(unmatched_right[:4])}{'...' if len(unmatched_right) > 4 else ''}).")
        c.tables[tid] = Table(id=tid, kind="derived", label=text, columns=[*lt.columns, *names], rows=rows, question=text,
                              lineage=f"join of {lt.id} ({lt.source}) and {rt.id} ({rt.source}) on {lt.columns[li]}; took {', '.join(rt.columns[i] for i in take_idx)}",
                              truncated=lt.truncated or rt.truncated)
        return {"table": tid, "columns": c.tables[tid].columns, "rows": rows, "notes": notes, "matched": len(used),
                "unmatched_left": len(unmatched_left), "unmatched_right": len(unmatched_right)}


# ---------------------------------------------------------------- cell references and safe arithmetic

AGGS = {"SUM": sum, "MAX": max, "MIN": min, "COUNT": len, "AVG": lambda v: sum(v) / len(v)}


def to_number(v: Any) -> float:
    if isinstance(v, bool) or v is None:
        raise ValueError("value is empty or not a number")
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace(",", ""))


def resolve_cell(c: CaseLedger, ref: str) -> tuple[float, str]:
    """'R3:Stores:cy_nmv' (row by label), 'R3:#0:cy_nmv' (row by position), 'R3:SUM:cy_nmv' (aggregate) or 'D4'."""
    ref = ref.strip()
    if re.fullmatch(r"D\d+", ref, re.I):
        d = c.calcs.get(ref.upper())
        if not d:
            raise KeyError(f"Unknown calculation {ref}")
        return d.value, f"{d.id} ({d.label})"
    parts = ref.split(":")
    if len(parts) != 3:
        raise ValueError(f"Bad reference '{ref}'. Use RESULT:ROW:COLUMN, for example R3:Stores:cy_nmv, R3:#0:cy_nmv or R3:SUM:cy_nmv")
    table = get_table(c.case_id, parts[0])
    col = table.col_index(parts[2])
    key = parts[1].strip()
    if key.upper() in AGGS:
        values = []
        for r in table.rows:
            try:
                values.append(to_number(r[col]))
            except (ValueError, TypeError):
                continue
        if not values:
            raise ValueError(f"{ref}: no numeric values in column '{parts[2]}'")
        return float(AGGS[key.upper()](values)), f"{table.id}.{table.columns[col]} {key.upper()}"
    if key.startswith("#"):
        idx = int(key[1:])
        if idx >= len(table.rows):
            raise IndexError(f"{ref}: {table.id} has only {len(table.rows)} rows")
        return to_number(table.rows[idx][col]), f"{table.id}.{table.columns[col]} row {idx}"
    matches = [r for r in table.rows if any(isinstance(v, str) and v.strip().lower() == key.lower() for v in r)]
    if not matches:
        raise KeyError(f"{ref}: no row labelled '{key}' in {table.id}")
    if len(matches) > 1:
        raise KeyError(f"{ref}: {len(matches)} rows match '{key}' in {table.id}; use a row position like {table.id}:#0:{parts[2]}")
    return to_number(matches[0][col]), f"{table.id}.{table.columns[col]} where {key}"


_FUNCS = {"abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt, "sum": lambda *a: sum(a), "log": math.log}
_BIN = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
        ast.Pow: lambda a, b: a ** b, ast.Mod: lambda a, b: a % b}
_CMP = {ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b, ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def safe_eval(formula: str, variables: dict[str, float]) -> float:
    tree = ast.parse(formula.strip(), mode="eval")

    def go(node):
        if isinstance(node, ast.Expression):
            return go(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise NameError(f"'{node.id}' is not defined. Defined: {', '.join(variables) or 'none'}")
            return variables[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](go(node.left), go(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = go(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
            return 1.0 if _CMP[type(node.ops[0])](go(node.left), go(node.comparators[0])) else 0.0
        if isinstance(node, ast.BoolOp):
            vals = [go(v) for v in node.values]
            return 1.0 if (all(vals) if isinstance(node.op, ast.And) else any(vals)) else 0.0
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return float(_FUNCS[node.func.id](*[go(a) for a in node.args]))
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    return float(go(tree))


def calc(case_id: str, items: list[dict]) -> list[dict]:
    """items: [{label, formula, inputs: {name: 'R3:Stores:cy_nmv' | 'D2' | number}, unit?, note?}]. Number inputs are recorded as assumptions."""
    out = []
    with _lock:
        c = case(case_id)
        for item in items:
            label, formula = str(item.get("label", "")).strip(), str(item.get("formula", "")).strip()
            try:
                variables, provenance = {}, {}
                for name, src in (item.get("inputs") or {}).items():
                    if isinstance(src, (int, float)) and not isinstance(src, bool):
                        variables[name] = float(src)
                        provenance[name] = {"value": float(src), "source": "assumption"}
                    else:
                        value, where = resolve_cell(c, str(src))
                        variables[name] = value
                        provenance[name] = {"value": value, "source": where}
                value = safe_eval(formula, variables)
                if math.isnan(value) or math.isinf(value):
                    raise ValueError("result is not a finite number")
            except ZeroDivisionError:
                out.append({"label": label, "error": "division by zero (an input is 0). The comparison base may be empty; say 'new' or 'n/a' rather than a percentage."})
                continue
            except Exception as exc:
                out.append({"label": label, "error": f"{type(exc).__name__}: {exc}"})
                continue
            did = c.next_id("D")
            c.calcs[did] = Calc(id=did, label=label, formula=formula, value=value, inputs=provenance, unit=str(item.get("unit", "")), note=str(item.get("note", "")))
            out.append({"id": did, "label": label, "value": round(value, 6), "formula": formula})
    return out


def derive_table(case_id: str, label: str, source: str, add_columns: list[dict] | None = None, keep: list[str] | None = None, rename: dict | None = None,
                 sort_by: str = "", descending: bool = True, limit: int = 0, join_with: str = "", join_on: list[str] | None = None,
                 where: str = "") -> dict:
    """Row-wise derived table. add_columns: [{name, formula}] with formulas over column names (lower case, underscores).
    join_with/join_on does a left join of a second result on the key columns first. where: a row formula that must be true, e.g. 'cy_nmv > 0'."""
    with _lock:
        c = case(case_id)
        base = get_table(case_id, source)
        columns, rows = list(base.columns), [list(r) for r in base.rows]
        lineage = [f"from {base.id}"]
        if join_with:
            other = get_table(case_id, join_with)
            keys = join_on or []
            if not keys:
                raise ValueError("join_with needs join_on (the key column names shared by both results)")
            li, ri = [base.col_index(k) for k in keys], [other.col_index(k) for k in keys]
            index = {tuple(str(r[i]).strip().lower() for i in ri): r for r in other.rows}
            extra_idx = [i for i in range(len(other.columns)) if i not in ri]
            existing = {norm(x) for x in columns}
            extra_names = [other.columns[i] if norm(other.columns[i]) not in existing else f"{other.columns[i]}_{other.id}" for i in extra_idx]
            joined = []
            for r in rows:
                m = index.get(tuple(str(r[i]).strip().lower() for i in li))
                joined.append(r + ([m[i] for i in extra_idx] if m else [None] * len(extra_idx)))
            columns, rows = columns + extra_names, joined
            lineage.append(f"joined {other.id} on {', '.join(keys)}")

        def row_vars(row):
            out = {}
            for i, name in enumerate(columns):
                try:
                    out[norm(name)] = to_number(row[i])
                except (ValueError, TypeError):
                    continue
            return out

        for spec in add_columns or []:
            name, formula = spec["name"], spec["formula"]
            for r in rows:
                try:
                    r.append(round(safe_eval(formula, row_vars(r)), 6))
                except Exception:
                    r.append(None)
            columns.append(name)
            lineage.append(f"{name} = {formula}")
        if where:
            kept = []
            for r in rows:
                try:
                    if safe_eval(where, row_vars(r)):
                        kept.append(r)
                except Exception:
                    continue
            rows = kept
            lineage.append(f"where {where}")
        if sort_by:
            si = next(i for i, x in enumerate(columns) if norm(x) == norm(sort_by))
            numeric = [r for r in rows if isinstance(r[si], (int, float))]
            other_rows = [r for r in rows if not isinstance(r[si], (int, float))]
            numeric.sort(key=lambda r: r[si], reverse=descending)
            rows = numeric + other_rows
        if keep:
            idx = [next(i for i, x in enumerate(columns) if norm(x) == norm(k)) for k in keep]
            columns, rows = [columns[i] for i in idx], [[r[i] for i in idx] for r in rows]
        if rename:
            columns = [rename.get(x, rename.get(norm(x), x)) for x in columns]
        if limit:
            rows = rows[:limit]
        tid = c.next_id("T")
        c.tables[tid] = Table(id=tid, kind="derived", label=label, columns=columns, rows=rows, question=label, lineage="; ".join(lineage), truncated=base.truncated)
        return c.tables[tid].brief()


def rescale_table(case_id: str, source: str, factors: dict[str, float], label: str = "") -> str:
    """A copy of a result with some columns divided by a factor (rupees to crore: 10,000,000; a fraction to a percentage: 0.01), kept as a derived table. The original
    rows, and the SQL that produced them, stay the evidence; the copy is what an analysis computes on when the answer shown to the user used another unit.
    Returns the new table's id."""
    with _lock:
        c = case(case_id)
        base = get_table(case_id, source)
        divide = {base.col_index(name): float(f) for name, f in factors.items()}
        rows = []
        for r in base.rows:
            row = list(r)
            for i, f in divide.items():
                try:
                    row[i] = to_number(row[i]) / f
                except (ValueError, TypeError):
                    pass  # a label or an empty cell stays as it is
            rows.append(row)
        how = ", ".join(f"{base.columns[i]} divided by {f:,.0f}" if f >= 1 else f"{base.columns[i]} divided by {f:g}" for i, f in divide.items())
        tid = c.next_id("T")
        c.tables[tid] = Table(id=tid, kind="derived", label=label or f"{base.id} in the unit the answer shows", columns=list(base.columns), rows=rows, question=base.question,
                              lineage=f"from {base.id}: {how}", truncated=base.truncated)
        return tid


def dump(case_id: str) -> dict:
    """The whole ledger of a case as plain data (for export, so a case survives a redeploy and can be kept as a test fixture)."""
    c = case(case_id)
    return {"tables": [t.__dict__ for t in c.tables.values()], "calcs": [d.__dict__ for d in c.calcs.values()], "counters": dict(c._n)}


def load(case_id: str, data: dict) -> None:
    with _lock:
        c = CaseLedger(case_id)
        for t in data.get("tables", []):
            c.tables[t["id"]] = Table(**t)
        for d in data.get("calcs", []):
            c.calcs[d["id"]] = Calc(**d)
        c._n.update(data.get("counters", {}))
        _cases[case_id] = c


def numeric_summary(table: Table) -> dict:
    sums = {}
    for i, name in enumerate(table.columns):
        vals = []
        for r in table.rows:
            try:
                vals.append(to_number(r[i]))
            except (ValueError, TypeError):
                vals = []
                break
        if vals and len(vals) == len(table.rows):
            sums[name] = {"sum": round(sum(vals), 4), "min": round(min(vals), 4), "max": round(max(vals), 4)}
    return sums


NUMBER_IN_TEXT = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")


def text_numbers(text: str) -> list[float]:
    """The numbers written in a passage of text (a discount of 12%, a target of 1,250 crore), so a figure quoted from a document can be traced to it."""
    out = []
    for m in NUMBER_IN_TEXT.finditer(text):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def candidate_numbers(case_id: str) -> list[float]:
    """Every number a report may legitimately quote: result cells, column totals, numbers written in document passages, and derived values."""
    c = case(case_id)
    out: list[float] = []
    for t in c.tables.values():
        if t.kind == "document":
            for r in t.rows:
                out.extend(n for cell in r if isinstance(cell, str) for n in text_numbers(cell))
            continue
        for i in range(len(t.columns)):
            col = []
            for r in t.rows:
                try:
                    col.append(to_number(r[i]))
                except (ValueError, TypeError):
                    col = None
                    break
            if col:
                out.extend(col)
                out.append(sum(col))
    out.extend(d.value for d in c.calcs.values())
    return out
