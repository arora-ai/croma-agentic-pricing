<#
.SYNOPSIS
  Live checks of the three APIM gateways, with PASS/FAIL output and no secrets printed. Safe to repeat.

.DESCRIPTION
  FRONT   no key -> 401; a key or an Entra token -> the chat API gets the caller's identity and forged identity headers are ignored; two Entra identities
          are kept apart (conversations, memory scopes); streaming is not buffered (synthetic stream and a real agent turn); rate limit -> 429;
          the chat container refuses direct calls that are not signed with the gateway key.
  TOOLS   tools/list and one harmless tools/call on each of the four MCP endpoints; the hub key alone is not enough; the hub's other routes are not exposed.
  DATA    the Genie allow-list, the space check, the question rate limit, and a real Genie question through the hub's own backend in apim mode.
  Needs .apim.env (deploy_apim.ps1), .env (for the hub key, used only to prove it is refused) and infra/config.ps1 (the Genie space id).
  The real agent turn and the Genie questions each cost a little model or warehouse time: -SkipChatTurn and -SkipGenie leave them out.

.EXAMPLE
  .\infra\apim\test_gateway.ps1
  .\infra\apim\test_gateway.ps1 -Only tools
  .\infra\apim\test_gateway.ps1 -LongStream          # adds the 5 minute keep-alive check (takes about 10 minutes)
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "front", "tools", "data")][string]$Only = "all",
    [string]$Python,                       # a python with httpx and azure-identity, for the data gateway question (default: .venv, else python)
    [switch]$SkipChatTurn,
    [switch]$SkipGenie,
    [switch]$LongStream,
    [switch]$CheckLogs                     # also look for this run's traces in Log Analytics (arrive a few minutes late)
)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$build = "$PSScriptRoot\.build"
New-Item -ItemType Directory -Force $build | Out-Null
function Read-EnvFile([string]$path) {
    $map = @{}
    if (Test-Path $path) { Get-Content $path | ForEach-Object { if ($_ -match '^\s*([A-Za-z0-9_]+)=(.*)$') { $map[$Matches[1]] = $Matches[2] } } }
    return $map
}
if (-not (Test-Path "$root\.apim.env")) { throw ".apim.env is missing: run infra\apim\deploy_apim.ps1 first." }
$e = Read-EnvFile "$root\.apim.env"
$hubEnv = Read-EnvFile "$root\.env"
if (Test-Path "$root\infra\config.ps1") { . "$root\infra\config.ps1" }
$front = $e["APIM_FRONT_URL"]; $tools = $e["APIM_TOOLS_URL"]; $data = $e["APIM_DATA_URL"]; $chat = $e["CHAT_APP_URL"]
$spaceId = if ($Cfg -and $Cfg.Genie) { $Cfg.Genie.SpaceId } else { "" }
$chatLimit = if ($e["LIMIT_CHAT_TURNS_PER_MINUTE"]) { [int]$e["LIMIT_CHAT_TURNS_PER_MINUTE"] } else { 10 }
$questionLimit = if ($e["LIMIT_DATA_QUESTIONS_PER_MINUTE"]) { [int]$e["LIMIT_DATA_QUESTIONS_PER_MINUTE"] } else { 30 }
$jwtOn = $e["JWT_ENABLED"] -eq "true" -and $e["ENTRA_CLIENT_ID"]

$script:passed = 0; $script:failed = 0; $script:skipped = 0
function Check([string]$name, [bool]$ok, [string]$detail = "") {
    if ($ok) { $script:passed++; Write-Host ("  PASS  {0}{1}" -f $name, $(if ($detail) { "  ($detail)" })) -ForegroundColor Green }
    else { $script:failed++; Write-Host ("  FAIL  {0}{1}" -f $name, $(if ($detail) { "  ($detail)" })) -ForegroundColor Red }
}
function Skip([string]$name, [string]$why) { $script:skipped++; Write-Host ("  SKIP  {0}  ({1})" -f $name, $why) -ForegroundColor Yellow }
function Section([string]$title) { Write-Host ""; Write-Host "== $title" -ForegroundColor Cyan }

