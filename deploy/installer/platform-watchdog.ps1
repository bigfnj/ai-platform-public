<#
  AI-Platform watchdog service script.

  Runs as an NSSM system service (LocalSystem) with the user's environment
  variables injected via NSSM AppEnvironmentExtra, so Podman can find its
  machine config and SSH key in the user profile.

  This service owns the platform lifecycle end to end.

    COLD START (service start / boot)
      Health is checked FIRST - restarting the service must never bounce a
      platform that is already up. If it is down, the platform is started; the
      attempt is retried a few times because the service starts early in boot
      and Hyper-V's VMMS may not be ready to boot the podman machine yet.

    STEADY STATE (every minute)
      GET /api/platform/healthz on localhost:1111. Two consecutive failures
      (30 s apart) -> restart the machine and bring the compose stack back up.
      A single failure that self-heals is logged as a blip, with no restart.

  WHO STARTS THE MACHINE MATTERS - the reason this file is more than a poll loop.

  gvproxy creates \\.\pipe\podman-machine-default with a DACL belonging to the
  account that started the machine. When LocalSystem starts it, the interactive
  user gets "permission denied" from docker-compose for the rest of the session -
  no rebuilds, no `compose up` - even though the platform is serving fine on
  :1111. Worse, `podman machine stop` does not reap the proxy, and the user
  cannot kill a SYSTEM-owned one, so the machine becomes unstartable by anyone.

  So: whenever somebody is logged on, the platform is started IN THEIR SESSION
  via a scheduled task, and the machine belongs to them. LocalSystem only starts
  it when nobody is logged on - and hands it back (stop, reap, restart in
  session) as soon as somebody logs on.

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
$LauncherVbs = Join-Path $Installer 'platform-startup-launcher.vbs'
$HealthUrl   = 'http://localhost:1111/api/platform/healthz'
$TaskName    = 'AI-Platform user-session start'

$PollSeconds     = 60    # steady-state health check interval
$ConfirmSeconds  = 30    # gap before a second opinion on a failed check
$ColdAttempts    = 6     # cold-start tries before falling back to the poll loop
$ColdRetrySeconds = 30   # gap between cold-start tries
$LogonWaitSeconds = 180  # at boot, how long to wait for a logon before starting as SYSTEM
$SettleSeconds   = 90    # how long a user-session start gets before we judge it

$RuntimeMode = 'podman'
$TempBase    = $env:TEMP

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

# Clear-StalePodmanProxy lives here; it is the only way to release a pipe held by another account.
. (Join-Path $Installer 'lib-runtime.ps1')

