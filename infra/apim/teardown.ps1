<#
.SYNOPSIS
  Deletes everything infra/apim created. Needs -Yes, because it is destructive and API Management is billed by the hour until it is gone.

.DESCRIPTION
  Deletes, in this order:
    1. the chat container app ($ChatApp, default croma-chat-v2) in the Container Apps resource group, and the role assignments its identity held on Foundry
    2. the whole resource group $ResourceGroup (default rg-croma-v2): the API Management instance, Application Insights and Log Analytics
    3. the soft-deleted API Management instance, so its name can be used again (-NoPurge keeps it; Azure removes it itself after 48 hours)
    4. the Foundry project connections and agent made by the optional tool-gateway probe (<prefix>-apimprobe-*), if they exist
    5. the Entra app registrations recorded in .apim.env by create_entra_app.ps1
    6. .apim.env itself (it holds subscription keys that stop working anyway)
  It never touches the hub, the agents, the memory store, the v1 resources or the other connections. The one exception: with -RestoreDirect it first puts the
  live Foundry connections (<prefix>-hub-*) back to the hub (create_hub_connection.ps1 -Direct). It refuses to delete the gateway while those connections,
  or the hub's Databricks access (DATABRICKS_AUTH=apim), still go through it, because the agents would lose their tools.

.EXAMPLE
  .\infra\apim\teardown.ps1                # lists what would be deleted
  .\infra\apim\teardown.ps1 -Yes           # deletes it
  .\infra\apim\teardown.ps1 -Yes -RestoreDirect   # switches the live connections back to the hub, then deletes it
