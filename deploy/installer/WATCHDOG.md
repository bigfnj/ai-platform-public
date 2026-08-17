# AI-Platform Watchdog Service

Owns the platform lifecycle: brings the stack up at boot, and recovers it if it goes down
mid-session — without a logon, a reboot, or any manual intervention.

## Architecture

```
SCM (auto-start, after vmms)
  └─ platform-watchdog  (NSSM, LocalSystem)
       └─ platform-watchdog.ps1
            ├─ COLD START  health check first; wait briefly for a logon, then
            │              platform-startup.ps1, retried 6× at 30 s
            └─ STEADY STATE  every 60 s (skipped while a compose command is running)
                 ├─ GET localhost:1111/api/platform/healthz
                 │    └─ two consecutive failures (30 s apart) → recovery
                 ├─ Test-ControlPlane  (podman volume ls, 10 s timeout)
                 │    └─ 2+ consecutive failures while healthz 200
                 │         → Invoke-ForceRestartMachine (Stop-VM + clear proxy + handoff)
                 └─ SYSTEM-owned machine + user logged on → handoff
```

### Who starts the podman machine matters

`gvproxy` creates `\\.\pipe\podman-machine-default` with a DACL belonging to **whichever
account started the machine.** When LocalSystem starts it, the interactive user gets
`permission denied` from `docker-compose` for the rest of the session — no rebuilds, no
`compose up` — even though the platform serves fine on :1111.

It gets worse: `podman machine stop` does **not** reap the proxy, and a standard user cannot
kill a SYSTEM-owned one. The machine then becomes unstartable by *anyone* —
`could not start api proxy since expected pipe is not available`. `Clear-StalePodmanProxy`
in `lib-runtime.ps1` exists to break that deadlock, and only LocalSystem can run it.

So the rule is: **whenever somebody is logged on, the platform is started in their session**,
via a scheduled task with an Interactive principal (the one handoff that needs no stored
password). LocalSystem starts it only when nobody is logged on, and hands it back — stop,
reap, restart in session — as soon as somebody logs on. A failed handoff is attempted **once**
and then logged, because falling back to a LocalSystem start would otherwise bounce the
platform every minute forever.

### Control-plane hardening

`healthz 200` only proves the gateway container is responding. The podman SSH transport — the
tunnel gvproxy uses to reach the VM's container API — can die independently. When that happens:

- `docker-compose` commands fail (`npipe EOF`), so any compose-based recovery would be stuck.
- `podman volume ls` exits 125 while :1111 keeps serving.
- `Get-PodmanMachineState` returns 'stopped' (because `podman info` uses SSH), so
  `podman machine start` tries to start a machine that is already running in Hyper-V and fails.

The watchdog adds a second probe each cycle: `Test-ControlPlane` in `lib-runtime.ps1` runs
`podman volume ls` with a 10-second timeout. After **two consecutive failures** while healthz is
still 200, `Invoke-ForceRestartMachine` runs: it hard-stops the VM via Hyper-V (`Stop-VM
-Force`, bypassing SSH), clears the orphan proxy, and restarts the platform in the user's session.

As defense-in-depth, `Initialize-PodmanMachine` (Branch 3) also handles this case: if
`podman machine start` fails but Hyper-V reports the VM is Running, it issues `Stop-VM -Force`
and retries. This covers the window before the 2-cycle CP threshold is reached or when startup.ps1
is called directly.

### Cold start

The service checks health **before** doing anything, so restarting it mid-session never
bounces a platform that is already up. If the platform is down — the normal case at boot —
it waits up to 3 minutes for a logon (so it can start in the right session), then runs
`platform-startup.ps1` and retries, because the service starts early and Hyper-V may not be
ready to start the podman machine on the first try.

The service declares a dependency on **`vmms`** (Hyper-V Virtual Machine Management) so the
SCM does not start it before the hypervisor can serve it.

The logon Startup shortcut (`AI-Platform startup.lnk` → `platform-startup-launcher.vbs`) is
**still installed**, as a belt-and-braces path that always yields a user-owned machine. The
two starters cannot collide: `platform-startup.ps1` takes a lock file
(`deploy/logs/startup.lock`) and the loser exits without doing anything.

