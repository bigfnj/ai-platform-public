<#
  AI-Platform logon startup (Podman / Docker Desktop on Windows).

  Installed by install.ps1 as a Startup-folder shortcut:
    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AI-Platform startup.lnk
  (Task Scheduler is Access-denied for non-elevated users on managed boxes.)

  What it does, in order:
    1. starts the podman machine if it isn't running
    2. re-detects the container -> Windows host address and rewrites WINDOWS_HOST in deploy/.env
    3. brings the compose stack up (idempotent; recreates only what changed)

  This is the Podman counterpart of platform-startup.sh, and it is deliberately SHORTER: a Hyper-V
  podman machine does not idle-shut-down the way WSL2 does, so there is no `sleep infinity`
  keep-alive to hold a VM up. Containers with `restart: always` are brought back inside the machine
  by podman-restart.service (enabled at install time).

  Run by hand to bring the platform up:
    powershell -ExecutionPolicy Bypass -File deploy\installer\platform-startup.ps1
#>
[CmdletBinding()]
param(
  # Override the auto-detected runtime; normally inferred from what is installed.
  [ValidateSet('', 'podman', 'desktop')]
  [string]$RuntimeMode
)

$ErrorActionPreference = 'Continue'   # native tools report status on stderr; judge by exit codes

$Installer = $PSScriptRoot
$Root      = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TempBase  = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
$LogDir    = Join-Path $Root 'deploy\logs'
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {}
$LogFile   = Join-Path $LogDir 'startup.log'

# Same PATH problem as install.ps1: podman/docker-compose land on the USER PATH, which a session
# started before their install cannot see.
foreach ($dir in @((Join-Path $env:LOCALAPPDATA 'Programs\Podman'), (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links'))) {
  if ((Test-Path $dir) -and ($env:Path -notlike "*$dir*")) { $env:Path = "$dir;$env:Path" }
}

function Write-Log($m) {
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Host $line
}

. (Join-Path $PSScriptRoot 'lib-runtime.ps1')

if (-not $RuntimeMode) {
  $RuntimeMode = if (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' } else { 'desktop' }
}

# Two things can start the platform: the logon Startup shortcut, and the platform-watchdog service
# (which also triggers this script in the user's session). Two concurrent `podman machine start`
# runs fight over \\.\pipe\<machine> and can leave the machine unstartable by ANY account, so
# serialise them. A lock FILE rather than a Global\ mutex: creating a Global object needs a
# privilege a standard user does not have, and these two callers run under different accounts.
$LockFile = Join-Path $LogDir 'startup.lock'

function Enter-StartupLock {
  # 24 x 5 s = 2 min. If the other run has not finished by then it is either done enough that the
  # platform is up, or it is in trouble - and a second concurrent machine start would not help either.
  for ($i = 0; $i -lt 24; $i++) {
    if (Test-Path $LockFile) {
      # Honour a live lock, but drop one whose owner died or wedged - a run that exits early (or is
      # killed) would otherwise deadlock every future startup behind a file nobody owns.
      $stale = $false
      try {
        $info = Get-Content $LockFile -Raw -ErrorAction Stop | ConvertFrom-Json
        if (-not (Get-Process -Id $info.pid -ErrorAction SilentlyContinue)) { $stale = $true }
        elseif (((Get-Date) - [datetime]$info.at).TotalMinutes -gt 10) { $stale = $true }
      } catch { $stale = $true }

      if ($stale) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
      else { Start-Sleep -Seconds 5; continue }
    }
    try {
      # CreateNew + no sharing: whoever wins the race owns the lock and the loser retries.
      $fs = [System.IO.File]::Open($LockFile, 'CreateNew', 'Write', 'None')
      $bytes = [Text.Encoding]::UTF8.GetBytes((@{ pid = $PID; at = (Get-Date).ToString('o') } | ConvertTo-Json -Compress))
      $fs.Write($bytes, 0, $bytes.Length)
      $fs.Close()
      return $true
    } catch { Start-Sleep -Seconds 2 }
  }
  return $false
}

function Exit-StartupLock {
  try {
    $info = Get-Content $LockFile -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($info.pid -eq $PID) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
  } catch {}
}

if (-not (Enter-StartupLock)) {
  Write-Log 'another startup run holds the lock and has not finished; leaving the work to it.'
  exit 0
}

Write-Log "=== AI-Platform startup ($RuntimeMode) ==="

$envFile = Join-Path $Root 'deploy\.env'
if (-not (Test-Path $envFile)) { Write-Log "no deploy\.env - is the platform installed? aborting."; exit 1 }

# 1. runtime up
if ($RuntimeMode -eq 'podman') {
  if (-not (Initialize-PodmanMachine)) {
    # This runs unattended at logon, so the log is the ONLY place a failure can be seen. Say why.
    $advice = Get-PodmanMachineMemoryAdvice
    if ($advice) { Write-Log "  $advice" }
    Write-Log 'the podman machine did not start; aborting.'
    exit 1
  }
  Write-Log 'podman machine is running.'
}

# 2. re-sync the host address. It is stable for gvproxy but dynamic for WSL, and re-detecting costs
# nothing - a stale value here is the classic "everything is up but every rail 502s" failure.
$winHost = Get-ContainerHostIp -Mode $RuntimeMode
if ($winHost) {
  Set-EnvValue -Path $envFile -Key 'WINDOWS_HOST' -Value $winHost
  Write-Log "WINDOWS_HOST=$winHost"
}

# 3. compose up, with the profiles that match the installed rails
$apps = ''
foreach ($l in Get-Content $envFile) { if ($l -match '^\s*PLATFORM_ENABLED_APPS\s*=\s*(.*)$') { $apps = $Matches[1].Trim() } }
Write-Log "enabled apps: $apps"

$paths = Get-ComposePaths -Mode $RuntimeMode
$cargs = @('--env-file', $paths.Env, '-f', $paths.Compose)
$cargs += Get-ComposeProfiles -Apps $apps
Initialize-ComposeVolumes -ComposeArgs $cargs -Mode $RuntimeMode   # external volumes must pre-exist
$cargs += @('up', '-d')
Invoke-Compose -Arguments $cargs -Mode $RuntimeMode
if ($LASTEXITCODE -ne 0) { Write-Log "compose up failed (exit $LASTEXITCODE)"; exit 1 }

# 4. wait for the front door so the log records a usable end state
for ($i = 0; $i -lt 30; $i++) {
  try { Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null; break } catch { Start-Sleep 2 }
}
try {
  Invoke-WebRequest 'http://localhost:1111/api/platform/healthz' -TimeoutSec 3 -UseBasicParsing | Out-Null
  Write-Log 'platform is up at http://localhost:1111'
} catch {
  Write-Log 'WARNING: the gateway did not answer on :1111 - check `podman ps` and the container logs.'
}
Write-Log '=== startup done ==='
Exit-StartupLock
