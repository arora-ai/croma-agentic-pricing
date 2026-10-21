"""The Analysis and Research harnesses end to end, with scripted workers and a stub Data agent that registers real results in the evidence ledger.
No Azure access is needed: what is tested is the code that owns the stages (ordering, validation, repair, auditing, resuming), not the language model."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hub"))

import pytest  # noqa: E402

import events  # noqa: E402
from harness import execute, runtime, workers  # noqa: E402
from harness.governor import Governor  # noqa: E402
from harness.schemas import AnalysisPlan, ResearchPlan  # noqa: E402
from studio import ledger  # noqa: E402

CATEGORY = {"columns": ["category", "cur_nmv", "cur_profit", "base_nmv", "base_profit"],
            "rows": [["Phones", 150, 15, 100, 10], ["Laptops", 50, 12.5, 100, 20]], "sql": "select 1"}
CLASS = {"columns": ["class", "cur_nmv", "base_nmv"], "rows": [["Gaming", 30, 60], ["Thin and light", 20, 40]], "sql": "select 2"}

PLAN = {
    "restated": "What moved Stores margin % between August and September 2026, and which categories drove it.",
    "kpi": "Merchandise margin % (Stores)", "unit": "\u20b9 Cr", "current_label": "September 2026", "base_label": "August 2026", "basis": ["Stores only"],
    "questions": [{"id": "q1", "purpose": "category cut", "ask": "For Stores only, by category: NMV and merchandise profit for 2026-09-01 to 2026-09-30 and 2026-08-01 to 2026-08-31.",
                   "columns": ["category", "cur_nmv", "cur_profit", "base_nmv", "base_profit"]}],
    "steps": [{"id": "s1", "label": "Margin % drivers by category", "op": "rate_mix", "source": "q1",
               "params": {"key": "category", "cur_num": "cur_profit", "cur_den": "cur_nmv", "base_num": "base_profit", "base_den": "base_nmv"}}],
}


class Script:
    """Answers workers by task. A handler is a dict/str reply, a function of the message, or a list of those used one after the other."""

    def __init__(self, **handlers):
        self.handlers, self.calls = handlers, []

    async def __call__(self, role, message):
        body = json.loads(message)
        self.calls.append((role, body["task"], body))
        handler = self.handlers[body["task"]]
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        reply = handler(body) if callable(handler) else handler
        if isinstance(reply, Exception):
            raise reply
        return (reply if isinstance(reply, str) else json.dumps(reply)), {"input_tokens": 1000, "output_tokens": 400}

    def count(self, task):
        return sum(1 for _r, t, _b in self.calls if t == task)


def data_agent(tables):
    """A stub Data agent: registers the matching table in the ledger the way the Genie gateway does and answers citing its result id."""
    asked = []

    async def ask(question, case_id):
        asked.append(question)
        for key, payload in tables.items():
            if key in question:
                rid = ledger.register_genie(case_id, {**payload, "question": question})
                return f"| a | b |\n|---|---|\n| 1 | 2 |\n\nSource: Result {rid}; Stores only; Miscellaneous and Charges excluded."
        return "ERROR: the data agent could not answer (TimeoutError)."

    ask.asked = asked
    return ask


def make_deps(tables, saved):
    return runtime.Deps(ask_data=data_agent(tables), worker=workers.call_worker, save_finding=lambda *a: saved.append(a), get_case=lambda cid: None)


def figures(body, step="s1"):
    return next(s for s in body["evidence"]["steps"] if s["step"] == step)["figures"]


def good_findings(body, extra_driver=None):
    f = figures(body)
    drivers = [{"headline": f"Sales shifted towards lower-margin Phones: mix effect {f['mix_effect_total']['shown']}.", "evidence": ["T1", f["mix_effect_total"]["id"]],
                "why": f"Laptops improved their own rate ({f['rate_effect_total']['shown']}) but could not offset the shift.", "confidence": "high"}]
    if extra_driver:
        drivers.append(extra_driver)
    return {"findings": {"headline": f"Margin fell from {f['base_rate']['shown']} to {f['current_rate']['shown']} ({f['rate_change']['shown']}).", "drivers": drivers,
                         "not_drivers": [], "basis": ["Stores only; Miscellaneous and Charges excluded."], "caveats": [], "gaps": [], "next_questions": ["Why did Phones grow?"]}}


ACCEPT = {"verdict": "accept", "issues": [], "missing_checks": ["Markdown depth by category was not checked."]}


@pytest.fixture(autouse=True)
def clean():
    runtime._runs.clear()
    runtime._live.clear()
    events.EVENTS.clear()
    yield
    workers.set_invoker(None)


def run_harness(name, case, script, tables, brief="Why did margin move?"):
    workers.set_invoker(script)
    ledger._cases.pop(case, None)
    saved = []
    run = asyncio.run(execute(name, brief, case, make_deps(tables, saved)))
    return run, saved


# ---------------------------------------------------------------- the analysis harness

def test_analysis_runs_every_stage_and_the_numbers_are_the_engines():
    script = Script(analysis_plan=PLAN, analysis_findings=good_findings, review_analysis=ACCEPT)
    run, saved = run_harness("analysis", "C-h1", script, {"by category": CATEGORY})
    assert run.status == "done", run.error
    assert [s.name for s in run.stages] == ["scope", "gather", "columns", "compute", "conclude", "verify", "challenge", "deliver"]
    assert all(s.status == "done" for s in run.stages)
    md = run.result["markdown"]
    assert "Margin fell from 15.00% to 13.75% (-1.25 pp)." in md  # quoted from the engine, not computed by the model
    assert "| Segment |" in md and "Rate effect (pp)" in md and "Phones" in md and "mix effect -3.75 pp" in md
    assert "Markdown depth by category was not checked." in md  # what the critic said was not looked at
    assert run.result["critic"] == "accept" and run.result["rounds"] == 1 and run.result["removed"] == []
    assert [t for _r, t, _b in script.calls] == ["analysis_plan", "analysis_findings", "review_analysis"]  # three worker calls in the plain case
    titles = [s[2] for s in saved]
    assert titles[0] == "Headline" and titles[1].startswith("Driver 1") and all(s[1] == "analysis" for s in saved)
    stages = [(e["type"], e["stage"]) for e in events.EVENTS if e["type"].startswith("stage")]
    assert stages[0] == ("stage_start", "scope") and stages[-1] == ("stage_end", "deliver")
    assert {e["agent"] for e in events.EVENTS if e["type"] == "agent_start"} >= {"scoper", "analyst", "critic"}


def test_a_figure_the_worker_invented_is_caught_and_repaired():
    def invented(body):
        reply = good_findings(body)
        reply["findings"]["drivers"][0]["headline"] = "Phones lifted margin by 73.4% on the month."
        return reply

    script = Script(analysis_plan=PLAN, analysis_findings=[invented, good_findings], review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h2", script, {"by category": CATEGORY})
    assert run.status == "done" and "73.4" not in run.result["markdown"]
    assert script.count("analysis_findings") == 2
    repair = [b for _r, t, b in script.calls if t == "analysis_findings"][1]
    assert "73.4" in repair["problems"][0] and repair["previous"]["findings"]["drivers"][0]["headline"].startswith("Phones lifted")
    assert run.data["verify"]["repaired"] is True


def test_a_figure_that_stays_untraceable_is_removed_not_shown():
    def invented(body):
        reply = good_findings(body, {"headline": "Accessories lost 88.8% of their sales.", "evidence": ["T1"], "why": ""})
        reply["findings"]["headline"] = "Margin collapsed by 91.7% in September."
        return reply

    script = Script(analysis_plan=PLAN, analysis_findings=invented, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h3", script, {"by category": CATEGORY})
    md = run.result["markdown"]
    assert run.status == "done" and "91.7" not in md and "88.8" not in md and "Accessories" not in md
    assert "Merchandise margin % (Stores) moved from 15.00% in August 2026 to 13.75% in September 2026 (-1.25 pp)." in md  # rebuilt from the engine's figures
    assert len(run.result["removed"]) == 2 and "left out" in md  # the reader is told that something was dropped


def test_evidence_ids_that_do_not_exist_are_repaired_or_dropped():
    def bad_ids(body):
        reply = good_findings(body)
        reply["findings"]["drivers"][0]["evidence"] = ["T99"]
        return reply

    script = Script(analysis_plan=PLAN, analysis_findings=bad_ids, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h4", script, {"by category": CATEGORY})
    assert run.status == "done" and "What drove it" not in run.result["markdown"]  # its only driver cited nothing real, so it is gone
    assert any("cited no evidence" in r for r in run.result["removed"])


def test_critic_revision_is_applied_and_checked_again():
    critique = {"verdict": "revise", "issues": [{"severity": "major", "where": "drivers[0]", "problem": "Overstates certainty.", "fix": "Lower the confidence."}]}

    def revise(body):
        reply = good_findings(body)
        if body.get("critique"):
            reply["findings"]["drivers"][0]["confidence"] = "medium"
        return reply

    script = Script(analysis_plan=PLAN, analysis_findings=revise, review_analysis=critique)
    run, _saved = run_harness("analysis", "C-h5", script, {"by category": CATEGORY})
    assert run.status == "done", run.error
    assert [s.name for s in run.stages][-4:] == ["challenge", "revise", "verify_2", "deliver"]
    revision = [b for _r, t, b in script.calls if t == "analysis_findings"][-1]
    assert revision["critique"][0]["problem"] == "Overstates certainty." and revision["previous"]["findings"]["drivers"][0]["confidence"] == "high"
    assert "(medium confidence" in run.result["markdown"] and "verify_2" in run.data


def test_a_follow_up_round_runs_more_evidence_and_the_analyst_concludes_on_all_of_it():
    follow = {"follow_up": {"why": "Phones moved most; check which class.", "questions": [{"id": "q1", "purpose": "classes", "ask": "For Stores, Phones by class: NMV for September and August 2026.",
                                                                                                "columns": ["class", "cur_nmv", "base_nmv"]}],
                            "steps": [{"id": "s1", "label": "NMV change by class", "op": "contribution", "source": "q1", "params": {"key": "class", "cur": "cur_nmv", "base": "base_nmv"}}]}}

    def analyst(body):
        if body["round"] == 1:
            assert body["can_follow_up"] is True
            return follow
        assert body["round"] == 2 and body["can_follow_up"] is False
        steps = {s["step"]: s for s in body["evidence"]["steps"]}
        assert set(steps) == {"s1", "x1"} and steps["x1"]["from"] == "R2"  # follow-up ids are renumbered and read the second result
        reply = good_findings(body)
        reply["findings"]["drivers"].append({"headline": f"Gaming fell most within Phones ({steps['x1']['figures']['total_change']['shown']} in total).", "evidence": [steps["x1"]["table"]], "why": ""})
        return reply

    script = Script(analysis_plan=PLAN, analysis_findings=analyst, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h6", script, {"by category": CATEGORY, "Phones by class": CLASS})
    assert run.status == "done", run.error
    assert [s.name for s in run.stages][:9] == ["scope", "gather", "columns", "compute", "conclude", "gather_2", "columns_2", "compute_2", "conclude_2"]
    assert run.result["rounds"] == 2 and "Gaming fell most within Phones" in run.result["markdown"]


def test_missing_columns_are_remapped_or_sent_to_the_scoper_once():
    renamed = {**CATEGORY, "columns": ["Category", "Cur NMV (Cr)", "Cur Profit (Cr)", "Base NMV (Cr)", "Base Profit (Cr)"]}
    script = Script(analysis_plan=PLAN, analysis_findings=good_findings, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h7", script, {"by category": renamed})
    assert run.status == "done" and script.count("fix_steps") == 0  # near matches are mapped by code, no model call
    assert run.data["columns"]["steps"][0]["params"]["cur_den"] == "Cur NMV (Cr)"

    odd = {**CATEGORY, "columns": ["category", "sales_now", "profit_now", "sales_before", "profit_before"]}
    fixed = {"steps": [{**PLAN["steps"][0], "params": {"key": "category", "cur_num": "profit_now", "cur_den": "sales_now", "base_num": "profit_before", "base_den": "sales_before"}}]}
    script = Script(analysis_plan=PLAN, fix_steps=fixed, analysis_findings=good_findings, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h8", script, {"by category": odd})
    assert run.status == "done" and script.count("fix_steps") == 1
    asked = [b for _r, t, b in script.calls if t == "fix_steps"][0]
    assert "sales_now" in asked["results"]["q1"]["columns"] and "cur_profit" in asked["problems"][0] and len(asked["problems"]) == 4


def test_a_run_with_no_data_stops_with_a_reason_for_the_user():
    script = Script(analysis_plan=PLAN)
    run, _saved = run_harness("analysis", "C-h9", script, {})
    assert run.status == "failed" and "could not answer any of the questions" in run.error
    assert [t for _r, t, _b in script.calls] == ["analysis_plan"]  # nothing is spent on interpreting nothing


def test_the_critic_failing_does_not_fail_the_run():
    script = Script(analysis_plan=PLAN, analysis_findings=good_findings, review_analysis="I could not decide.")
    run, _saved = run_harness("analysis", "C-h10", script, {"by category": CATEGORY})
    assert run.status == "done" and run.data["challenge"]["skipped"] and run.result["critic"] == "accept"


def test_resuming_skips_finished_stages_and_does_not_ask_the_data_agent_again():
    script = Script(analysis_plan=PLAN, analysis_findings=[RuntimeError("model down")], review_analysis=ACCEPT)
    workers.set_invoker(script)
    ledger._cases.pop("C-h11", None)
    deps = make_deps({"by category": CATEGORY}, [])
    first = asyncio.run(execute("analysis", "Why did margin move?", "C-h11", deps))
    assert first.status == "failed" and first.stages[-1].name == "conclude" and first.stages[-1].status == "failed"
    asked_before = len(deps.ask_data.asked)

    script.handlers["analysis_findings"] = good_findings
    second = asyncio.run(execute("analysis", "Why did margin move?", "C-h11", deps))
    assert second.id == first.id and second.status == "done"
    assert script.count("analysis_plan") == 1 and len(deps.ask_data.asked) == asked_before  # scope and gather were not repeated
    assert "Margin fell from 15.00%" in second.result["markdown"]


def test_runs_travel_with_the_case():
    script = Script(analysis_plan=PLAN, analysis_findings=good_findings, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h12", script, {"by category": CATEGORY})
    dumped = runtime.dump("C-h12")
    runtime._runs.clear()
    runtime.load(json.loads(json.dumps(dumped)))
    again = runtime.get(run.id)
    assert again and again.status == "done" and again.result["headline"] == run.result["headline"] and again.data["compute"]


# ---------------------------------------------------------------- the research harness

RESEARCH_PLAN = {
    **PLAN, "sizing_step": "s1",
    "hypotheses": [
        {"id": "h1", "claim": "Mix shifted towards low-margin Phones", "mechanism": "More Phones sales dilute the average.", "expected": "Mix effect dominates the rate effect.",
         "step": "s1", "supports_if": "mix_share_of_gross_pct >= 50 and rate_change < 0", "refutes_if": "mix_share_of_gross_pct < 20"},
        {"id": "h2", "claim": "Category margins themselves fell", "mechanism": "Deeper markdown.", "expected": "Rate effect is negative and large.",
         "step": "s1", "supports_if": "rate_effect_total < -1", "refutes_if": ""},
        {"id": "h3", "claim": "One category explains most of the movement", "mechanism": "A single launch.", "expected": "Top segment carries most of the movement.",
         "step": "s1", "supports_if": "top3_share_of_gross_pct >= 99999"},
    ],
}


def research_conclusion(body):
    verdicts = {h["id"]: h for h in body["evidence"]["hypotheses"]}
    assert verdicts["h1"]["status"] == "supported" and verdicts["h2"]["status"] == "not_supported" and verdicts["h3"]["status"] == "not_supported"
    assert body["evidence"]["movement_to_explain"]["step"] == "s1"
    f = figures(body)
    return {"conclusion": {"headline": f"Margin fell {f['rate_change']['shown']}, mostly because sales moved to lower-margin Phones.",
                           "causes": [{"claim": "Mix shift towards Phones", "size": f"mix effect {f['mix_effect_total']['shown']}", "evidence": ["T1", f["mix_effect_total"]["id"]], "confidence": "high"}],
                           "ruled_out": ["Category margins did not fall on their own: the rate effect was positive."], "unknowns": ["Why Phones sales grew."], "caveats": [], "next_steps": []}}


def test_research_rules_are_applied_by_code_and_the_conclusion_cites_the_verdicts():
    script = Script(research_plan=RESEARCH_PLAN, research_conclusion=research_conclusion, review_research=ACCEPT)
    run, saved = run_harness("research", "C-r1", script, {"by category": CATEGORY}, brief="Why did Stores margin fall?")
    assert run.status == "done", run.error
    assert [s.name for s in run.stages] == ["scope", "gather", "columns", "compute", "evaluate", "conclude", "verify", "challenge", "deliver"]
    md = run.result["markdown"]
    assert "| Mix shifted towards low-margin Phones | Supported |" in md and "| Category margins themselves fell | Not supported |" in md
    assert "Most likely causes, in order" in md and "Still unknown" in md
    assert any(s[1] == "research" and s[2].startswith("Supported:") for s in saved) and any(s[2].startswith("Not supported:") for s in saved)
    assert run.data["evaluate"]["hypotheses"][0]["figures"]["rate_change"]["shown"] == "-1.25 pp"


def test_a_rule_that_cannot_be_evaluated_is_inconclusive_not_a_guess():
    plan = json.loads(json.dumps(RESEARCH_PLAN))
    plan["hypotheses"][1]["supports_if"] = "average_price_change_pct > 1"  # not an output of rate_mix: refused when the plan is read, so the scoper must fix it
    script = Script(research_plan=[plan, RESEARCH_PLAN], research_conclusion=research_conclusion, review_research=ACCEPT)
    run, _saved = run_harness("research", "C-r2", script, {"by category": CATEGORY})
    assert run.status == "done" and script.count("research_plan") == 2
    repair = [b for _r, t, b in script.calls if t == "research_plan"][1]
    assert "average_price_change_pct" in repair["problem"] and "not outputs of rate_mix" in repair["problem"]


def test_the_critic_can_ask_for_one_follow_up_round_in_research():
    critique = {"verdict": "revise", "issues": [{"severity": "major", "where": "causes[0]", "problem": "Phones' own class mix is unchecked.", "fix": "Check classes."}],
                "follow_up": {"why": "Check the class mix within Phones.", "questions": [{"id": "q1", "ask": "For Stores, Phones by class: NMV for September and August 2026.", "columns": ["class", "cur_nmv", "base_nmv"]}],
                              "steps": [{"id": "s1", "label": "NMV by class", "op": "contribution", "source": "q1", "params": {"key": "class", "cur": "cur_nmv", "base": "base_nmv"}}]}}

    def conclude(body):
        reply = research_conclusion(body)
        if body["round"] == 3:
            assert {s["step"] for s in body["evidence"]["steps"]} == {"s1", "x1"} and body["critique"][0]["problem"].startswith("Phones' own class")
        return reply

    script = Script(research_plan=RESEARCH_PLAN, research_conclusion=conclude, review_research=critique)
    run, _saved = run_harness("research", "C-r3", script, {"by category": CATEGORY, "Phones by class": CLASS})
    assert run.status == "done", run.error
    names = [s.name for s in run.stages]
    assert names.index("challenge") < names.index("gather_2") < names.index("revise") < names.index("verify_2") and run.result["rounds"] == 2


# ---------------------------------------------------------------- the pieces

def test_worker_replies_are_parsed_leniently_and_repaired_once():
    from harness.schemas import FixedSteps

    assert workers.extract_json('Here you go:\n```json\n{"a": {"b": 1}}\n```\nDone.') == {"a": {"b": 1}}
    assert workers.extract_json('{"a": 1} trailing words') == {"a": 1}
    with pytest.raises(ValueError, match="no JSON"):
        workers.extract_json("no braces here")
    with pytest.raises(ValueError, match="cut off|not valid"):
        workers.extract_json('{"a": [1, 2')

    good = {"steps": [PLAN["steps"][0]]}
    script = Script(fix_steps=['{"steps": [', good])
    workers.set_invoker(script)
    out = asyncio.run(workers.call_worker("scoper", "fix_steps", {"x": 1}, FixedSteps, case_id="C-w", parent="analysis"))
    assert out.steps[0].op == "rate_mix" and script.count("fix_steps") == 2
    assert "problem" in script.calls[1][2] and "previous_reply" in script.calls[1][2]

    script = Script(fix_steps=["nope"])
    workers.set_invoker(script)
    with pytest.raises(workers.WorkerError, match="did not return a usable 'fix_steps' reply"):
        asyncio.run(workers.call_worker("scoper", "fix_steps", {}, FixedSteps, case_id="C-w", parent="analysis"))
    assert script.count("fix_steps") == 2  # one repair, then it gives up


def test_schemas_say_what_to_fix():
    bad_op = json.loads(json.dumps(PLAN))
    bad_op["steps"][0]["params"] = {"key": "category"}
    with pytest.raises(ValueError, match=r"missing parameter.*cur_num"):
        AnalysisPlan.model_validate(bad_op)
    bad_src = json.loads(json.dumps(PLAN))
    bad_src["steps"][0]["source"] = "q9"
    with pytest.raises(ValueError, match="must read a question id"):
        AnalysisPlan.model_validate(bad_src)
    bad_rule = json.loads(json.dumps(RESEARCH_PLAN))
    bad_rule["hypotheses"][0]["supports_if"] = "1 < x < 5"
    with pytest.raises(ValueError, match="not outputs of rate_mix|chains comparisons"):
        ResearchPlan.model_validate(bad_rule)
    over = json.loads(json.dumps(RESEARCH_PLAN))
    over["hypotheses"] = [{**RESEARCH_PLAN["hypotheses"][0], "id": f"h{i}"} for i in range(9)]
    assert len(ResearchPlan.model_validate(over).hypotheses) == 5  # an over-full list is cut, not an error


def test_governor_holds_a_call_back_until_the_minute_has_room():
    clock = [0.0]
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    gov = Governor(tpm_budget=10_000, parallelism=2, clock=lambda: clock[0], sleep=sleep)

    async def go():
        async with gov.slot(6_000) as first:
            first[1] = 7_000.0  # the actual usage replaces the estimate
        assert gov.used() == 7_000
        async with gov.slot(6_000):  # 7,000 + 6,000 > 10,000: waits for the first call to leave the minute
            pass
        async with gov.slot(50_000):  # larger than the whole budget: still runs, alone
            pass

    asyncio.run(go())
    assert slept and clock[0] >= 60.0


def test_the_stage_log_is_what_the_admin_view_reads():
    script = Script(analysis_plan=PLAN, analysis_findings=good_findings, review_analysis=ACCEPT)
    run, _saved = run_harness("analysis", "C-h13", script, {"by category": CATEGORY})
    summary = run.to_dict(with_data=False)
    assert "data" not in summary and [s["name"] for s in summary["stages"]][0] == "scope"
    assert next(s for s in summary["stages"] if s["name"] == "gather")["summary"] == "1 of 1 questions answered"
    assert next(s for s in summary["stages"] if s["name"] == "compute")["summary"] == "1 of 1 computed"
