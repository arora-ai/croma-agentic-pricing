# Croma agentic profit planning and pricing

A multi-agent assistant for profit planning and pricing, built on **Microsoft Foundry** with a pluggable data layer (**Azure Databricks Genie** or your own data agent).
A business user talks to one conversational agent. It clarifies the question, then brings in specialists that answer data lookups, analyse a KPI, investigate causes, or
produce a designed, audited business report (Word, with charts and an evidence appendix).

> Status: a working prototype for internal testing, not a hardened product. See [Known limits](#known-limits).

## How it works

```
                 you
                  |
            CX agent  (asks the right questions first, remembers preferences)
   ______________|______________________________
   |            |             |                  |
 Lookup     Analysis      Research          Reporting (editor of a report team)
   |            |             |              Planner, Insight, Strategist, Writer, Reviewer
   |____________|_____________|                  |  \_ report studio: evidence ledger, number audit, DOCX renderer
                |                                |
            Data agent  (the only agent that touches data)
                |
        hub data tool  ->  backend: Databricks Genie | your function | stub
```

- **CX** talks the request through before any work starts: it says back what it understood, then asks only the few questions whose answers would change the analysis
  (what "category drivers" means, the purpose, the depth), offers options with a suggested default, and proceeds when you say "go with your suggestions".
  Plain lookups skip all of that. See `agents/prompts/cx.md`.
- **Lookup** answers one-figure questions on a fast path. **Analysis** decomposes a number (what happened, how big). **Research** tests causes (why, is X true).
  In v2 both are harnesses (`hub/harness/`): code owns the stages, the arithmetic and the checks, and three model workers (Scoper, Analyst, Critic) reason inside them. Drivers are
  computed exactly (rate versus mix, price versus volume versus mix), every figure is traced to the data, and research hypotheses are decided by rules over computed figures.
  See [`docs/V2.md`](docs/V2.md).
- **Reporting** is a harness, not one call: an Editor runs a team (Planner, Insight analyst, Strategist, Writer, Reviewer) that shares a per-case workspace. Every data result gets
  an id in an **evidence ledger**; tables and charts are drawn from ledger rows, never typed by a model; a **number audit** traces every figure in the text to a result or a recorded
  calculation and **blocks publishing** while any is untraced. See [The report studio](#the-report-studio).
- **Genie** (or your own backend) is behind a single tool on the hub, so the data layer can be swapped without touching the agents. See [`integrations/genie/README.md`](integrations/genie/README.md).

The full target architecture (including later phases such as API gateways, Teams and scheduled nudges) is in [`docs/architecture.md`](docs/architecture.md).

## Repository map

| Path | What it is |
|---|---|
| `agents/` | The Foundry agents: instructions (`prompts/*.md`), tool wiring and models (`definitions.py`), deploy and memory scripts, and `data_profile.py` |
| `config/` | `data_profile.example.json`: describes your data space to the agents (copy to `data_profile.json`) |
| `hub/` | The tools hub, one container with four MCP servers: data tool, agent bridge, case context, report studio. `hub/backends/` holds the data backends |
| `hub/harness/` | v2: the Analysis and Research harnesses: runtime, pipeline, typed worker contracts, token governor |
| `hub/studio/` | The report studio: ledger, calc, audit, renderers, `playbook.md`, and the deterministic analytics engine (`analytics.py`, `analyze.py`) |
| `integrations/genie/` | How to connect your Genie space or data agent, an example custom backend, and `try_question.py` |
| `infra/` | PowerShell deploy scripts and `config.example.ps1`. `infra/apim/` is the optional Azure API Management layer (Bicep, policies, deploy, test and teardown scripts) |
| `ui/` | The chat UI: FastAPI backend and React front end, with an admin pane (agent network, telemetry, case file, memory). `ui/Dockerfile` runs it as a container app |
| `tests/` | Unit tests (no Azure needed) and small tools for testing a live deployment |
| `docs/` | `DEPLOYMENT.md` (the step-by-step deployment runbook), `V2.md` (the harness design and how v2 runs next to v1), `APIM.md` (the three API gateways), `architecture.md` (diagrams) |

## Prerequisites

- An Azure subscription with a **Microsoft Foundry** account and project, an **Azure Container Registry** and a **Container Apps environment**.
- Model deployments in the Foundry account: a conversational model (default name `gpt-5.4-mini`), an embedding model for memory (`text-embedding-3-large`) and a deep-reasoning
  model for the report team (default `gpt-6-sol`). Different names are fine: set `AGENT_MODEL`, `EMBEDDING_MODEL` and `REPORT_MODEL` in your shell before deploying the agents.
- Tools: Azure CLI (with the `containerapp` extension), PowerShell, Python 3.11+, Node 20 (for the UI). Sign in with `az login`.
- A data source: a Databricks Genie space, or your own data agent. Optional at first: the default `stub` backend lets you deploy and explore everything else.

## Quick start

**Deploying to a new Azure tenant? Follow the step-by-step runbook: [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).** The summary below is the short version.

```powershell
# 1. Python environment
python -m venv .venv
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest tests -q          # unit tests, no Azure needed

# 2. Describe your Azure environment (git-ignored) and your data
Copy-Item infra\config.example.ps1 infra\config.ps1                 # fill it in
Copy-Item config\data_profile.example.json config\data_profile.json # fill it in
.\.venv\Scripts\python -m agents.deploy_agents --check              # renders every prompt; fails if anything is unfilled

# 3. Deploy the hub (builds the image, deploys it, writes .env, creates the project connections)
.\infra\deploy_hub.ps1

# 4. Deploy the agents into your Foundry project
.\.venv\Scripts\python -m agents.deploy_agents

# 5. Connect your data (see integrations/genie/README.md), then check it
.\.venv\Scripts\python integrations\genie\try_question.py "How many stores are there in each zone?"

# 6. Talk to it
.\.venv\Scripts\python chat.py "How many sales did we do last month?"
.\ui\run.ps1                                                         # the chat UI at http://localhost:8000
```

With `GENIE_BACKEND=stub` (the default) every data question answers "No data source is connected yet", so agents report a gap rather than inventing numbers. Connect a backend in step 5
to get real answers.

## Connect your data

Follow [`integrations/genie/README.md`](integrations/genie/README.md). In short: set `Backend = "databricks"` (host and space id) or `Backend = "custom"` (your own function) in
`infra/config.ps1`, grant the hub's identity access, redeploy the hub, and verify with `try_question.py`. Then fill `config/data_profile.json` so the agents plan around the data you really have.

## Configuration reference

| Where | What |
|---|---|
| `infra/config.ps1` | Subscription, resource group, ACR, Container Apps environment, Foundry account and project, and the `Genie` block (backend, host, space id, auth, tool description) |
| `config/data_profile.json` | Date coverage, fiscal year, zones, channels, categories, and planning facts, rendered into the agent prompts at deploy time |
| `.env` | `HUB_URL`, `HUB_API_KEY`, `FOUNDRY_PROJECT_ENDPOINT`: written by `deploy_hub.ps1`. Secret, git-ignored. See `.env.example` |
| Shell environment at agent deploy | `NAME_PREFIX`, `AGENT_MODEL`, `AGENT_REASONING_EFFORT`, `REPORT_MODEL`, `REPORT_EFFORT_<ROLE>`, `REPORT_MODEL_<ROLE>`, `WORKER_MODEL`, `WORKER_MODEL_<ROLE>`, `WORKER_EFFORT_<ROLE>`, `MEMORY_STORE`, `MEMORY_SCOPE` |
| Hub container app settings | `NAME_PREFIX`, `HARNESS_MODE`, `GENIE_BACKEND`, `DATABRICKS_HOST`, `GENIE_SPACE_ID`, `DATABRICKS_AUTH`, `CUSTOM_BACKEND`, `GENIE_TOOL_DESCRIPTION`, `GENIE_MAX_ROWS`, `DATA_PARALLELISM`, `AGENT_CALL_TIMEOUT_S`, `WORKER_TPM_BUDGET`, `WORKER_PARALLELISM` (set from `infra/config.ps1`; the full list with defaults is in [`docs/V2.md`](docs/V2.md)) |

## The report studio

A report request goes CX to the Reporting agent (the editor), which runs: **plan** (report type, sections, data requests) then **gather** (data questions in parallel) then **think** (the Insight
analyst reconciles and decomposes, and may ask follow-ups) then **prescribe** (actions with owner, size, guardrail, KPI) then **write** (frame and sections to a document spec) then **audit**
then **review** (independent critique, with repair rounds) then **publish** (DOCX and HTML).

- The playbook the team works from is [`hub/studio/playbook.md`](hub/studio/playbook.md): report types, the quality bar, the exact document spec, writing rules.
- The report team runs on a deep-reasoning model with a high reasoning budget (see `agents/definitions.py`). The conversational agents use a fast model.
- Output: a designed Word report (cover, bottom line, KPI tiles, heat-coloured tables, charts and bridges, action table, evidence register, the calculations it cites, the SQL behind each
  figure, the audit result) and the same document as HTML for the in-app viewer.
- `python tests/make_sample_report.py` renders a sample from inline data with no agents, for a quick look at the format.

## Testing

```powershell
.\.venv\Scripts\python -m pytest tests -q                    # analytics, harnesses (scripted workers), ledger, audit, renderers, studio tools, backends, data profile, prompts
.\.venv\Scripts\python -m tests.cx_dialog "first turn || reply"   # script a CX conversation (needs a deployment)
.\.venv\Scripts\python tests\harness_call.py analysis "<brief>"   # run the Analysis (or research) harness on the hub with per-stage timings
.\.venv\Scripts\python tests\watch.py                        # live view of agent and tool calls on the hub
.\.venv\Scripts\python tests\studio_call.py audit_report <case_id>     # call a studio tool directly
.\.venv\Scripts\python tests\fetch_report.py <case_id> 1     # download a published report and render it to page images (Windows with Word)
```

## Operating notes

- **Foundry cuts a single MCP tool call at 100 seconds.** Agent-to-agent calls therefore run as background jobs: the tool waits up to 80 seconds, returns `STILL RUNNING (job J-...)`, and the
  caller keeps collecting with `wait_for_agent`. Hub-to-Foundry agent runs are background responses that are polled.
- **A streamed response is cut at about ten minutes.** The UI backend and `chat.py` run the response in background mode and resume the stream from the last event.
- **Model quota is the ceiling on the report team.** Each deployment has a tokens-per-minute quota shared by all agents on it, and every model turn re-sends the agent's context. The design keeps
  calls few and contexts small and the hub retries a rate-limited run after the 60-second window, but a full report still takes 30+ minutes at modest quotas. A higher quota shortens it noticeably.
- **State.** Case state (evidence ledger, workspace, case file, harness runs) is in the hub's memory on a single replica, so a redeploy loses in-flight cases. `GET /state/<case>` exports a case with its
  runs and `POST /state` restores it. A durable store is the next step.
- **Data warehouse sizing.** The Data agent may ask up to `DATA_PARALLELISM` (default 4) questions at once; an undersized warehouse queues them and Genie gives up at 90 seconds.

## Security

- No secret is in this repository. `.env`, `infra/config.ps1` and `config/data_profile.json` are git-ignored; check before every commit that you have not added one.
- The hub is protected by an API key held in Foundry project connections (not in agent definitions). The hub itself signs in to Foundry and Databricks with its managed identity.
  With the optional API gateways ([`docs/APIM.md`](docs/APIM.md)) the hub key lives only in Azure API Management, the Foundry connections hold a revocable subscription key, the
  chat API accepts a user identity only from the gateway, and Databricks is reached with APIM's own identity.
  `DATABRICKS_AUTH=token` (a personal access token) is for quick tests only and is stored as a container app secret.
- Data calls run as the hub's identity, not as the signed-in user, so row and column security applies to that identity. Per-user pass-through is not built.
- The UI keeps chat history in a local SQLite file (`ui/backend/data/`, git-ignored). It contains real conversations: do not commit or share it.
- Keep this repository **private**. It encodes business logic and prompts specific to a retail profit-planning data model.

## Known limits

- Single replica, in-memory case state; chat history in local SQLite. Without the API gateway the UI is single user (memory scope `croma-user-1`); behind it, history and memory are per signed-in user ([`docs/APIM.md`](docs/APIM.md)).
- The API gateways (Azure API Management in front of the chat API, the hub's MCP endpoints and Databricks) are optional and in [`docs/APIM.md`](docs/APIM.md). Data calls still run as the hub's identity, not the signed-in user. No Teams channel, no scheduled nudges, no automated evaluations yet.
- Natural-language-to-SQL can vary between runs: keep the Genie space's instructions and example queries strong, and add an example whenever a question type proves unstable. The Analysis and Research
  harnesses detect basis differences (reconciles against the data space's own figures, the filters of each query shown to the Critic) but cannot make the data space deterministic.
- The Reporting pipeline's stage order is still decided by the Editor agent following its prompt. The harness runtime from v2 is ready for it to become a code-driven pipeline with model workers
  (the recommended next step for speed and reliability).

## License

No license is set. Add one before sharing outside the pilot.
