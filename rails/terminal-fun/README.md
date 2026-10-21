# Terminal Fun 🕹️

A **platform** rail: a browser terminal that lets you pick from a menu of local terminal
games and toys — ASCII Star Wars, matrix rain, a bonsai generator, NetHack and other
roguelikes, arcade games — and run them right in the page, with a docked AI assistant that
answers questions about the page and tunes the toys on the fly.

**Status: BUILT + DEPLOYED LIVE** (2026-07-26) as the container `platform-terminal-fun-1`
on `:8730`, entitlement-gated behind the platform gateway. Everything is self-hosted by
default. The one exception is opt-in: when the broker's Claude CLI backend is on and the
assistant's role is switched to it, an owner account's own assistant questions are answered
by Claude (see `services/broker/README.md`).

## What's in it

- **23 games/toys** across six shelves (screensavers, hacker vibes, toys, roguelikes,
  arcade, and **web toys**). Each tile has an **ⓘ how-to-play** panel (goal, controls, how to quit).
- **Web toys** — a tile type that is *not* a terminal program: a self-contained graphical
  HTML/canvas toy served as a static file and rendered in a sandboxed `<iframe>` in the page
  (e.g. **Buzzed Eyes 👀**, a bloodshot cursor-follower that trails cannabis leaves, smoke,
  glitter, and sparkles). Move your mouse inside the panel; there's nothing to type.
- **A docked AI assistant** ("Ask me about anything on this page"):
  - answers page/game questions, grounded in the same ⓘ instructions (no invented controls);
  - **tunes toys live** — "make it red and rainbow", "make it a grumpy dragon" — for the 7
    tunable toys (cmatrix, cbonsai, pipes, genact, sl, cowsay, Star Wars), relaunching the
    toy in place over the same WebSocket;
  - stays on-topic (declines off-page requests).

## Architecture

A fork of the platform's `workstation` rail, but pointed at local games instead of a host
SSH shell — and safer, because it needs **no host access at all**.

- **`backend/`** — FastAPI on `:8730`. A local-PTY-over-WebSocket bridge: games run as a
  **non-root PTY subprocess inside this container** with an ephemeral tmpfs `$HOME`. Routes:
  `/api/catalog`, `/api/tunables/{id}`, `/api/chat` (→ the platform broker), `WS /ws/{id}`
  (a `0x02` "apply" frame relaunches a toy with new settings). The gateway authenticates the
  handshake + entitlement and injects `x-platform-user`, and this backend believes that header
  only from the gateway's address (see *Trust model* below). **Web toys** are the exception to the
  PTY model: they're static files mounted at `/api/webtoys/<id>/` (from `deploy/rootfs/opt/fun/
  webtoys/`, or the repo copy in local dev) and never touch a WebSocket.
- **`frontend/`** — React + Vite **module-federation remote** (`terminal_fun`): an xterm.js
  terminal + a category picker grid + the ⓘ panels + the bottom AI chat. Palette-token themed
  per the platform theming contract.
- **`deploy/Dockerfile`** — `python:3.11-slim` + the games (apt packages + a few tiny source
  builds: Star Wars `ascii-movie`, `no-more-secrets`, `2048`, `genact`, `hollywood`,
  `asciiquarium`) + wrapper scripts under `deploy/rootfs/opt/fun/`.

**Safety model for tuning:** the AI never emits CLI flags. It only picks *values* from a
fixed per-toy schema (`backend/terminal_fun_app/tunables.py`); the backend validates (enum
whitelist / int clamp / printable string) and maps them to flags/env itself.

## Trust model

This is the one rail that runs code its users choose. A roguelike's shell escape (NetHack's
`!`) and the tmux/byobu panes hand the player a real bash, by design, as `gameuser` inside this
container. That shell is the boundary everything below is drawn around.

Every rail identifies its caller from `X-Platform-User` / `X-Platform-Admin`. The gateway strips
any copy a browser sends and sets its own, so the headers are trusted **from the network**:
whatever can open a socket to a rail and send them is believed. Until 2026-10-09 this container
sat on the compose default network beside every rail, and measured from that shell as
`gameuser`, `GET http://recipe-book:8830/api/capabilities` answered 401 with no header and 200
with `X-Platform-User: admin`. Any player could be any user, admin included, to every rail.
Two independent layers close it, and each is checked by tests that fail if it is dropped:

- **The network.** This container joins only `terminal_fun_net`, a compose network it shares
  with the gateway and nothing else (both `deploy/docker-compose.yml` and the lean
  `deploy/installer/docker-compose.installer.yml`). No other rail resolves or routes from
  here. The gateway joins it as well as `default`, which is how it still reaches
  `http://terminal-fun:8730`. It is an ordinary bridge, not `internal`, so the broker
  (`host.docker.internal:11500`) and internet egress are unchanged; no catalog item needs the
  internet at runtime (every game and toy is baked into the image), so egress is kept only
  because nothing asked for it to go. `tools/tests/test_terminal_fun_network.py`.
