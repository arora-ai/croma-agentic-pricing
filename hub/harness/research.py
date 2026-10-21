"""The Research harness: why did something happen? The scoper turns the question into hypotheses, each with a rule over figures the analytics will compute;
code applies the rules, so what is supported and what is ruled out is not a matter of opinion. The analyst then ranks the causes and says what is still
unknown, and the critic tries to break the conclusion. See harness/pipeline.py for the stages."""
from __future__ import annotations

import ast

from harness import core
from harness.pipeline import Spec
from harness.runtime import Ctx, Run
from harness.schemas import Plan, ResearchConclusion, ResearchPlan, ResearchTurn, RULE_FUNCTIONS
from settings import RESEARCH_MAX_QUESTIONS
from studio import ledger

STATUS_WORDS = {"supported": "Supported", "not_supported": "Not supported", "refuted": "Ruled out", "inconclusive": "Could not be tested"}


def _names(rule: str) -> set[str]:
    return {n.id for n in ast.walk(ast.parse(rule.strip(), mode="eval")) if isinstance(n, ast.Name)} - RULE_FUNCTIONS


def evaluate(case_id: str, plan: ResearchPlan, results: list[dict]) -> dict:
    """Applies every hypothesis's rules to the figures of its step. Returns the verdicts and the movement they are meant to explain."""
    by_step = {r["step"]: r for r in results}
    verdicts = []
    for h in plan.hypotheses:
        r = by_step.get(h.step)
        base = {"id": h.id, "claim": h.claim, "expected": h.expected, "step": h.step, "supports_if": h.supports_if, "refutes_if": h.refutes_if}
        if not r or not r.get("ok"):
            verdicts.append({**base, "status": "inconclusive", "reason": (r or {}).get("error", "its step did not run")})
            continue
        values = {n: o["value"] for n, o in r["outputs"].items()}
        unit = r.get("unit") or plan.unit
        has_refute = bool(h.refutes_if.strip())
        try:
            supports = bool(ledger.safe_eval(h.supports_if, values))
            refutes = bool(ledger.safe_eval(h.refutes_if, values)) if has_refute else False
        except Exception as exc:  # an output the rule needs was empty
            verdicts.append({**base, "status": "inconclusive", "reason": f"its rule could not be evaluated: {exc}", "table": r["table"]})
            continue
        if supports and refutes:
            status = "inconclusive"
            base["reason"] = "the supporting and the refuting rule both hold"
        else:
            status = "refuted" if refutes else ("supported" if supports else "not_supported")
        used = _names(h.supports_if) | (_names(h.refutes_if) if has_refute else set())
        figures = {n: {"id": r["outputs"][n]["id"], "shown": core.shown(r["outputs"][n]["value"], r["outputs"][n]["unit"], unit)} for n in sorted(used) if n in r["outputs"]}
        verdicts.append({**base, "status": status, "figures": figures, "table": r["table"]})
    extras: dict = {"hypotheses": verdicts}
    sizing = by_step.get(plan.sizing_step)
    if sizing and sizing.get("ok"):
        unit = sizing.get("unit") or plan.unit
        extras["movement_to_explain"] = {"step": sizing["step"], "label": sizing["label"], "table": sizing["table"],
                                         "figures": {n: {"id": o["id"], "shown": core.shown(o["value"], o["unit"], unit)} for n, o in sizing["outputs"].items()}}
    return extras


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _label(name: str) -> str:
    """'mix_share_of_gross_pct' -> 'mix share of gross' (the unit shows in the figure)."""
    return " ".join(name.replace("_pct", "").replace("_total", "").replace("_", " ").split())


def render(case_id: str, plan: Plan, data: dict, results: list[dict], gathered: dict, extras: dict, critique: dict | None, verified: dict, run: Run) -> str:
    c = ResearchConclusion.model_validate(data)
    verdicts = extras.get("hypotheses", [])
    lines = [f"**{c.headline}**", ""]
    movement = extras.get("movement_to_explain")
    if movement:
        sized = next((r for r in results if r.get("ok") and r["step"] == movement["step"]), None)
        if sized:
            lines += [f"**{core.table_title(sized, plan)}**", core.step_table(case_id, sized, plan), ""]
    if verdicts:
        lines += ["**What the data says about each explanation**", "", "| Explanation | Verdict | Key figures | Evidence |", "|---|---|---|---|"]
        for v in verdicts:
            figs = "; ".join(f"{_label(n)} {f['shown']}" for n, f in v.get("figures", {}).items()) or v.get("reason", "")
            lines.append(f"| {_cell(v['claim'])} | {STATUS_WORDS[v['status']]} | {_cell(figs)} | {v.get('table', '')} |")
        lines.append("")
    if c.causes:
        lines.append("**Most likely causes, in order**")
        for i, cause in enumerate(c.causes, 1):
            size = f" {cause.size}" if cause.size else ""
            lines.append(f"{i}. **{cause.claim}**{size}{' *(inference)*' if cause.inference else ''} *({cause.confidence} confidence; {', '.join(cause.evidence)})*")
        lines.append("")
    else:
        lines += ["None of the explanations tested is supported strongly enough to call it the cause.", ""]
    problems = [*c.caveats, *core.failed_checks(results)]
    if verified["removed"]:
        problems.append("Some supporting detail was left out because its figures could not be traced to the data.")
    for title, items in (("Ruled out", c.ruled_out), ("Still unknown", c.unknowns), ("Checks passed", core.passed_checks(results)), ("Basis", c.basis or plan.basis), ("Caveats", problems),
                         ("Not checked", (critique or {}).get("missing_checks", [])), ("Suggested next steps", c.next_steps)):
        if items:
            lines += [f"**{title}**", *[f"- {i}" for i in items], ""]
    lines.append(core.evidence_line(case_id, gathered, results, run.id, "Research"))
    return "\n".join(lines).strip()


def save(ctx: Ctx, data: dict, extras: dict) -> int:
    c = ResearchConclusion.model_validate(data)
    ctx.deps.save_finding(ctx.case_id, "research", "Conclusion", c.headline)
    n = 1
    for i, cause in enumerate(c.causes, 1):
        ctx.deps.save_finding(ctx.case_id, "research", f"Cause {i}: {cause.claim[:70]}", f"{cause.claim} {cause.size} Evidence: {', '.join(cause.evidence)}.".replace("  ", " "))
        n += 1
    for v in extras.get("hypotheses", []):
        figs = "; ".join(f"{_label(k)} {f['shown']}" for k, f in v.get("figures", {}).items()) or v.get("reason", "")
        ctx.deps.save_finding(ctx.case_id, "research", f"{STATUS_WORDS[v['status']]}: {v['claim'][:70]}", f"{v['claim']} Test: {v['supports_if']}. Result: {figs}. Evidence: {v.get('table', '')}.")
        n += 1
    return n


SPEC = Spec(name="research", plan_cls=ResearchPlan, plan_task="research_plan", max_questions=RESEARCH_MAX_QUESTIONS, turn_cls=ResearchTurn, turn_task="research_conclusion",
            result_field="conclusion", critic_task="review_research", critic_on=True, evaluate=evaluate, render=render, save=save)
