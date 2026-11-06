<#
.SYNOPSIS
  Deploys the APIM gateways (front, tool, data) and the containerised chat API. Safe to re-run: it ships whatever changed.

.DESCRIPTION
  1. creates the resource group (default rg-croma-v2)
  2. builds the chat image (ui/Dockerfile) in ACR and creates or updates the chat container app (default croma-chat-v2) with a managed identity that
     may call Foundry, and generates the shared gateway key the chat API trusts
  3. deploys infra/apim/main.bicep: Log Analytics, Application Insights, the API Management instance, named values, backends, the three APIs with their
     policies, the products and the subscriptions
  4. reads the subscription keys from APIM and writes them, with the gateway URLs, to the git-ignored .apim.env (used by test_gateway.ps1 and
     create_hub_connection.ps1)
  Reads infra/config.ps1 (the Azure environment) and .env (HUB_URL, HUB_API_KEY). Secrets are never printed and never written to a tracked file.
  Does not touch the hub container app or any agent, connection or memory store.

.EXAMPLE
  .\infra\apim\deploy_apim.ps1                                   # subscription-key access only
  .\infra\apim\deploy_apim.ps1 -EnableJwt                        # also accept Entra tokens for the app registration in .apim.env (create_entra_app.ps1)
  .\infra\apim\deploy_apim.ps1 -SkipChat                         # change only the gateways
  .\infra\apim\deploy_apim.ps1 -WhatIf                           # show what the Bicep deployment would change
  .\infra\apim\deploy_apim.ps1 -GrantDatabricks -WarehouseId <id> -Catalog <catalog>   # also give APIM's identity access to the Genie space
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup,                 # default rg-croma-v2 (or $Cfg.Apim.ResourceGroup)
    [string]$ApimName,                      # default apim-<NamePrefix> (or $Cfg.Apim.Name); globally unique
    [string]$Location,                      # default: the region of the Container Apps environment
    [ValidateSet("BasicV2", "StandardV2")][string]$Sku = "BasicV2",
    [string]$ChatApp,                       # default croma-chat-v2
    [switch]$SkipChat,                      # do not build or change the chat container app
    [switch]$SkipBuild,                     # reuse the image the chat container app already runs (or -Image)
    [string]$Image,
    [switch]$SkipGateway,                   # do not run the Bicep deployment
    [switch]$UpdatePlatform,                # also re-apply the APIM instance itself (SKU, capacity); slow, and not needed to change policies or limits
    [switch]$WhatIf,                        # Bicep what-if only; changes nothing
    [switch]$EnableJwt,                     # accept Entra ID access tokens on the front gateway (needs an audience, see -EntraAudience)
    [string]$EntraTenantId,                 # default: the tenant of the signed-in az account
    [string]$EntraAudience,                 # default: api://<ENTRA_CLIENT_ID from .apim.env>
    [string]$EntraAudienceAlt,              # default: <ENTRA_CLIENT_ID from .apim.env>
    [string]$EntraClientId,                 # built into the web app as VITE_ENTRA_CLIENT_ID (default: ENTRA_CLIENT_ID from .apim.env when -EnableJwt)
    [string]$EntraApiScope,                 # built into the web app as VITE_API_SCOPE
    [string]$CorsOrigins = "",              # extra browser origins allowed to call the front API, comma separated
    [int]$FrontCallsPerMinute = 120,        # per user, whole front API
    [int]$ChatTurnsPerMinute = 10,          # per user, POST /api/chat
    [int]$ChatTurnsPerDay = 0,              # per user, 0 = no daily quota
    [int]$ToolsCallsPerMinute = 600,        # per subscription
    [int]$DataCallsPerMinute = 600,         # per subscription, Genie calls including status polls
    [int]$DataQuestionsPerMinute = 30,      # per subscription, new Genie questions
    [switch]$DisableStreamCheck,            # do not serve the synthetic /api/stream-check stream used by test_gateway.ps1
    [switch]$ShareHubEvents,                # show the hub's live event log to every user (only sensible for one user at a time, see docs/APIM.md)
    [switch]$GrantDatabricks,               # give APIM's managed identity access to the Genie space (needs a Databricks workspace admin sign-in)
    [string]$WarehouseId,                   # for -GrantDatabricks: the SQL warehouse the Genie space uses
    [string]$Catalog,                       # for -GrantDatabricks: the Unity Catalog catalog the Genie space reads
    [string]$Python                         # for -GrantDatabricks: a python with httpx and azure-identity (default: .venv, else python)
)
$ErrorActionPreference = "Continue"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$root\infra\config.ps1")) { throw "infra/config.ps1 is missing. Copy infra/config.example.ps1 to infra/config.ps1 and fill it in." }
if (-not (Test-Path "$root\.env")) { throw ".env is missing (HUB_URL and HUB_API_KEY: written by infra/deploy_hub.ps1)." }
. "$root\infra\config.ps1"

