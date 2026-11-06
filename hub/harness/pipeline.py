"""One pipeline for the investigative harnesses (Analysis and Research). The stages, their order, the retries, the checks and the stopping rules are code;
language-model workers do the reasoning inside them.

    scope      the scoper turns the brief into a plan: data questions, analytics steps and (research) hypotheses with rules      [worker]
    evidence   ask each source's agent (Databricks: the Data agent; SharePoint: the SharePoint agent) in parallel, join, map the columns, run the analytics  [code]
    evaluate   (research) apply each hypothesis's rule to its computed figures                                                   [code]
    conclude   the analyst reads the evidence and writes the result, or asks for one follow-up round                             [worker]
    verify     every figure in the result is traced to the ledger; one repair by the worker, then untraceable parts are removed [code + worker]
    challenge  the critic looks for what is wrong or missing; a fix or a follow-up round is allowed once                         [worker]
    deliver    the answer is rendered from the evidence and the findings are saved on the case                                    [code]

A spec (harness/analysis.py, harness/research.py) says which schemas, tasks and renderer each harness uses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

import settings
from harness import core
from harness.runtime import Ctx, HarnessError
from harness.schemas import Critique, FollowUp, Plan
from harness.workers import WorkerError


@dataclass
class Spec:
    name: str  # analysis | research
    plan_cls: type[Plan]
    plan_task: str
    max_questions: int
    turn_cls: type[BaseModel]  # what the analyst returns: the result, or a follow-up request
    turn_task: str
    result_field: str  # which field of the turn holds the result ("findings" | "conclusion")
    critic_task: str
    critic_on: bool
    evaluate: Callable[[str, Plan, list[dict]], dict] | None  # (case_id, plan, results) -> extra evidence for the analyst, such as hypothesis verdicts
    render: Callable[..., str]  # (case_id, plan, result, results, gathered, extras, critique, verified, run) -> the markdown the CX agent relays
    save: Callable[[Ctx, dict, dict], int]  # (ctx, result, extras): saves the result on the case; returns how many findings


def plan_brief(plan: Plan) -> dict:
    return {"restated": plan.restated, "kpi": plan.kpi, "unit": plan.unit, "current": plan.current_label, "base": plan.base_label, "basis": plan.basis,
            "materiality": plan.materiality.model_dump(), "assumptions": plan.assumptions, "sources": sorted({q.source for q in plan.questions})}


async def run(ctx: Ctx, spec: Spec) -> dict:
    case = ctx.deps.get_case(ctx.case_id) or {}

    async def scope() -> dict:
        def check(plan: Plan) -> None:
            if len(plan.questions) > spec.max_questions:
                raise ValueError(f"ask at most {spec.max_questions} data questions in the first round (the plan has {len(plan.questions)}): merge them or drop the least useful")
            off = [f"{q.id} uses '{q.source}'" for q in plan.questions if q.source not in settings.SOURCES]
            if off:
                raise ValueError(f"{'; '.join(off)}, which is not connected. Connected sources: {', '.join(settings.SOURCES)}. Plan only with those")

        payload: dict = {"brief": ctx.run.brief, "limits": {"max_questions": spec.max_questions, "sources": list(settings.SOURCES)}}
        if case.get("findings"):
            payload["case"] = {"request": case.get("request", ""), "earlier_findings": [f"{f['title']}: {f['finding'][:160]}" for f in case["findings"][-6:]]}
        return (await ctx.worker("scoper", spec.plan_task, payload, spec.plan_cls, check=check)).model_dump()

    plan = spec.plan_cls.model_validate(await ctx.step("scope", scope, lambda p: f"{len(p['questions'])} questions, {len(p['steps'])} steps"))

    questions = list(plan.questions)
    gathered, results = await core.evidence_round(ctx, plan, plan.questions, plan.steps, "", required=True)
    gathered, results = dict(gathered), list(results)
    extras: dict = {}
    if spec.evaluate:
        async def evaluate() -> dict:
            return spec.evaluate(ctx.case_id, plan, results)
        extras = await ctx.step("evaluate", evaluate, lambda e: ", ".join(f"{h['id']} {h['status']}" for h in e.get("hypotheses", [])))

    def pack() -> dict:
        return {**core.evidence_pack(ctx.case_id, plan, questions, gathered, results), **extras}

    def no_follow_up(turn: BaseModel) -> None:
        if getattr(turn, spec.result_field) is None:
            raise ValueError(f"a follow-up is not allowed now: return the '{spec.result_field}'")

    async def conclude(name: str, round_no: int, can_follow_up: bool, extra: dict | None = None) -> dict:
        async def go() -> dict:
            payload = {"round": round_no, "can_follow_up": can_follow_up, "plan": plan_brief(plan), "evidence": pack(), **(extra or {})}
            turn = await ctx.worker("analyst", spec.turn_task, payload, spec.turn_cls, check=None if can_follow_up else no_follow_up)
            return turn.model_dump()
        return await ctx.step(name, go, lambda t: "follow-up requested" if t.get("follow_up") else "result written")

    rounds_left = 1

    async def second_round(fu_data: dict) -> None:
        nonlocal rounds_left
        rounds_left -= 1
        fu = core.renumber(FollowUp.model_validate(fu_data))
        questions.extend(fu.questions)
        g2, r2 = await core.evidence_round(ctx, plan, fu.questions, fu.steps, "_2", required=False)
        gathered.update(g2)
        results.extend(r2)

    turn = await conclude("conclude", 1, True)
    if turn.get("follow_up"):
        await second_round(turn["follow_up"])
        turn = await conclude("conclude_2", 2, False)
    if turn.get(spec.result_field) is None:
        raise HarnessError("the analyst did not return a result")

    async def verify(name: str, data: dict, round_no: int) -> dict:
        async def go() -> dict:
            result, repaired = dict(data), False
            untraced, bad_ids = core.audit_result(ctx.case_id, result)
            if untraced or bad_ids:
                try:
                    fixed = await ctx.worker("analyst", spec.turn_task, {"round": round_no, "can_follow_up": False, "plan": plan_brief(plan), "evidence": pack(),
                                                                          "previous": {spec.result_field: result}, "problems": core.describe_problems(untraced, bad_ids)},
                                             spec.turn_cls, check=no_follow_up)
                    result, repaired = fixed.model_dump()[spec.result_field], True
                    untraced, bad_ids = core.audit_result(ctx.case_id, result)
                except WorkerError:
                    pass
            removed: list[str] = []
            if untraced or bad_ids:
                result, removed = core.strip_untraced(result, untraced, bad_ids, core.fallback_headline(plan, results))
            return {"result": result, "repaired": repaired, "removed": removed}
        return await ctx.step(name, go, lambda v: ("repaired; " if v["repaired"] else "") + (f"{len(v['removed'])} untraceable part(s) removed" if v["removed"] else "all figures traced"))

    verified = await verify("verify", turn[spec.result_field], 2 if "conclude_2" in ctx.run.data else 1)

    critique: dict | None = None
    if spec.critic_on:
        async def challenge() -> dict:
            try:
                reply = await ctx.worker("critic", spec.critic_task, {"plan": plan_brief(plan), "evidence": pack(), spec.result_field: verified["result"],
                                                                      "can_follow_up": rounds_left > 0}, Critique)
                return reply.model_dump()
            except WorkerError as exc:
                return {"verdict": "accept", "issues": [], "missing_checks": [], "follow_up": None, "skipped": str(exc)[:200]}
        critique = await ctx.step("challenge", challenge, lambda c: c["verdict"] + (f", {len(c['issues'])} issue(s)" if c["issues"] else ""))
        c = Critique.model_validate({k: v for k, v in critique.items() if k != "skipped"})
        wants_round = bool(c.follow_up) and rounds_left > 0
        if c.must_fix or wants_round:
            if wants_round:
                await second_round(critique["follow_up"])
            revised = await conclude("revise", 3, False, {"previous": {spec.result_field: verified["result"]}, "critique": [i.model_dump() for i in c.issues]})
            if revised.get(spec.result_field) is None:
                raise HarnessError("the analyst did not return a revised result")
            verified = await verify("verify_2", revised[spec.result_field], 3)

    async def deliver() -> dict:
        data = verified["result"]
        markdown = spec.render(ctx.case_id, plan, data, results, gathered, extras, critique, verified, ctx.run)
        saved = spec.save(ctx, data, extras)
        ids = sorted({r["source"] for r in results if r.get("ok")} | {r["table"] for r in results if r.get("ok")})
        return {"markdown": markdown, "headline": data["headline"], "saved_findings": saved, "evidence": ids, "rounds": 2 if rounds_left == 0 else 1,
                "critic": (critique or {}).get("verdict", "off"), "removed": verified["removed"]}

    return await ctx.step("deliver", deliver, lambda d: f"{d['saved_findings']} findings saved on the case")
