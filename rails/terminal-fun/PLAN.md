# Build plan — "Fun in the Term" 🕹️ rail

Status: **plan / not yet building.** Local-only (everything self-hosted in this rail's own
container; no dial-out). Chess skipped (user). mapscii recommended OUT on size (see §5).

## 1. Platform-conformance checklist (the tenets this must follow)

- [x] **Sibling repo**, like finance / recipe-book / job-aid: `bigfnj/terminal-fun`
      (this dir), pulled into the stack via compose `build.context: ../../terminal-fun`.
- [x] **Backend = FastAPI**, exposes an internal port (**8730**), never directly reachable;
      the gateway reverse-proxies `/terminal-fun/api/*` and `/terminal-fun/ws/*` and injects
      the verified `x-platform-user`. Entitlement-gated by the gateway (incl. the WS path).
- [x] **Frontend = React 18 + Vite + module federation** (`@originjs/vite-plugin-federation`),
      exposes `./module`, built to `frontend/dist`, mounted read-only into the gateway.
      Terminal via `@xterm/xterm` ^5.5 + `@xterm/addon-fit` (same as workstation).
- [x] **No broker / no GPU.** This rail does zero model work, so — like `workstation` — it
      legitimately never calls the broker. (The "everything talks to the broker" tenet is
      about GPU work; there is none here.)
