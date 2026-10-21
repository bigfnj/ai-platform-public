<#
  Register (or update) the platform-watchdog NSSM service.

  Must run elevated (an administrator, or LocalSystem). Idempotent: safe to re-run after an upgrade.

  Usage, from an elevated PowerShell (the standard UAC or BeyondTrust prompt; <repo> is the checkout):
    & '<repo>\deploy\installer\register-watchdog.ps1'
  An elevation as another account (an administrator signing in over your shoulder) has that
  account's USERPROFILE: pass -WatchdogUser <your account name> then. Without it, a checkout in
  another user's profile folder is refused, naming the -WatchdogUser to pass.
  Optional, on a box that has RunAS Helper, with its gate open, from a non-elevated shell (the
  console launcher, which waits and hands back the output and the exit code):
    RunAsHelper /capture /timeout:300 /ps:5 /as:system "<repo>\deploy\installer\register-watchdog.ps1"
  Prints DONE or FAILED with the reason and exits 0 or 1; the full log is
  C:\Users\Public\register-watchdog.txt. WATCHDOG.md has the whole procedure.

  The install root and the user profile are auto-detected from well-known paths.
  Override with env vars:
    AIPLATFORM_DIR   — install root   (default: %USERPROFILE%\ai-platform resolved
                                       from the script's own location)
    WATCHDOG_USER    — Windows account the env vars are baked for
                       (default: the caller's USERPROFILE, refused when the install
                       root's parent is ANOTHER user's profile folder; run as SYSTEM, the
                       install root's parent when that is a user's profile folder, else
                       refused).
                       Also -WatchdogUser <name>, after the script path on either command line.
#>
param([string]$WatchdogUser = $env:WATCHDOG_USER)
$out = 'C:\Users\Public\register-watchdog.txt'
"=== register-watchdog  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File $out -Encoding utf8
"whoami: $(whoami)" | Out-File $out -Append -Encoding utf8

$ErrorActionPreference = 'Continue'

# A refusal or a failure goes to the console as well as to $out, and ends the run with exit 1. It
# used to reach $out alone and `return`, and the script has no other exit, so run through RunAS
# Helper's /capture (which hands back the console and the script's own exit code) a refused
# registration exited 0 with nothing on screen. Success ends with exit 0 after DONE.
function Stop-Registration([string]$Message) {
    "ERROR: $Message" | Out-File $out -Append -Encoding utf8
    "FAILED" | Out-File $out -Append -Encoding utf8
    Write-Host "register-watchdog FAILED: $Message (log: $out)" -ForegroundColor Red
    exit 1
}

# The service's log settings (lib-runtime Get-NssmLogSettings), each nssm call's output appended to
# this registrar's log, then the check that NSSM kept the Rotate/Post prune hook. NSSM 2.24 has no
# event hooks and refuses it: not fatal, since the watchdog prunes deploy\logs once a day itself, but
# said, in the log and as a warning. A function so a test can run it with a stand-in nssm. Returns
# the warning, '' when the hook is there.
function Set-WatchdogServiceLogs([string]$NssmExe, [string]$Name, [string]$LogFile, [string]$Lib, [string]$Out) {
    $hookExit = 0
    foreach ($a in @(Get-NssmLogSettings -Name $Name -Stdout $LogFile -Stderr $LogFile -Lib $Lib -RotateBytes 524288)) {
        & $NssmExe @a 2>&1 | Out-File $Out -Append -Encoding utf8
        if (Test-NssmHookSetting $a) { $hookExit = $LASTEXITCODE }
    }
    $warning = Get-NssmHookWarning -Name $Name -HookExit $hookExit -WatchdogRegistered $true
    if ($warning) {
        "WARNING: $warning" | Out-File $Out -Append -Encoding utf8
        Write-Warning $warning
    }
    return $warning
}

try {
    # --- resolve paths -------------------------------------------------------
    $Installer  = $PSScriptRoot                                   # ..\deploy\installer
    $Root       = Split-Path (Split-Path $Installer -Parent) -Parent   # install root

    # Allow env-var overrides
    if ($env:AIPLATFORM_DIR)  { $Root        = $env:AIPLATFORM_DIR }

    # The user's profile. Podman keeps its machine config and SSH key there, and the service
    # (LocalSystem) is given this via AppEnvironmentExtra so it can reach the machine. The real
    # $env:USERPROFILE is preferred to the install path's parent (a checkout on another drive has
    # none), but not when it is SYSTEM's own, which it is through the RunAS Helper gate:
    # lib-runtime's Resolve-WatchdogProfile says the order, and refuses when no user is found.
    $lib = Join-Path $Installer 'lib-runtime.ps1'
    . $lib
    $prof = Resolve-WatchdogProfile -EnvProfile $env:USERPROFILE -Root $Root -WatchdogUser $WatchdogUser
    if ($prof.Problem) { Stop-Registration $prof.Problem }
    $UserProfile = $prof.Profile
    "profile from: $($prof.Why)" | Out-File $out -Append -Encoding utf8

    $NssmExe    = Join-Path $Root 'deploy\bin\nssm.exe'
    $WatchdogPs = Join-Path $Installer 'platform-watchdog.ps1'

    "Root:        $Root"        | Out-File $out -Append -Encoding utf8
    "UserProfile: $UserProfile" | Out-File $out -Append -Encoding utf8
    "nssm:        $NssmExe"     | Out-File $out -Append -Encoding utf8
    "watchdog:    $WatchdogPs"  | Out-File $out -Append -Encoding utf8

    foreach ($p in @($NssmExe, $WatchdogPs)) {
        if (-not (Test-Path $p)) { Stop-Registration "Required file not found: $p" }
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

    # Stdout/stderr → watchdog-svc.log: rotated at start and online at 512 KB, rotated copies pruned
    # (lib-runtime Get-NssmLogSettings; it used to rotate only at start and keep every copy), and a
    # warning when this NSSM kept no prune hook (Set-WatchdogServiceLogs, above).
    $logFile = Join-Path $Root 'deploy\logs\watchdog-svc.log'
    $null = Set-WatchdogServiceLogs -NssmExe $NssmExe -Name $svc -LogFile $logFile -Lib $lib -Out $out

    "starting service..." | Out-File $out -Append -Encoding utf8
    & $NssmExe start $svc 2>&1 | Out-File $out -Append -Encoding utf8

    Start-Sleep 3
    $status = (Get-Service $svc -ErrorAction SilentlyContinue).Status
    "service status: $status" | Out-File $out -Append -Encoding utf8

    "DONE" | Out-File $out -Append -Encoding utf8
    Write-Host "register-watchdog DONE: platform-watchdog is registered, service status: $status (log: $out)"
    exit 0
} catch {
    Stop-Registration "$_"
}
