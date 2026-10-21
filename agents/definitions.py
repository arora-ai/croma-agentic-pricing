"""The Foundry agents: instructions (agents/prompts/*.md) and tools. The tool wiring below is the architecture:
who can call whom is set by each agent's allowed_tools, so there is no fixed flow, only permitted calls.

    CX        ->  Lookup, Research, Analysis, Reporting (hand-off), case context
    Lookup    ->  Data agent (plain "talk to my data" questions only)
    Research  ->  a harness in the hub (HARNESS_MODE=harness, the default): its workers are the Scoper, Analyst and Critic below, and it reaches data through
    Analysis      the Data agent. With HARNESS_MODE=agent the Research and Analysis agents defined here run the whole job themselves (the v1 behaviour).
    Scoper, Analyst, Critic  ->  no tools: each gets one JSON message from the harness and answers with one JSON object
    Reporting ->  the editor of the report team: Data agent, case context, report studio, and the team below
                  Planner, Insight analyst, Strategist, Writer, Reviewer  ->  report studio (evidence ledger, workspace, audit); Planner, Insight
                  and Strategist may also ask the Data agent a few questions
    Data      ->  Genie (the only agent with a data tool)

The report team and the harness workers run on a deep-reasoning model (REPORT_MODEL, default gpt-6-sol); the conversational agents stay on the fast model.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from azure.ai.projects.models import MCPTool, MemorySearchOptions, MemorySearchPreviewTool, PromptAgentDefinition, Reasoning

from agents import data_profile
from agents.memory_store import MEMORY_STORE, MEMORY_TOOL_SCOPE

HERE = Path(__file__).parent
MODEL = os.getenv("AGENT_MODEL", "gpt-5.4-mini")
EFFORT = os.getenv("AGENT_REASONING_EFFORT", "low")
REPORT_MODEL = os.getenv("REPORT_MODEL", "gpt-6-sol")
# Each deployment has its own token-per-minute quota that every agent on it shares, and every model turn re-sends the agent's whole context. The editor
# mostly polls (steady, light), so it sits on the second deep-reasoning deployment and the working agents get the larger one to themselves.
REPORT_MODELS = {"reporting": REPORT_MODEL, "planner": REPORT_MODEL, "writer": REPORT_MODEL,
                 "insight": REPORT_MODEL, "strategist": REPORT_MODEL, "reviewer": REPORT_MODEL}
# gpt-6.1-sol (250K tokens per minute) is available for the lighter roles through REPORT_MODEL_<ROLE> if the main deployment is busy with other users.
REPORT_MODELS = {k: os.getenv(f"REPORT_MODEL_{k.upper()}", v) for k, v in REPORT_MODELS.items()}
# Reasoning budget per role of the report team (override with REPORT_EFFORT_<ROLE>).
REPORT_EFFORT = {k: os.getenv(f"REPORT_EFFORT_{k.upper()}", "medium" if k == "writer" else "high") for k in ("reporting", "planner", "insight", "strategist", "writer", "reviewer")}
# The harness workers (WORKER_MODEL_<ROLE> and WORKER_EFFORT_<ROLE> override). Their inputs are small and structured, so the quota is not the limit here.
WORKER_MODELS = {k: os.getenv(f"WORKER_MODEL_{k.upper()}", os.getenv("WORKER_MODEL", REPORT_MODEL)) for k in ("scoper", "analyst", "critic")}
WORKER_EFFORT = {k: os.getenv(f"WORKER_EFFORT_{k.upper()}", "medium" if k == "scoper" else "high") for k in ("scoper", "analyst", "critic")}

NAME_PREFIX = os.getenv("NAME_PREFIX", "croma")
CONNECTIONS = {kind: f"{NAME_PREFIX}-hub-{kind}" for kind in ("genie", "agents", "context", "studio")}

# Same names as hub/settings.py (the hub is a separate container and cannot import from here; tests/test_names.py checks they agree).
ROLE_SUFFIX = {
    "cx": "cx", "lookup": "lookup", "research": "research", "analysis": "analysis", "reporting": "reporting",
    "planner": "report-planner", "insight": "report-insight", "strategist": "report-strategist", "writer": "report-writer", "reviewer": "report-reviewer",
    "data": "data", "scoper": "scoper", "analyst": "analyst", "critic": "critic",
}
NAMES = {role: f"{NAME_PREFIX}-{suffix}" for role, suffix in ROLE_SUFFIX.items()}


def _mcp(label: str, base: str, path: str, tools: list[str]) -> MCPTool:
    return MCPTool(server_label=label, server_url=f"{base}{path}", project_connection_id=CONNECTIONS[label], allowed_tools=tools, require_approval="never")


EVIDENCE = ["get_report_playbook", "list_evidence", "get_result", "get_results", "calc", "derive_table", "get_workspace"]


def analytics_catalog() -> str:
    """The operations the harness can run, as text for the Scoper's prompt: generated from hub/studio/analytics.py so the prompt cannot drift from the code."""
    hub = str(HERE.parent / "hub")
    if hub not in sys.path:
        sys.path.insert(0, hub)
    from studio import analytics

    return analytics.catalog()