function Invoke-Http([string[]]$CurlArgs) {
    # curl with headers included; returns the status, the response headers (lower-case names) and the body.
    $text = (& curl.exe -s -i --max-time 180 @CurlArgs 2>$null) -join "`n"
    $parts = $text -split "`n`n", 2
    while ($parts[0] -match '^HTTP/\S+ 1\d\d' -and $parts.Count -gt 1) { $parts = $parts[1] -split "`n`n", 2 }
    $lines = $parts[0] -split "`n"
    $headers = @{}
    foreach ($line in $lines | Select-Object -Skip 1) { if ($line -match '^([^:]+):\s*(.*)$') { $headers[$Matches[1].ToLower()] = $Matches[2].Trim() } }
    $status = if ($lines[0] -match '^HTTP/\S+ (\d+)') { [int]$Matches[1] } else { 0 }
    [pscustomobject]@{ Status = $status; Headers = $headers; Body = $(if ($parts.Count -gt 1) { $parts[1] } else { "" }) }
}
function Invoke-Stream([string[]]$CurlArgs, [int]$MaxSeconds = 900) {
    # curl -N: every event and keep-alive comment with the time it arrived, and the text of the delta events.
    $clock = [Diagnostics.Stopwatch]::StartNew()
    $events = New-Object System.Collections.Generic.List[object]
    $state = @{ current = ""; text = ""; error = "" }
    & curl.exe -s -N --max-time $MaxSeconds @CurlArgs 2>$null | ForEach-Object {
        if ($_ -match '^event: (.+)$') { $state.current = $Matches[1]; $events.Add([pscustomobject]@{ At = $clock.Elapsed.TotalSeconds; Event = $state.current }) }
        elseif ($_ -match '^: ') { $events.Add([pscustomobject]@{ At = $clock.Elapsed.TotalSeconds; Event = "comment" }) }
        elseif ($_ -match '^data: (.*)$') {
            try { $d = $Matches[1] | ConvertFrom-Json; if ($state.current -eq "delta") { $state.text += $d.text } elseif ($state.current -eq "error") { $state.error = $d.message } } catch { }
        }
    }
    [pscustomobject]@{ Events = $events; Total = $clock.Elapsed.TotalSeconds; Text = $state.text; Error = $state.error }
}
function Invoke-Burst([int]$Count, [string[]]$CurlArgs, [string]$Url) {
    # Many calls at once (curl --parallel): the status code of each. Every URL needs its own -o, or the bodies would be printed.
    $targets = 1..$Count | ForEach-Object { "-o"; "NUL"; $Url }
    @(& curl.exe -s -Z --parallel-max 20 -w "%{http_code}\n" --max-time 120 @CurlArgs @targets 2>$null | Where-Object { $_ -match '^\d{3}$' } | ForEach-Object { [int]$_ })
}
function Write-Body([string]$name, [string]$content) { $p = "$build\$name"; Set-Content -Path $p -Value $content -Encoding ascii; return $p }
function Decode-Jwt([string]$token) {
    $p = $token.Split(".")[1].Replace("-", "+").Replace("_", "/"); while ($p.Length % 4) { $p += "=" }
    [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p)) | ConvertFrom-Json
}
$keyHeader = @("-H", "Ocp-Apim-Subscription-Key: $($e['APIM_FRONT_KEY'])")
$jsonHeader = @("-H", "Content-Type: application/json")
$emptyBody = Write-Body "empty.json" "{}"

