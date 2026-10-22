"""Stages the Analysis and Research harnesses share: asking the Data agent, mapping columns, running the analytics, building the evidence a worker reads,
and checking what a worker wrote against the evidence ledger."""
from __future__ import annotations

import asyncio
import copy
import re
from typing import Any

from harness import units
from harness.runtime import Ctx, HarnessError
from harness.schemas import FixedSteps, FollowUp, Materiality, Plan, Question, Step
from harness.workers import WorkerError
from studio import analytics, analyze, audit, ledger

RESULT_ID = re.compile(r"\bR(\d+)\b")
REF = re.compile(r"^(?P<q>[A-Za-z][A-Za-z0-9_-]*)(?P<rest>:.+)$")
STEP_REF = re.compile(r"^(?P<s>[A-Za-z][A-Za-z0-9_-]*)\.(?P<out>[a-z][a-z0-9_]*)$")
MAX_PACK_ROWS = 8


# ---------------------------------------------------------------- gather

def pick_result(case_id: str, answer: str, wanted: list[str], taken: set[str]) -> str | None:
    """The ledger result a Data agent answer is about: the result id it cites, preferring one that has the columns that were asked for."""
    c = ledger.case(case_id)
    cited: list[str] = []
    for m in RESULT_ID.finditer(answer):
        rid = f"R{m.group(1)}"
        if rid not in cited and rid in c.tables and c.tables[rid].kind == "genie":
            cited.append(rid)
    if not cited:
        return None
    want = {ledger.norm(w) for w in wanted}
    fits = [r for r in cited if want and want <= {ledger.norm(x) for x in c.tables[r].columns}]
    pool = fits or cited
    return ([r for r in pool if r not in taken] or pool)[-1]


async def gather(ctx: Ctx, questions: list[Question], stage: str) -> dict[str, dict]:
    """Asks every question through the Data agent (they run in parallel, capped by the bridge) and maps each answer to its ledger result."""
    results: dict[str, dict] = dict(ctx.run.data.get(stage) or {})
    todo = [q for q in questions if not (results.get(q.id) or {}).get("result_id")]
    taken = {r["result_id"] for r in results.values() if r.get("result_id")}

    async def one(q: Question) -> None:
        ask = q.ask if not q.columns else f"{q.ask.rstrip()} Use these column names: {', '.join(q.columns)}."
        answer = await ctx.ask_data(ask)
        rid = None if answer.startswith("ERROR") else pick_result(ctx.case_id, answer, q.columns, taken)
        if rid is None and not answer.startswith("ERROR"):
            answer = await ctx.ask_data(ask + " Put the result id (for example R7) in your reply.")
            rid = None if answer.startswith("ERROR") else pick_result(ctx.case_id, answer, q.columns, taken)
        if rid:
            taken.add(rid)
        results[q.id] = {"result_id": rid, "ask": ask, "answer": answer[:1800], **same_unit(ctx.case_id, rid, answer)}
        ctx.checkpoint(stage, results)

    await asyncio.gather(*(one(q) for q in todo))
    return {q.id: results[q.id] for q in questions}


def same_unit(case_id: str, rid: str | None, answer: str) -> dict:
    """When the ledger result is in a different unit from the table the Data agent showed (rupees against crore), the id of a rescaled copy to analyse. A unit check
    must never stop an analysis, so a result that cannot be compared is simply used as it is."""
    if not rid:
        return {}
    try:
        factors, scaled = units.align(case_id, rid, answer)
    except Exception:
        return {}
    return {"scales": factors, "scaled_id": scaled} if scaled else {}


def analysed_id(g: dict) -> str:
    """The ledger table an analysis reads for this question: the rescaled copy when there is one, else the result itself."""
    return g.get("scaled_id") or g.get("result_id") or ""


# ---------------------------------------------------------------- columns

