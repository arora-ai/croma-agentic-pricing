"""The Analysis harness: what moved a business KPI, and which segments, rates or prices drove it. See harness/pipeline.py for the stages."""
from __future__ import annotations

from harness import core
from harness.pipeline import Spec
from harness.runtime import Ctx, Run
from harness.schemas import AnalysisFindings, AnalysisPlan, AnalystTurn, Plan
from settings import ANALYSIS_CRITIC, ANALYSIS_MAX_QUESTIONS


def section(title: str, items: list[str]) -> list[str]:
    return [f"**{title}**", *[f"- {i}" for i in items], ""] if items else []


def render(case_id: str, plan: Plan, data: dict, results: list[dict], gathered: dict, extras: dict, critique: dict | None, verified: dict, run: Run) -> str:
    f = AnalysisFindings.model_validate(data)
    lines = [f"**{f.headline}**", ""]
    cited = {e.strip().upper() for d in f.drivers for e in d.evidence}
    ok = [r for r in results if r.get("ok") and r["op"] not in ("reconcile", "materiality", "join")]
    shown = [r for r in ok if r["table"] in cited or any(o["id"] in cited for o in r["outputs"].values())][:2] or ok[:1]
    for r in shown:
        lines += [f"**{core.table_title(r, plan)}**", core.step_table(case_id, r, plan), ""]
    if f.drivers:
        lines.append("**What drove it**")
        for i, d in enumerate(f.drivers, 1):
            why = f" {d.why}" + (" *(inference)*" if d.inference else "") if d.why else ""
            lines.append(f"{i}. **{d.headline}**{why} *({d.confidence} confidence; {', '.join(d.evidence)})*")
        lines.append("")
    else:
        lines += ["No single segment or rate stands out as a material driver in this data.", ""]
    lines += section("Checked and not a driver", f.not_drivers)
    lines += section("Checks passed", core.passed_checks(results))
    lines += section("Basis", f.basis or plan.basis)
    problems = [*f.caveats, *core.failed_checks(results)]
    if verified["removed"]:
        problems.append("Some supporting detail was left out because its figures could not be traced to the data.")
    lines += section("Caveats", problems)
    lines += section("Could not be determined", f.gaps)
    if critique:
        lines += section("Not checked", critique.get("missing_checks", []))
    lines += section("Worth asking next", f.next_questions)
    lines.append(core.evidence_line(case_id, gathered, results, run.id, "Analysis"))
    return "\n".join(lines).strip()


def save(ctx: Ctx, data: dict, extras: dict) -> int:
    f = AnalysisFindings.model_validate(data)
    ctx.deps.save_finding(ctx.case_id, "analysis", "Headline", f"{f.headline} {' '.join(f.basis)}".strip())
    for i, d in enumerate(f.drivers, 1):
        ctx.deps.save_finding(ctx.case_id, "analysis", f"Driver {i}: {d.headline[:70]}", f"{d.headline} {d.why} Evidence: {', '.join(d.evidence)}.".replace("  ", " "))
    return 1 + len(f.drivers)


SPEC = Spec(name="analysis", plan_cls=AnalysisPlan, plan_task="analysis_plan", max_questions=ANALYSIS_MAX_QUESTIONS, turn_cls=AnalystTurn, turn_task="analysis_findings",
            result_field="findings", critic_task="review_analysis", critic_on=ANALYSIS_CRITIC, evaluate=None, render=render, save=save)
