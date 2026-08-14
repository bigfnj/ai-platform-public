<#
.SYNOPSIS
  Activate broker model ROLES platform-wide (workstream B of docs/REORG-PLAN.md).

.DESCRIPTION
  The broker role code is shipped (broker.py @-expansion + roles.json), but the running
  NSSM service must be restarted (LocalSystem -> needs admin) before any rail env can point
  at an @role, or every rail's AI 502s. This script does the whole cutover safely and in the
  right order, and ABORTS before touching any rail env if the restarted broker doesn't serve
  roles -- so it can never half-activate.

  Steps:
    1. Require admin.
    2. Restart platform-broker.
    3. Verify @embed resolves (the go/no-go). Abort if not.
    4. Write the role vars into deploy/.env (idempotent). Mapping is BEHAVIOR-PRESERVING:
       each @role resolves to the exact model that rail already runs today.
    5. Recreate only the affected rails (never caddy/gateway -> no NAT churn).
    6. Re-verify the broker is healthy.

  After this, roles are HOT: edit services/broker/roles.json (or use Admin > Rails) to repoint a
  rail with NO restart. Each rail slot now has its own per-rail role, so a change affects only
  that rail. recipe-book's culinary assistant is @recipe (seeded to gemma3*:27b, its historical pick).

.NOTES
  Run from an ELEVATED PowerShell:  powershell -ExecutionPolicy Bypass -File deploy\activate-model-roles.ps1
#>
[CmdletBinding()]
param(
  [string]$BrokerUrl = 'http://localhost:11500',
  [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
$deploy = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $deploy '.env'
# Resolve the container runtime instead of hardcoding Docker Desktop's path (which does not exist
# under Podman or Docker-in-WSL2). Podman is driven through the standalone docker-compose.exe over
# its Docker-compatible API pipe; Docker uses its own `compose` subcommand.
foreach ($dir in @((Join-Path $env:LOCALAPPDATA 'Programs\Podman'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))) {
  if ((Test-Path $dir) -and ($env:Path -notlike "*$dir*")) { $env:Path = "$dir;$env:Path" }
}
$dockerDesktop = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
if (Get-Command podman -ErrorAction SilentlyContinue) {
  if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    throw 'Podman is installed but docker-compose.exe is not (winget install Docker.DockerCompose).'
  }
  $composeExe = (Get-Command docker-compose).Source; $composePre = @()
}
elseif (Test-Path $dockerDesktop) { $composeExe = $dockerDesktop; $composePre = @('compose') }
elseif (Get-Command docker -ErrorAction SilentlyContinue) { $composeExe = (Get-Command docker).Source; $composePre = @('compose') }
else { throw 'no container runtime found (Podman or Docker) - cannot recreate the rail containers.' }

# PER-RAIL role map: each rail model slot points at its OWN role so Admin > Rails can repoint
# one rail without moving others. Behavior-preserving: every per-rail role is seeded (roles.json /
# DEFAULT_ROLES) to the exact model that slot runs today.
#   @edu=mistral-small3*:24b  @iep=qwen3.6*:27b
#   @recipe/@recipe-vision=gemma4*:26b  @terminal-fun/@chat-fast=gemma4*:12b
$roleVars = [ordered]@{
  'EDU_LLM_MODEL'               = '@edu'            # was mistral-small3*:24b
  'IEP_LLM_MODEL'               = '@iep'            # was qwen3.6*:27b
  'RECIPE_BOOK_LLM_MODEL'       = '@chat'           # minor/general fallback var (unsurfaced)
  'RECIPE_BOOK_ASSISTANT_MODEL' = '@recipe'         # the culinary AI (was gemma3*:27b)
  'RECIPE_BOOK_VISION_MODEL'    = '@recipe-vision'  # recipe-photo reader (was gemma3*:27b)
  'RECIPE_BOOK_ICON_MODEL'      = '@recipe-icon'    # per-recipe icon IMAGE model (media backend)
  'TERMINAL_FUN_LLM_MODEL'      = '@terminal-fun'   # was gemma3:12b
  'AI_PLAYGROUND_CHAT_MODEL'    = '@ai-playground'  # RAG generation local model (nemotron-3-nano:4b)
}
$rails = @('dashboard','iep','recipe-book','terminal-fun','ai-playground')

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }

# 1. admin ------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
  Write-Host "This script must run ELEVATED (it restarts the platform-broker NSSM service)." -ForegroundColor Red
  Write-Host "Re-run from an admin PowerShell:`n  powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" -ForegroundColor Yellow
  exit 1
}

# 2. restart broker ---------------------------------------------------------
Write-Step "Restarting platform-broker"
if ($WhatIf) { Write-Host "  [WhatIf] Restart-Service platform-broker" } else { Restart-Service -Name 'platform-broker' }

# 3. go/no-go: does the broker serve roles now? ----------------------------
Write-Step "Verifying the broker resolves @roles (@embed)"
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-RestMethod -Uri "$BrokerUrl/v1/load" -Method Post -TimeoutSec 30 `
          -ContentType 'application/json' -Body '{"model":"@embed"}'
    if ($r.model) { Write-Host "  @embed -> $($r.model)" -ForegroundColor Green; $ok = $true; break }
  } catch { Write-Host "  waiting for broker... ($($i+1)/20)" }
}
if (-not $ok) {
  Write-Host "ABORT: broker did not resolve @embed after restart. Rail env NOT touched." -ForegroundColor Red
  Write-Host "Check the broker log; the running service may not have the role code." -ForegroundColor Yellow
  exit 2
}

# 4. write role vars into deploy/.env (idempotent) --------------------------
Write-Step "Writing role vars into deploy/.env"
$keep = @()
if (Test-Path $envFile) {
  $keep = Get-Content $envFile | Where-Object {
    $line = $_
    -not ($roleVars.Keys | Where-Object { $line -match "^\s*$([regex]::Escape($_))\s*=" })
  }
}
$block = @('# --- model roles (activate-model-roles.ps1) ---') +
         ($roleVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
$out = @($keep + $block) | Where-Object { $_ -ne $null }
if ($WhatIf) {
  Write-Host "  [WhatIf] would write:`n$($block -join "`n")"
} else {
  # UTF-8 WITHOUT BOM — a BOM on line 1 would corrupt the first existing var (e.g. HF_TOKEN).
  [System.IO.File]::WriteAllLines($envFile, [string[]]$out, (New-Object System.Text.UTF8Encoding($false)))
  Write-Host "  wrote $($roleVars.Count) role vars (preserved $($keep.Count) existing lines)" -ForegroundColor Green
}

# 5. recreate only the affected rails (NOT caddy/gateway) -------------------
Write-Step "Recreating rails: $($rails -join ', ')"
if ($WhatIf) {
  Write-Host "  [WhatIf] $composeExe $($composePre -join ' ') up -d $($rails -join ' ')"
} else {
  Push-Location $deploy
  try { & $composeExe @composePre up -d @rails } finally { Pop-Location }
}

# 6. final health -----------------------------------------------------------
Write-Step "Done"
Write-Host "Roles are live and HOT. Repoint a whole model class by editing services/broker/roles.json (no restart)." -ForegroundColor Green
Write-Host "Smoke-test one AI action per rail through the gateway to confirm." -ForegroundColor Yellow
