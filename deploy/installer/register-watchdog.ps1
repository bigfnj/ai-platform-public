<#
  Register (or update) the platform-watchdog NSSM service.

  Must run elevated (LocalSystem / TrustedInstaller) — use the RunAS Helper gate.
  Idempotent: safe to re-run after an upgrade.

  Usage (from the RunAS gate):
    & "C:\Program Files\RunAsHelper\RunAsHelper.exe" /as:system `
        C:\...\ai-platform\deploy\installer\register-watchdog.ps1

  The install root and the user profile are auto-detected from well-known paths.
  Override with env vars:
    AIPLATFORM_DIR   — install root   (default: %USERPROFILE%\ai-platform resolved
                                       from the script's own location)
    WATCHDOG_USER    — Windows account the env vars are baked for
                       (default: resolved from the install path)
#>
$out = 'C:\Users\Public\register-watchdog.txt'
"=== register-watchdog  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $out -Encoding utf8
"whoami: $(whoami)" | Out-File $out -Append -Encoding utf8

$ErrorActionPreference = 'Continue'

try {
    # --- resolve paths -------------------------------------------------------
    $Installer  = $PSScriptRoot                                   # ..\deploy\installer
    $Root       = Split-Path (Split-Path $Installer -Parent) -Parent   # install root

    # Best-guess user profile from install root path  (C:\Users\<user>\ai-platform)
    $UserProfile = Split-Path $Root -Parent   # C:\Users\<user>

    # Allow env-var overrides
    if ($env:AIPLATFORM_DIR)  { $Root        = $env:AIPLATFORM_DIR }
    if ($env:WATCHDOG_USER)   { $UserProfile = "C:\Users\$env:WATCHDOG_USER" }

    $NssmExe    = Join-Path $Root 'deploy\bin\nssm.exe'
    $WatchdogPs = Join-Path $Installer 'platform-watchdog.ps1'

    "Root:        $Root"        | Out-File $out -Append -Encoding utf8
    "UserProfile: $UserProfile" | Out-File $out -Append -Encoding utf8
    "nssm:        $NssmExe"     | Out-File $out -Append -Encoding utf8
    "watchdog:    $WatchdogPs"  | Out-File $out -Append -Encoding utf8

    foreach ($p in @($NssmExe, $WatchdogPs)) {
        if (-not (Test-Path $p)) {
            "ERROR: Required file not found: $p" | Out-File $out -Append -Encoding utf8
            "FAILED" | Out-File $out -Append -Encoding utf8
            return
        }
    }

    # --- SeServiceLogonRight for LocalSystem is implicit; nothing to grant ---

    # --- register / update service -------------------------------------------
    $svc = 'platform-watchdog'

    # Remove if it already exists (nssm install is not idempotent)
    $existing = Get-Service $svc -ErrorAction SilentlyContinue
    if ($existing) {
        "removing existing service..." | Out-File $out -Append -Encoding utf8
        & $NssmExe stop   $svc confirm 2>&1 | Out-File $out -Append -Encoding utf8
        Start-Sleep 2
        & $NssmExe remove $svc confirm 2>&1 | Out-File $out -Append -Encoding utf8
        Start-Sleep 2
    }

    "installing service..." | Out-File $out -Append -Encoding utf8
    & $NssmExe install $svc powershell.exe 2>&1 | Out-File $out -Append -Encoding utf8

    $psArgs = "-NonInteractive -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$WatchdogPs`""

    & $NssmExe set $svc AppParameters       $psArgs                            2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppDirectory        $Installer                         2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc DisplayName         'AI-Platform Watchdog'             2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc Description         'Starts the AI-Platform stack at boot and restarts it if health checks fail.' 2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc Start               SERVICE_AUTO_START                 2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppStopMethodSkip   6                                  2>&1 | Out-File $out -Append -Encoding utf8

    # The watchdog cold-starts the podman machine, so it must not run before Hyper-V's VM management
    # service is up. Without this the first boot attempt fails and recovery waits out a retry cycle.
    & $NssmExe set $svc DependOnService     vmms                               2>&1 | Out-File $out -Append -Encoding utf8

    # The loop is infinite; any exit is a fault. Restart it, but back off so a crash-loop cannot spin.
    & $NssmExe set $svc AppExit Default     Restart                            2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppRestartDelay     15000                              2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppThrottle         10000                              2>&1 | Out-File $out -Append -Encoding utf8

    # Inject the user profile env so Podman finds its machine config + SSH key
    $envExtra = @(
        "USERPROFILE=$UserProfile",
        "APPDATA=$UserProfile\AppData\Roaming",
        "LOCALAPPDATA=$UserProfile\AppData\Local",
        "HOME=$UserProfile"
    )
    & $NssmExe set $svc AppEnvironmentExtra @envExtra 2>&1 | Out-File $out -Append -Encoding utf8

    # Stdout/stderr → watchdog.log (NSSM native rotation)
    $logFile = Join-Path $Root 'deploy\logs\watchdog-svc.log'
    & $NssmExe set $svc AppStdout          $logFile  2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppStderr          $logFile  2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppRotateFiles     1         2>&1 | Out-File $out -Append -Encoding utf8
    & $NssmExe set $svc AppRotateBytes     524288    2>&1 | Out-File $out -Append -Encoding utf8

    "starting service..." | Out-File $out -Append -Encoding utf8
    & $NssmExe start $svc 2>&1 | Out-File $out -Append -Encoding utf8

    Start-Sleep 3
    $status = (Get-Service $svc -ErrorAction SilentlyContinue).Status
    "service status: $status" | Out-File $out -Append -Encoding utf8

    "DONE" | Out-File $out -Append -Encoding utf8
} catch {
    "ERROR: $_" | Out-File $out -Append -Encoding utf8
    "FAILED" | Out-File $out -Append -Encoding utf8
}
