<#
  Make the container runtime agree with PLATFORM_DISABLED_APPS in deploy\.env: a rail disabled on
  this box is not RUNNING on this box either.

  The gateway stops routing a disabled rail, but its container kept running, and came back at every
  boot, because every service is `restart: always` and the engine restarts such containers itself.
  This step, for each disabled rail's compose services (its own, and any labelled
  ai-platform.rail=<rail>, such as openmaic-app for openmaic):
    - sets the container's restart policy to `no`, so no engine restart brings it back;
    - stops it if it is running.
  And the way back: a container this step parked whose rail is NOT disabled any more gets its
  compose restart policy back and is started. So re-enabling is "remove it from the list, run this".
  Idempotent: a second run changes nothing and says so.

  rebuild-stale.ps1 and platform-startup.ps1 run the same step (lib-runtime's Sync-DisabledRails);
  this script is the way to run it by hand.

  Usage (from the deployment checkout):
    powershell -ExecutionPolicy Bypass -File deploy\installer\sync-disabled-rails.ps1 -FullStack -DryRun
    powershell -ExecutionPolicy Bypass -File deploy\installer\sync-disabled-rails.ps1 -FullStack

  -FullStack reads deploy\docker-compose.yml (the 24 GB Docker Desktop box); without it, the lean
  installer compose plus the box's override (a Podman box). -DeployDir points at another checkout's
  deploy folder (read-only use, e.g. a dry run from a worktree). Exit 0 when every action worked
  (or, with -DryRun, when the plan could be made), 1 otherwise.
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$FullStack,
  [ValidateSet('', 'podman', 'desktop', 'wsl')]
  [string]$RuntimeMode = '',
  [string]$DeployDir = ''
)

$ErrorActionPreference = 'Continue'
# Resolved here, not as the param() default: Windows PowerShell 5.1 started with -File leaves
# $PSScriptRoot empty while an advanced script's defaults are evaluated, and Split-Path refused
# the empty string, so the script never started.
if (-not $DeployDir) { $DeployDir = Split-Path $PSScriptRoot -Parent }
$DeployDir = (Resolve-Path -LiteralPath $DeployDir).Path
$Root      = Split-Path $DeployDir -Parent
$Installer = Join-Path $DeployDir 'installer'
$TempBase  = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
$LogFile   = Join-Path $TempBase 'ai-platform-sync-disabled-rails.log'
function Write-Log($m) { Add-Content -Path $LogFile -Value ("{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m) -Encoding utf8 }
. (Join-Path $PSScriptRoot 'lib-runtime.ps1')

# Before any CLI runs, as smoke-test and platform-startup do: a ~/.docker/config.json that is a
# DIRECTORY fails podman's own commands with exit 125, and makes compose warn on every call.
$dockerConfig = Repair-DockerConfigEnv
if ($dockerConfig) { Write-Host "WARNING: $($dockerConfig.Warning)" -ForegroundColor Yellow }

$envFile = Join-Path $DeployDir '.env'
$why = 'from -RuntimeMode'
if (-not $RuntimeMode) {
  $pick = Resolve-PlatformRuntime -EnvFile $envFile
  $RuntimeMode = $pick.Mode; $why = $pick.Reason
  if ($pick.Warning) { Write-Host "WARNING: $($pick.Warning)" -ForegroundColor Yellow }
}
Write-Host "runtime: $RuntimeMode ($why)"

try { $disabled = @(Get-DeclaredAppList -Key 'PLATFORM_DISABLED_APPS' -EnvFile $envFile) }
catch { Write-Host "PLATFORM_DISABLED_APPS does not parse ($($_.Exception.Message)); the gateway refuses it too. Nothing done." -ForegroundColor Red; exit 1 }
Write-Host ("disabled on this box: " + $(if ($disabled.Count) { $disabled -join ', ' } else { '(none)' }))

# -NoLog: the full render carries every secret interpolated from .env.
if ($FullStack) {
  $cargs = @('--env-file', $envFile, '-f', (Join-Path $DeployDir 'docker-compose.yml'))
} else {
  # EVERY enabled rail's profile, disabled ones included: a profiled-out service is absent from
  # the render, and its container could then not be found to park.
  $paths = Get-ComposePaths -Mode $RuntimeMode
  $cargs = @('--env-file', $paths.Env) + $paths.FileArgs + (Get-ComposeProfiles -Apps (
    @(Get-DeclaredAppList -Key 'PLATFORM_ENABLED_APPS' -EnvFile $envFile) -join ','))
}
# Stdout only (Invoke-ComposeConfigJson): a warning on compose's stderr used to land in the JSON.
$render = Invoke-ComposeConfigJson -Arguments $cargs -Mode $RuntimeMode
$services = @()
if ($render.Code -eq 0) { try { $services = @(Get-ComposeServices -ConfigJson $render.Json) } catch {} }
if ($services.Count -eq 0) {
  $why = if (@($render.Err).Count) { "compose said: $(@($render.Err)[-1])" } else { "compose config exit $($render.Code), no services in the render" }
  Write-Host "could not read the compose services ($why); nothing done." -ForegroundColor Red; exit 1
}

$res = Sync-DisabledRails -Disabled $disabled -Services $services -RailsDir (Join-Path $Root 'rails') -Mode $RuntimeMode -DryRun:$DryRun
foreach ($l in $res.Lines) { Write-Host "  $l" }
if (-not $res.Lines.Count) { Write-Host '  nothing to do: no disabled rail runs, and no parked container waits to come back.' }
if ($res.Failed -gt 0) { Write-Host "$($res.Failed) action(s) failed." -ForegroundColor Red; exit 1 }
exit 0