If you migrated from an earlier build, delete any leftover `AI-Platform (Podman).lnk` from
`shell:startup` — that one targets `powershell.exe -WindowStyle Hidden`, which Windows
Terminal ignores, so it shows a console window at every logon *and* double-starts the stack.

The service runs as **LocalSystem** with the logged-in user's profile paths injected
via NSSM `AppEnvironmentExtra`, so Podman can find its machine config and SSH key:

| Variable | Value injected at registration |
|---|---|
| `USERPROFILE` | `C:\Users\<user>` |
| `APPDATA` | `C:\Users\<user>\AppData\Roaming` |
| `LOCALAPPDATA` | `C:\Users\<user>\AppData\Local` |
| `HOME` | `C:\Users\<user>` |

### Why not Task Scheduler?

Task Scheduler is **access-denied** for non-elevated users on managed (Entra-joined +
BeyondTrust EPM) boxes. A system service registered via the RunAS Helper gate is the
only persistent, auto-restarting mechanism available without a UAC prompt at every logon.

### Why not run the service as the domain user?

`SeServiceLogonRight` is grantable on this machine (no GPO conflict, verified), but NSSM
requires the account's Windows password at registration time, and Entra-joined accounts
may use TPM-backed credentials rather than a traditional password. The LocalSystem +
env-var-injection approach avoids this entirely: LocalSystem can reach Hyper-V, open
`\\.\pipe\docker_engine`, and SSH into the Podman VM once the env vars are set.

## Files

| File | Purpose |
|---|---|
| `platform-watchdog.ps1` | The watchdog loop — runs inside the NSSM service |
| `register-watchdog.ps1` | Idempotent elevated registrar — run via RunAS gate |

## Installing on a new workstation

Prerequisites: the platform must already be installed (`install.ps1` completed, containers
running). The watchdog is a post-install step.

### 1 — Open the RunAS Helper gate

Tray icon → **Activate** → Settings → toggle **Allow command line**.

### 2 — Run the registrar (non-elevated shell)

```powershell
& "C:\Program Files\RunAsHelper\RunAsHelper.exe" /as:system `
    "$env:USERPROFILE\ai-platform\deploy\installer\register-watchdog.ps1"
```

Wait ~15 seconds, then read the result:

```powershell
Get-Content C:\Users\Public\register-watchdog.txt
```

The last line should be `DONE` and `service status: Running`.

### 3 — Verify

```powershell
Get-Service platform-watchdog | Select-Object Name, Status, StartType
# Expected: Running, Automatic

Get-Content "$env:USERPROFILE\ai-platform\deploy\logs\watchdog-svc.log" -Tail 5
# Expected: "=== platform-watchdog started (pid=..., user=C:\Users\<you>) ==="
```

### 4 — Close the gate

Close or restart the RunAS Helper tray app to close the gate.

## Updating / re-registering

`register-watchdog.ps1` is idempotent — it stops and removes the existing service before
reinstalling. Re-run it via the gate whenever `platform-watchdog.ps1` changes:

```powershell
# pull latest first
cd "$env:USERPROFILE\ai-platform"; git pull origin main
# then re-register via gate (gate must be open)
& "C:\Program Files\RunAsHelper\RunAsHelper.exe" /as:system `
    "$env:USERPROFILE\ai-platform\deploy\installer\register-watchdog.ps1"
```

## Logs

| Log | Written by | Rotation |
|---|---|---|
| `deploy\logs\watchdog-svc.log` | NSSM (stdout+stderr capture) | 512 KB |
| `deploy\logs\watchdog.log` | `platform-watchdog.ps1` (Write-Log) | 500 KB |
| `deploy\logs\startup.log` | `platform-startup.ps1` on recovery | — |

## Removing the service

Open the gate, then:

```powershell
$nssm = "$env:USERPROFILE\ai-platform\deploy\bin\nssm.exe"
& "C:\Program Files\RunAsHelper\RunAsHelper.exe" /as:system powershell.exe `
    -Command "& '$nssm' stop platform-watchdog confirm; & '$nssm' remove platform-watchdog confirm"
```
