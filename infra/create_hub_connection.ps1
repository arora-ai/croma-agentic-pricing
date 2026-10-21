# Stores the hub's API key as Custom Keys connections on the Foundry project (one per MCP endpoint, because Foundry uses the connection
# target as the server URL), so agents call the hub without the key appearing in any agent definition. Reads ../.env.
#
# Direct mode (the default): the connections point at the hub (HUB_URL from .env) and hold the hub key (HUB_API_KEY).
# APIM mode: the connections point at the Azure API Management tool gateway and hold an APIM subscription key instead (docs/APIM.md). The hub key then
# lives only in APIM. APIM mode is used with -Apim, or whenever $Cfg.Apim.GatewayUrl is set in infra/config.ps1; -Direct forces direct mode.
# The gateway URL is $Cfg.Apim.GatewayUrl or APIM_TOOLS_URL in .apim.env; the key is in the environment variable named by $Cfg.Apim.SubscriptionKeyEnv
# (default APIM_TOOLS_KEY), or that entry in .apim.env (written by infra/apim/deploy_apim.ps1).
# -NamePrefix creates the four connections under another name prefix (<prefix>-hub-genie ...), for example to try the gateway next to the live ones.
param([switch]$Apim, [switch]$Direct, [string]$NamePrefix)
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\config.ps1"
$root = Split-Path $PSScriptRoot -Parent
function Read-EnvFile([string]$path) {
    $map = @{}
    if (Test-Path $path) { Get-Content $path | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $map[$Matches[1]] = $Matches[2] } } }
    return $map
}
$env_ = Read-EnvFile "$root\.env"
$project = "$($Cfg.FoundryScope)/projects/$($Cfg.Project)"
$prefix = if ($NamePrefix) { $NamePrefix } elseif ($Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma" }

$useApim = $Apim -or (-not $Direct -and $Cfg.Apim -and $Cfg.Apim.GatewayUrl)
if ($useApim) {
    $apimEnv = Read-EnvFile "$root\.apim.env"
    $targetBase = if ($Cfg.Apim -and $Cfg.Apim.GatewayUrl) { $Cfg.Apim.GatewayUrl } else { $apimEnv["APIM_TOOLS_URL"] }
    $keyName = if ($Cfg.Apim -and $Cfg.Apim.SubscriptionKeyEnv) { $Cfg.Apim.SubscriptionKeyEnv } else { "APIM_TOOLS_KEY" }
    $secretValue = [Environment]::GetEnvironmentVariable($keyName)
    if (-not $secretValue) { $secretValue = $apimEnv[$keyName] }
    if (-not $targetBase -or -not $secretValue) { throw "APIM mode needs the tool gateway URL (Apim.GatewayUrl or APIM_TOOLS_URL in .apim.env) and the subscription key ($keyName in the environment or .apim.env)." }
    Write-Host "Mode: APIM tool gateway ($($targetBase.TrimEnd('/')))"
} else {
    $targetBase = $env_["HUB_URL"]
    $secretValue = $env_["HUB_API_KEY"]
    Write-Host "Mode: direct to the hub ($targetBase)"
}
$targetBase = $targetBase.TrimEnd("/")
$endpoints = @{ "$prefix-hub-genie" = "/genie/mcp"; "$prefix-hub-agents" = "/agents/mcp"; "$prefix-hub-context" = "/context/mcp"; "$prefix-hub-studio" = "/studio/mcp" }
foreach ($name in $endpoints.Keys) {
    $body = @{ properties = @{ category = "CustomKeys"; target = $targetBase + $endpoints[$name]; authType = "CustomKeys"; isSharedToAll = $true
                               credentials = @{ keys = @{ "x-api-key" = $secretValue } } } } | ConvertTo-Json -Depth 5
    # The body holds the key, so it goes in a git-ignored file next to the repository (not a shared temp folder) and is deleted straight away.
    $tmp = Join-Path $root ".connection-body.json"
    try {
        Set-Content -Path $tmp -Value $body -Encoding utf8
        az rest --method put --url "https://management.azure.com$project/connections/$name`?api-version=2025-04-01-preview" --body "@$tmp" --query "{name:name,target:properties.target}" -o tsv
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}