- **The backend.** The network cannot cover the last hop: the shell reaching THIS backend over
  `127.0.0.1` (or this container's own address) and asserting another player's identity to read
  their saves or spend the assistant as them. So `GatewayOnlyIdentity` in
  `backend/terminal_fun_app/main.py` refuses any `x-platform-*` header from a peer that is not
  one of the gateway's addresses: HTTP 403, or a websocket close (4403) before accept. The
  addresses come from resolving `TERMINAL_FUN_GATEWAY_HOST` (default `gateway`, the compose
  service name), cached and re-resolved on a miss at most every
  `TERMINAL_FUN_GATEWAY_RESOLVE_INTERVAL` seconds (default 5), because a recreated gateway can
  come back on a new IP. A request with no such header is untouched and meets the usual identity
  gate (401). uvicorn runs with `--no-proxy-headers`, because by default it rewrites the client
  address from `X-Forwarded-For` when the peer is `127.0.0.1`, which is exactly where the shell
  connects from. `backend/tests/test_gateway_peer.py`.

`/api/healthz` reports `peer_check`: `on` in a deployment; `degraded` when the gateway's name
resolves to a loopback or own address (a dev box with the gateway on localhost, where any process
here can assert an identity); `unresolved` when no lookup of the gateway's name has ever answered,
so no peer is trusted; `off (PLATFORM_STANDALONE)` with no gateway in front. The field looks the
gateway up itself when nothing has yet.
A deployment check should expect `on`, and has to probe this rail from inside the gateway's
container, since a probe from this container's own loopback meets the same 403 the shell does.
(The platform's fleet smoke check does both; it is maintainer tooling, not in the public
snapshot.) `backend/tests/mutate_peer_guard.py` breaks each guard of both layers on purpose and
requires a named test to fail.

What the shell still has, deliberately: the broker is reachable (the assistant needs it) but its
token is not. A game's environment is a full replacement that never carries it
(`pty_session.py`), and `/proc/1/environ` and `/data` belong to `funuser`, unreadable to
`gameuser` (the uid split, `deploy/Dockerfile`).

## Repo layout

```text
backend/terminal_fun_app/   FastAPI app: main, catalog, tunables, pty_session, broker, config
frontend/src/module.tsx     the federated React module (picker + terminal + iframe web toys + AI chat)
deploy/Dockerfile           the game image; deploy/rootfs/ holds wrapper scripts + assets
deploy/rootfs/opt/fun/webtoys/  static web toys (index.html + assets), served at /api/webtoys/
research/                   the original design research (historical; see index below)
PLAN.md                     the original build plan (historical; maintainer copy only)
```

## Deploy

Wired into `platform/deploy/docker-compose.yml` as the `terminal-fun` service (build context
`../../terminal-fun`) + gateway env/volume/catalog entry. Redeploy after a change:

```bash
docker compose up -d --build --no-deps terminal-fun   # backend/games
# (frontend) cd frontend && npm run build              # then the gateway serves the new dist
```

The gateway must be on `terminal_fun_net` before this container is: recreated alone onto a
network the running gateway has not joined, the rail is unreachable until the gateway is
recreated too (`docker compose up -d --no-deps gateway`, no build needed for a compose-only
change).

The assistant model is `TERMINAL_FUN_LLM_MODEL` (default the `@terminal-fun` role, which the
broker resolves and Admin -> Rails repoints).

## Research index (historical)

The design research that preceded the build. Note it explored *remote* telnet/MUD/chess
services too; the shipped rail is **local-only** (those were cut — see `research/06`).

| File | What's in it |
|---|---|
| [research/00-sources.md](research/00-sources.md) | Every source consulted |
| [research/01-remote-telnet-services.md](research/01-remote-telnet-services.md) | Dial-out telnet/ssh services (not shipped) |
| [research/02-local-games-and-toys.md](research/02-local-games-and-toys.md) | Local games/toys, packaged vs build |
| [research/03-build-architecture.md](research/03-build-architecture.md) | Build on the workstation template |
| [research/04-harvest-effort.md](research/04-harvest-effort.md) | Effort tiers for sourcing content |
| [research/05-concerns.md](research/05-concerns.md) | Security, sandboxing, licensing, content |
| [research/06-v1-local-catalog.md](research/06-v1-local-catalog.md) | The curated local-only catalog that shipped |

## Status log

- 2026-07-25 — repo created, research harvested.
- 2026-07-26 — **built + deployed live**; ⓘ how-to panels; AI assistant (page Q&A + live
  toy-tuning); renamed "Fun in the Term" → "Terminal Fun"; assistant model settled on `gemma3:12b`.
- 2026-08-13 — added a **web-toy** tile type (graphical, non-PTY toys served as static files +
  rendered in a sandboxed iframe) and the first one, **Buzzed Eyes 👀** — a bloodshot cursor-follower
  that trails cannabis leaves, smoke, glitter, and sparkles (ported in from a standalone side project).
