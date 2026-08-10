# Build architecture

The good news: this is ~80% a fork of the existing `workstation` rail. Same terminal UI,
same WS frame protocol, same gateway wiring. The differences are (1) *what* the backend
connects the terminal to, and (2) a picker/menu in the frontend instead of fixed tabs.

## The two backend "launchers"

The workstation backend has one launcher: `asyncssh.connect(host)` → `create_process(cmd)`.
We replace it with a `kind`-dispatch over a catalog entry:

- **`kind: local`** — spawn a game **inside our container** on a PTY, no SSH, no host.
  Python options: the stdlib `pty` module (`pty.openpty()` + `asyncio.create_subprocess_exec`
  with the slave fd as stdin/stdout/stderr, master fd bridged to the WS), or a helper lib
  (`ptyprocess`). Set `TERM`, and apply window size with `TIOCSWINSZ` on resize frames.
  Launch with an **argv list, never a shell string** (mirrors the workstation "no shell"
  property the security review liked).
- **`kind: remote`** — `asyncio.open_connection(host, port)` to a catalog-fixed host, pipe
  both directions. Add **minimal telnet (RFC 854) IAC handling**: reply WONT/DONT to the
  server's DO/WILL negotiations so option bytes don't garble the xterm stream. (Many
  servers tolerate a raw passthrough, but a ~30-line IAC filter makes them all clean.)
  Where a service offers SSH (e.g. NetHack NAO), prefer `asyncssh` over plaintext telnet.

Everything else — the `0x00` input / `0x00` output / `0x01` resize / `0x04` status frame
protocol, the `pump_in`/`pump_out`/`watchdog` tasks, the audit log — carries over as-is.

## The catalog (config, not code)

A list of entries the backend owns and the frontend renders as a menu. Never send the
launch command/host to the browser (workstation already redacts this in `/api/presets`).

```yaml
- id: starwars
  label: "Star Wars (ASCIImation)"
  icon: "🌌"
  category: watch
  kind: remote          # or: local
  host: towel.blinkenlights.nl   # remote only; NOT sent to the browser
  port: 23
  allow_input: false    # view-only items ignore keystrokes
  idle_timeout: 0       # don't kill a passive viewer on idle
- id: asciiquarium
  label: "ASCII Aquarium"
  icon: "🐟"
  category: watch
  kind: local
  command: ["asciiquarium"]
  allow_input: false
- id: nethack
  label: "NetHack"
  icon: "🗡️"
  category: roguelike
  kind: local
  command: ["nethack"]
  allow_input: true
```

Backend endpoints (fork of workstation's):
- `GET /api/catalog` → `[{id, label, icon, category}]` (redacted).
- `WS /ws/{id}` → opens the session for that catalog id.

## Frontend

Fork `workstation/frontend`:
- Keep `@xterm/xterm` + `@xterm/addon-fit` + the WS bridge from `module.tsx`.
- Add a **picker**: a grid of category sections → tiles. Selecting a tile opens
  `WS /ws/{id}` and mounts the terminal; a "back to menu" control closes the socket and
  returns to the grid.
- If `allow_input:false`, don't forward keystrokes (view-only).
- Same `@originjs/vite-plugin-federation` config, expose `./module`.

## Rail wiring (make it show up on the platform)

Mirror how `recipe-book`/`finance` are wired (this becomes a **sibling repo** like them —
`../../terminal-fun`, build context in compose):

1. **catalog.py** — add `{"id": "terminal-fun", "label": "Fun in the Term", "icon": "🕹️", "status": "ready"}`.
2. **docker-compose.yml** — a `terminal-fun` service (build `../../terminal-fun`, `expose: 8730`);
   gateway env `PLATFORM_APP_TERMINAL_FUN_URL: http://terminal-fun:8730` + `PLATFORM_TERMINAL_FUN_DIST`
   + a read-only dist volume mount (`../../terminal-fun/frontend/dist:/app/terminal-fun-dist:ro`).
3. **App.tsx** — `const TerminalFunModule = lazy(() => import('terminal_fun/module'))` and a branch for the id.
4. Entitlement-gated automatically by the gateway's `app_access_gate` (same as every rail),
   so it's protected by Cloudflare Access + login + per-user entitlement with no extra work.
   The **WebSocket** path is important here: the gateway's `ws_proxy` already enforces
   entitlements for `/{app}/ws/*` (the security review confirmed this), which this rail relies on.

## Why this is *safer* than workstation

Workstation intentionally SSHes to the **host** as a shared user (its whole purpose, and
its biggest risk). Fun-in-the-term does the opposite: everything runs **inside its own
container with no host mounts and no host SSH**. A game escape reaches only an ephemeral,
unprivileged container — a much smaller blast radius. See the concerns doc for the sandbox
spec.