def build(hub_url: str, allow_example: bool = False) -> dict[str, PromptAgentDefinition]:
    genie = lambda: _mcp("genie", hub_url, "/genie/mcp", ["query_space_croma_pricing"])
    agents = lambda tools: _mcp("agents", hub_url, "/agents/mcp", tools)
    context = lambda tools: _mcp("context", hub_url, "/context/mcp", tools)
    studio = lambda tools: _mcp("studio", hub_url, "/studio/mcp", tools)
    ask = ["ask_data_agent", "wait_for_agent"]
    batch = [*ask, "ask_data_batch"]

    tools = {
        "cx": [context(["create_case", "get_case"]), agents(["ask_lookup_agent", "ask_research_agent", "ask_analysis_agent", "ask_reporting_agent", "wait_for_agent"]),
               MemorySearchPreviewTool(memory_store_name=MEMORY_STORE, scope=MEMORY_TOOL_SCOPE, update_delay=int(os.getenv("MEMORY_UPDATE_DELAY", "5")),
                                       search_options=MemorySearchOptions(max_memories=8))],
        "lookup": [agents(ask)],
        "research": [agents(ask), context(["get_case", "save_finding"])],
        "analysis": [agents(ask), context(["get_case", "save_finding"])],
        "reporting": [context(["get_case"]), agents([*batch, "ask_report_specialist"]),
                      studio([*EVIDENCE, "save_frame", "get_document", "audit_report", "render_report"])],
        "planner": [context(["get_case"]), studio(["get_report_playbook", "list_evidence", "get_workspace", "save_blueprint"])],
        "insight": [context(["get_case"]), agents(batch), studio([*EVIDENCE, "analyze", "save_insights"])],
        "strategist": [context(["get_case"]), agents(ask), studio([*EVIDENCE, "save_actions"])],
        "writer": [studio([*EVIDENCE, "get_section_pack", "save_frame", "save_section", "get_document", "audit_report"])],
        "reviewer": [studio(["get_review_pack", "get_report_playbook", "list_evidence", "get_result", "calc", "get_workspace", "get_document", "audit_report", "save_review"])],
        "data": [genie()],
        "scoper": [], "analyst": [], "critic": [],
    }
    out = {}
    profile = data_profile.load(allow_example=allow_example)
    extra = {"ANALYTICS_CATALOG": analytics_catalog()}
    for key, t in tools.items():
        instructions = data_profile.render((HERE / "prompts" / f"{key}.md").read_text(encoding="utf-8"), profile, extra)
        if key in WORKER_EFFORT:
            model, effort = WORKER_MODELS[key], WORKER_EFFORT[key]
        elif key in REPORT_EFFORT:
            model, effort = REPORT_MODELS[key], REPORT_EFFORT[key]
        else:
            model, effort = MODEL, EFFORT
        out[key] = PromptAgentDefinition(model=model, instructions=instructions, tools=t, reasoning=Reasoning(effort=effort))
    return out


DESCRIPTIONS = {
    "cx": "Front end for users: collects and confirms request details, routes to specialists, returns the answer.",
    "lookup": "Answers plain data lookups (one figure or one breakdown) through the Data agent, or says NEEDS ANALYSIS.",
    "research": "Investigates topics, anomalies and hypotheses by asking the Data agent, and saves findings. (v1 single-agent mode; the harness replaces it by default.)",
    "analysis": "Analyses a business KPI (level, trend, variance, drivers) via the Data agent and saves findings. (v1 single-agent mode; the harness replaces it by default.)",
    "reporting": "Editor of the report team: plans, gathers evidence, directs the team, audits and publishes the designed business report.",
    "planner": "Report team: designs the report blueprint (type, sections, data requests) from the request.",
    "insight": "Report team: deep analysis of the evidence into ranked, evidenced insight cards (drivers, risks, anomalies).",
    "strategist": "Report team: turns insights into sized, owned, prescriptive actions.",
    "writer": "Report team: writes the report frame and sections to the document spec, with tables and charts drawn from the evidence ledger.",
    "reviewer": "Report team: independent critique of the finished report for accuracy, insight, consistency and actionability.",
    "data": "The only agent that touches data: queries the Genie data space and returns figures with their source.",
    "scoper": "Harness worker (no tools): turns a business brief into a typed plan of data questions, exact analytics steps and, for research, testable hypotheses.",
    "analyst": "Harness worker (no tools): reads the computed evidence and writes the typed findings or research conclusion, quoting only figures from the evidence.",
    "critic": "Harness worker (no tools): independent review of an analysis or research result; returns a verdict, precise issues and what was not checked.",
}
