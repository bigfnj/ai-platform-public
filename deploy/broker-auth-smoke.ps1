<#
Broker-auth smoke test — confirms every broker-client rail can authenticate to the platform broker.

Why this exists: the broker enforces a bearer token (BROKER_AUTH_TOKEN) app-wide on every /v1/*
route (services/broker/app/main.py `require_token`). A rail whose client omits the Authorization
header, or whose container is missing/holds a stale token, silently 401s — its LLM features fail
without an obvious error. (job-aid's own urllib client had exactly this bug on both its chat and
embed paths; fixed 2026-08-13.) This sweep catches the whole class.

What it checks, per broker-client container:
  * the broker is ENFORCING  (unauthenticated /v1/status -> 401), and
  * the container can AUTHENTICATE  (authenticated /v1/status with its own token -> 200).
Then it exercises job-aid's REAL chat + embed clients live (the outlier client), so a dropped
header on a specific method is caught, not just container env/enforcement.

Usage:   pwsh deploy/bin/broker-auth-smoke.ps1
Exit 0 = all PASS (or broker unenforced = WARN); non-zero = at least one rail FAILs to authenticate.
#>
# Continue (not Stop): docker writes progress to stderr, and PS 5.1 turns a native command's
# stderr under `2>&1` into terminating NativeCommandErrors — which would abort the sweep.
$ErrorActionPreference = "Continue"
$env:Path = "$env:ProgramFiles\Docker\Docker\resources\bin;$env:Path"
$probe = Join-Path $PSScriptRoot "broker_auth_probe.py"
# Ship the probe as base64 to `python -c` (no docker cp): robust even where a container's /tmp is
# read-only or restricted (e.g. the sandboxed terminal-fun rail).
$probeB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes((Get-Content $probe -Raw)))
$probeCmd = "import base64; exec(base64.b64decode('$probeB64').decode('utf-8'))"

# Rail -> container. Every rail that calls the broker via its own client, plus the gateway (which
# uses platform_core's BrokerClient). Keep in sync when a rail is added.
$containers = [ordered]@{
  "job-aid"       = "platform-job-aid-1"
  "finance"       = "platform-finance-1"
  "recipe-book"   = "platform-recipe-book-1"
  "bouquet"       = "platform-bouquet-1"
  "terminal-fun"  = "platform-terminal-fun-1"
  "ai-playground" = "platform-ai-playground-1"
  "edu-suite"     = "platform-dashboard-1"
  "iep"           = "platform-iep-1"
  "ai-voice"      = "platform-ai-voice-1"
  "gateway"       = "platform-gateway-1"
}

$running = @(docker ps --format "{{.Names}}")
$pass = 0; $warn = 0; $fail = 0
Write-Host "== Broker-auth smoke: /v1/status enforcement + per-container token ==`n"
foreach ($rail in $containers.Keys) {
  $c = $containers[$rail]
  if ($running -notcontains $c) { Write-Host ("{0,-14} SKIP  ({1} not running)" -f $rail, $c); continue }
  $line = (docker exec $c python -c $probeCmd 2>&1) -join " "
  Write-Host ("{0,-14} {1}" -f $rail, $line)
  if     ($line -match "^\s*PASS") { $pass++ }
  elseif ($line -match "^\s*WARN") { $warn++ }
  else   { $fail++ }
}

# job-aid was the outlier (urllib client with per-call-site auth); exercise its REAL broker paths so
# a method that forgets the header (the fixed bug) fails here, not just the env/enforcement probe.
Write-Host "`n== job-aid real client exercise (chat + embed via broker) =="
$ja = $containers["job-aid"]
if ($running -contains $ja) {
  $jaPy = @'
from job_aid.llm.client import LLMConfig, generate, embed
cfg = LLMConfig.from_env()
r = generate("ping - reply with the word ok.", config=cfg)
v = embed(["ping"], config=cfg)
print("chat_ok=%s embed_ok=%s dim=%d" % (bool(r.text), len(v) == 1, len(v[0]) if v else 0))
'@
  $jaB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($jaPy))
  $out = (docker exec $ja python -c "import base64; exec(base64.b64decode('$jaB64').decode('utf-8'))" 2>&1) -join " "
  Write-Host ("  {0}" -f $out)
  if ($LASTEXITCODE -ne 0 -or $out -match "401|HTTPError|Traceback") { $fail++; Write-Host "  -> job-aid client FAIL" }
  else { Write-Host "  -> job-aid client PASS" }
}

Write-Host ("`n== Result: {0} pass, {1} warn, {2} fail ==" -f $pass, $warn, $fail)
if ($fail -gt 0) { Write-Host "One or more rails cannot authenticate to the broker." ; exit 1 }
exit 0
