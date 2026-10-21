# Deployment runbook: Croma Azure tenant, from a laptop

This takes you from an empty Azure subscription to a working system: the hub running in Azure Container Apps, the agents deployed in Microsoft Foundry, your data connected,
and the chat UI running on your laptop. Allow **half a day** the first time, mostly waiting on resource creation, role propagation and image builds.

Everything is run from a laptop with PowerShell and Python. Nothing is installed on a server except the hub container, which the scripts build and deploy for you.

```
 Laptop (az login)                       Azure (Croma tenant)
 ------------------                      -----------------------------------------------
 infra\deploy_hub.ps1   --builds-->      Azure Container Registry  --> Container App "croma-tools-hub"  (the hub)
 agents.deploy_agents   --creates-->     Microsoft Foundry project: 11 agents + memory store, calling the hub as MCP tools
 ui\run.ps1  (localhost:8000)  <------>  Foundry agents (CX), hub, and your data layer (Databricks Genie or your own backend)
```

## 0. Before you start

### 0.1 What you need

| Need | Detail |
|---|---|
| Azure subscription in the Croma tenant | You must be able to create resources and **assign roles** in one resource group: **Owner** on the resource group, or Contributor plus User Access Administrator. The deploy script assigns roles to the hub's identity. |
| Foundry data-plane access | The role **Azure AI User** (called **Foundry User** in some tenants) on the Foundry account or project, for whoever runs `deploy_agents` and the UI. Role assignments take a few minutes to take effect. |
| Laptop tools | **Windows PowerShell 5.1 or PowerShell 7**, **Python 3.11 or newer**, **Azure CLI 2.60+** with the `containerapp` extension, **git**. **Node 20** only if you want to build the chat UI. |
| Network | The laptop must reach `management.azure.com`, `*.azurecr.io`, `*.services.ai.azure.com`, `*.azurecontainerapps.io`. The **hub must be reachable from Microsoft Foundry**: the scripts deploy it with public ingress, protected by an API key. If Croma policy forbids public ingress, stop and read [Private networking](#private-networking-a-decision-to-make-early). |
| A data source | A Databricks Genie space (and rights to grant access to it), or your own data agent. Optional for the first run: the default `stub` backend lets you deploy and explore everything else. |

### 0.2 Decide these and write them down

You will paste these into `infra/config.ps1` later.

| Value | Example | Notes |
|---|---|---|
| Tenant id | `<guid>` | `az account show --query tenantId` |
| Subscription id | `<guid>` | |
| Region | `eastus2` | Pick one where your model deployments are available. Keep everything in the same region. |
| Resource group | `rg-croma-agentic` | New, dedicated. |
| Foundry account / project | `croma-foundry` / `croma-pricing` | Account names are global: choose a unique one. |
| ACR name | `acrcromaagentic` | 5 to 50 lowercase letters or digits, globally unique. |
| Container Apps environment | `cae-croma-agentic` | |

### 0.3 Check which models you can deploy

The system uses three kinds of model. Names below are the defaults in the code; use whatever your tenant offers and tell the deploy step (section 4).

| Role | Default deployment name | What it is for | Quota to ask for |
|---|---|---|---|
| Conversational | `gpt-5.4-mini` | CX, Lookup, Analysis, Research, Data agents | 150K tokens per minute or more |
| Deep reasoning | `gpt-6-sol` | The report team (editor, planner, insight, strategist, writer, reviewer), at high reasoning effort | **500K tokens per minute or more.** This is the main limit on report speed. |
| Embedding | `text-embedding-3-large` | Foundry memory (the user's saved preferences) | 100K or more |

```powershell
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
az cognitiveservices model list -l <region> --query "[?kind=='OpenAI'].{name:model.name, version:model.version, sku:model.skus[0].name}" -o table
az cognitiveservices usage list -l <region> -o table      # shows your quota per model
```

If your tenant has no model called `gpt-6-sol`, pick the strongest reasoning model it does have and pass its **deployment name** as `REPORT_MODEL` in step 5. The report team needs a model that accepts a reasoning effort of `high`.

## 1. Prepare the laptop

```powershell
git clone https://github.com/arora-ai/croma-agentic-pricing.git      # a private repo: you need to be added as a collaborator first
cd croma-agentic-pricing

python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements-dev.txt

az extension add --name containerapp --upgrade
az login --tenant <tenant-id>
az account set --subscription <subscription-id>
az account show --query "{tenant:tenantId, subscription:name}" -o table
```

If PowerShell refuses to run the scripts ("running scripts is disabled"), allow them for this window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**Checkpoint:** run the unit tests. They need no Azure access.

```powershell
.\.venv\Scripts\python -m pytest tests -q          # expect: 22 passed
```

## 2. Create the Azure resources (once)

If Croma already has a Foundry project, a registry and a Container Apps environment you may reuse them: skip to section 3 and use their names in the config.

### 2.1 Resource providers, resource group, registry, Container Apps environment

```powershell
$rg = "<resource group>"; $loc = "<region>"
foreach ($p in "Microsoft.App","Microsoft.ContainerRegistry","Microsoft.OperationalInsights","Microsoft.CognitiveServices") { az provider register -n $p --wait }

az group create -n $rg -l $loc
az acr create -n <acr name> -g $rg --sku Basic                       # Basic is enough; it must allow `az acr build` (ACR Tasks)
az containerapp env create -n <environment name> -g $rg -l $loc       # creates a Log Analytics workspace for you
```

### 2.2 The Foundry account and project (portal, simplest)

1. Open **https://ai.azure.com** and sign in to the Croma tenant.
2. **Create new** project. Choose the subscription, the **resource group from 2.1**, the **region**, a unique **Foundry resource name** and the **project name** from your table.
3. Wait for it to finish. In the project's overview, note the **project endpoint**. It looks like `https://<account>.services.ai.azure.com/api/projects/<project>`.

Then make sure the account has a **system-assigned managed identity** (the memory feature needs it): Azure portal, the Foundry resource, Identity, System assigned, On.

### 2.3 Deploy the three models

Use the portal (Foundry project, Models + endpoints, Deploy model) or the CLI. Use **exactly the deployment names** you will configure. The version comes from the list in 0.3.

```powershell
$acct = "<foundry account name>"
az cognitiveservices account deployment create -g $rg -n $acct --deployment-name gpt-5.4-mini --model-name gpt-5.4-mini --model-version <version> --model-format OpenAI --sku-name GlobalStandard --sku-capacity 150
az cognitiveservices account deployment create -g $rg -n $acct --deployment-name gpt-6-sol --model-name gpt-6-sol --model-version <version> --model-format OpenAI --sku-name GlobalStandard --sku-capacity 500
az cognitiveservices account deployment create -g $rg -n $acct --deployment-name text-embedding-3-large --model-name text-embedding-3-large --model-version 1 --model-format OpenAI --sku-name GlobalStandard --sku-capacity 100
az cognitiveservices account deployment list -g $rg -n $acct --query "[].{name:name, model:properties.model.name, capacity:sku.capacity}" -o table
```

`--sku-capacity` is in thousands of tokens per minute. If a request is rejected for quota, request more in the portal (Quotas) or lower the number; you can raise it later.

### 2.4 Give yourself Foundry access

```powershell
$me = az ad signed-in-user show --query id -o tsv
$scope = az cognitiveservices account show -g $rg -n $acct --query id -o tsv
az role assignment create --assignee-object-id $me --assignee-principal-type User --role "Azure AI User" --scope $scope
```

If the role name is not found, list the available ones with `az role definition list --query "[?contains(roleName,'AI')].roleName" -o tsv` and pick the Foundry data-plane user role. Wait **5 to 10 minutes** before section 5.

**Checkpoint:** in https://ai.azure.com you can open the project and see the three deployments.

## 3. Configure the repository

```powershell
Copy-Item infra\config.example.ps1 infra\config.ps1
Copy-Item config\data_profile.example.json config\data_profile.json
notepad infra\config.ps1
notepad config\data_profile.json
```

### 3.1 `infra\config.ps1`

Fill the values from your table: `Subscription`, `ResourceGroup`, `Acr` (name only, no `.azurecr.io`), `Environment`, `Foundry`, `Project`. Leave `App` as `croma-tools-hub` and `NamePrefix` as `croma`. (To run a second copy of the system next to the first, for example to try a new version, give it a different `App` and `NamePrefix`, such as `croma-tools-hub-v2` and `croma2`: every agent, connection and memory store then carries the prefix. See [V2.md](V2.md#5-running-v1-and-v2-side-by-side).)

For the first deployment keep `$Cfg.Genie.Backend = "stub"`. You connect data in section 6, after you have confirmed everything else works.

### 3.2 `config\data_profile.json`: describe Croma's data to the agents

This file is what turns the generic agents into Croma's. The agents treat it as ground truth and will not ask users for it. Replace every `<...>`:

- `coverage`: the first and last date in the data, for example `from 1 April 2023 to 20 October 2026`.
- `fiscal_year`: for example `April to March`.
- `zones`, `channels`, `categories`: the names the business uses.
- `standing_scope`: the filters every data question must carry unless the user asks otherwise, in one sentence, for example `Merchandise articles only, with the Miscellaneous and Charges groups excluded`. The data space does not always apply a standing filter unless it is told, so the Scoper repeats this sentence in every question and the Data agent checks the SQL carries it.
- `facts`: one line each, stating what the data can and cannot do. Typical ones: which target types exist (AOP, SOP, mission) and at what grain; which are comparable with which actuals (for example zone targets cover Stores only); which measures exist only for part of the business (margin and markdown for Stores only); what is always excluded; how weeks and year-on-year comparisons are aligned. **State how each ratio is defined**: which table and which rows give the numerator and the denominator of margin %, markdown % and average price. The analysis harness decomposes these ratios and checks the result against the data space's own figure, so a wrong basis is caught only if the profile says what the right one is.

Then validate it. This renders every agent prompt and fails with a clear message if anything is unfilled:

```powershell
.\.venv\Scripts\python -m agents.deploy_agents --check        # expect: OK: 14 agents render from config/data_profile.json
```

## 4. Deploy the hub

```powershell
.\infra\deploy_hub.ps1
```

This takes **5 to 10 minutes**. It builds the container image in your registry, creates the container app with a managed identity, generates a random **hub API key** (stored as a container app secret), gives the hub's identity the roles it needs on the Foundry account, gives the Foundry account's own identity access to its models (for memory), writes `.env`, and creates four **project connections** in Foundry that let the agents call the hub with the key, so the key never appears in an agent definition.

**Checkpoint:**

```powershell
Get-Content .env | ForEach-Object { ($_ -split "=",2)[0] }          # HUB_URL, HUB_API_KEY, FOUNDRY_PROJECT_ENDPOINT
$hub = (Get-Content .env | Select-String "^HUB_URL=").ToString().Split("=",2)[1]
Invoke-RestMethod "$hub/healthz"                                      # expect: status ok, genie_backend stub
```

`.env` now contains a secret. It is git-ignored: never commit it or send it around.

## 5. Deploy the agents

Wait until the role assignments from 2.4 and step 4 have propagated (about 5 to 10 minutes after they were made). Then:

```powershell
# Only if your deployment names differ from the defaults. These are read from the shell, not from .env.
$env:AGENT_MODEL  = "<conversational deployment name>"       # default gpt-5.4-mini
$env:REPORT_MODEL = "<deep reasoning deployment name>"       # default gpt-6-sol
$env:EMBEDDING_MODEL = "<embedding deployment name>"         # default text-embedding-3-large

.\.venv\Scripts\python -m agents.deploy_agents
```

Expect: `Memory store: croma-user-memory`, then fourteen lines such as `croma-cx  version 1  tools: [...]` (the Scoper, Analyst and Critic have no tools: they are the workers of the Analysis and Research harnesses). Each run creates a new **version** of each agent, so you can safely re-run it after changing a prompt or the data profile. If your `NamePrefix` is not `croma`, set `$env:NAME_PREFIX` to the same value in this shell first.

If you see **401 or 403**, the Foundry role has not propagated yet (or was not granted to you in 2.4). Wait a few minutes and re-run.

**Checkpoint:** talk to the CX agent. With the `stub` backend it cannot fetch data, but it should converse and ask clarifying questions.

```powershell
.\.venv\Scripts\python chat.py "Why did merchandise margin % for Stores move last month versus the month before? Show the biggest category drivers."
```

Expect a friendly reply that restates the question and asks two or three clarifying questions with suggested options. That confirms the agents, the Foundry connection and the CX behaviour.

## 6. Connect Croma's data

Follow **[`integrations/genie/README.md`](../integrations/genie/README.md)** for the details. In outline:

1. **Grant the hub's identity access in Databricks.** The hub's identity is the container app's managed identity. A Databricks workspace admin adds it as a service principal and grants **CAN USE** on the SQL warehouse, **CAN RUN** on the Genie space, and `USE CATALOG`, `USE SCHEMA`, `SELECT` on the data. `integrations/genie/grant_hub_access.py` does exactly this, signed in as that admin (`az login`):
   ```powershell
   .\.venv\Scripts\python integrations\genie\grant_hub_access.py --host https://adb-<workspace-id>.<n>.azuredatabricks.net --warehouse-id <id> --space-id <id> --catalog <catalog> --hub-app croma-tools-hub --resource-group <rg>
   ```
   Run it again whenever the hub is recreated (a new container app has a new identity).
2. **Set the Genie block** in `infra\config.ps1`:
   ```powershell
   $Cfg.Genie = @{
       Backend = "databricks"
       DatabricksHost = "https://adb-<workspace-id>.<n>.azuredatabricks.net"
       SpaceId = "<genie space id>"
       AuthMode = "managed_identity"
       ToolDescription = "<one or two sentences on what the data covers>"
   }
   ```
3. **Redeploy the hub** (`.\infra\deploy_hub.ps1`) and check the connection before involving any agent:
   ```powershell
   .\.venv\Scripts\python integrations\genie\try_question.py "How many stores are there in each zone?"
   ```
   You should see the SQL Genie ran and a few rows. **Note:** every hub redeploy clears in-memory case state; do it before testing reports, not in the middle of one.
4. Re-run `.\.venv\Scripts\python -m agents.deploy_agents` if you changed `data_profile.json`.

For a quick test before the identity is set up, `AuthMode = "token"` with `$env:DATABRICKS_TOKEN` (a personal access token) set when you run `deploy_hub.ps1` works too. It acts as one person and expires, so use it only to prove the plumbing.

## 7. Run the chat UI on the laptop

```powershell
.\ui\run.ps1                 # builds the front end on first run (needs Node 20), then serves http://localhost:8000
```

The UI signs in to Foundry as **you** (your `az login`), so you need the Foundry role from 2.4. Try, in order:

1. A plain lookup: "How many sales did we do last month?" Expect a figure and the basis used, in under a minute.
2. The margin question from step 5. Expect clarifying questions, then reply "go with your suggestions" and expect an analysis.
3. The **admin pane** on the right: the agent network lights up as agents work; the Activity tab shows each call, the SQL and the rows.
4. A report: "Create a standard weekly performance report for the CEO office for the latest full week versus the same week last year, enterprise level, with recommended actions." Expect a progress stepper, and eventually a report card with **Open report** and **Download Word**.

**Report timing:** a full report currently takes **30 minutes or more**, mostly the deep analysis stage, and the report model's token quota is the main limit. Raising that quota shortens it noticeably. Treat the first report as a test of the plumbing, and run it when nobody else is using the same model deployment.

## 8. Day-2 operations

| Task | How |
|---|---|
| Change a prompt or the data profile | Edit, then `python -m agents.deploy_agents` (new agent versions, no downtime) |
| Ship hub code changes | `.\infra\deploy_hub.ps1` (clears in-memory cases; a few minutes) |
| Watch live activity | `python tests\watch.py`; or Container App log stream: `az containerapp logs show -n croma-tools-hub -g <rg> --follow` |
| Look at a published report | In the UI, or `python tests\fetch_report.py <case_id> 1` |
| Rotate the hub API key | `az containerapp secret set -n croma-tools-hub -g <rg> --secrets hubkey=<new key>`, then restart the active revision, put the same new key in `HUB_API_KEY` in `.env`, and re-run `.\infra\create_hub_connection.ps1` so the Foundry connections carry it |
| Remove everything | `az group delete -n <rg>` deletes all resources created in section 2 |

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `deploy_hub.ps1`: "infra/config.ps1 is missing" | Copy `infra\config.example.ps1` to `infra\config.ps1` and fill it in. |
| Image build fails: "ACR tasks not permitted" | The registry SKU or a policy blocks `az acr build`. Use Basic or higher, or build and push the image another way and set the container app image by hand. |
| Container app stays unhealthy, image pull error | The app's identity needs the **AcrPull** role on the registry: `az role assignment create --assignee <hub principal id> --role AcrPull --scope <acr id>`, then redeploy. |
| `deploy_agents`: 401 / 403 / "PermissionDenied" | The Foundry data-plane role is missing or has not propagated. Check 2.4, wait 5 to 10 minutes. |
| `deploy_agents`: "Data profile problem" | `config\data_profile.json` is missing or still has `<...>` placeholders. |
| Memory store creation fails | The embedding deployment name does not match `EMBEDDING_MODEL`, or the Foundry account has no managed identity (2.2). |
| An agent says a tool is unreachable or returns 401 | The project connections point at an old hub URL or key. Re-run `infra\create_hub_connection.ps1` after `.env` is correct. |
| "Genie could not answer within 90 seconds" | The warehouse is starting or undersized. Retry, or use a larger or serverless warehouse. |
| Reports fail with a rate limit message | The model deployment's token quota is exhausted. Raise the quota, run one report at a time, or lower reasoning effort for some roles (`REPORT_EFFORT_<ROLE>`). |
| A report call "times out" after 30 to 60 minutes | The agent timeout is `AGENT_CALL_TIMEOUT_S` on the hub (default 3600 seconds). Raise it in `deploy_hub.ps1` if reports legitimately take longer. |
| Every data answer says "No data source is connected yet" | The hub is still on the `stub` backend. Finish section 6. |
| `deploy_hub.ps1` reports "image build failed" (or hangs) after ten minutes although the build succeeded | The Azure CLI lost its connection while waiting for the registry build. Check `az acr task list-runs -r <acr> --top 3`; if the latest run succeeded, point the app at it: `az containerapp update -n <app> -g <rg> --image <acr>.azurecr.io/<app>:<tag>`. |
| `deploy_agents` or the chat UI: "Failed to invoke the Azure CLI" | The Azure CLI took longer than the credential's patience to hand out a token (a busy laptop). The code waits up to 90 seconds; run `az account show` to confirm you are signed in, then retry. |
| An analysis or research answer says "left out because its figures could not be traced" | The Analyst quoted a figure that is not in the evidence twice. The run's stages (`GET /runs/<case>?data=1`) show what it wrote. Usually a sign the question is outside what the plan computed: ask it more narrowly. |
| The analysis headline differs from the figure the business reports | The data space computed the ratio on a different basis than the business uses. Look at the "Checks passed" and "Caveats" lines of the brief (a failed reconcile says so), and describe the ratio's definition in `data_profile.json` `facts` and `standing_scope`. |

## Private networking: a decision to make early

Foundry calls the hub's tools from Microsoft's service, so the hub is deployed with a **public HTTPS endpoint protected by an API key**. If Croma's policy requires private endpoints only, this needs a different design (the Foundry project and the hub in a virtual network with Foundry's network-injection option, private DNS, and a private Container Apps environment). That is not covered by these scripts: raise it with Croma's cloud team before starting, because it changes sections 2 and 4.

## What this runbook has and has not proven

The Python code, the unit tests, the UI build and the prompt rendering are verified. The repository's PowerShell deployment path (`deploy_hub.ps1`, `create_hub_connection.ps1`, `deploy_agents`) has now been run end to end for a second, side-by-side copy of the system in the prototype subscription (see [V2.md](V2.md)), but it has **not yet been run in a fresh Croma subscription**: expect small differences (names, role names, regional model availability) and treat the Troubleshooting table as the first place to look.
