# Terminal Fun 🕹️

A **platform** rail: a browser terminal that lets you pick from a menu of local terminal
games and toys — ASCII Star Wars, matrix rain, a bonsai generator, NetHack and other
roguelikes, arcade games — and run them right in the page, with a docked AI assistant that
answers questions about the page and tunes the toys on the fly.

**Status: BUILT + DEPLOYED LIVE** (2026-07-26) as the container `platform-terminal-fun-1`
on `:8730`, entitlement-gated behind the platform gateway. Everything is self-hosted —
nothing leaves the box.

## What's in it

- **22 games/toys** across five shelves (screensavers, hacker vibes, toys, roguelikes,
  arcade). Each tile has an **ⓘ how-to-play** panel (goal, controls, how to quit).
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
  handshake + entitlement and injects `x-platform-user`.
- **`frontend/`** — React + Vite **module-federation remote** (`terminal_fun`): an xterm.js
  terminal + a category picker grid + the ⓘ panels + the bottom AI chat. Palette-token themed
  per the platform theming contract.
- **`deploy/Dockerfile`** — `python:3.11-slim` + the games (apt packages + a few tiny source
  builds: Star Wars `ascii-movie`, `no-more-secrets`, `2048`, `genact`, `hollywood`,
  `asciiquarium`) + wrapper scripts under `deploy/rootfs/opt/fun/`.

**Safety model for tuning:** the AI never emits CLI flags. It only picks *values* from a
fixed per-toy schema (`backend/terminal_fun_app/tunables.py`); the backend validates (enum
whitelist / int clamp / printable string) and maps them to flags/env itself.

## Repo layout

```text
backend/terminal_fun_app/   FastAPI app: main, catalog, tunables, pty_session, broker, config
frontend/src/module.tsx     the federated React module (picker + terminal + AI chat)
deploy/Dockerfile           the game image; deploy/rootfs/ holds wrapper scripts + assets
research/                   the original design research (historical; see index below)
PLAN.md                     the original build plan (historical)
```

## Deploy

Wired into `platform/deploy/docker-compose.yml` as the `terminal-fun` service (build context
`../../terminal-fun`) + gateway env/volume/catalog entry. Redeploy after a change:

```bash
docker compose up -d --build terminal-fun   # backend/games
# (frontend) cd frontend && npm run build    # then the gateway serves the new dist
```

The assistant model is `TERMINAL_FUN_LLM_MODEL` (default `gemma3:12b`, resolved by the broker).

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
| [PLAN.md](PLAN.md) | The original build plan |

## Status log

- 2026-07-25 — repo created, research harvested.
- 2026-07-26 — **built + deployed live**; ⓘ how-to panels; AI assistant (page Q&A + live
  toy-tuning); renamed "Fun in the Term" → "Terminal Fun"; assistant model settled on `gemma3:12b`.
