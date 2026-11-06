# Builds the tools hub image in ACR, deploys it to the Container Apps environment next to the Foundry project, gives its managed identity
# access to Foundry agents, and creates the project connections the agents use to call it.
# Re-run to ship a new version. Writes ../.env with HUB_URL, HUB_API_KEY and FOUNDRY_PROJECT_ENDPOINT for deploy_agents.py and chat.py.
# Needs infra/config.ps1: copy infra/config.example.ps1 and fill it in.
$ErrorActionPreference = "Continue"
if (-not (Test-Path "$PSScriptRoot\config.ps1")) { throw "infra/config.ps1 is missing. Copy infra/config.example.ps1 to infra/config.ps1 and fill it in." }
. "$PSScriptRoot\config.ps1"
$root = Split-Path $PSScriptRoot -Parent
$app = $Cfg.App
$rg = $Cfg.ResourceGroup
$tag = (Get-Date -Format "yyyyMMdd-HHmmss")
az account set --subscription $Cfg.Subscription
Write-Host "Subscription: $(az account show --query name -o tsv)"

Write-Host "Building image $($Cfg.Acr)/$app`:$tag"
az acr build -r $Cfg.Acr -t "$app`:$tag" -f "$root\hub\Dockerfile" $root --no-logs -o none
if ($LASTEXITCODE) { throw "image build failed" }

$image = "$($Cfg.Acr).azurecr.io/$app`:$tag"
$g = $Cfg.Genie
$prefix = if ($Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma" }
$settings = @("FOUNDRY_PROJECT_ENDPOINT=$($Cfg.ProjectEndpoint)", "AGENT_CALL_TIMEOUT_S=3600", "GENIE_BACKEND=$($g.Backend)", "NAME_PREFIX=$prefix")
if ($g.DatabricksHost) { $settings += "DATABRICKS_HOST=$($g.DatabricksHost)" }
if ($g.SpaceId) { $settings += "GENIE_SPACE_ID=$($g.SpaceId)" }
if ($g.AuthMode) { $settings += "DATABRICKS_AUTH=$($g.AuthMode)" }
if ($g.CustomBackend) { $settings += "CUSTOM_BACKEND=$($g.CustomBackend)" }
if ($g.ToolDescription) { $settings += "GENIE_TOOL_DESCRIPTION=$($g.ToolDescription)" }
# SharePoint, the second data source (docs/SHAREPOINT.md). Off unless $Cfg.SharePoint.Backend is "graph" or "folder".
$sp = $Cfg.SharePoint
if ($sp -and $sp.Backend -and $sp.Backend -ne "off") {
    $settings += "SHAREPOINT_BACKEND=$($sp.Backend)"
    if ($sp.SiteUrl) { $settings += "SHAREPOINT_SITE_URL=$($sp.SiteUrl)" }
    if ($sp.Library) { $settings += "SHAREPOINT_LIBRARY=$($sp.Library)" }
    if ($sp.ToolDescription) { $settings += "SHAREPOINT_TOOL_DESCRIPTION=$($sp.ToolDescription)" }
} else {
    $settings += "SHAREPOINT_BACKEND=off"
}
$secrets = @()
if ($g.AuthMode -eq "token") {
    if (-not $env:DATABRICKS_TOKEN) { throw "Genie.AuthMode is 'token' but `$env:DATABRICKS_TOKEN is not set in this shell." }
    $secrets += "dbxtoken=$($env:DATABRICKS_TOKEN)"
    $settings += "DATABRICKS_TOKEN=secretref:dbxtoken"
} elseif ($g.AuthMode -eq "apim") {
    # The data gateway (infra/apim): DatabricksHost is its URL, and the hub sends only this subscription key.
    $apimKey = $env:APIM_SUBSCRIPTION_KEY
    if (-not $apimKey -and (Test-Path "$root\.apim.env")) { $apimKey = (Get-Content "$root\.apim.env" | Where-Object { $_ -match '^APIM_DATA_KEY=' } | ForEach-Object { $_.Split("=", 2)[1] }) }
    if (-not $apimKey) { throw "Genie.AuthMode is 'apim' but there is no data gateway subscription key: set `$env:APIM_SUBSCRIPTION_KEY or run infra/apim/deploy_apim.ps1 (APIM_DATA_KEY in .apim.env)." }
    $secrets += "apimkey=$apimKey"
    $settings += "APIM_SUBSCRIPTION_KEY=secretref:apimkey"
}
$exists = az containerapp show -n $app -g $rg --query name -o tsv 2>$null
if (-not $exists) {
    $key = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]]).Replace("+", "-").Replace("/", "_").TrimEnd("=")
    az containerapp create -n $app -g $rg --environment $Cfg.Environment --image $image `
        --system-assigned --registry-server "$($Cfg.Acr).azurecr.io" --registry-identity system `
        --ingress external --target-port 8080 --min-replicas 1 --max-replicas 1 --cpu 1 --memory 2Gi `
        --secrets "hubkey=$key" @secrets --env-vars "HUB_API_KEY=secretref:hubkey" @settings -o none
    if ($LASTEXITCODE) { throw "container app create failed" }
} else {
    if ($secrets) { az containerapp secret set -n $app -g $rg --secrets @secrets -o none }
    az containerapp update -n $app -g $rg --image $image --set-env-vars @settings -o none
    if ($LASTEXITCODE) { throw "container app update failed" }
}

# Managed identity of the hub: invoke Foundry agents (the agent bridge). No keys.
$principal = az containerapp show -n $app -g $rg --query identity.principalId -o tsv
foreach ($role in "Cognitive Services OpenAI User", "Foundry User") {
    az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal --role $role --scope $Cfg.FoundryScope -o none 2>$null
}

# Foundry memory calls the embedding deployment as the Foundry account's own identity (keys are disabled on this account), so that identity
# needs access to the account's models too.
$accountPrincipal = az cognitiveservices account show -g $rg -n $Cfg.Foundry --query identity.principalId -o tsv
if ($accountPrincipal) {
    az role assignment create --assignee-object-id $accountPrincipal --assignee-principal-type ServicePrincipal --role "Cognitive Services OpenAI User" --scope $Cfg.FoundryScope -o none 2>$null
}

$fqdn = az containerapp show -n $app -g $rg --query properties.configuration.ingress.fqdn -o tsv
$secret = az containerapp secret show -n $app -g $rg --secret-name hubkey --query value -o tsv
Set-Content -Path "$root\.env" -Value "HUB_URL=https://$fqdn`nHUB_API_KEY=$secret`nFOUNDRY_PROJECT_ENDPOINT=$($Cfg.ProjectEndpoint)" -Encoding utf8
Write-Host "Deployed: https://$fqdn  (settings written to .env)"
& "$PSScriptRoot\create_hub_connection.ps1"
