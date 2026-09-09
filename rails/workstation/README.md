# Workstation — a browser terminal into the host, as a platform rail module

A federated rail module that gives you a real terminal in the browser, attached to
a shell on your workstation over SSH. One tile, three presets:

- **🖥 Shell** — a plain login shell.
- **🤖 Claude Code** — drops you into `claude` (your workstation's own install + creds).
- **◆ Codex** — drops you into `codex`.

It is the same capability behind all three: a browser xterm.js terminal → a WebSocket
→ the gateway → this backend → an SSH connection into the workstation. "Powered on the
backend by sshd" — the backend is an SSH *client*; the machine's own sshd does the work.
The browser never holds an SSH key; the key lives server-side in this backend.

## Where a session starts (`WORKSTATION_START_DIR`)

Unset, a preset runs the bare program and lands wherever the SSH target's login shell
lands — on Windows OpenSSH, the SSH user's home. Set it to a path **on the target** and
every preset that has a command is prefixed with `cd '<dir>'; …` (a form that parses in
PowerShell and in POSIX shells alike, so it works for a Windows or a Linux target). The
same value is the folder the published VS Code RemoteApp opens.

This is not cosmetic. Claude Code asks whether it may trust the folder it opens, the home
directory is exactly the folder you should not blanket-trust, and **the default answer on
that prompt is "No, exit"** — so landing at home means one Enter quits the session before
you have typed anything, which reads as a terminal that ignores keystrokes. Point it at
the tree you actually work in and answer the trust prompt once.

The **Shell** preset is deliberately never wrapped: an empty command means asyncssh runs
no command at all and the target opens a *login* shell, and prefixing a `cd` would quietly
downgrade that to a non-login shell. Shell therefore follows the target's own landing
directory. On Windows that is `HKLM:\SOFTWARE\OpenSSH` → `DefaultShell`.

## Why it's shaped this way

- **Rail-native, not a bastion.** It rides the platform shell/gateway/Caddy/Cloudflare
  path you already run, so it inherits the gateway login + Cloudflare Access + per-user
  entitlements. No second auth system (which is why we did not use Warpgate here).
- **One mounted app, preset tabs (not three rail tiles).** The rail couples one catalog
  id to one mounted remote + backend. Three tiles would mean three mounts. So the module
  hosts the preset switcher itself. Making them *look* like three separate rail entries
  is a possible follow-up (a catalog "alias" that points several rail ids at one mount);
  see "Future" below.
- **No session persistence (by choice).** A dropped connection ends the live process, not
  your work: both `claude` and `codex` persist their transcripts and can resume
  (`claude --continue` / `codex resume`). If persistence is wanted later, point the SSH
  target at a Linux host/WSL and wrap the preset command in `tmux new -As <preset>`.

## Shape

```
browser (xterm.js)
   │  WebSocket  /workstation/ws/<preset>
   ▼
gateway  ── authenticates the WS handshake (session cookie + entitlement),
            forwards the verified identity, proxies frames to ↓
   │  ws://workstation:8720/ws/<preset>
   ▼
workstation backend (this app)  ── asyncssh client, allocates a PTY, runs the
            preset command, bridges PTY ⇆ WS, handles resize
   │  SSH
   ▼
workstation sshd  (host.docker.internal / WSL / Kryptos — config-driven)
```

## Published apps (RemoteApp) — VS Code without the desktop

Added 2026-08-18. The rail's second job: hand out launchers for **desktop** applications on
the host, rendered as a single seamless window on your own machine rather than a whole
remote desktop. The Citrix "published application" model, using Windows' own RemoteApp.

```
browser  ── "Published apps: 📝 VS Code"
   │  GET /workstation/api/remoteapp/vscode.rdp   (gateway-gated, download)
   ▼
your machine's RDP client (mstsc)
   │  RDP 3389, remoteapplicationmode:i:1, program ||vscode
   ▼
host  ── termsrv checks TSAppAllowList, launches ONLY Code.exe
```

**Nothing streams through the platform.** The backend never speaks RDP; it renders a text
`.rdp` file. That keeps the rail's shape intact (no new proxy, no stored Windows
credentials, no pixel path through the gateway) at the cost of needing an RDP client and a
network route to 3389 on the client side.

**Host setup, once:** `deploy/install-remoteapp.ps1` (self-elevating, idempotent, `-Remove`
to revert, `-List` to inspect). It writes the publish entry under
`HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList` and sets
`fDisabledAllowList=0`, so **only** listed executables can be published. It does not touch
whether RDP is enabled, the port, NLA, or ordinary full-desktop sessions. Publish something
else with `-Alias/-Path/-DisplayName`.

**Rail config** (`deploy/.env`, all `WORKSTATION_`-prefixed): `RDP_HOST` is the address
**the client dials**, not one this backend reaches — a LAN IP, a Tailscale/WARP name, or
`127.0.0.1` when `cloudflared access rdp` is forwarding locally. Unset disables the feature
and the frontend row disappears. `RDP_PORT` (3389), `RDP_USERNAME` (prefill only, not a
credential), `RDP_APPS_JSON` (replace the published list wholesale). The folder VS Code
opens comes from `WORKSTATION_START_DIR`, because it is resolved by VS Code **on the
host**: it used to be this backend's own `REPO_ROOT`, which is `/app` inside the
container, so the launcher shipped `remoteapplicationcmdline:s:/app`.

### Two things that will bite

- **One interactive session.** Windows Pro allows a single session, and a RemoteApp
  connection attaches to the same one a full desktop would. You can alternate between
  seamless VS Code and a full desktop, but not hold both, and connecting disconnects
  whoever is at the console. This is unchanged from how RDP already behaves here.
