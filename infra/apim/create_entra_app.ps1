<#
.SYNOPSIS
  Creates the Microsoft Entra ID app registration for the front gateway and the web app, and records its ids in .apim.env.

.DESCRIPTION
  One app registration plays both parts:
    * the API: it exposes the delegated scope access_as_user, and its application id is the audience APIM checks in an access token;
    * the single-page app: the web app signs users in to it with MSAL and asks for that scope (build-time settings VITE_ENTRA_CLIENT_ID, VITE_API_SCOPE).
  Access tokens are version 2 (issuer https://login.microsoftonline.com/<tenant>/v2.0, audience = the application id). The Azure CLI is pre-authorised for the
  scope, so `az account get-access-token --scope api://<id>/access_as_user` returns a token for tests. With -TestClient a second registration with a client
  secret is created as well: test_gateway.ps1 uses it to obtain a token for a second identity (a service principal) to prove that users are kept apart.
  Run it once; it does nothing if .apim.env already records an app (-Recreate replaces it). infra/apim/teardown.ps1 deletes what this creates.
  You need permission to create app registrations in the tenant (the default for members, unless the tenant restricts it).

.EXAMPLE
  .\infra\apim\create_entra_app.ps1 -TestClient
  .\infra\apim\deploy_apim.ps1 -EnableJwt        # APIM validates tokens for that app; the web app image is built with sign-in switched on
#>
[CmdletBinding()]
param(
    [string]$Name = "croma2-chat",
    [string[]]$RedirectUris = @(),          # extra single-page-app redirect URIs; the APIM front URL (when known), http://localhost:5173 and :8000 are always added
    [switch]$TestClient,                    # also create a service principal with a secret, for the second-identity test
    [switch]$Recreate
)
$ErrorActionPreference = "Continue"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$apimEnvPath = "$root\.apim.env"
$build = "$PSScriptRoot\.build"
New-Item -ItemType Directory -Force $build | Out-Null
function Read-EnvFile([string]$path) {
    $map = [ordered]@{}
    if (Test-Path $path) { Get-Content $path | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $map[$Matches[1]] = $Matches[2] } } }
    return $map
}
function Save-EnvFile($map) { Set-Content -Path $apimEnvPath -Value (($map.Keys | ForEach-Object { "$_=$($map[$_])" }) -join "`n") -Encoding ascii }
function Graph([string]$method, [string]$url, $body) {
    $file = "$build\graph-body.json"
    Set-Content -Path $file -Value ($body | ConvertTo-Json -Depth 10) -Encoding utf8
    try { $out = az rest --method $method --url "https://graph.microsoft.com/v1.0$url" --headers "Content-Type=application/json" --body "@$file" 2>&1 }
    finally { Remove-Item $file -Force -ErrorAction SilentlyContinue }
    if ($LASTEXITCODE) { throw "Graph $method $url failed: $(($out | Where-Object { $_ -notmatch '^WARNING' }) -join ' ')" }
}

$cfg = Read-EnvFile $apimEnvPath
if ($cfg["ENTRA_CLIENT_ID"] -and -not $Recreate) { Write-Host "An app registration is already recorded in .apim.env (client id $($cfg['ENTRA_CLIENT_ID'])). Use -Recreate to replace it."; return }
$tenant = az account show --query tenantId -o tsv

Write-Host "Creating the app registration '$Name'"
$app = az ad app create --display-name $Name --sign-in-audience AzureADMyOrg --query "{appId:appId, id:id}" -o json | ConvertFrom-Json
if (-not $app.appId) { throw "az ad app create failed (the tenant may restrict who can create app registrations)." }
$scopeId = [guid]::NewGuid().ToString()
$azureCli = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"     # the well-known application id of the Azure CLI
Graph PATCH "/applications/$($app.id)" @{
    identifierUris = @("api://$($app.appId)")
    api = @{
        requestedAccessTokenVersion = 2
        oauth2PermissionScopes = @(@{
            id = $scopeId; type = "User"; value = "access_as_user"; isEnabled = $true
            adminConsentDisplayName = "Use the Croma assistant"; adminConsentDescription = "Lets the app call the Croma assistant API on behalf of the signed-in user."
            userConsentDisplayName = "Use the Croma assistant"; userConsentDescription = "Lets the app call the Croma assistant on your behalf."
        })
    }
}
$uris = @("http://localhost:5173", "http://localhost:8000") + $RedirectUris
if ($cfg["APIM_FRONT_URL"]) { $uris += $cfg["APIM_FRONT_URL"] }
Graph PATCH "/applications/$($app.id)" @{
    spa = @{ redirectUris = @($uris | Select-Object -Unique) }
    api = @{ preAuthorizedApplications = @(
        @{ appId = $app.appId; delegatedPermissionIds = @($scopeId) },
        @{ appId = $azureCli; delegatedPermissionIds = @($scopeId) }) }
}
az ad sp create --id $app.appId -o none 2>$null
$cfg["ENTRA_TENANT_ID"] = $tenant
$cfg["ENTRA_CLIENT_ID"] = $app.appId
$cfg["ENTRA_APP_OBJECT_ID"] = $app.id
$cfg["ENTRA_API_SCOPE"] = "api://$($app.appId)/access_as_user"

if ($TestClient) {
    Write-Host "Creating the test client '$Name-test-client' (a service principal with a 30 day secret)"
    $t = az ad app create --display-name "$Name-test-client" --sign-in-audience AzureADMyOrg --query "{appId:appId, id:id}" -o json | ConvertFrom-Json
    az ad sp create --id $t.appId -o none 2>$null
    $secret = az ad app credential reset --id $t.appId --display-name "gateway-tests" --end-date (Get-Date).AddDays(30).ToString("yyyy-MM-dd") --query password -o tsv
    if (-not $secret) { throw "Could not create a secret for the test client." }
    $cfg["ENTRA_TEST_CLIENT_ID"] = $t.appId
    $cfg["ENTRA_TEST_APP_OBJECT_ID"] = $t.id
    $cfg["ENTRA_TEST_CLIENT_SECRET"] = $secret
}
Save-EnvFile $cfg
Write-Host "App registration '$Name': client id $($app.appId), scope api://$($app.appId)/access_as_user, tenant $tenant (recorded in .apim.env, git-ignored)."
Write-Host "Next: .\infra\apim\deploy_apim.ps1 -EnableJwt   (give it -CorsOrigins only if the web app is served from somewhere other than the gateway)"
