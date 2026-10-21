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
  [switch]$WhatIf,
  # Normally the runtime whose CLI lists the platform's containers; this overrides.
  [ValidateSet('', 'podman', 'desktop')]
  [string]$RuntimeMode = '',
  # Print the runtime and compose entry point this run would use, and why, then exit.
  [switch]$ShowRuntime,
  # Only write the role vars into deploy\.env (step 4) and exit: no admin, no broker restart, no
  # recreate. For preparing a .env before the cutover, and how the .env write is tested.
  [switch]$EnvOnly
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
if ((Test-Path $dockerDesktop) -and ($env:Path -notlike "*$(Split-Path $dockerDesktop)*")) {
  $env:Path = "$(Split-Path $dockerDesktop);$env:Path"
}
# PER-RAIL role map: each rail model slot points at its OWN role, so Admin > Rails can repoint one
# rail without moving others. READ from deploy\docker-compose.yml, every `NAME: ${NAME:-@role}`
# there, rather than listed here: the hand list had stopped at 14 of the stack's role variables (no
# co-worker, smb-partner-enablement, gemini-cx, openmaic or servicenow, and no embedder slot at all)
# while this script said it activated roles platform-wide. $rails is the services that use them.
function Get-ComposeRoleVars([string]$ComposeFile) {
  if (-not (Test-Path -LiteralPath $ComposeFile)) { throw "no $ComposeFile to read the role variables from" }
  $vars = [ordered]@{}; $services = New-Object System.Collections.Generic.List[string]
  $svc = $null; $inServices = $false
  foreach ($l in [System.IO.File]::ReadAllLines($ComposeFile, [System.Text.Encoding]::UTF8)) {
    if ($l -match '^(\S[^:]*):') { $inServices = ($Matches[1] -eq 'services'); $svc = $null; continue }
    if ($inServices -and $l -match '^  ([a-z0-9][a-z0-9_-]*):\s*(#.*)?$') { $svc = $Matches[1]; continue }
    if ($svc -and $l -cmatch '^\s+([A-Z][A-Z0-9_]*):\s*\$\{([A-Z][A-Z0-9_]*):-(@[a-z0-9][a-z0-9-]*)\}' -and $Matches[1] -ceq $Matches[2]) {
      if (-not $vars.Contains($Matches[1])) { $vars[$Matches[1]] = $Matches[3] }
      if (-not $services.Contains($svc)) { $services.Add($svc) }
    }
  }
  if ($vars.Count -eq 0) { throw "found no `${NAME:-@role} variables in $ComposeFile" }
  return @{ Vars = $vars; Services = $services.ToArray() }
}
$composeRoles = Get-ComposeRoleVars (Join-Path $deploy 'docker-compose.yml')
$roleVars = $composeRoles.Vars
$rails = @($composeRoles.Services)

# The role vars, written through lib-runtime's Set-EnvBlock: UTF-8 on both engines, the old lines
# and the old header dropped first, so a re-run leaves deploy\.env byte-identical. The inline
# version read the file as ANSI under Windows PowerShell 5.1 (a non-ASCII byte came back as two or
# three characters, then went out as UTF-8) and kept the previous header while adding a new one.
$roleHeader = '# --- model roles (activate-model-roles.ps1) ---'
. (Join-Path $deploy 'installer\lib-runtime.ps1')
if ($EnvOnly) {
  Set-EnvBlock -Path $envFile -Values $roleVars -Header $roleHeader
  Write-Host "wrote $($roleVars.Count) role vars to $envFile"
  exit 0
}

# The runtime whose CLI lists the platform's containers (lib-runtime's Resolve-PlatformRuntime),
# not the first one installed: "podman is on PATH" sent a Docker Desktop box's rail recreate to a
# podman machine that holds none of them. (lib-runtime is dot-sourced above, for Set-EnvBlock.)
# Its own variable, not $RuntimeMode: that parameter's ValidateSet has no 'wsl', so assigning a
# resolved 'wsl' to it threw a validation error instead of the message below.
$mode = $RuntimeMode; $runtimeWhy = 'from -RuntimeMode'; $runtimeWarning = ''
if (-not $mode) {
  $pick = Resolve-PlatformRuntime -EnvFile $envFile
  $mode = $pick.Mode
  $runtimeWhy = $pick.Reason
  $runtimeWarning = $pick.Warning
}
if ($runtimeWarning) { Write-Host "WARNING: $runtimeWarning" -ForegroundColor Yellow }
if ($mode -eq 'podman') {
  if (-not (Get-Command docker-compose -ErrorAction SilentlyContinue)) {
    throw 'Podman is installed but docker-compose.exe is not (winget install Docker.DockerCompose).'
  }
  $composeExe = (Get-Command docker-compose).Source; $composePre = @()
  # Name podman's pipe: with Docker Desktop also installed, //./pipe/docker_engine is Docker's.
  if (-not $env:DOCKER_HOST) { $env:DOCKER_HOST = "npipe:////./pipe/$PodmanMachine" }
}
elseif ($mode -eq 'desktop' -and (Get-Command docker -ErrorAction SilentlyContinue)) {
  $composeExe = (Get-Command docker).Source; $composePre = @('compose')
}
else { throw "no usable container runtime ($mode; $runtimeWhy) - cannot recreate the rail containers." }
if ($ShowRuntime) { Write-Host "runtime: $mode ($runtimeWhy); compose: $composeExe $($composePre -join ' ')"; exit 0 }


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
# With its token: the broker refuses /v1/* without it once one is configured, which the full stack
# always is and every lean install now is. This check used to send none, so it got 401 twenty
# times, called that "waiting for broker", and aborted as though @embed did not resolve.
Write-Step "Verifying the broker resolves @roles (@embed)"
$brokerHeaders = Get-BrokerAuthHeaders -EnvFile $envFile
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 3
  try {
    $r = Invoke-RestMethod -Uri "$BrokerUrl/v1/load" -Method Post -TimeoutSec 30 -Headers $brokerHeaders `
          -ContentType 'application/json' -Body '{"model":"@embed"}'
    if ($r.model) { Write-Host "  @embed -> $($r.model)" -ForegroundColor Green; $ok = $true; break }
  } catch {
    $code = 0; try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    if ($code -eq 401 -or $code -eq 403) {
      Write-Host "  the broker refused this script's token (HTTP $code): BROKER_AUTH_TOKEN in $envFile is not the one the service runs with." -ForegroundColor Red
      break
    }
    Write-Host "  waiting for broker... ($($i+1)/20)"
  }
}
if (-not $ok) {
  Write-Host "ABORT: broker did not resolve @embed after restart. Rail env NOT touched." -ForegroundColor Red
  Write-Host "Check the broker log; the running service may not have the role code." -ForegroundColor Yellow
  exit 2
}

# 4. write role vars into deploy/.env (idempotent) --------------------------
Write-Step "Writing role vars into deploy/.env"
$block = @($roleHeader) + ($roleVars.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })
if ($WhatIf) {
  Write-Host "  [WhatIf] would write:`n$($block -join "`n")"
} else {
  # UTF-8 WITHOUT BOM: a BOM on line 1 would corrupt the first existing var (e.g. HF_TOKEN).
  Set-EnvBlock -Path $envFile -Values $roleVars -Header $roleHeader
  Write-Host "  wrote $($roleVars.Count) role vars" -ForegroundColor Green
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