function Read-EnvFile([string]$path) {
    $map = [ordered]@{}
    if (Test-Path $path) {
        Get-Content $path | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $map[$Matches[1]] = $Matches[2] } }
    }
    return $map
}
function Invoke-Az {
    # Runs az and throws when it fails. Returns stdout only; never echoes anything (some commands here carry secrets).
    $all = & az @args 2>&1
    $code = $LASTEXITCODE
    $errors = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() } | Where-Object { $_ -notmatch '^WARNING' })
    if ($code) { throw "az $(($args | Select-Object -First 3) -join ' ') failed: $(($errors -join ' ').Substring(0, [Math]::Min(500, ($errors -join ' ').Length)))" }
    return @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
}
function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}
function Grant-Role([string]$principal, [string]$role, [string]$scope) {
    # A new identity can take a little while to be visible to role assignment, so retry; "already exists" is fine.
    foreach ($try in 1..4) {
        $out = az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role $role --scope $scope -o none 2>&1
        if (-not $LASTEXITCODE -or "$out" -match "already exists") { return }
        Start-Sleep -Seconds 10
    }
    Write-Warning "Could not assign '$role' to $principal. Assign it by hand on the Foundry account."
}

$envFile = Read-EnvFile "$root\.env"
$apimEnvPath = "$root\.apim.env"
$apimEnv = Read-EnvFile $apimEnvPath
$a = if ($Cfg.Apim) { $Cfg.Apim } else { @{} }
$prefix = if ($Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma2" }
if (-not $ResourceGroup) { $ResourceGroup = if ($a.ResourceGroup) { $a.ResourceGroup } else { "rg-croma-v2" } }
if (-not $ApimName) { $ApimName = if ($a.Name) { $a.Name } else { "apim-$prefix" } }
if (-not $ChatApp) { $ChatApp = if ($a.ChatApp) { $a.ChatApp } else { "croma-chat-v2" } }
$hubUrl = $envFile["HUB_URL"].TrimEnd("/")
$hubKey = $envFile["HUB_API_KEY"]
if (-not $hubUrl -or -not $hubKey) { throw ".env must contain HUB_URL and HUB_API_KEY." }
$g = $Cfg.Genie
if (-not $g.DatabricksHost -or -not $g.SpaceId) { throw "infra/config.ps1: Genie.DatabricksHost and Genie.SpaceId are needed for the data gateway." }
# The data gateway's backend is the real Databricks workspace. Once the hub uses the gateway (Genie.AuthMode = "apim"), Genie.DatabricksHost is the gateway's own URL,
# so the workspace URL is given separately in Apim.DatabricksUrl. Without it, the gateway would be pointed at itself and every Genie call would fail with a 401.
$databricksUrl = if ($a -and $a.DatabricksUrl) { $a.DatabricksUrl } else { $g.DatabricksHost }
$databricksUrl = $databricksUrl.TrimEnd("/")
if ($databricksUrl -match '\.azure-api\.net') { throw "The Databricks URL for the data gateway is an API Management address ($databricksUrl): the gateway would call itself. Set `$Cfg.Apim.DatabricksUrl in infra/config.ps1 to the workspace URL (https://adb-<workspace-id>.<n>.azuredatabricks.net)." }

az account set --subscription $Cfg.Subscription
$tenantId = Invoke-Az account show --query tenantId -o tsv
$me = Invoke-Az account show --query user.name -o tsv
Write-Host "Subscription: $(Invoke-Az account show --query name -o tsv)   resource group: $ResourceGroup   APIM: $ApimName ($Sku)   chat app: $ChatApp"

if (-not $Location) {
    $Location = if ($a.Location) { $a.Location } else { ((Invoke-Az containerapp env show -n $Cfg.Environment -g $Cfg.ResourceGroup --query location -o tsv) -replace "\s", "").ToLower() }
}

# ---------------------------------------------------------------- 1. resource group
if (-not $WhatIf) {
    Invoke-Az group create -n $ResourceGroup -l $Location --tags project=croma-agentic-pricing version=v2 purpose=apim-gateways -o none | Out-Null
}

# ---------------------------------------------------------------- 2. the chat container app
$chatFqdn = $null
$gatewayKey = $null
$chatExists = [bool](az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query name -o tsv 2>$null)
if ($chatExists) { $gatewayKey = az containerapp secret show -n $ChatApp -g $Cfg.ResourceGroup --secret-name gatewaykey --query value -o tsv 2>$null }
if (-not $gatewayKey) { $gatewayKey = New-Secret }

if (-not $SkipChat -and -not $WhatIf) {
    if ($EnableJwt -and -not $EntraClientId -and $apimEnv["ENTRA_CLIENT_ID"]) { $EntraClientId = $apimEnv["ENTRA_CLIENT_ID"] }
    if ($EntraClientId -and -not $EntraApiScope) { $EntraApiScope = "api://$EntraClientId/access_as_user" }
    if (-not $Image) {
        if ($SkipBuild) {
            if (-not $chatExists) { throw "-SkipBuild needs an existing chat container app or -Image." }
            $Image = az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query "properties.template.containers[0].image" -o tsv
        } else {
            $tag = Get-Date -Format "yyyyMMdd-HHmmss"
            $Image = "$($Cfg.Acr).azurecr.io/croma-chat-v2:$tag"
            Write-Host "Building $Image (the web app, then the backend)"
            $build = @("acr", "build", "-r", $Cfg.Acr, "-t", "croma-chat-v2:$tag", "-f", "$root\ui\Dockerfile", $root, "--no-logs", "-o", "none")
            if ($EntraClientId) {
                $build += @("--build-arg", "VITE_ENTRA_CLIENT_ID=$EntraClientId", "--build-arg", "VITE_ENTRA_TENANT_ID=$(if ($EntraTenantId) { $EntraTenantId } else { $tenantId })", "--build-arg", "VITE_API_SCOPE=$EntraApiScope")
            }
            Invoke-Az @build | Out-Null
        }
    }
    $prefixEnv = "NAME_PREFIX=$prefix"
    $settings = @("FOUNDRY_PROJECT_ENDPOINT=$($Cfg.ProjectEndpoint)", "HUB_URL=$hubUrl", "HUB_API_KEY=secretref:hubkey", $prefixEnv, "GATEWAY_KEY=secretref:gatewaykey", "SSE_HEARTBEAT_S=15")
    if (-not $DisableStreamCheck) { $settings += "ENABLE_STREAM_CHECK=1" }
    if ($ShareHubEvents) { $settings += "SHARE_HUB_EVENTS=1" }
    foreach ($name in "CX_AGENT", "MEMORY_STORE", "MEMORY_SCOPE", "AGENT_MODEL") {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $settings += "$name=$value" }
    }
    $secrets = @("hubkey=$hubKey", "gatewaykey=$gatewayKey")
    if (-not $chatExists) {
        Write-Host "Creating container app $ChatApp"
        Invoke-Az containerapp create -n $ChatApp -g $Cfg.ResourceGroup --environment $Cfg.Environment --image $Image `
            --system-assigned --registry-server "$($Cfg.Acr).azurecr.io" --registry-identity system `
            --ingress external --target-port 8080 --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1Gi `
            --secrets @secrets --env-vars @settings -o none | Out-Null
    } else {
        Write-Host "Updating container app $ChatApp"
        Invoke-Az containerapp secret set -n $ChatApp -g $Cfg.ResourceGroup --secrets @secrets -o none | Out-Null
        Invoke-Az containerapp update -n $ChatApp -g $Cfg.ResourceGroup --image $Image --set-env-vars @settings -o none | Out-Null
    }
    # The chat app's own identity calls Foundry (the CX agent, memory): the same roles the hub has. No keys.
    $principal = Invoke-Az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query identity.principalId -o tsv
    foreach ($role in "Cognitive Services OpenAI User", "Foundry User") { Grant-Role $principal $role $Cfg.FoundryScope }
}
$chatFqdn = az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query properties.configuration.ingress.fqdn -o tsv 2>$null
if (-not $chatFqdn) {
    # Not created yet (a what-if before the first deployment): the address is predictable from the environment's domain.
    $chatFqdn = "$ChatApp.$(az containerapp env show -n $Cfg.Environment -g $Cfg.ResourceGroup --query properties.defaultDomain -o tsv 2>$null)"
}
$chatUrl = "https://$chatFqdn"
Write-Host "Chat API: $chatUrl"

# ---------------------------------------------------------------- 3. APIM, named values, backends, APIs, policies, products, subscriptions
if ($SkipGateway) { Write-Host "Skipping the gateway deployment (-SkipGateway)."; return }

if ($EnableJwt) {
    if (-not $EntraAudience -and $apimEnv["ENTRA_CLIENT_ID"]) { $EntraAudience = "api://$($apimEnv['ENTRA_CLIENT_ID'])"; if (-not $EntraAudienceAlt) { $EntraAudienceAlt = $apimEnv["ENTRA_CLIENT_ID"] } }
    if (-not $EntraAudience) { throw "-EnableJwt needs -EntraAudience (or an app registration recorded in .apim.env by create_entra_app.ps1)." }
    if (-not $EntraAudienceAlt) { $EntraAudienceAlt = $EntraAudience }
}
$apimExists = [bool](az apim show -n $ApimName -g $ResourceGroup --query name -o tsv 2>$null)
$deployEnv = @{
    APIM_PREFIX = $prefix; APIM_LOCATION = $Location; APIM_NAME = $ApimName; APIM_SKU = $Sku; APIM_PUBLISHER_EMAIL = $me
    APIM_CHAT_BACKEND_URL = $chatUrl; APIM_HUB_BACKEND_URL = $hubUrl; APIM_DATABRICKS_URL = $databricksUrl; APIM_GENIE_SPACE_ID = $g.SpaceId
    APIM_HUB_API_KEY = $hubKey; APIM_GATEWAY_KEY = $gatewayKey
    APIM_JWT_ENABLED = "$([bool]$EnableJwt)".ToLower(); APIM_ENTRA_TENANT_ID = $(if ($EntraTenantId) { $EntraTenantId } else { $tenantId })
    APIM_ENTRA_AUDIENCE = $(if ($EntraAudience) { $EntraAudience } else { "api://not-configured" }); APIM_ENTRA_AUDIENCE_ALT = $(if ($EntraAudienceAlt) { $EntraAudienceAlt } else { "api://not-configured" })
    APIM_CORS_ORIGINS = $CorsOrigins
    APIM_FRONT_CALLS_PER_MINUTE = "$FrontCallsPerMinute"; APIM_CHAT_TURNS_PER_MINUTE = "$ChatTurnsPerMinute"; APIM_CHAT_TURNS_PER_DAY = "$ChatTurnsPerDay"
    APIM_TOOLS_CALLS_PER_MINUTE = "$ToolsCallsPerMinute"; APIM_DATA_CALLS_PER_MINUTE = "$DataCallsPerMinute"; APIM_DATA_QUESTIONS_PER_MINUTE = "$DataQuestionsPerMinute"
    APIM_EXPOSE_STREAM_CHECK = "$(-not $DisableStreamCheck)".ToLower()
    APIM_DEPLOY_PLATFORM = "$($UpdatePlatform -or -not $apimExists)".ToLower()
}
foreach ($k in $deployEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $deployEnv[$k], "Process") }   # this process only; main.bicepparam reads them

$bicep = "$PSScriptRoot\main.bicep"
if ($WhatIf) {
    if ((az group exists -n $ResourceGroup) -ne "true") { Write-Host "Resource group $ResourceGroup does not exist yet: run the deployment once, then -WhatIf shows later changes."; return }
    Write-Host "What-if (nothing is changed):"
    az deployment group what-if -g $ResourceGroup -p "$PSScriptRoot\main.bicepparam" --result-format ResourceIdOnly 2>$null
    return
}
Write-Host "Deploying APIM (the first run provisions the instance: a few minutes)"
$outputs = az deployment group create -g $ResourceGroup -n apim-main -p "$PSScriptRoot\main.bicepparam" --query properties.outputs -o json 2>&1
if ($LASTEXITCODE) {
    # A network blip on this machine can break az's progress polling while the deployment carries on in Azure, so ask Azure for the real outcome.
    $why = (($outputs | Where-Object { $_ -notmatch '^WARNING' }) -join ' ')
    Write-Warning "az lost track of the deployment ($($why.Substring(0, [Math]::Min(120, $why.Length)))...); checking its state in Azure."
    $state = ""
    foreach ($try in 1..60) {
        $state = az deployment group show -g $ResourceGroup -n apim-main --query properties.provisioningState -o tsv 2>$null
        if ($state -in "Succeeded", "Failed", "Canceled") { break }
        Start-Sleep -Seconds 10
    }
    if ($state -ne "Succeeded") { throw "Bicep deployment ended as '$state'. See: az deployment operation group list -g $ResourceGroup -n apim-main" }
    $outputs = az deployment group show -g $ResourceGroup -n apim-main --query properties.outputs -o json 2>$null
}
$o = ($outputs | Where-Object { $_ -notmatch '^WARNING' }) -join "`n" | ConvertFrom-Json
foreach ($k in $deployEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $null, "Process") }

# ---------------------------------------------------------------- 4. subscription keys, URLs and .apim.env
$sid = $Cfg.Subscription
$base = "https://management.azure.com/subscriptions/$sid/resourceGroups/$ResourceGroup/providers/Microsoft.ApiManagement/service/$ApimName"
function Get-SubscriptionKey([string]$name) {
    foreach ($try in 1..3) {
        $key = az rest --method POST --url "$base/subscriptions/$name/listSecrets?api-version=2024-05-01" --query primaryKey -o tsv 2>$null
        if ($key) { return $key }
        Start-Sleep -Seconds 5
    }
    throw "Could not read the key of APIM subscription '$name'."
}
$apimPrincipal = $o.apimPrincipalId.value
$apimAppId = az ad sp show --id $apimPrincipal --query appId -o tsv 2>$null
$new = [ordered]@{
    APIM_NAME = $ApimName; APIM_RESOURCE_GROUP = $ResourceGroup
    APIM_GATEWAY_URL = $o.gatewayUrl.value; APIM_FRONT_URL = $o.frontUrl.value; APIM_TOOLS_URL = $o.toolsUrl.value; APIM_DATA_URL = $o.dataUrl.value
    APIM_FRONT_KEY = (Get-SubscriptionKey $o.subscriptionNames.value.front)
    APIM_TOOLS_KEY = (Get-SubscriptionKey $o.subscriptionNames.value.tools)
    APIM_DATA_KEY = (Get-SubscriptionKey $o.subscriptionNames.value.data)
    APIM_PRINCIPAL_ID = $apimPrincipal; APIM_APPLICATION_ID = $apimAppId
    CHAT_APP = $ChatApp; CHAT_APP_URL = $chatUrl; CHAT_GATEWAY_KEY = $gatewayKey
    JWT_ENABLED = "$([bool]$EnableJwt)".ToLower()
    LIMIT_CHAT_TURNS_PER_MINUTE = "$ChatTurnsPerMinute"; LIMIT_DATA_QUESTIONS_PER_MINUTE = "$DataQuestionsPerMinute"
}
foreach ($k in $new.Keys) { $apimEnv[$k] = $new[$k] }
Set-Content -Path $apimEnvPath -Value (($apimEnv.Keys | ForEach-Object { "$_=$($apimEnv[$_])" }) -join "`n") -Encoding ascii
Write-Host ""
Write-Host "Front gateway : $($o.frontUrl.value)   (the web app and /api)"
Write-Host "Tool gateway  : $($o.toolsUrl.value)/genie/mcp  /agents/mcp  /context/mcp  /studio/mcp"
Write-Host "Data gateway  : $($o.dataUrl.value)   (the hub's DATABRICKS_HOST in DATABRICKS_AUTH=apim mode)"
Write-Host "Keys and URLs : $apimEnvPath  (git-ignored, secret)"
Write-Host "APIM identity : application id $apimAppId (give it access to the Genie space: -GrantDatabricks)"

# ---------------------------------------------------------------- 5. the data gateway's Databricks access
if ($GrantDatabricks) {
    if (-not $WarehouseId -or -not $Catalog) { throw "-GrantDatabricks needs -WarehouseId and -Catalog." }
    if (-not $Python) { $Python = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" } }
    & $Python "$root\integrations\genie\grant_hub_access.py" --application-id $apimAppId --host $databricksUrl --warehouse-id $WarehouseId --space-id $g.SpaceId --catalog $Catalog --name $ApimName
    if ($LASTEXITCODE) { throw "grant_hub_access.py failed" }
}
Write-Host "Done. Check it with: .\infra\apim\test_gateway.ps1"
