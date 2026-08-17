<#
  AI-Platform watchdog service script.

  Runs as an NSSM system service (LocalSystem) with the user's environment
  variables injected via NSSM AppEnvironmentExtra, so Podman can find its
  machine config and SSH key in the user profile.

  This service owns the platform lifecycle end to end:

    COLD START (service start / boot)
      Health is checked FIRST - a service restart mid-session must never bounce
      a platform that is already up. If it is down, platform-startup.ps1 runs
      immediately and is retried a few times: the service starts early in boot
      and Hyper-V's VMMS may not be ready to start the podman machine yet.

    STEADY STATE (every 5 minutes)
      GET /api/platform/healthz on localhost:1111.
      Two consecutive failures (30 s apart) -> platform-startup.ps1 to restart
      the podman machine and bring the compose stack back up.
      One failure that self-heals -> logged as a transient blip, no restart.

  Because cold start is handled here, no logon Startup shortcut is required:
  the platform comes up at boot, before any user logs on.

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

$PollSeconds     = 300   # steady-state health check interval
$ConfirmSeconds  = 30    # gap before a second opinion on a failed check
$ColdAttempts    = 6     # cold-start tries before falling back to the poll loop
$ColdRetrySeconds = 30   # gap between cold-start tries

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

# platform-startup.ps1 is idempotent: it starts the machine only if stopped, re-syncs WINDOWS_HOST,
# and `compose up -d` recreates only what changed. Safe to call for both cold start and recovery.
function Invoke-PlatformStartup($why) {
    Write-Log "$why - running platform-startup.ps1"
    try {
        & powershell.exe -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $StartupPs1
        Write-Log "platform-startup.ps1 exited (code $LASTEXITCODE)"
    } catch {
        Write-Log "recovery invocation failed: $_"
    }
}

Write-Log "=== platform-watchdog started (pid=$PID, user=$env:USERPROFILE) ==="

try {
    # --- cold start ----------------------------------------------------------
    if (Test-Health) {
        Write-Log 'platform already healthy at service start; entering monitor loop.'
    }
    else {
        for ($i = 1; $i -le $ColdAttempts; $i++) {
            Invoke-PlatformStartup "cold start (attempt $i/$ColdAttempts)"
            if (Test-Health) { Write-Log 'platform is up; entering monitor loop.'; break }
            if ($i -lt $ColdAttempts) {
                # Usually Hyper-V's VMMS is not ready yet this early in boot; it is worth waiting for.
                Write-Log "still down; retrying in $ColdRetrySeconds s"
                Start-Sleep -Seconds $ColdRetrySeconds
            }
            else {
                Write-Log 'WARNING: cold start did not succeed; falling back to the monitor loop.'
            }
        }
    }

    # --- steady state --------------------------------------------------------
    while ($true) {
        Start-Sleep -Seconds $PollSeconds

        if (Test-Health) { continue }

        Write-Log "health check failed; confirming in $ConfirmSeconds s..."
        Start-Sleep -Seconds $ConfirmSeconds

        if (Test-Health) {
            Write-Log 'transient blip - platform recovered on its own'
            continue
        }

        Invoke-PlatformStartup 'platform confirmed down'
    }
}
catch {
    # The loop above never returns normally, so reaching here means an unhandled fault. Record it:
    # NSSM restarts the process, and without this the only trace was a bare second "started" line.
    Write-Log "FATAL: watchdog loop threw: $_"
    Write-Log $_.ScriptStackTrace
    throw
}
finally {
    Write-Log '=== platform-watchdog exiting ==='
}
