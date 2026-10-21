# Connecting your data layer (Databricks Genie or your own data agent)

The agents never touch data directly. Every number comes through one tool on the hub, `query_space_croma_pricing`, which hands the question to a **backend**
and returns the SQL and rows. Swapping the backend is how you point this system at your own Genie space or data agent. Nothing else changes.

```
Data agent (Foundry)  ->  hub /genie/mcp  ->  backend  ->  your data
                                   |
                                   +-> evidence ledger (every result gets an id that reports cite)
```

| Backend | `GENIE_BACKEND` | Use it when |
|---|---|---|
| Stub | `stub` (default) | You are deploying and want the system up before data is connected. Every question answers "not configured", so agents report a gap instead of inventing numbers. |
| Databricks Genie | `databricks` | Your data is behind a Genie space. Ready to use: set the host and space id. |
| Custom | `custom` | Your data agent is something else (another Genie wrapper, a SQL service, an internal API). You write one small async function. |

## Option A: a Databricks Genie space

**1. Have a Genie space that answers well.** Accuracy lives in the space, not in the agents: put the business definitions (what NMV, margin, achievement
mean), default filters, join logic, measure SQL and tested example queries in the space's instructions. The agents deliberately do not restate these.
Test the space in the Databricks UI with the kind of questions in `tests/` and `config/data_profile.example.json` before connecting it.

**2. Give the hub an identity in the workspace.** In Azure the hub runs as a managed identity. A workspace admin adds it as a service principal and grants it
access:
- Databricks workspace > Settings > Identity and access > Service principals > Add > *Microsoft Entra ID managed identity*, using the managed identity's
  **client (application) id**. Find it with `az containerapp show -n <app> -g <rg> --query identity.principalId` and then the identity's client id in Entra,
  or take the application id from Entra > Enterprise applications.
- On the SQL warehouse the space uses: **CAN USE**.
- On the Genie space: **CAN RUN**.
- On the data: `GRANT USE CATALOG ON CATALOG <catalog> TO <service principal>;`, `GRANT USE SCHEMA ON SCHEMA <catalog>.<schema> TO ...;`,
  `GRANT SELECT ON SCHEMA <catalog>.<schema> TO ...;` (or table by table).

For a quick test without an identity, use a personal access token instead (see `DATABRICKS_AUTH=token` below).

**3. Configure.** In `infra/config.ps1` (copied from `infra/config.example.ps1`):

```powershell
$Cfg.Genie = @{
    Backend         = "databricks"
    DatabricksHost  = "https://adb-<workspace-id>.<n>.azuredatabricks.net"
    SpaceId         = "<genie space id>"
    AuthMode        = "managed_identity"      # or "token" with $env:DATABRICKS_TOKEN set when you deploy
    ToolDescription = "Retail profit planning data: sales, markdown, margin, targets and plans by store, zone, channel, category and brand."
}
```

`ToolDescription` is what the Data agent reads about the connected data. Write it for your data.

**4. Deploy and check.**

```powershell
.\infra\deploy_hub.ps1
python integrations\genie\try_question.py "How many stores are there in each zone?"
```

`try_question.py` calls the hub's data tool directly and prints the SQL, columns and rows, so you can verify the connection before involving any agent.
`<hub url>/healthz` shows which backend is active.

### Authentication modes

| `DATABRICKS_AUTH` | Credential | Notes |
|---|---|---|
| `managed_identity` (default) | Entra token for Azure Databricks | In Azure: the Container App's managed identity. Locally: your `az login`. With `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` set: that Entra service principal. No secret is stored in the app when it is a managed identity. |
| `token` | `DATABRICKS_TOKEN` | A personal access token or OAuth token. For a quick test only: it acts as one person and expires. `deploy_hub.ps1` stores it as a container app secret, never in an environment variable in clear. |

Calls run as the hub's identity, not as the signed-in user, so Unity Catalog row and column security is applied for that identity. Per-user pass-through
(on-behalf-of) is not built.

## Option B: your own data agent or service

Write an async function and point the hub at it.

```python
# hub/my_backend.py
async def ask(question: str, conversation_id: str = "") -> dict:
    ...  # call your agent or service
    return {"question": question, "sql": "...", "columns": ["zone", "nmv"], "rows": [["West", 12.3]], "row_count": 1}
```

```powershell
$Cfg.Genie = @{ Backend = "custom"; CustomBackend = "my_backend:ask"; ToolDescription = "..." }
.\infra\deploy_hub.ps1
```

The function must return the dictionary described in `hub/backends/base.py` (success or `{"error": ...}`). `integrations/genie/custom_backend_example.py` is a small
working example. Rules the rest of the system relies on:

- **Numbers as numbers**, never formatted strings. Percentages in percent units (51.4 means 51.4%) unless the column name says otherwise.
- **Stable, plain column names** (`cy_nmv`, `ly_nmv`, `plan_nmv`). The report team builds tables and charts from them by name, and the Data agent can ask for the names it wants.
- **Return the SQL** if you have it. It is shown in the admin view and kept in each report's evidence appendix.
- **Finish within 90 seconds** (`GENIE_TIMEOUT_S`). Foundry cuts a tool call off at 100 seconds.
- **Be safe to call concurrently.** The Data agent may ask several independent questions at once (the hub caps concurrency at `DATA_PARALLELISM`, default 4).
- **Do not raise for business failures**: return `{"error": "..."}` so the agent can rephrase or record a gap.

If you would rather use Databricks' own managed MCP server for Genie, wrap it as a custom backend: the hub is what captures every result in the evidence ledger,
which the report studio's number audit depends on.

## Tell the agents about your data

Everything that describes *your* data lives in one file, not in the prompts:

```powershell
Copy-Item config\data_profile.example.json config\data_profile.json   # then edit it
python -m agents.deploy_agents --check                                  # renders every prompt; fails on leftover placeholders
python -m agents.deploy_agents                                          # deploys the agents
```

`data_profile.json` holds the date coverage, fiscal year, zones, channels, example categories and the planning facts (which targets exist at which grain, what is
Stores-only, how weeks and year-on-year comparisons work). The planner and CX prompts are rendered from it, so reports are planned around data that exists.

Also worth reviewing for your data: `agents/prompts/data.md` (how the Data agent phrases questions), `hub/studio/playbook.md` (report types and what the data space can
answer) and the starter prompts in `ui/frontend/src/components/Chat.tsx`.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Every answer says "No data source is connected yet" | `GENIE_BACKEND` is still `stub`. Check `/healthz` and `infra/config.ps1`. |
| "Could not get a Databricks token" | The hub's identity is not set up for Azure Databricks (managed identity not added to the workspace), or `AuthMode = "token"` with no token. |
| Genie returns 403 or "not found" | The identity lacks CAN RUN on the space, CAN USE on the warehouse, or SELECT on the tables. |
| "Genie could not answer within 90 seconds" | The warehouse is starting or a query is heavy. Retry; consider a larger or serverless warehouse. A burst of parallel questions can queue on a small warehouse. |
| Reports have tables with missing columns | The backend returns different column names than the report team asked for. Return the names the question requested, or normalise them in your backend. |
| Rows are cut off | `GENIE_MAX_ROWS` (default 200) caps a result. Raise it on the hub, or ask for top-N. |