- [x] **Deploy discipline:** Claude runs the deploy; rebuild ONLY this service
      (`docker compose up -d --build terminal-fun`), never a full-stack `up` (caddy churn
      drops the host's internet). After the shell `App.tsx` change, rebuild the shell dist
      and **restart the gateway** (else `/` 404s).
- [x] **Security posture** (from the platform security review): shell-free subprocess launch
      (argv lists, never a shell string); run as **non-root** in-container; **no host mounts /
      no host SSH** (strictly safer than workstation); fixed catalog (no user-supplied command
      or host → no injection / no SSRF); per-session teardown + watchdog; audit log.

## 2. Architecture

Fork the `workstation` rail; it is ~80% of this.

```
Browser (xterm.js)  ──wss──>  gateway  ──/terminal-fun/ws/{id}──>  terminal-fun backend
   picker grid                (auth + entitlement + x-platform-user)   │
                                                                       ▼
                                                        local PTY subprocess (a game/toy)
                                                        inside THIS container, non-root
```

- **One launcher only** (local-only simplifies the design): `spawn_local(argv)` on a stdlib
  `pty` (`pty.openpty()` + `asyncio.create_subprocess_exec`, master fd bridged to the WS,
  `TIOCSWINSZ` on resize). No SSH, no telnet, no outbound sockets.
- **Same WS frame protocol** as workstation: `0x00` in/out bytes, `0x01` JSON resize,
  `0x04` status text. Reuse `pump_in`/`pump_out`/`watchdog`/audit wholesale.
- **Catalog is config**, not code. Backend owns it; `/api/catalog` returns only
  `{id,label,icon,category}` (never the launch command). `WS /ws/{id}` runs it.

Backend routes: `GET /api/healthz`, `GET /api/catalog`, `WS /ws/{id}`.

## 3. The full catalog (v1)

Type: **Watch** = view-only (frontend ignores keystrokes, no idle-kill, absolute cap only);
**Play** = interactive (needs stdin). "Source" = how it gets into the image.

### 🌌 Watch — screensavers & animations
| id | Item | Launch (illustrative) | Source |
|---|---|---|---|
| starwars | Star Wars ASCIImation | `ascii-movie play` (bundled SW movie) | build (`gabe565/ascii-movie`) |
| asciiquarium | ASCII Aquarium | `asciiquarium` | apt |
| cmatrix | Matrix rain | `cmatrix -ab` | apt |
| sl | Steam locomotive | `sl -e` | apt |
| pipes | Pipes screensaver | `pipes.sh` | apt `pipes-sh` / script |
| cbonsai | Bonsai grower | `cbonsai -li` | apt (newer) / build |
| nyancat | Nyan Cat | `nyancat` | apt |
| nms | no-more-secrets (Sneakers FX) | `figlet WELCOME | nms -a` | build (`bartobri/no-more-secrets`) |
| genact | Fake activity (genact) | `genact` | build (`svenstaro/genact`, single binary) |
| hollywood | Hollywood hacker screen | `hollywood` | apt `hollywood` (pulls byobu/tmux) |

### 😹 Toys — novelties
| id | Item | Launch | Source |
|---|---|---|---|
| cowsay | Fortune cow | `fortune | cowsay | lolcat` (loop) | apt `cowsay fortune-mod lolcat` |
| banner | Big ASCII banners | small `figlet`/`toilet` demo | apt `figlet toilet` |
| bofh | BOFH excuse server | tiny excuse generator | self-host (excuse list) |

### 🗡️ Roguelikes
| id | Item | Launch | Source |
|---|---|---|---|
| nethack | NetHack | `nethack` | apt `nethack-console` |
| crawl | Dungeon Crawl Stone Soup | `crawl` | apt `crawl` |

### 🕹️ Arcade & puzzle
| id | Item | Launch | Source |
|---|---|---|---|
| bastet | Bastet (evil Tetris) | `bastet` | apt |
| ninvaders | Space Invaders | `ninvaders` | apt |
| g2048 | 2048 | `2048` | build (`2048.c`, trivial) |
| moonbuggy | Moon Buggy | `moon-buggy` | apt |

### 📜 Interactive fiction & classics
| id | Item | Launch | Source |
|---|---|---|---|
| adventure | Colossal Cave (Frotz) | `frotz /opt/if/advent.z5` | apt `frotz` + free story files |
| classics | BSD games pack | a small chooser → `adventure`,`worm`,`robots`,`hangman`,`trek`,`wump`,`canfield` | apt `bsdgames` |

**Sourcing tally (21 tiles):** ~16 via `apt` (zero harvest, incl. hollywood), 3 tiny source
builds (starwars/ascii-movie, nms, 2048) + 1 single-binary build (genact), 2 self-host assets
(SW movie file, BOFH list) + free IF story files. Everything fits comfortably on a
Debian-slim base (~300–500 MB total image).

## 4. Sandbox / hardening spec

- Container runs as a dedicated **non-root** user; `no-new-privileges`; drop caps; **no host
  bind-mounts**; read-only rootfs where feasible with a `tmpfs` writable scratch.
- Each session gets an **ephemeral `HOME`** (tmpfs). Trade-off: NetHack/Crawl saves & scores
  don't persist across sessions in v1 — acceptable; a per-user persistent volume is a later
  enhancement if wanted.
- Minimal `PATH`; `SHELL=/bin/false`; nothing on PATH that a game's shell-escape (`nethack !`)
  could use to reach a real shell or the network.
- Argv-list launch, no shell string. Per-session `process.terminate()` on disconnect +
  idle/absolute watchdog (idle disabled for Watch items).
- Backend `expose` only (no published host port); reachable solely via the gateway.

## 5. Decisions (resolved 2026-07-26)

- **mapscii: CUT.** Offline world map needs the OpenMapTiles planet MBTiles ≈ 80–83 GB; a
  region extract is ~0.9 GB+ and no longer "the world"; live tiles violate local-only. Not
  worth 200× the image. (Revisit only as a far-future single-region novelty.)
- **chess: SKIPPED** (user).
- **fake-hacker toy: BOTH** genact *and* hollywood, as two separate tiles.
- **game saves: EPHEMERAL** — per-session tmpfs home; NetHack/Crawl saves & scores don't
  persist in v1. A per-user persistent volume is a later enhancement.

## 6. Phased build

- **P0 — Scaffold** (sibling-repo layout): `backend/terminal_fun_app/`, `frontend/` (fork
  workstation), `deploy/Dockerfile`, catalog config, README. Choose port 8730.
- **P1 — Backend**: `/api/healthz`, `/api/catalog`, `WS /ws/{id}`; local PTY launcher;
  per-item `allow_input`/`idle_timeout`; audit; identity from `x-platform-user`.
- **P2 — Content image**: Dockerfile — Debian-slim, `apt-get` the package set, build
  ascii-movie/nms/2048/genact, fetch SW movie + BOFH list + free IF `.z5` files, create the
  non-root user, smoke each launches.
- **P3 — Frontend**: fork `module.tsx`; add the **picker grid** (category sections → tiles),
  "back to menu", Watch/Play input handling; federation `expose: ./module`.
- **P4 — Rail wiring**: `catalog.py` entry (`terminal-fun`, "Fun in the Term", 🕹️, ready);
  compose `terminal-fun` service + gateway env (`PLATFORM_APP_TERMINAL_FUN_URL`,
  `PLATFORM_TERMINAL_FUN_DIST`) + dist volume + `depends_on`; shell `App.tsx` lazy
  `import('terminal_fun/module')` + federation remote alias + `remotes.d.ts`; entitle the user.
- **P5 — Deploy + verify**: build frontend dists (shell + this remote), `docker compose up -d
  --build terminal-fun`, restart the gateway; smoke each tile over the live gateway; confirm
  the entitlement gate (a non-entitled user gets 403 on `/terminal-fun/*` incl. the WS).

## 7. Effort

~**3–4 focused days**: P0–P1 ~1 day (mostly forking workstation), P2 ~1 day (Dockerfile +
per-item smoke), P3 ~1 day (the picker UI is the only net-new frontend), P4–P5 ~½–1 day
(wiring is well-trodden from finance/recipe-book).
