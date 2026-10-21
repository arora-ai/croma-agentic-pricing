"""The worker prompts and the code agree: every JSON example in a prompt is accepted by the model that will parse the worker's reply, and the analytics catalogue
in the Scoper's prompt is generated from the code."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hub"))

import pytest  # noqa: E402

from agents import data_profile  # noqa: E402
from agents.definitions import analytics_catalog, build  # noqa: E402
from harness.schemas import AnalysisPlan, AnalystTurn, Critique, FixedSteps, ResearchPlan, ResearchTurn  # noqa: E402
from studio import analytics  # noqa: E402

MODELS = {"analysis_plan": AnalysisPlan, "research_plan": ResearchPlan, "fix_steps": FixedSteps, "analysis_findings": AnalystTurn, "research_conclusion": ResearchTurn}
BLOCK = re.compile(r"Example reply \((?P<what>[^)]*)\):\s*```json\n(?P<body>.*?)```", re.S)


def rendered(role: str) -> str:
    profile = data_profile.load(allow_example=True)
    text = (ROOT / "agents" / "prompts" / f"{role}.md").read_text(encoding="utf-8")
    return data_profile.render(text, profile, {"ANALYTICS_CATALOG": analytics.catalog()})


@pytest.mark.parametrize("role", ["scoper", "analyst", "critic"])
def test_prompt_examples_are_accepted_by_the_schemas(role):
    blocks = list(BLOCK.finditer(rendered(role)))
    assert blocks, f"{role}.md has no 'Example reply (...)' blocks"
    for m in blocks:
        data = json.loads(m["body"])
        task = re.search(r"task: (\w+)", m["what"])
        model = Critique if role == "critic" else MODELS[task.group(1)]
        model.model_validate(data)


def test_every_task_has_an_example_and_every_worker_task_is_known_to_the_pipeline():
    scoper = {re.search(r"task: (\w+)", m["what"]).group(1) for m in BLOCK.finditer(rendered("scoper"))}
    analyst = {re.search(r"task: (\w+)", m["what"]).group(1) for m in BLOCK.finditer(rendered("analyst"))}
    assert scoper == {"analysis_plan", "research_plan", "fix_steps"} and analyst == {"analysis_findings", "research_conclusion"}
    from harness import analysis, research

    tasks = {analysis.SPEC.plan_task, research.SPEC.plan_task, analysis.SPEC.turn_task, research.SPEC.turn_task}
    assert tasks == {"analysis_plan", "research_plan", "analysis_findings", "research_conclusion"}
    assert {analysis.SPEC.critic_task, research.SPEC.critic_task} == {"review_analysis", "review_research"}
    for role in ("scoper", "analyst", "critic"):
        text = rendered(role)
        assert all(t in text for t in {"scoper": ["analysis_plan", "research_plan", "fix_steps"], "analyst": ["analysis_findings", "research_conclusion"],
                                       "critic": ["review_analysis", "review_research"]}[role])


def test_the_scoper_prompt_lists_every_operation_with_its_outputs():
    text = rendered("scoper")
    assert analytics_catalog() == analytics.catalog() and "{{" not in text
    for name, spec in analytics.OPS.items():
        assert f"- {name}:" in text
        assert all(output in text for output in spec.outputs)


def test_workers_deploy_without_tools_and_report_agents_get_analyze():
    agents = build("https://hub.example", allow_example=True)
    for role in ("scoper", "analyst", "critic"):
        assert agents[role].tools == [] and agents[role].reasoning.effort in ("medium", "high")
    insight_tools = [t for tool in agents["insight"].tools for t in (getattr(tool, "allowed_tools", None) or [])]
    editor_tools = [t for tool in agents["reporting"].tools for t in (getattr(tool, "allowed_tools", None) or [])]
    assert "analyze" in insight_tools and "analyze" not in editor_tools
    assert "analyze" in agents["insight"].instructions
