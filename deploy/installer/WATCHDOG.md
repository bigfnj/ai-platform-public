# AI-Platform Watchdog Service

Owns the platform lifecycle: brings the stack up at boot, and recovers it if it goes down
mid-session — without a logon, a reboot, or any manual intervention.

> **Not registered on the development box, deliberately.** This machine runs Docker Desktop,
> whose daemon already restarts `restart: always` containers at boot and after a crash — the
> watchdog would be a second thing managing the same stack. It exists for **Podman** installs,
> which are daemonless: `podman-restart.service` (enabled by `Initialize-PodmanMachine`) covers
> containers once the machine is up, but nothing starts the *machine* without a logon and nothing
> notices when healthz stops answering. Register it with `register-watchdog.ps1` on such a box.
>
> The third section below — who owns the podman machine — is the part that is easy to get wrong
> and expensive to debug, so read it before registering anything.

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
                 ├─ Get-ControlPlaneProbe  (podman volume ls, 10 s timeout)
                 │    └─ 2+ consecutive failures while healthz 200
                 │         → one confirming probe (60 s timeout)
                 │              ├─ answers → logged as slow, no restart
                 │              └─ fails too → Invoke-ForceRestartMachine
                 │                             (Stop-VM + clear proxy + handoff)
                 └─ SYSTEM-owned machine + user logged on → handoff

  every start above (cold start, recovery, control-plane recovery) first asks
  Get-ShutdownState: during a shutdown or restart it logs one line, starts nothing
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

The watchdog adds a second probe each cycle: `Get-ControlPlaneProbe` in `lib-runtime.ps1` runs
`podman volume ls` with a 10-second timeout (`-ProbeSeconds`), reading both of its pipes as it
goes and killing its whole process tree on a timeout. After **two consecutive failures** while
healthz is still 200 it asks once more with a **60-second** timeout (`-ConfirmProbeSeconds`).
Only when that one fails too does `Invoke-ForceRestartMachine` run: it hard-stops the VM via
Hyper-V (`Stop-VM -Force`, bypassing SSH), clears the orphan proxy, and restarts the platform
in the user's session. A confirming probe that answers is logged ("the transport is slow, not
dead") and nothing is restarted. Every failed probe's line in `watchdog.log` says why it failed.

Why the confirmation: a force-stop is an outage, and a missed 10-second probe is not proof of a
dead transport. The probe used to redirect podman's output and never read it, so a box whose
volume names passed about 4 KB timed out on a healthy podman, and heavy load does the same. On
2026-10-14 two such misses during a parallel test run force-stopped the VM and the box was down
for three hours.

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

Run through the gate, the registrar runs as SYSTEM, whose own `USERPROFILE` is
`C:\Windows\System32\config\systemprofile`, so `<user>` is not taken from there: it is
`WATCHDOG_USER` when set, else the install folder's parent when that is a user profile folder (the
`%USERPROFILE%\ai-platform` clone `get.ps1` makes), and with neither the registrar refuses naming
`WATCHDOG_USER` (lib-runtime `Resolve-WatchdogProfile`): `FAILED` in its log, the reason on the
console, exit code 1. A checkout outside the profile passes `-WatchdogUser <name>` after the script
path.

From an elevated shell that is another account's (an administrator's credentials at the elevation
prompt), `USERPROFILE` is that administrator's. When the install folder's parent is a different
user's profile folder, there are two candidate users and nothing says whose podman machine runs the
stack, so the registrar refuses, naming both folders and the `-WatchdogUser` to pass, rather than
bake the administrator's profile in. Your own account elevated, and a checkout outside every
profile folder, register as before.

### Shutdowns and restarts

A power-off logs the user off first, which stops their podman machine, and the services stop only
after that. In between, the platform is down and nobody is logged on, which is exactly when the
watchdog used to cold-start it as LocalSystem: on 2026-10-15 it did, 13 s before the event log
stopped. So before it starts anything (a cold start, a confirmed-down restart, or a control-plane
recovery, whose `Stop-VM` it skips too) it asks `Get-ShutdownState` in `lib-runtime.ps1`, and while
Windows is shutting down or restarting it logs one line (`... - Windows is shutting down or
restarting (System event 1074 at ...); not starting anything`) and starts nothing.

The signal is the System log's User32 event **1074**, written when a shutdown or restart is asked
for (Start > Shut down, `shutdown.exe`, Windows Update), minutes before the services stop. It counts
while it is the newest of three: no **1075** after it (the request was aborted, `shutdown /a`) and
no Kernel-Boot **27** (written at every boot, a fast-startup one too, which leaves
`LastBootUpTime` where it was). It counts for 30 minutes at most, so a shutdown abandoned without
a 1075 holds starts off no longer than that, and a System log that cannot be read is not taken
for a shutdown. The check is one log query over at most those 30 minutes, made only on a path
about to start something, never every cycle.

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
| `register-watchdog.ps1` | Idempotent elevated registrar — run from an elevated PowerShell (or RunAS Helper) |

