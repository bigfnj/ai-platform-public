# AI-Platform Watchdog Service

Owns the platform lifecycle: brings the stack up at boot, and recovers it if it goes down
mid-session — without a logon, a reboot, or any manual intervention.

## Architecture

```
SCM (auto-start, after vmms)
  └─ platform-watchdog  (NSSM, LocalSystem)
       └─ platform-watchdog.ps1
            ├─ COLD START  health check first; if down, platform-startup.ps1,
            │              retried 6× at 30 s while Hyper-V finishes booting
            └─ STEADY STATE  GET localhost:1111/api/platform/healthz every 5 min
                 ├─ two consecutive failures (30 s apart) → recovery
                 └─ platform-startup.ps1  (restart machine + compose up)
```

### Cold start

The service checks health **before** doing anything, so restarting it mid-session never
bounces a platform that is already up. If the platform is down — the normal case at boot —
it runs `platform-startup.ps1` immediately and retries, because the service starts early
and Hyper-V may not be ready to start the podman machine on the first try.

The service declares a dependency on **`vmms`** (Hyper-V Virtual Machine Management) so the
SCM does not start it before the hypervisor can serve it.

Because cold start lives here, **no logon Startup shortcut is required.** The platform is up
before anyone logs on. (If you migrated from an earlier build, delete any leftover
`AI-Platform*.lnk` from `shell:startup` — otherwise the stack is started twice at logon.)

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