def match_column(name: str, available: list[str]) -> str | None:
    """The result column a step means: an exact match ignoring case and punctuation, else the only column that contains or is contained in the name."""
    n = ledger.norm(name)
    by_norm = {ledger.norm(c): c for c in available}
    if n in by_norm:
        return by_norm[n]
    near = [c for k, c in by_norm.items() if n and k and (k.startswith(n) or n.startswith(k) or k.endswith(n) or n.endswith(k))]
    return near[0] if len(near) == 1 else None


def remap_steps(case_id: str, steps: list[Step], gathered: dict[str, dict]) -> tuple[list[Step], list[str], list[str]]:
    """Maps the column names each step asks for onto the columns its result really has.
    Returns (steps, column problems a worker could fix, steps that cannot run because their question returned no data)."""
    out: list[Step] = []
    problems: list[str] = []
    no_data: list[str] = []
    for step in steps:
        spec = analytics.OPS[step.op]
        if spec.scalar:
            out.append(step)
            continue
        rid = (gathered.get(step.source) or {}).get("result_id")
        if not rid:
            no_data.append(f"{step.id} ({step.label}): question {step.source} returned no data")
            out.append(step)
            continue
        columns = ledger.get_table(case_id, rid).columns
        params = dict(step.params)
        for p in spec.columns:
            want = params.get(p)
            if want is None:
                continue
            found = match_column(str(want), columns)
            if found is None:
                problems.append(f"step {step.id}: column '{want}' (param {p}) is not in {step.source}'s result. It has: {', '.join(columns)}")
            else:
                params[p] = found
        out.append(step.model_copy(update={"params": params}))
    return out, problems, no_data


def result_shapes(case_id: str, gathered: dict[str, dict], rows: int = 2) -> dict[str, dict]:
    shapes = {}
    for qid, g in gathered.items():
        if g.get("result_id"):
            t = ledger.get_table(case_id, g["result_id"])
            shapes[qid] = {"result": t.id, "columns": t.columns, "row_count": len(t.rows), "preview": [[_round(v, 4) for v in r] for r in t.rows[:rows]]}
    return shapes


async def fix_columns(ctx: Ctx, steps: list[Step], gathered: dict[str, dict]) -> dict:
    """Maps step columns onto what came back; when a column is really missing, the scoper gets one chance to re-point the step."""
    mapped, problems, no_data = remap_steps(ctx.case_id, steps, gathered)
    if problems:
        names = {re.match(r"step (\S+):", p).group(1) for p in problems if re.match(r"step (\S+):", p)}
        try:
            fixed = await ctx.worker("scoper", "fix_steps", {"problems": problems, "steps": [s.model_dump() for s in steps if s.id in names],
                                                             "results": result_shapes(ctx.case_id, gathered)}, FixedSteps)
            by_id = {s.id: s for s in fixed.steps}
            mapped, problems, no_data = remap_steps(ctx.case_id, [by_id.get(s.id, s) if s.id in names else s for s in steps], gathered)
        except WorkerError:
            pass  # the steps keep their columns; the ones that cannot run are reported as such
    return {"steps": [s.model_dump() for s in mapped], "problems": problems, "no_data": no_data}


async def evidence_round(ctx: Ctx, plan: Plan, questions: list[Question], steps: list[Step], suffix: str, required: bool) -> tuple[dict[str, dict], list[dict]]:
    """Gather, map columns, compute: one round of evidence, each its own saved stage. A required round that yields nothing stops the run with a reason."""
    gathered = await ctx.step(f"gather{suffix}", lambda: gather(ctx, questions, f"gather{suffix}"),
                              lambda g: f"{sum(1 for x in g.values() if x.get('result_id'))} of {len(g)} questions answered")
    if required and not any(g.get("result_id") for g in gathered.values()):
        raise HarnessError("the data agent could not answer any of the questions: " + (data_note(next(iter(gathered.values()), {}).get("answer", ""), 200) or "no answer"))
    fixed = await ctx.step(f"columns{suffix}", lambda: fix_columns(ctx, steps, gathered), lambda f: f"{len(f['problems'])} column problem(s) left")
    runnable = [Step.model_validate(s) for s in fixed["steps"]]

    async def run_compute() -> list[dict]:
        return compute(ctx.case_id, runnable, gathered, plan.materiality)

    results = await ctx.step(f"compute{suffix}", run_compute, lambda r: f"{sum(1 for x in r if x['ok'])} of {len(r)} computed")
    if required and not any(r["ok"] for r in results):
        raise HarnessError("no analysis could be computed from the data that came back: " + "; ".join(r["error"] for r in results[:3]))
    return gathered, results