#>
[CmdletBinding()]
param(
    [switch]$Yes,
    [string]$ResourceGroup,
    [string]$ChatApp,
    [switch]$KeepChatApp,
    [switch]$KeepEntraApps,
    [switch]$NoPurge,
    [switch]$RestoreDirect,                 # put the live <prefix>-hub-* connections back to the hub before deleting
    [switch]$IgnoreLive                     # delete even if the live connections or the hub still go through the gateway (they will stop working)
)
$ErrorActionPreference = "Continue"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
. "$root\infra\config.ps1"
function Read-EnvFile([string]$path) {
    $map = [ordered]@{}
    if (Test-Path $path) { Get-Content $path | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $map[$Matches[1]] = $Matches[2] } } }
    return $map
}
$a = if ($Cfg.Apim) { $Cfg.Apim } else { @{} }
$apimEnv = Read-EnvFile "$root\.apim.env"
$prefix = if ($Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma2" }
if (-not $ResourceGroup) { $ResourceGroup = if ($a.ResourceGroup) { $a.ResourceGroup } else { "rg-croma-v2" } }
if (-not $ChatApp) { $ChatApp = if ($a.ChatApp) { $a.ChatApp } else { "croma-chat-v2" } }
$apimName = if ($apimEnv["APIM_NAME"]) { $apimEnv["APIM_NAME"] } elseif ($a.Name) { $a.Name } else { "apim-$prefix" }
az account set --subscription $Cfg.Subscription

$rgExists = (az group exists -n $ResourceGroup) -eq "true"
$chatExists = [bool](az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query name -o tsv 2>$null)
$project = "$($Cfg.FoundryScope)/projects/$($Cfg.Project)"
$probeConnections = @("genie", "agents", "context", "studio") | ForEach-Object { "$prefix-apimprobe-hub-$_" }
$probeFound = @($probeConnections | Where-Object { az rest --method GET --url "https://management.azure.com$project/connections/$_`?api-version=2025-04-01-preview" --query name -o tsv 2>$null })

# The live connections and the hub must not depend on the gateway when it goes.
$gatewayUrl = if ($apimEnv["APIM_GATEWAY_URL"]) { $apimEnv["APIM_GATEWAY_URL"] } else { "https://$apimName.azure-api.net" }
$gatewayHost = ([uri]$gatewayUrl).Host
$viaGateway = @(@("genie", "agents", "context", "studio") | ForEach-Object { "$prefix-hub-$_" } | Where-Object {
    "$(az rest --method GET --url "https://management.azure.com$project/connections/$_`?api-version=2025-04-01-preview" --query properties.target -o tsv 2>$null)" -like "*$gatewayHost*" })
$hubDataViaGateway = "$(az containerapp show -n $Cfg.App -g $Cfg.ResourceGroup --query "properties.template.containers[0].env[?name=='DATABRICKS_AUTH'].value | [0]" -o tsv 2>$null)" -eq "apim"

Write-Host "This would delete:"
if ($rgExists) { Write-Host "  resource group ${ResourceGroup}:"; az resource list -g $ResourceGroup --query "[].{type:type, name:name}" -o tsv 2>$null | ForEach-Object { Write-Host "      $_" } } else { Write-Host "  (resource group $ResourceGroup does not exist)" }
if ($chatExists -and -not $KeepChatApp) { Write-Host "  container app $ChatApp in $($Cfg.ResourceGroup), and its role assignments on the Foundry account" }
if ($probeFound) { Write-Host "  Foundry connections: $($probeFound -join ', ') (and the agent $prefix-apimprobe)" }
if (-not $KeepEntraApps) { foreach ($k in "ENTRA_APP_OBJECT_ID", "ENTRA_TEST_APP_OBJECT_ID") { if ($apimEnv[$k]) { Write-Host "  Entra app registration $($apimEnv[$k])  ($k)" } } }
if ($viaGateway) { Write-Host "`nWARNING: these live Foundry connections point at the gateway: $($viaGateway -join ', '). Deleting the gateway breaks the agents' tools. Switch them back first (.\infra\create_hub_connection.ps1 -Direct), or pass -RestoreDirect." -ForegroundColor Yellow }
if ($hubDataViaGateway) { Write-Host "`nWARNING: the hub reaches Databricks through the data gateway (DATABRICKS_AUTH=apim). Set Genie.AuthMode back in infra\config.ps1 and run .\infra\deploy_hub.ps1 first." -ForegroundColor Yellow }
if (-not $Yes) { Write-Host "`nNothing was deleted. Run again with -Yes to delete it."; return }

if ($viaGateway -and $RestoreDirect) {
    Write-Host "Putting the live connections back to the hub (create_hub_connection.ps1 -Direct)"
    & "$root\infra\create_hub_connection.ps1" -Direct
    $viaGateway = @()
}
if (($viaGateway -or $hubDataViaGateway) -and -not $IgnoreLive) {
    Write-Host "`nStopped before deleting anything: the live system still depends on the gateway (see the warnings above). Fix that, or pass -IgnoreLive to delete it anyway." -ForegroundColor Red
    return
}

if ($chatExists -and -not $KeepChatApp) {
    $principal = az containerapp show -n $ChatApp -g $Cfg.ResourceGroup --query identity.principalId -o tsv 2>$null
    if ($principal) { az role assignment delete --assignee $principal --scope $Cfg.FoundryScope -o none 2>$null }
    az containerapp delete -n $ChatApp -g $Cfg.ResourceGroup --yes -o none
    Write-Host "Deleted container app $ChatApp"
}
foreach ($name in $probeFound) {
    az rest --method DELETE --url "https://management.azure.com$project/connections/$name`?api-version=2025-04-01-preview" -o none 2>$null
    Write-Host "Deleted Foundry connection $name"
}
if ($probeFound) {
    $py = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" }
    $env:FOUNDRY_PROJECT_ENDPOINT = $Cfg.ProjectEndpoint
    & $py -c "from azure.ai.projects import AIProjectClient; from azure.identity import DefaultAzureCredential; import os; c = AIProjectClient(endpoint=os.environ['FOUNDRY_PROJECT_ENDPOINT'], credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)); c.agents.delete(agent_name='$prefix-apimprobe')" 2>$null
}
if ($rgExists) {
    $location = az group show -n $ResourceGroup --query location -o tsv 2>$null
    Write-Host "Deleting resource group $ResourceGroup (this waits for the API Management instance to go)"
    az group delete -n $ResourceGroup --yes -o none
    if (-not $NoPurge -and $apimName) {
        az apim deletedservice purge --service-name $apimName --location $location -o none 2>$null
        if ($LASTEXITCODE -eq 0) { Write-Host "Purged the soft-deleted API Management instance $apimName" }
    }
}
if (-not $KeepEntraApps) {
    foreach ($k in "ENTRA_APP_OBJECT_ID", "ENTRA_TEST_APP_OBJECT_ID") {
        if ($apimEnv[$k]) { az ad app delete --id $apimEnv[$k] 2>$null; Write-Host "Deleted Entra app registration $($apimEnv[$k])" }
    }
}
if (Test-Path "$root\.apim.env") { Remove-Item "$root\.apim.env" -Force; Write-Host "Deleted .apim.env" }
Write-Host "Not removed (not an Azure resource): if you gave APIM's identity access in Databricks (deploy_apim.ps1 -GrantDatabricks), delete its service principal in the workspace (Settings, Identity and access, Service principals)."
Write-Host "Done."
