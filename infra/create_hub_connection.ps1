# Stores the hub's API key as Custom Keys connections on the Foundry project (one per MCP endpoint, because Foundry uses the connection
# target as the server URL), so agents call the hub without the key appearing in any agent definition. Reads ../.env.
$ErrorActionPreference = "Continue"
. "$PSScriptRoot\config.ps1"
$root = Split-Path $PSScriptRoot -Parent
$env_ = @{}
Get-Content "$root\.env" | ForEach-Object { $k, $v = $_ -split "=", 2; $env_[$k] = $v }
$project = "$($Cfg.FoundryScope)/projects/$($Cfg.Project)"
$prefix = if ($Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma" }
$endpoints = @{ "$prefix-hub-genie" = "/genie/mcp"; "$prefix-hub-agents" = "/agents/mcp"; "$prefix-hub-context" = "/context/mcp"; "$prefix-hub-studio" = "/studio/mcp" }
foreach ($name in $endpoints.Keys) {
    $body = @{ properties = @{ category = "CustomKeys"; target = $env_["HUB_URL"] + $endpoints[$name]; authType = "CustomKeys"; isSharedToAll = $true
                               credentials = @{ keys = @{ "x-api-key" = $env_["HUB_API_KEY"] } } } } | ConvertTo-Json -Depth 5
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $body -Encoding utf8
    az rest --method put --url "https://management.azure.com$project/connections/$name`?api-version=2025-04-01-preview" --body "@$tmp" --query "{name:name,target:properties.target}" -o tsv
    Remove-Item $tmp -Force
}
