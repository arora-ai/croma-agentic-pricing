"""Settings for the tools hub. Everything comes from environment variables so the same code runs locally and in Container Apps."""
from __future__ import annotations

import os

API_KEY = os.getenv("HUB_API_KEY", "")

# Foundry project that hosts the agents (required): https://<account>.services.ai.azure.com/api/projects/<project>
PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")

# Data tool controls. In a larger deployment these sit in an API gateway in front of the data layer.
MAX_ROWS = int(os.getenv("GENIE_MAX_ROWS", "200"))
MAX_QUESTIONS_PER_CASE = int(os.getenv("GENIE_MAX_QUESTIONS_PER_CASE", "120"))

# Which backend answers the data questions: "databricks" (a Databricks Genie space), "custom" (your own function, see integrations/genie) or
# "stub" (answers every question with a clear "not configured" message, so the rest of the system can be exercised without data).
GENIE_BACKEND = os.getenv("GENIE_BACKEND", "stub")
GENIE_TIMEOUT_S = float(os.getenv("GENIE_TIMEOUT_S", "90"))  # Foundry cuts a tool call off at 100 seconds, so give up before that
GENIE_TOOL_DESCRIPTION = os.getenv("GENIE_TOOL_DESCRIPTION", "")  # shown to the Data agent: what the connected data space covers

# Databricks Genie backend. Nothing secret lives in code: the host and space id come from here, credentials from the environment.
DATABRICKS_HOST = os.getenv("DATABRICKS_HOST", "").rstrip("/")  # https://adb-<workspace-id>.<n>.azuredatabricks.net
GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")
DATABRICKS_AUTH = os.getenv("DATABRICKS_AUTH", "managed_identity")  # managed_identity (also covers an Entra service principal via env vars) | token
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN", "")  # only for DATABRICKS_AUTH=token (a personal access token for a quick test)

# Custom backend: "package.module:function", an async function ask(question, conversation_id) -> dict (see integrations/genie/README.md).
CUSTOM_BACKEND = os.getenv("CUSTOM_BACKEND", "")

# Foundry agent names: <NAME_PREFIX>-<suffix>. A different prefix gives a second, independent set of agents in the same Foundry project, so two versions
# of the system can run side by side. agents/definitions.py builds the same names (tests/test_names.py keeps the two in step).
# The report team sits behind the Reporting agent (the editor); scoper, analyst and critic are the workers of the Analysis and Research harnesses.
NAME_PREFIX = os.getenv("NAME_PREFIX", "croma")
ROLE_SUFFIX = {
    "cx": "cx", "lookup": "lookup", "research": "research", "analysis": "analysis", "reporting": "reporting",
    "planner": "report-planner", "insight": "report-insight", "strategist": "report-strategist", "writer": "report-writer", "reviewer": "report-reviewer",
    "data": "data", "scoper": "scoper", "analyst": "analyst", "critic": "critic",
}
AGENT_NAMES = {role: f"{NAME_PREFIX}-{suffix}" for role, suffix in ROLE_SUFFIX.items()}
REPORT_TEAM = ("planner", "insight", "strategist", "writer", "reviewer")
WORKERS = ("scoper", "analyst", "critic")
AGENT_CALL_TIMEOUT_S = float(os.getenv("AGENT_CALL_TIMEOUT_S", "3600"))

# Analysis and Research run as harnesses: code owns the stages, the checks and the arithmetic, and language-model workers (scoper, analyst, critic) do the
# reasoning inside them. HARNESS_MODE=agent falls back to v1, where one agent with tools runs the whole job.
HARNESS_MODE = os.getenv("HARNESS_MODE", "harness")  # harness | agent
ANALYSIS_MAX_QUESTIONS = int(os.getenv("ANALYSIS_MAX_QUESTIONS", "6"))  # data questions in the first round of an analysis
RESEARCH_MAX_QUESTIONS = int(os.getenv("RESEARCH_MAX_QUESTIONS", "8"))
ANALYSIS_CRITIC = os.getenv("ANALYSIS_CRITIC", "on").lower() in ("on", "1", "true", "yes")  # the red-team step is always on for research
WORKER_TPM_BUDGET = int(os.getenv("WORKER_TPM_BUDGET", "150000"))  # tokens per minute the workers may use on their deployment (keep under its quota)
WORKER_PARALLELISM = int(os.getenv("WORKER_PARALLELISM", "2"))  # worker calls in flight at once, across all runs
WORKER_TIMEOUT_S = float(os.getenv("WORKER_TIMEOUT_S", "900"))