# ---------------------------------------------------------------- compute

def _rewrite(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        m = REF.match(value)
        if m and m["q"] in mapping:
            return mapping[m["q"]] + m["rest"]
    return value


def resolve_refs(params: dict, gathered: dict[str, dict], step_outputs: dict[str, dict[str, str]] | None = None) -> dict:
    """Lets a plan point at things that do not have ids yet. 'q2:SUM:cur_nmv' becomes 'R7:SUM:cur_nmv' (a question's result); 's1.current_rate' becomes 'D14'
    (a figure an earlier step produced)."""
    mapping = {qid: analysed_id(g) for qid, g in gathered.items() if g.get("result_id")}
    out = {}
    for k, v in params.items():
        if isinstance(v, str):
            m = REF.match(v)
            if m and m["q"] in gathered and m["q"] not in mapping:
                raise analytics.AnalyticsError(f"question {m['q']} returned no data")
            s = STEP_REF.match(v)
            if s and step_outputs is not None:
                outputs = step_outputs.get(s["s"])
                if outputs is None:
                    raise analytics.AnalyticsError(f"step {s['s']} produced no figures (it failed, or it is not a step of this plan), so {v} cannot be used")
                if s["out"] not in outputs:
                    raise analytics.AnalyticsError(f"step {s['s']} has no figure '{s['out']}'. It has: {', '.join(outputs)}")
                v = outputs[s["out"]]
        out[k] = _rewrite(v, mapping)
    return out


def compute(case_id: str, steps: list[Step], gathered: dict[str, dict], materiality: Materiality) -> list[dict]:
    """Runs every step's analytics on the ledger: steps that read a result first, then the steps that compare figures (reconcile, materiality), which may point at the
    figures of the first kind ('s1.current_rate'). A step that cannot run is reported with the reason, so the analyst can say what is missing. Results keep the plan's order."""
    done: dict[str, dict] = {}
    outputs_by_step: dict[str, dict[str, str]] = {}
    for step in sorted(steps, key=lambda s: analytics.OPS[s.op].scalar):
        try:
            params = resolve_refs(step.params, gathered, outputs_by_step)
            spec = analytics.OPS[step.op]
            if not spec.scalar:
                for name, value in (("min_share", materiality.min_share), ("abs_floor", materiality.abs_floor)):
                    if name in spec.optional:
                        params.setdefault(name, value)
            source = ""
            if step.source:
                source = analysed_id(gathered.get(step.source) or {})
                if not source:
                    raise analytics.AnalyticsError(f"question {step.source} returned no data")
            run = analyze.run_op(case_id, step.op, source, params, step.label, preview_rows=MAX_PACK_ROWS + 4)
            done[step.id] = {"step": step.id, "ok": True, "unit": step.unit, **run}
            outputs_by_step[step.id] = {name: o["id"] for name, o in run["outputs"].items()}
        except Exception as exc:
            reason = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
            done[step.id] = {"step": step.id, "ok": False, "label": step.label, "op": step.op, "error": f"{type(exc).__name__}: {reason}"}
    return [done[s.id] for s in steps]


def failed_checks(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        if not r.get("ok"):
            continue
        for name, passed in (r.get("checks") or {}).items():
            if passed is not False:
                continue
            if r["op"] == "reconcile":
                o = {k: v["value"] for k, v in r["outputs"].items()}
                out.append(f"{r['label']}: {o.get('first', 0):,.2f} against {o.get('second', 0):,.2f} from the data space. The two do not agree, so the breakdown may rest on a different basis than the data space's own figure.")
            else:
                out.append(f"{r['label']}: the check '{name.replace('_', ' ')}' did not pass.")
    return out


def renumber(fu: FollowUp) -> FollowUp:
    """Follow-up ids are made new (f1.., x1..) so they cannot collide with the plan's."""
    qmap = {q.id: f"f{i}" for i, q in enumerate(fu.questions, 1)}
    smap = {s.id: f"x{i}" for i, s in enumerate(fu.steps, 1)}

    def rewrite(v: Any) -> Any:
        v = _rewrite(v, qmap)
        m = STEP_REF.match(v) if isinstance(v, str) else None
        return f"{smap[m['s']]}.{m['out']}" if m and m["s"] in smap else v

    questions = [q.model_copy(update={"id": qmap[q.id]}) for q in fu.questions]
    steps = [s.model_copy(update={"id": smap[s.id], "source": qmap.get(s.source, s.source), "params": {k: rewrite(v) for k, v in s.params.items()}}) for s in fu.steps]
    return FollowUp(why=fu.why, questions=questions, steps=steps)


# ---------------------------------------------------------------- what a worker reads

def _round(v: Any, digits: int = 2) -> Any:
    return round(v, digits) if isinstance(v, float) else v


def shown(value: float | None, hint: str, unit: str) -> str:
    """The figure as it should be written, so a worker quotes it instead of formatting it."""
    if value is None:
        return "n/a"
    if hint == "pp":
        return f"{value:+,.2f} pp"
    if hint == "pct":
        return f"{value:,.2f}%"
    if hint == "count":
        return f"{value:,.0f}"
    if "cr" in unit.lower():
        return f"{'-' if value < 0 else ''}\u20b9{abs(value):,.2f} Cr"
    return f"{value:,.2f}" + (f" {unit}" if unit and unit != "\u20b9" else "")


def data_note(answer: str, limit: int = 700) -> str:
    """The source and caveat lines of a Data agent answer, without its table (the rows are in the ledger)."""
    lines = [ln.strip() for ln in answer.splitlines() if ln.strip() and not ln.strip().startswith("|")]
    return " ".join(lines)[:limit]


WHERE = re.compile(r"\bWHERE\b(.*?)(?=\bGROUP BY\b|\bORDER BY\b|\bHAVING\b|\bQUALIFY\b|\bLIMIT\b|\)\s*,|\)\s*SELECT\b|$)", re.I)


def where_filters(sql: str, limit: int = 420) -> list[str]:
    """The WHERE conditions of the SQL the data space ran (best effort, one per query block), so a reviewer can compare the basis of different questions."""
    seen: list[str] = []
    for m in WHERE.finditer(re.sub(r"\s+", " ", sql or "")):
        text = m.group(1).replace("`", "").strip()
        while text.endswith(")") and text.count(")") > text.count("("):  # the bracket that closes the enclosing block, not one of the condition's own
            text = text[:-1].rstrip()
        if text and text not in seen:
            seen.append(text[:limit])
    return seen[:3]


def evidence_pack(case_id: str, plan: Plan, questions: list[Question], gathered: dict[str, dict], results: list[dict]) -> dict:
    """Everything a worker may rely on, compactly: what was asked and what came back, and for each computed step its figures (with ids and the way to write
    them) and the top rows of its table."""
    c = ledger.case(case_id)
    data = []
    for q in questions:
        g = gathered.get(q.id) or {}
        rid = g.get("result_id")
        entry = {"id": q.id, "purpose": q.purpose, "result": rid}
        if rid:
            entry["rows"] = len(c.tables[rid].rows)
            entry["data_space_note"] = data_note(g.get("answer", ""))
            entry["filters"] = where_filters(c.tables[rid].sql)
            if g.get("scaled_id"):
                entry["unit_note"] = units.describe(g.get("scales") or {}, g["scaled_id"])
        else:
            entry["problem"] = "no usable data came back: " + data_note(g.get("answer", ""), 240)
        data.append(entry)
    steps = []
    for r in results:
        if not r.get("ok"):
            steps.append({"step": r["step"], "label": r["label"], "op": r["op"], "problem": r["error"]})
            continue
        unit = r.get("unit") or plan.unit
        figures = {name: {"id": o["id"], "shown": shown(o["value"], o["unit"], unit)} for name, o in r["outputs"].items()}
        table = c.tables[r["table"]]
        steps.append({"step": r["step"], "label": r["label"], "op": r["op"], "table": r["table"], "from": r["source"], "figures": figures, "columns": table.columns,
                      "rows": [[_round(v) for v in row] for row in table.rows[:MAX_PACK_ROWS]], "more_rows": max(0, len(table.rows) - MAX_PACK_ROWS),
                      "notes": r.get("notes", []), "checks": r.get("checks", {})})
    return {"data": data, "steps": steps}


# ---------------------------------------------------------------- what a worker wrote

def known_ids(case_id: str) -> set[str]:
    c = ledger.case(case_id)
    return set(c.tables) | set(c.calcs)


def unknown_evidence(case_id: str, ids: list[str]) -> list[str]:
    known = known_ids(case_id)
    return [i for i in ids if i.strip().upper() not in known]


def untraced_numbers(case_id: str, texts: list[tuple[str, str]]) -> list[dict]:
    """Numbers in the narrative that are not in the ledger (results, tables or calculations), allowing for the rounding shown and unit changes."""
    problems = []
    for where, text in texts:
        if not text:
            continue
        _matched, missing = audit.trace(case_id, text)
        problems += [{"where": where, "number": m["token"], "context": m["context"]} for m in missing]
    return problems


def describe_problems(untraced: list[dict], bad_ids: dict[str, list[str]]) -> list[str]:
    out = [f"{p['where']}: the figure {p['number']} (\"...{p['context']}...\") is not in the evidence. Use a figure from the evidence exactly as shown, or remove the claim." for p in untraced]
    out += [f"{where}: evidence id(s) {', '.join(ids)} do not exist. Cite only ids from the evidence." for where, ids in bad_ids.items() if ids]
    return out


# Which parts of a worker's result are narrative: lists of objects (name -> primary text field, other text fields) and lists of plain sentences.
ITEM_LISTS = {"drivers": ("headline", ["why"]), "causes": ("claim", ["size"])}
STRING_LISTS = ("not_drivers", "basis", "caveats", "gaps", "next_questions", "ruled_out", "unknowns", "next_steps")


def narrative(data: dict) -> list[tuple[str, str]]:
    """(path, text) for every sentence a worker wrote, the path locating it in the result (for example 'drivers[1].why')."""
    out = [("headline", data.get("headline", ""))]
    for name, (primary, others) in ITEM_LISTS.items():
        for i, item in enumerate(data.get(name) or []):
            out += [(f"{name}[{i}].{f}", item.get(f, "")) for f in [primary, *others]]
    for name in STRING_LISTS:
        out += [(f"{name}[{i}]", text) for i, text in enumerate(data.get(name) or [])]
    return [(w, t) for w, t in out if t]


def evidence_refs(data: dict) -> dict[str, list[str]]:
    return {f"{name}[{i}]": list(item.get("evidence") or []) for name in ITEM_LISTS for i, item in enumerate(data.get(name) or [])}


def audit_result(case_id: str, data: dict) -> tuple[list[dict], dict[str, list[str]]]:
    """(figures that are not in the ledger, evidence ids that do not exist) in a worker's result."""
    untraced = untraced_numbers(case_id, narrative(data))
    bad_ids = {where: unknown_evidence(case_id, ids) for where, ids in evidence_refs(data).items()}
    return untraced, {w: ids for w, ids in bad_ids.items() if ids}


def strip_untraced(data: dict, untraced: list[dict], bad_ids: dict[str, list[str]], fallback: str) -> tuple[dict, list[str]]:
    """What is left after a worker has had its chance to fix a result: figures that cannot be traced are removed rather than shown."""
    data, removed = copy.deepcopy(data), []
    bad = {p["where"] for p in untraced}
    if "headline" in bad:
        data["headline"] = fallback
        removed.append("The headline quoted a figure that could not be traced to the data, so it was rebuilt from the computed figures.")
    for name, (primary, others) in ITEM_LISTS.items():
        keep = []
        for i, item in enumerate(data.get(name) or []):
            if f"{name}[{i}].{primary}" in bad:
                removed.append(f"One finding was left out because it quoted a figure that could not be traced to the data ({item.get(primary, '')[:60]}...).")
                continue
            for f in others:
                if f"{name}[{i}].{f}" in bad:
                    item[f] = ""
                    removed.append("An explanation was left out because it quoted a figure that could not be traced to the data.")
            ids = [e for e in item.get("evidence") or [] if e not in bad_ids.get(f"{name}[{i}]", [])]
            if not ids:
                removed.append(f"One finding was left out because it cited no evidence that exists ({item.get(primary, '')[:60]}...).")
                continue
            item["evidence"] = ids
            keep.append(item)
        if name in data:
            data[name] = keep
    for name in STRING_LISTS:
        if name in data:
            kept = [t for i, t in enumerate(data[name]) if f"{name}[{i}]" not in bad]
            if len(kept) < len(data[name]):
                removed.append(f"A line under '{name.replace('_', ' ')}' was left out because it quoted a figure that could not be traced to the data.")
            data[name] = kept
    return data, removed


# ---------------------------------------------------------------- rendering

VIEW = {
    "contribution": ["segment", "current", "base", "change", "change_pct", "share_of_gross_pct"],
    "rate_mix": ["segment", "base_rate_pct", "current_rate_pct", "rate_effect_pp", "mix_effect_pp", "total_effect_pp"],
    "price_volume_mix": ["segment", "volume_effect", "mix_effect", "price_effect", "total_effect"],
    "target_gap": ["segment", "actual", "target", "gap", "achievement_pct"],
    "concentration": ["segment", "value", "share_pct", "cumulative_share_pct"],
    "trend": ["period", "value", "change", "change_pct"],
}


def fmt_cell(column: str, v: Any) -> str:
    if v is None:
        return "\u2014"
    if isinstance(v, str):
        return v
    c = column.lower()
    if c.endswith("_pp"):
        text = f"{v:+,.2f}"
        return "0.00" if text in ("+0.00", "-0.00") else text
    if c.endswith("_pct"):
        return f"{v:,.1f}%"
    if c == "rank":
        return f"{v:.0f}"
    return f"{v:,.0f}" if float(v).is_integer() and abs(v) >= 1000 else f"{v:,.2f}"


def header(column: str, plan: Plan) -> str:
    fixed = {"segment": "Segment", "current": plan.current_label, "base": plan.base_label, "change": "Change", "change_pct": "Change %", "share_of_gross_pct": "Share of movement",
             "base_rate_pct": f"{plan.base_label} rate %", "current_rate_pct": f"{plan.current_label} rate %", "rate_effect_pp": "Rate effect (pp)", "mix_effect_pp": "Mix effect (pp)",
             "total_effect_pp": "Total (pp)", "achievement_pct": "Achievement %", "share_pct": "Share %", "cumulative_share_pct": "Cumulative %"}
    return fixed.get(column) or column.replace("_pct", " %").replace("_pp", " (pp)").replace("_", " ").capitalize()


VALUE_OPS = {"contribution", "price_volume_mix", "target_gap", "concentration", "trend"}


def table_title(result: dict, plan: Plan) -> str:
    """The step's label, with the unit its amounts are in when it has any (ratio tables carry their units in the column headers)."""
    unit = result.get("unit") or plan.unit
    return f"{result['label']} ({unit})" if result["op"] in VALUE_OPS and unit else result["label"]


def step_table(case_id: str, result: dict, plan: Plan, rows: int = 6) -> str:
    """A compact markdown table of a computed step, straight from the ledger (so every figure in it is the engine's)."""
    table = ledger.case(case_id).tables[result["table"]]
    wanted = [c for c in VIEW.get(result["op"], table.columns) if c in table.columns] or table.columns
    idx = [table.columns.index(c) for c in wanted]
    lines = ["| " + " | ".join(header(c, plan) for c in wanted) + " |", "|" + "|".join("---" for _ in wanted) + "|"]
    for row in table.rows[:rows]:
        lines.append("| " + " | ".join(fmt_cell(c, row[i]) for c, i in zip(wanted, idx)) + " |")
    if len(table.rows) > rows:
        lines.append(f"\n*Top {rows} of {len(table.rows)} (all rows: {table.id}).*")
    return "\n".join(lines)


def passed_checks(results: list[dict]) -> list[str]:
    """The comparisons with the data space's own figures that held, so the reader can see what the numbers were tied to."""
    return [r["label"] for r in results if r.get("ok") and r["op"] == "reconcile" and (r.get("checks") or {}).get("within_tolerance") is True]


def evidence_line(case_id: str, gathered: dict[str, dict], results: list[dict], run_id: str, kind: str) -> str:
    data = sorted({g["result_id"] for g in gathered.values() if g.get("result_id")}, key=lambda r: int(r[1:]))
    tables = sorted({g["scaled_id"] for g in gathered.values() if g.get("scaled_id")}, key=lambda t: int(t[1:])) + [r["table"] for r in results if r.get("ok") and not analytics.OPS[r["op"]].scalar]
    return f"*Evidence: data results {', '.join(data) or 'none'}; tables {', '.join(tables) or 'none'}. {kind} run {run_id}, case {case_id}.*"


def fallback_headline(plan: Plan, results: list[dict]) -> str:
    """Built by code from the engine's own figures, used when a worker's headline cannot be traced."""
    for r in results:
        if not r.get("ok"):
            continue
        o = {k: v["value"] for k, v in r["outputs"].items()}
        unit = r.get("unit") or plan.unit
        if r["op"] == "rate_mix" and {"base_rate", "current_rate", "rate_change"} <= o.keys():
            hint = r["outputs"]["rate_change"]["unit"]
            return (f"{plan.kpi} moved from {o['base_rate']:,.2f}{'%' if hint == 'pp' else ''} in {plan.base_label} to {o['current_rate']:,.2f}{'%' if hint == 'pp' else ''} "
                    f"in {plan.current_label} ({shown(o['rate_change'], hint, unit)}).")
        if r["op"] == "contribution" and {"total_base", "total_current", "total_change"} <= o.keys():
            return f"{plan.kpi} moved from {shown(o['total_base'], 'value', unit)} in {plan.base_label} to {shown(o['total_current'], 'value', unit)} in {plan.current_label} ({shown(o['total_change'], 'value', unit)})."
        if r["op"] == "price_volume_mix" and {"total_base_value", "total_current_value", "total_change"} <= o.keys():
            return (f"{plan.kpi} moved from {shown(o['total_base_value'], 'value', unit)} in {plan.base_label} to {shown(o['total_current_value'], 'value', unit)} "
                    f"in {plan.current_label} ({shown(o['total_change'], 'value', unit)}).")
    return plan.restated
