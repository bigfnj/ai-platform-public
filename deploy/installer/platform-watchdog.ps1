<#
  AI-Platform watchdog service script.

  Runs as an NSSM system service (LocalSystem) with the user's environment
  variables injected via NSSM AppEnvironmentExtra, so Podman can find its
  machine config and SSH key in the user profile.

  What it does:
    Every 5 minutes: GET /api/platform/healthz on localhost:1111.
    Two consecutive failures (30 s apart) → invoke platform-startup.ps1 to
    restart the Podman machine and bring the compose stack back up.
    One failure that self-heals → logged as a transient blip, no restart.

  Installed by install.ps1 (Podman mode) and register-watchdog.ps1.
  Log: <install-root>\deploy\logs\watchdog.log  (rotates at 500 KB).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

# NSSM injects USERPROFILE / APPDATA / LOCALAPPDATA / HOME via AppEnvironmentExtra.
# Add Podman and WinGet to PATH exactly as platform-startup.ps1 does.
foreach ($d in @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Podman'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
)) {
    if ((Test-Path $d) -and ($env:Path -notlike "*$d*")) { $env:Path = "$d;$env:Path" }
}

$Installer   = $PSScriptRoot
$Root        = Split-Path (Split-Path $Installer -Parent) -Parent
$LogDir      = Join-Path $Root 'deploy\logs'
try { New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction Stop | Out-Null } catch {}
$LogFile     = Join-Path $LogDir 'watchdog.log'
$MaxLogBytes = 500KB
$StartupPs1  = Join-Path $Installer 'platform-startup.ps1'
$HealthUrl   = 'http://localhost:1111/api/platform/healthz'

function Write-Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    try {
        if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt $MaxLogBytes) {
            Move-Item $LogFile "$LogFile.1" -Force -ErrorAction SilentlyContinue
        }
        Add-Content -Path $LogFile -Value $line -Encoding utf8
    } catch {}
    Write-Host $line
}

function Test-Health {
    try {
        $r = Invoke-WebRequest $HealthUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

Write-Log "=== platform-watchdog started (pid=$PID, user=$env:USERPROFILE) ==="

while ($true) {
    Start-Sleep -Seconds 300   # check every 5 minutes

    if (Test-Health) { continue }

    Write-Log 'health check failed; confirming in 30 s...'
    Start-Sleep -Seconds 30

    if (Test-Health) {
        Write-Log 'transient blip — platform recovered on its own'
        continue
    }

    Write-Log 'platform confirmed down; invoking platform-startup.ps1 for recovery'
    try {
        & powershell.exe -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $StartupPs1
        Write-Log "recovery script exited (code $LASTEXITCODE)"
    } catch {
        Write-Log "recovery invocation failed: $_"
    }
}
