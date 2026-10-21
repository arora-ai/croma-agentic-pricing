# Changelog

## v2.0.0 (branch `v2`)

Added
- **Analysis and Research as harnesses** (`hub/harness/`): code-owned stages (scope, evidence, evaluate, conclude, verify, challenge, deliver), typed worker contracts with one repair, a token governor, resumable runs saved stage by stage, and `GET /runs/<case>`. Workers: Scoper, Analyst, Critic (Foundry agents with no tools). `HARNESS_MODE=agent` keeps the v1 single-agent behaviour. See `docs/V2.md`.
- **Deterministic analytics engine** (`hub/studio/analytics.py`, `analyze.py`): contribution, rate versus mix, price versus volume versus mix, target gap, concentration, trend, reconcile, materiality; exact decompositions with unit tests and a random exactness check. Exposed to the report team as the studio tool `analyze`.
- **Guardrails from live runs**: a decomposed ratio must be reconciled with the data space's own figure; figures are traced to the evidence ledger and untraceable ones removed; the standing scope and units go in every data question; the filters each result applied are shown to the Critic.
- **`NAME_PREFIX`**: agents, connections and the memory store carry a prefix, so a second copy of the system runs next to the first without touching it.
- `integrations/genie/grant_hub_access.py`: gives the hub's managed identity its Databricks access.
- Chat UI: Plan / Get data / Compute / Conclude / Verify figures / Challenge / Deliver stepper, harness stage on the Analysis and Research nodes, worker dots, stage lines in the logs, and a collapsible full brief under the answer. Chat API: `/api/runs/<case>`.
- `tests/harness_call.py`: run a harness on the hub with per-stage timings.
- API gateway integration: see `docs/APIM.md` (front gateway, tool gateway, data tool gateway).

Changed
- `ask_analysis_agent` and `ask_research_agent` return a finished, checked brief instead of a short summary; the CX prompt relays it.
- `data_profile.json` gains `standing_scope` and should state how each ratio is defined; the Data agent checks that the SQL carries the standing scope.
- `load_env()` falls back to environment variables and `project()` uses `DefaultAzureCredential` (waits up to 90 s for the Azure CLI), so the chat API can run in a container.
- The Insight agent gets the `analyze` tool and a prompt paragraph about it.

## v1.0.0 (branch `main`, tag `v1.0.0`)

First private release: CX, Lookup, Analysis, Research and Reporting agents with a Data agent in front of Databricks Genie (or a custom backend or a stub), the report studio (evidence ledger, number audit, DOCX and HTML renderers), the chat UI with admin pane and Foundry memory, the deployment runbook.