function Test-Health {
    try {
        $r = Invoke-WebRequest $HealthUrl -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

# The owner of a named process, or $null when there is no such process / the owner is unreadable.
function Get-ProcessOwner($name) {
    foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='$name'" -ErrorAction SilentlyContinue)) {
        $o = Invoke-CimMethod -InputObject $p -MethodName GetOwner -ErrorAction SilentlyContinue
        if ($o -and $o.ReturnValue -eq 0 -and $o.User) { return "$($o.Domain)\$($o.User)" }
    }
    return $null
}

# explorer.exe only runs on an interactive desktop, so its owner is the logged-on user.
function Get-InteractiveUser { return (Get-ProcessOwner 'explorer.exe') }

# Who owns the podman machine right now? gvproxy is started by whoever started the machine.
function Get-MachineOwner { return (Get-ProcessOwner 'gvproxy.exe') }

function Test-SystemOwned($owner) {
    return ($owner -and $owner -match '\\(SYSTEM|LOCAL SERVICE|NETWORK SERVICE)$')
}

# Never fight a compose command the user is running: a rebuild takes the gateway down for minutes,
# and a "recovery" fired into the middle of one would race their build for the same containers.
function Test-ComposeBusy {
    return [bool](Get-Process docker-compose, podman-compose -ErrorAction SilentlyContinue)
}

# Run the startup script in the interactive user's session, so the podman machine - and therefore
# \\.\pipe\podman-machine-default - belongs to them and their docker-compose keeps working.
# A scheduled task with an Interactive principal is the one handoff that needs no stored password.
function Start-PlatformInUserSession($user) {
    try {
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wscript.exe') `
                                          -Argument "`"$LauncherVbs`""
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                                                 -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal `
                               -Settings $settings -Force -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        return $true
    }
    catch {
        Write-Log "could not start in ${user}'s session: $($_.Exception.Message)"
        return $false
    }
}

function Start-PlatformAsSystem {
    try {
        & powershell.exe -NonInteractive -NoProfile -ExecutionPolicy Bypass -File $StartupPs1
        Write-Log "platform-startup.ps1 exited (code $LASTEXITCODE)"
    } catch {
        Write-Log "startup invocation failed: $_"
    }
}

# Bring the platform up, preferring the logged-on user's session so they keep compose access.
function Invoke-PlatformStartup($why) {
    $user = Get-InteractiveUser
    if ($user) {
        Write-Log "$why - starting in ${user}'s session (keeps the compose API usable for them)"
        if (Start-PlatformInUserSession $user) {
            for ($i = 0; $i -lt ($SettleSeconds / 5); $i++) {
                Start-Sleep -Seconds 5
                if (Test-Health) { return }
            }
            Write-Log 'the user-session start has not reported healthy yet'
            return
        }
        Write-Log 'falling back to a LocalSystem start'
    }
    else {
        Write-Log "$why - nobody is logged on; starting as LocalSystem"
    }
    Start-PlatformAsSystem
}

# Force-stop the Hyper-V VM, clear any stale proxy, then start the platform in the user's session.
#
# Used when the SSH transport is dead: `podman machine stop` uses SSH and will hang or fail, so we
# bypass it with a direct Hyper-V hard-stop. Only safe to call from LocalSystem (which has the
# rights to Stop-VM). After the stop, Clear-StalePodmanProxy and Invoke-PlatformStartup handle the
# rest the same way as a normal recovery.
function Invoke-ForceRestartMachine {
    Write-Log 'force-stopping Hyper-V VM to recover a dead SSH transport...'
    if (Get-Command Stop-VM -ErrorAction SilentlyContinue) {
        Stop-VM -Name $PodmanMachine -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
    }
    else { Write-Log 'Hyper-V module not available; skipping Stop-VM (Clear-StalePodmanProxy will try)' }
    Clear-StalePodmanProxy | Out-Null
    Invoke-PlatformStartup 'control-plane recovery'
}

# Give a SYSTEM-owned machine back to the user who just logged on. Only LocalSystem can do this:
# stopping the machine leaves the SYSTEM-owned gvproxy alive, and the user cannot kill it.
function Invoke-MachineHandoff($user, $owner) {
    Write-Log "machine is owned by $owner but $user is logged on - handing it back"
    & podman machine stop 2>&1 | Write-NativeLine | Out-Null
    Clear-StalePodmanProxy | Out-Null
    Invoke-PlatformStartup 'handoff'
}

Write-Log "=== platform-watchdog started (pid=$PID, user=$env:USERPROFILE) ==="

try {
    # --- cold start ----------------------------------------------------------
    if (Test-Health) {
        Write-Log 'platform already healthy at service start; entering monitor loop.'
    }
    else {
        # Wait briefly for a logon before starting anything. On a workstation somebody logs on within
        # a minute of boot, and starting in their session now avoids having to bounce the platform to
        # hand it over a moment later.
        for ($w = 0; $w -lt ($LogonWaitSeconds / 10); $w++) {
            if (Get-InteractiveUser) {
                # The logon Startup shortcut fires at the same moment and starts the platform in the
                # right session already. Give it a head start rather than racing it for the machine.
                Start-Sleep -Seconds 30
                break
            }
            Start-Sleep -Seconds 10
        }

        for ($i = 1; $i -le $ColdAttempts; $i++) {
            if (Test-Health) { Write-Log 'platform is up; entering monitor loop.'; break }
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
    # One handoff attempt only. If starting in the user's session is blocked on this machine (no
    # rights to register the task, say), Invoke-PlatformStartup falls back to LocalSystem - which
    # re-creates the very SYSTEM ownership that triggered the handoff. Retrying that would bounce
    # the platform every minute forever, so a failed attempt is logged once and then left alone.
    $handoffDone  = $false
    $cpFailCount  = 0   # consecutive cycles where Test-ControlPlane returned $false

    while ($true) {
        Start-Sleep -Seconds $PollSeconds

        if (Test-ComposeBusy) { continue }

        # Control-plane probe: separate from the HTTP health check. Healthz 200 only proves the
        # gateway is responding; the SSH transport to the VM can be dead at the same time (observed
        # 2026-08-17: `podman volume ls` exit 125 while healthz 200). A dead transport means that
        # any compose-based recovery will fail. Catching it here lets us restart proactively while
        # the platform is still serving - a planned 30-second bounce beats a stuck recovery loop.
        $cpAlive = Test-ControlPlane
        if (-not $cpAlive) { $cpFailCount++ } else { $cpFailCount = 0 }
        if (-not $cpAlive) {
            Write-Log "control-plane probe failed ($cpFailCount consecutive cycle(s))"
        }

        if (Test-Health) {
            # Healthy, but possibly owned by the wrong account: hand it over so the user can use
            # compose. Costs one short bounce, and only ever after a boot nobody was logged on for.
            $user = Get-InteractiveUser
            if ($user -and -not $handoffDone) {
                $owner = Get-MachineOwner
                if (Test-SystemOwned $owner) {
                    $handoffDone = $true
                    Invoke-MachineHandoff $user $owner
                    if (Test-SystemOwned (Get-MachineOwner)) {
                        Write-Log 'handoff did not take; leaving the machine with LocalSystem.'
                        Write-Log '  docker-compose will not work from the user session until the'
                        Write-Log '  machine is next started by them. See WATCHDOG.md.'
                    }
                    else { Write-Log "machine now belongs to $user; compose is usable there." }
                }
            }

            # Proactive control-plane recovery: SSH transport dead for 2+ cycles while healthz 200.
            # The containers are serving fine right now but compose-based recovery would be stuck if
            # a container went down. Restart the machine early, in a controlled way, before that
            # happens. $handoffDone is reset so the new machine start triggers a fresh handoff check.
            if (-not $cpAlive -and $cpFailCount -ge 2) {
                Write-Log ("control plane unresponsive for $cpFailCount consecutive cycles; " +
                           "restarting machine to restore compose access before a container " +
                           "failure makes recovery impossible")
                $handoffDone = $false
                Invoke-ForceRestartMachine
                $cpFailCount = 0
            }
            continue
        }

        Write-Log "health check failed; confirming in $ConfirmSeconds s..."
        Start-Sleep -Seconds $ConfirmSeconds

        if (Test-Health) {
            Write-Log 'transient blip - platform recovered on its own'
            continue
        }
        if (Test-ComposeBusy) {
            Write-Log 'a compose command is running - leaving it alone'
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