# ================================================================ FRONT
if ($Only -in "all", "front") {
    Section "FRONT gateway: $front"
    $r = Invoke-Http @("$front/api/me")
    Check "no credential -> 401" ($r.Status -eq 401) "HTTP $($r.Status), $($r.Body.Substring(0, [Math]::Min(60, $r.Body.Length)))..."
    Check "the 401 carries a generated x-correlation-id" ([bool]$r.Headers["x-correlation-id"])
    $r = Invoke-Http @("-H", "Ocp-Apim-Subscription-Key: not-a-real-key", "$front/api/me")
    Check "an invalid subscription key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-user-id: attacker", "-H", "x-gateway-key: guess", "$front/api/me")
    Check "forged identity headers without a credential -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"

    $r = Invoke-Http ($keyHeader + @("-H", "x-user-id: attacker", "-H", "x-user-name: Mallory", "-H", "x-gateway-key: forged", "-H", "x-correlation-id: test-corr-0001", "$front/api/me"))
    $me = if ($r.Status -eq 200) { $r.Body | ConvertFrom-Json } else { $null }
    Check "a subscription key -> 200, and the chat API sees the gateway's identity, not the forged one" ($me -and $me.user_id -like "sub-*" -and $me.authenticated -and $me.gateway) "user_id=$($me.user_id)"
    Check "a client x-correlation-id is passed through and returned" ($r.Headers["x-correlation-id"] -eq "test-corr-0001")
    Check "the rate-limit headers are returned" ([bool]$r.Headers["x-ratelimit-remaining"]) "remaining=$($r.Headers['x-ratelimit-remaining'])"

    $r = Invoke-Http @("$front/")
    Check "the web app loads without a credential (GET /)" ($r.Status -eq 200 -and $r.Body -match "<div id=`"root`">") "HTTP $($r.Status)"
    $asset = [regex]::Match($r.Body, 'src="(/assets/[^"]+\.js)"').Groups[1].Value
    if ($asset) { $a = Invoke-Http @("$front$asset"); Check "its script loads through the gateway ($asset)" ($a.Status -eq 200 -and $a.Body.Length -gt 1000) "HTTP $($a.Status), $($a.Body.Length) characters" }
    foreach ($p in "/docs", "/openapi.json", "/api/unknown", "/redoc") {
        $r = Invoke-Http ($keyHeader + @("$front$p")); Check "$p is not exposed -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
    }
    $r = Invoke-Http @("-X", "OPTIONS", "-H", "Origin: $front", "-H", "Access-Control-Request-Method: POST", "-H", "Access-Control-Request-Headers: authorization,content-type", "$front/api/chat")
    Check "CORS preflight from the gateway's own origin is answered" ($r.Status -in 200, 204 -and $r.Headers["access-control-allow-origin"] -eq $front) "HTTP $($r.Status)"
    $r = Invoke-Http @("-X", "OPTIONS", "-H", "Origin: https://evil.example", "-H", "Access-Control-Request-Method: POST", "$front/api/chat")
    Check "CORS preflight from another origin gets no allow-origin header" (-not $r.Headers["access-control-allow-origin"]) "HTTP $($r.Status)"

    # ---- streaming
    $s = Invoke-Stream ($keyHeader + @("$front/api/stream-check?count=6&interval=2"))
    $ticks = @($s.Events | Where-Object { $_.Event -eq "tick" })
    if ($ticks.Count -eq 0) { Skip "synthetic stream" "GET /api/stream-check answered nothing: it is switched off (deploy without -DisableStreamCheck)" }
    else {
        $spread = $ticks[-1].At - $ticks[0].At
        Check "streaming is not buffered: 6 events 2 s apart arrive 2 s apart" ($ticks.Count -eq 6 -and $spread -ge 8 -and $ticks[0].At -lt 4) ("first event after {0:N1}s, last after {1:N1}s, total {2:N1}s; arrival times: {3}" -f $ticks[0].At, $ticks[-1].At, $s.Total, (($ticks | ForEach-Object { "{0:N1}" -f $_.At }) -join " "))
    }
    if ($SkipChatTurn) { Skip "real agent turn through the gateway" "-SkipChatTurn" }
    else {
        $s = Invoke-Stream ($keyHeader + $jsonHeader + @("--data-binary", "@$(Write-Body 'chat.json' '{"message":"Reply with exactly the word: gateway-ok"}')", "$front/api/chat")) 240
        $kinds = @($s.Events | ForEach-Object { $_.Event })
        $summary = ($kinds | Group-Object | ForEach-Object { "$($_.Name)x$($_.Count)" }) -join " "
        Check "a chat turn streams: turn, deltas, end" ($kinds -contains "turn" -and $kinds -contains "end" -and ($kinds -contains "delta" -or $kinds -contains "error")) ("events: {0}; answer: '{1}'{2}" -f $summary, $s.Text, $(if ($s.Error) { "; error: $($s.Error)" }))
        $firstEvent = ($s.Events | Select-Object -First 1).At
        $lastEvent = ($s.Events | Select-Object -Last 1).At
        $deltas = @($s.Events | Where-Object { $_.Event -eq "delta" })
        Check "the stream arrives progressively, not as one burst when the turn ends" ($s.Events.Count -gt 0 -and $lastEvent - $firstEvent -ge 2 -and $firstEvent -lt $s.Total - 2) ("first event after {0:N1}s, the last after {1:N1}s; {2} answer fragments, the first after {3}" -f $firstEvent, $lastEvent, $deltas.Count, $(if ($deltas) { "{0:N1}s" -f $deltas[0].At } else { "n/a" }))
    }

    # ---- Entra identities
    if (-not $jwtOn) { Skip "Entra token checks" "JWT_ENABLED is not true in .apim.env: deploy with -EnableJwt after create_entra_app.ps1" }
    else {
        Write-Host "  (signing in through the Azure CLI takes a few seconds)"
        $tokA = az account get-access-token --scope $e["ENTRA_API_SCOPE"] --query accessToken -o tsv 2>$null
        $tokB = $null
        if ($e["ENTRA_TEST_CLIENT_ID"]) {
            try { $tokB = (Invoke-RestMethod -Method Post -Uri "https://login.microsoftonline.com/$($e['ENTRA_TENANT_ID'])/oauth2/v2.0/token" -Body @{ client_id = $e["ENTRA_TEST_CLIENT_ID"]; client_secret = $e["ENTRA_TEST_CLIENT_SECRET"]; scope = ($e["ENTRA_API_SCOPE"] -replace '/access_as_user$', '/.default'); grant_type = "client_credentials" }).access_token } catch { }
        }
        if (-not ($tokA -match '^eyJ')) { Skip "Entra token checks" "could not get a token for $($e['ENTRA_API_SCOPE']) from the Azure CLI" }
        else {
            $oidA = (Decode-Jwt $tokA).oid
            $r = Invoke-Http @("-H", "Authorization: Bearer $tokA", "$front/api/me")
            $me = if ($r.Status -eq 200) { $r.Body | ConvertFrom-Json } else { $null }
            Check "an Entra access token -> 200, user id = the token's oid claim" ($me -and $me.user_id -eq $oidA) "HTTP $($r.Status), oid $oidA"
            $r = Invoke-Http @("-H", "Authorization: Bearer $($tokA.Substring(0, $tokA.Length - 6))AAAAAA", "$front/api/me")
            Check "a token with a damaged signature -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
            $r = Invoke-Http @("-H", "Authorization: Bearer not.a.token", "$front/api/me")
            Check "a malformed token -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
            $arm = az account get-access-token --resource https://management.azure.com/ --query accessToken -o tsv 2>$null
            if ($arm -match '^eyJ') { $r = Invoke-Http @("-H", "Authorization: Bearer $arm", "$front/api/me"); Check "a genuine token for another audience (Azure Resource Manager) -> 401" ($r.Status -eq 401) "HTTP $($r.Status)" }
            $r = Invoke-Http @("-H", "Authorization: Bearer $tokA", "-H", "x-user-id: attacker", "-H", "x-gateway-key: guess", "$front/api/me")
            Check "forged headers next to a valid token are ignored" (($r.Body | ConvertFrom-Json).user_id -eq $oidA)
            if (-not $tokB) { Skip "two identities" "no test client in .apim.env (create_entra_app.ps1 -TestClient)" }
            else {
                $oidB = (Decode-Jwt $tokB).oid
                $scopeA = ((Invoke-Http @("-H", "Authorization: Bearer $tokA", "$front/api/health")).Body | ConvertFrom-Json).memory_scope
                $scopeB = ((Invoke-Http @("-H", "Authorization: Bearer $tokB", "$front/api/health")).Body | ConvertFrom-Json).memory_scope
                Check "two identities get different memory scopes" ($scopeA -ne $scopeB -and $scopeA -like "*$oidA" -and $scopeB -like "*$oidB") "$scopeA | $scopeB"
                if ($SkipChatTurn) { Skip "separate conversation histories" "-SkipChatTurn" }
                else {
                    $ids = @{}
                    foreach ($who in @(@("A", $tokA), @("B", $tokB))) {
                        $s = Invoke-Stream @("-H", "Authorization: Bearer $($who[1])", "-H", "Content-Type: application/json", "--data-binary", "@$(Write-Body 'chat-id.json' ('{"message":"Reply with exactly: ' + $who[0] + '-first"}'))", "$front/api/chat") 240
                        $ids[$who[0]] = [regex]::Match((& curl.exe -s -H "Authorization: Bearer $($who[1])" "$front/api/conversations"), '"id":"(conv_[^"]+)"').Groups[1].Value
                    }
                    $listA = (Invoke-Http @("-H", "Authorization: Bearer $tokA", "$front/api/conversations")).Body | ConvertFrom-Json
                    $listB = (Invoke-Http @("-H", "Authorization: Bearer $tokB", "$front/api/conversations")).Body | ConvertFrom-Json
                    Check "each identity sees only its own conversations" ($ids["A"] -and $ids["B"] -and $ids["A"] -ne $ids["B"] -and @($listA.id) -contains $ids["A"] -and @($listA.id) -notcontains $ids["B"] -and @($listB.id) -contains $ids["B"] -and @($listB.id) -notcontains $ids["A"]) "A has $(@($listA).Count), B has $(@($listB).Count)"
                    $r = Invoke-Http @("-H", "Authorization: Bearer $tokB", "$front/api/conversations/$($ids['A'])"); Check "B cannot read A's conversation -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
                    $r = Invoke-Http @("-X", "DELETE", "-H", "Authorization: Bearer $tokB", "$front/api/conversations/$($ids['A'])"); Check "B cannot delete A's conversation -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
                    $r = Invoke-Http @("-H", "Authorization: Bearer $tokB", "-H", "Content-Type: application/json", "--data-binary", "@$(Write-Body 'chat-steal.json' ('{"message":"hello","conversation_id":"' + $ids['A'] + '"}'))", "$front/api/chat"); Check "B cannot continue A's conversation -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
                    foreach ($who in @(@("A", $tokA), @("B", $tokB))) { $null = Invoke-Http @("-X", "DELETE", "-H", "Authorization: Bearer $($who[1])", "$front/api/conversations/$($ids[$who[0]])") }
                }
                $add = Invoke-Http @("-H", "Authorization: Bearer $tokA", "-H", "Content-Type: application/json", "--data-binary", "@$(Write-Body 'memory.json' '{"content":"gateway test preference"}')", "$front/api/memory")
                if ($add.Status -ne 200) { Skip "memory isolation" "the memory store answered HTTP $($add.Status) (deploy the agents first: it creates the store)" }
                else {
                    $memId = ($add.Body | ConvertFrom-Json).id
                    $listB = (Invoke-Http @("-H", "Authorization: Bearer $tokB", "$front/api/memory")).Body | ConvertFrom-Json
                    $listA = (Invoke-Http @("-H", "Authorization: Bearer $tokA", "$front/api/memory")).Body | ConvertFrom-Json
                    Check "a preference saved by A is in A's memory scope and not in B's" (@($listA.id) -contains $memId -and @($listB.id) -notcontains $memId)
                    $r = Invoke-Http @("-X", "DELETE", "-H", "Authorization: Bearer $tokB", "$front/api/memory/$memId"); Check "B cannot delete A's memory item -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
                    $null = Invoke-Http @("-X", "DELETE", "-H", "Authorization: Bearer $tokA", "$front/api/memory/$memId")
                }
            }
        }
    }

    # ---- the chat container itself
    Write-Host "  direct calls to the container ($chat), around the gateway:"
    $r = Invoke-Http @("-H", "x-user-id: attacker", "$chat/api/me"); Check "forged x-user-id, no gateway key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-user-id: attacker", "-H", "x-gateway-key: wrong", "$chat/api/conversations"); Check "forged x-user-id, wrong gateway key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-gateway-key: $($e['CHAT_GATEWAY_KEY'])", "$chat/api/me"); Check "the right gateway key but no user id -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-user-id: direct-test-user", "-H", "x-gateway-key: $($e['CHAT_GATEWAY_KEY'])", "$chat/api/me")
    Check "positive control: the right gateway key makes the user id count" ($r.Status -eq 200 -and ($r.Body | ConvertFrom-Json).user_id -eq "direct-test-user") "HTTP $($r.Status)"
    $r = Invoke-Http @("$chat/docs"); Check "the interactive API docs are not served" ($r.Status -eq 404) "HTTP $($r.Status)"

    # ---- rate limit (last: it blocks this subscription's chat calls for a minute). A burst, because APIM's v2 tiers use a token bucket: a slow trickle of
    # calls is refilled as fast as it is spent and would take a minute to run dry.
    $burst = Invoke-Burst ($chatLimit + 15) ($keyHeader + $jsonHeader + @("-X", "POST", "--data-binary", "@$emptyBody")) "$front/api/chat"
    $allowed = @($burst | Where-Object { $_ -ne 429 }).Count; $refused = @($burst | Where-Object { $_ -eq 429 }).Count
    $r = Invoke-Http ($keyHeader + $jsonHeader + @("--data-binary", "@$emptyBody", "$front/api/chat"))
    Check "POST /api/chat is rate limited per user: 429 with Retry-After" ($refused -gt 0 -and $allowed -le $chatLimit + 6 -and $r.Status -eq 429 -and $r.Headers["retry-after"]) ("limit {0}/min; a burst of {1} calls: {2} answered by the chat API (422 for the empty body), {3} refused with 429; Retry-After {4}s" -f $chatLimit, $burst.Count, $allowed, $refused, $r.Headers["retry-after"])

    if ($LongStream) {
        Write-Host "  long stream: 2 events 270 s apart with keep-alive comments every 15 s (about 5 minutes)..."
        $s = Invoke-Stream ($keyHeader + @("$front/api/stream-check?count=2&interval=270&heartbeat=15")) 400
        $kinds = @($s.Events | ForEach-Object { $_.Event })
        Check "a stream silent for 270 s survives when keep-alives are sent" ($kinds -contains "end" -and ($kinds | Where-Object { $_ -eq "comment" }).Count -ge 10) ("{0:N0}s, {1} keep-alives, ended: {2}" -f $s.Total, ($kinds | Where-Object { $_ -eq "comment" }).Count, ($kinds -contains "end"))
    }
}

# ================================================================ TOOLS
if ($Only -in "all", "tools") {
    Section "TOOL gateway: $tools"
    $toolKey = @("-H", "x-api-key: $($e['APIM_TOOLS_KEY'])", "-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream")
    $list = Write-Body "rpc-list.json" '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
    foreach ($ep in "genie", "agents", "context", "studio") {
        $r = Invoke-Http ($toolKey + @("--data-binary", "@$list", "$tools/$ep/mcp"))
        $names = @([regex]::Matches($r.Body, '"name":"([a-z_]+)"') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
        Check "tools/list on /$ep/mcp" ($r.Status -eq 200 -and $names.Count -ge 1) ("HTTP $($r.Status), $($names.Count) tools: " + (($names | Select-Object -First 4) -join ", ") + $(if ($names.Count -gt 4) { ", ..." }))
    }
    $calls = @(
        @{ ep = "agents"; label = "wait_for_agent (unknown job)"; body = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"wait_for_agent","arguments":{"job_id":"J-gateway-test"}}}'; expect = "unknown job" },
        @{ ep = "context"; label = "get_case (unknown case)"; body = '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_case","arguments":{"case_id":"C-gateway-test"}}}'; expect = "Unknown case_id" },
        @{ ep = "studio"; label = "get_report_playbook"; body = '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_report_playbook","arguments":{}}}'; expect = "playbook" }
    )
    foreach ($c in $calls) {
        $r = Invoke-Http ($toolKey + @("--data-binary", "@$(Write-Body 'rpc-call.json' $c.body)", "$tools/$($c.ep)/mcp"))
        Check "tools/call $($c.label) on /$($c.ep)/mcp" ($r.Status -eq 200 -and $r.Body -match [regex]::Escape($c.expect)) "HTTP $($r.Status)"
    }
    if ($SkipGenie) { Skip "tools/call query_space_croma_pricing" "-SkipGenie" }
    else {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $r = Invoke-Http ($toolKey + @("--data-binary", "@$(Write-Body 'rpc-genie.json' '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"query_space_croma_pricing","arguments":{"query":"What was the total NMV in March 2026 in Rs crore?","conversation_id":"C-apim"}}}')", "$tools/genie/mcp"))
        $value = $null
        try { $inner = (($r.Body | ConvertFrom-Json).result.content[0].text | ConvertFrom-Json); $value = [double]$inner.rows[0][0] } catch { }
        Check "tools/call query_space_croma_pricing: total NMV for March 2026 (about 154.59 Rs crore)" ($r.Status -eq 200 -and $value -and [Math]::Abs($value - 154.59) -le 154.59 * 0.05) ("HTTP $($r.Status), {0:N1}s, answer {1}; Genie's SQL varies a little between runs (150.73 to 154.59 seen)" -f $sw.Elapsed.TotalSeconds, $value)
    }
    $r = Invoke-Http @("-H", "x-api-key: $($hubEnv['HUB_API_KEY'])", "-H", "Content-Type: application/json", "--data-binary", "@$list", "$tools/genie/mcp")
    Check "the hub's own key in x-api-key, without an APIM key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "Content-Type: application/json", "--data-binary", "@$list", "$tools/genie/mcp"); Check "no key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-api-key: $($e['APIM_FRONT_KEY'])", "-H", "Content-Type: application/json", "--data-binary", "@$list", "$tools/genie/mcp"); Check "a key for another gateway (front) -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
    foreach ($p in "/trace", "/state/C-1", "/events", "/runs/C-1", "/case/C-1", "/report/C-1/1.docx", "/healthz") {
        $r = Invoke-Http @("-H", "x-api-key: $($e['APIM_TOOLS_KEY'])", "$tools$p"); Check "the hub's GET $p is not exposed -> 404" ($r.Status -in 403, 404) "HTTP $($r.Status)"
    }
    $r = Invoke-Http @("-X", "POST", "-H", "x-api-key: $($e['APIM_TOOLS_KEY'])", "-H", "Content-Type: application/json", "--data-binary", "@$emptyBody", "$tools/state"); Check "the hub's POST /state is not exposed -> 404" ($r.Status -in 403, 404) "HTTP $($r.Status)"
    $r = Invoke-Http @("-X", "PUT", "-H", "x-api-key: $($e['APIM_TOOLS_KEY'])", "$tools/genie/mcp"); Check "PUT /genie/mcp (not an exposed method) -> 404" ($r.Status -in 404, 405) "HTTP $($r.Status)"
    $r = Invoke-Http @("-H", "x-api-key: $($e['APIM_TOOLS_KEY'])", "$tools/genie/mcp/")
    Check "a redirect from the hub does not leak the hub's address and stays on the gateway" ($r.Status -ne 307 -or ($r.Headers["location"] -notmatch "azurecontainerapps.io" -and $r.Headers["location"] -match "^/tools/genie/mcp")) "HTTP $($r.Status), Location: $($r.Headers['location'])"
    if ($CheckLogs) {
        $logPrefix = if ($Cfg -and $Cfg.NamePrefix) { $Cfg.NamePrefix } else { "croma2" }
        $ws = az monitor log-analytics workspace show -n "law-$logPrefix" -g $e["APIM_RESOURCE_GROUP"] --query customerId -o tsv 2>$null
        $q = "AppTraces | where TimeGenerated > ago(30m) | where Properties.mcp_tool == 'query_space_croma_pricing' | count"
        $n = (az monitor log-analytics query -w $ws --analytics-query $q --query "[0].Count" -o tsv 2>$null)
        if ($n -match '^\d+$' -and [int]$n -gt 0) { Check "per-tool traces (mcp_tool) reach Application Insights" $true "$n traces for query_space_croma_pricing in the last 30 minutes" }
        else { Skip "per-tool traces" "none yet: they arrive a few minutes after the call" }
    }
}

# ================================================================ DATA
if ($Only -in "all", "data") {
    Section "DATA tool gateway: $data"
    if (-not $spaceId) { Skip "data gateway checks" "infra/config.ps1 has no Genie.SpaceId" }
    else {
        $dataKey = @("-H", "Ocp-Apim-Subscription-Key: $($e['APIM_DATA_KEY'])", "-H", "Content-Type: application/json")
        $ask = Write-Body "genie-ask.json" '{"content":"gateway test"}'
        $r = Invoke-Http @("-X", "POST", "-H", "Content-Type: application/json", "--data-binary", "@$ask", "$data/api/2.0/genie/spaces/$spaceId/start-conversation"); Check "no key -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
        $r = Invoke-Http @("-X", "POST", "-H", "Ocp-Apim-Subscription-Key: $($e['APIM_TOOLS_KEY'])", "-H", "Content-Type: application/json", "--data-binary", "@$ask", "$data/api/2.0/genie/spaces/$spaceId/start-conversation"); Check "a key for another gateway (tools) -> 401" ($r.Status -eq 401) "HTTP $($r.Status)"
        $r = Invoke-Http ($dataKey + @("-X", "POST", "--data-binary", "@$ask", "$data/api/2.0/genie/spaces/00000000000000000000000000000000/start-conversation")); Check "another Genie space id -> 403" ($r.Status -eq 403) "HTTP $($r.Status)"
        foreach ($p in "/api/2.0/sql/statements", "/api/2.0/genie/spaces", "/api/2.0/clusters/list", "/api/2.0/preview/scim/v2/Users") {
            $r = Invoke-Http ($dataKey + @("$data$p")); Check "Databricks route $p is not exposed -> 404" ($r.Status -eq 404) "HTTP $($r.Status)"
        }
        $r = Invoke-Http ($dataKey + @("-X", "DELETE", "$data/api/2.0/genie/spaces/$spaceId/conversations/c/messages/m")); Check "an unlisted method on a listed path (DELETE a message) -> 404/405" ($r.Status -in 404, 405) "HTTP $($r.Status)"
        # Rejected attempts count against the question limit too (the limit is checked first), so the limit can be shown without starting a single query.
        $burst = Invoke-Burst ($questionLimit + 20) ($dataKey + @("-X", "POST", "--data-binary", "@$ask")) "$data/api/2.0/genie/spaces/00000000000000000000000000000000/start-conversation"
        $allowed = @($burst | Where-Object { $_ -ne 429 }).Count; $refused = @($burst | Where-Object { $_ -eq 429 }).Count
        $r = Invoke-Http ($dataKey + @("-X", "POST", "--data-binary", "@$ask", "$data/api/2.0/genie/spaces/00000000000000000000000000000000/start-conversation"))
        Check "starting questions is rate limited per subscription: 429" ($refused -gt 0 -and $allowed -le $questionLimit + 6) ("limit {0}/min; a burst of {1} attempts: {2} reached the space check (403), {3} refused with 429{4}" -f $questionLimit, $burst.Count, $allowed, $refused, $(if ($r.Headers["retry-after"]) { "; Retry-After $($r.Headers['retry-after'])s" }))
        if ($SkipGenie) { Skip "a real Genie question through the gateway" "-SkipGenie" }
        else {
            if (-not $Python) { $Python = if (Test-Path "$root\.venv\Scripts\python.exe") { "$root\.venv\Scripts\python.exe" } else { "python" } }
            Write-Host "  waiting a minute for the question limit to renew, then asking Genie through the gateway..."
            Start-Sleep -Seconds 62
            $out = & $Python "$PSScriptRoot\try_data_gateway.py" --space-id $spaceId --json 2>&1 | Out-String
            $result = $null; try { $result = $out | ConvertFrom-Json } catch { }
            $value = $null; if ($result -and $result.rows) { $value = [double]@($result.rows)[0][0] }
            Check "a real Genie question through the hub's backend in apim mode (about 154.59 Rs crore)" ($value -and [Math]::Abs($value - 154.59) -le 154.59 * 0.05) ("answer {0}, {1}s; backend {2}" -f $value, $result.elapsed_s, $result.backend)
        }
    }
}

Write-Host ""
Write-Host ("Result: {0} passed, {1} failed, {2} skipped" -f $script:passed, $script:failed, $script:skipped) -ForegroundColor $(if ($script:failed) { "Red" } else { "Green" })
if ($script:failed) { exit 1 }