## Installing on a new workstation

Prerequisites: the platform must already be installed (`install.ps1` completed, containers
running). The watchdog is a post-install step.

### 1 — Run the registrar elevated

`<repo>` below is the checkout, `%USERPROFILE%\ai-platform` where `get.ps1` put it. From an elevated
PowerShell (Run as administrator: the standard UAC or BeyondTrust prompt):

```powershell
& '<repo>\deploy\installer\register-watchdog.ps1'
```

If the elevation prompt asked for another account (an administrator signing in over your shoulder),
that shell's `USERPROFILE` is that account's, not yours: add `-WatchdogUser <your account name>`, so
the service drives your podman machine. Without it, a checkout in your profile folder is refused,
and the refusal names the `-WatchdogUser` to pass.

**Optional, on a box that has RunAS Helper:** open its gate (tray icon → **Activate** → Settings →
toggle **Allow command line**), then from a non-elevated shell:

```powershell
RunAsHelper /capture /timeout:300 /ps:5 /as:system "<repo>\deploy\installer\register-watchdog.ps1"
```

Either way the registrar waits until it is done, prints `register-watchdog DONE: ... service status:
Running` and exits 0, or prints `register-watchdog FAILED: <why>` and exits 1. Its full log is
`C:\Users\Public\register-watchdog.txt`, whose last line is `DONE` or `FAILED`.

### 2 — Verify

```powershell
Get-Service platform-watchdog | Select-Object Name, Status, StartType
# Expected: Running, Automatic

Get-Content "$env:USERPROFILE\ai-platform\deploy\logs\watchdog-svc.log" -Tail 5
# Expected: "=== platform-watchdog started (pid=..., user=C:\Users\<you>) ==="
```

### 3 — Close the gate (RunAS Helper only)

If you used RunAS Helper, close or restart its tray app to close the gate.

## Updating / re-registering

`register-watchdog.ps1` is idempotent — it stops and removes the existing service before
reinstalling. The running service reads `platform-watchdog.ps1` and `lib-runtime.ps1` only when
it starts, so a `git pull` alone changes nothing until the service restarts. Re-run it whenever
`platform-watchdog.ps1` changes:

```powershell
# pull latest first (your own shell)
git -C '<repo>' pull origin main
# then re-register, from an elevated PowerShell
& '<repo>\deploy\installer\register-watchdog.ps1'
```

Optional, on a box that has RunAS Helper, with its gate open, from a non-elevated shell:

```powershell
RunAsHelper /capture /timeout:300 /ps:5 /as:system "<repo>\deploy\installer\register-watchdog.ps1"
```

## Logs

| Log | Written by | Rotation |
|---|---|---|
| `deploy\logs\watchdog-svc.log` | NSSM (stdout+stderr capture) | 512 KB |
| `deploy\logs\watchdog.log` | `platform-watchdog.ps1` (Write-Log) | 500 KB |
| `deploy\logs\startup.log` | `platform-startup.ps1` on recovery | 10 MB |

**Rotated copies are pruned by the watchdog.** The installers set an NSSM `Rotate/Post` hook that
keeps the newest ten rotated copies of each log, but NSSM 2.24 (the build the installers fetch)
predates event hooks, so on it that hook does not exist: `install-services.ps1`,
`install-native.ps1` and this registrar check for it after registration and print a warning when it
is missing (the registrar also writes it to `register-watchdog.txt`). The watchdog prunes
`deploy\logs` itself once a day: on its first monitor cycle after it starts, then every 24 hours,
with a `log prune: removed N rotated log file(s)` line in `watchdog.log`. A box with no watchdog
service (the full stack registers none) prunes `deploy\logs` when `deploy\rebuild-stale.ps1` runs,
at most once a day (`deploy\logs\log-prune.last` keeps the time), and whenever `smoke-test.ps1` or
the podman logon startup runs; one that runs none of them keeps every rotated copy.

## Removing the service

From an elevated PowerShell:

```powershell
$nssm = '<repo>\deploy\bin\nssm.exe'
& $nssm stop platform-watchdog confirm; & $nssm remove platform-watchdog confirm
```

Optional, on a box that has RunAS Helper, with its gate open, from a non-elevated shell:

```powershell
$nssm = '<repo>\deploy\bin\nssm.exe'
RunAsHelper /capture /timeout:60 /as:system powershell.exe -NoProfile -Command "& '$nssm' stop platform-watchdog confirm; & '$nssm' remove platform-watchdog confirm"
```