- **VS Code is a single-instance Electron app.** If an instance is already running in that
  session, the launched process hands off and exits immediately, which RemoteApp can read
  as "the app closed". If that happens, either close VS Code on the host first or give the
  published entry its own profile by adding `--user-data-dir=...` to the app's `args`.

### Why not stream it into the browser

Considered and deferred: Apache Guacamole (`guacd` + webapp) can RDP to the host with
`remote-app` set and render the result into a canvas, which is the literal browser answer.
It needs the gateway's proxy broadened (it only routes `/{app}/api/*` and `/{app}/ws/*`,
and the HTTP path buffers whole responses), plus stored Windows credentials at rest. The
launcher above delivers better fidelity for a fraction of that, so Guacamole stays an
option rather than a plan.

### WS frame protocol (browser ⇆ backend)

Binary frames, first byte is a type tag so keystrokes and control never collide:

- `0x00` + bytes → terminal input (written to the PTY)
- `0x01` + JSON `{"cols":N,"rows":N}` → resize the PTY

Server → browser: raw PTY output as binary `0x00`-tagged frames (and a final text
`\x04`-prefixed status line when the remote process exits).

## Status — LIVE (admin-only, verified 2026-07-24)

Shipped and running. Branch `add-workstation-terminal` was merged into `main` (merge
`fd595b9`), enabled in `ca56221` (catalog `status:"ready"` + `workstation` added to
`enabled_apps`), and the backend image was fixed in `99607d6` (install `httpx`, which
`platform_core/__init__` imports for the broker client).

- **SSH target:** Windows OpenSSH on ELSEWHERE (the box's own sshd), custom **port 2222**,
  user **`Admin`**. The backend authenticates with a dedicated ed25519 key in gitignored
  `deploy/workstation-keys/`; its public key lives in
  `C:\ProgramData\ssh\administrators_authorized_keys`. Container SSH knobs are in gitignored
  `deploy/.env` (`WORKSTATION_SSH_PORT=2222`, `WORKSTATION_SSH_USER=Admin`,
  `WORKSTATION_INSECURE_SKIP_HOST_KEY_CHECK=true` — see Security posture / HARDENING.md).
- **Verified end to end:** the full chain (gateway WS-proxy + auth gate + backend → SSH) with
  the Shell + `claude` + `codex` TUIs all rendering over the socket; live container
  `platform-workstation-1` reaches `host.docker.internal:2222` and returns a shell as
  `elsewhere\admin`; gateway `healthz` lists workstation; the federation bundle is served.
- **Exposure:** rides the existing Cloudflare tunnel, so it is reachable at
  `platform.example.com` behind **Cloudflare Access + the gateway login + admin-only**
  (admins auto-see every app; no non-admin entitlement is granted).

> `claude` / `codex` are launched as standalone CLIs on the target account's PATH
> (npm-installed; they are VS Code extensions otherwise, not on PATH).

## Enable + verify runbook (done 2026-07-24 — kept for rebuild / re-verify)

> This ran once and the rail is live; steps 1–3 are already in place. Keep it for
> rebuilding the image or re-verifying the chain after a change.

1. **Pick + prep the SSH target** (one of):
   - Windows OpenSSH on this box: `Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`,
     then `Start-Service sshd`. `claude` and `codex` must be on that user's PATH.
   - WSL or Kryptos: their sshd is already there; `npm i -g @anthropic-ai/claude-code`.
2. **Give the backend a key** that logs into that target's account, and record its
   host key. Set the `WORKSTATION_SSH_*` env (see `.env.example`). Mount the key into
   the container read-only.
3. **Enable the app:** add `"workstation"` to `PLATFORM_ENABLED_APPS` (or the
   `enabled_apps` default) and set `status: "ready"` in `catalog.py`.
4. **Build the frontend:** `cd apps/workstation/frontend && npm install && npm run build`.
5. **Bring it up:** `docker compose up -d --build workstation gateway` in `deploy/`.
6. **Entitle only yourself:** in ⚙ Admin, grant `workstation` to `admin` and no one else.
7. **Verify:** open the platform, click the Workstation tile, confirm the Shell preset
   gives a prompt, then `claude` and `codex` presets launch their TUIs and render clean.

## Security posture

Highest-value target on the platform: a live shell on the host. It is entitlement-gated
(admin-only), always behind Cloudflare Access + the gateway login, and never a published SSH
port — the backend is an SSH *client*, and the only listening SSH port (2222) is bound to the
host and reached only from the Docker network. The backend's key is scoped to exactly the
account you land in (`Admin`).

A hardening pass shipped 2026-07-25 (deployed + live-verified) — see [HARDENING.md](./HARDENING.md)
for the full plan and status. Live now: a three-role model (user / admin / **super-admin =
`admin`**) so a host shell needs an explicit grant even for admins; SSH host-key pinning
(`known_hosts`, insecure skip OFF); an idle + absolute session timeout; a rotating, 30-day-retention
session audit trail; a non-root, read-only, capability-dropped container; and an anti-CSWSH Origin
check on the gateway's WS handshake.

Still open:

- **Cloudflare Access policy** for the shell path should be tightened (single identity, short
  session, hardware key / WARP) — the chosen exposure control since the rail stays
  internet-reachable by design (P2.4, owner task).
- **Kept internet-reachable on purpose:** no LAN-only edge block (that would defeat the intent);
  CF Access is the front door.

## Future (optional)

- **True separate rail tiles** via a catalog alias (rail ids `terminal`/`claude-code`/`codex`
  → one `workstation` mount + a launch-preset hint).
- **tmux-backed persistence** preset for reconnect-after-drop.
- **Session recording** (log PTY output to the library volume) if you ever want an audit trail.
