# AI Platform

A self-hosted AI platform that converges a set of loose single-user local dashboards onto one
shared core: one way to host, one way to authenticate, one way to talk to the GPU. Each app is a
federated **rail** served behind a **gateway**, and all model work goes through a single GPU
**broker**.

## Quick start

The fastest way to stand up a lean platform on a modest (8 GB VRAM) Windows box is the one-command
installer (in-terminal or GUI) described in [`docs/INSTALL.md`](docs/INSTALL.md). It brings up the admin shell,
Terminal Fun, and optional Recipe Book on two small Ollama models, with no HuggingFace token and no
image/TTS pipeline. From any PowerShell — no manual clone:

```powershell
irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex
```

For the design rationale and broker API, see [`docs/architecture.md`](docs/architecture.md).

## The rails

A rail is a FastAPI `/api` backend plus a React module-federation remote, containerized behind the
gateway, entitlement-gated, with all model work routed through the broker. The shipped rails:

| Rail | Icon | What it does |
|---|---|---|
| **edu-suite** | 🎓 | Bilingual (EN / es-MX) classroom content: translation, CVC worksheets, TeachTown units. |
| **IEP** | 📝 | A second instance of the edu-suite dashboard, isolated to drafting IEP Present Levels narratives (its own library/DB so student data stays separate). |
| **recipe-book** | 🍳 | Multi-tenant cooking assistant: meal plans, recipe help, pantry and bar reasoning, plus generated recipe-card icons. |
| **co-worker** | 🗂️ | Exec-brief dashboard: harvests email, calendar, and Teams activity and synthesizes it into prioritized attention items (client interactions, open threads, missed responses, agenda-less meetings). Attention only — not another inbox. |
| **workstation** | 💻 | A browser terminal into the host over SSH. Highest-privilege rail, entitlement-gated to a single owner. |
| **terminal-fun** | 🕹️ | Self-hosted terminal games and toys with an in-terminal AI helper. Sandboxed, no host access. |
| **ai-playground** | 🛝 | A multi-demo rail: a RAG-over-documents demo (local or NVIDIA NIM generation, WebSocket token streaming) plus an Embedding Lab that benchmarks embedders head-to-head (GPU vs CPU-ONNX) with optional CPU cross-encoder reranking. |

Rails enforce identity in-rail too, not just at the gateway: jobs and data are owner-scoped where a
rail is multi-user, and requests fail closed without the gateway's trusted identity header.

## The GPU / Model Broker

The broker (`services/broker`, native at `:11500`) is the single owner of the GPU. No rail touches
Ollama / SDXL / FLUX / XTTS directly. They call the broker, which serves chat / embed / vision /
image / tts, resolves `@role` to a concrete model (per-rail roles in `roles.json`, hot-read), and
enforces one heavy model at a time on a 24 GB GPU (a small embedder may co-reside). It also exposes
a live job queue (active and waiting, per rail). The control plane is token-authenticated: every
`/v1/*` route requires a shared `BROKER_AUTH_TOKEN` bearer (kept in the gitignored `deploy/.env`),
so only platform components can drive the GPU.

## Admin

An admin-only console (opened by clicking the brand in the shell) provides:

- **Users** — accounts and per-rail entitlements.
- **Rails** — a per-rail model picker (capability-filtered, revert-to-default, image slots too);
  changes apply live via the broker.
- **Models** — the whole-machine model pool (In-Use / Loaded / Enabled), with disable or `ollama rm`
  and a delete-block while a role still depends on a model.
- **Schedule** — a central scheduler with per-task recurrence, run-now, and next/last-run.

GPU control (cancel a job, unload, load) is admin-only.

## Layout

```
packages/platform_core/   Shared Python (config base, BrokerClient)
services/broker/          GPU / Model Broker, the only thing that touches the GPU
apps/platform/            Gateway (reverse proxy + auth + entitlements) + React shell host + Admin
web/                      Shared @web-core design system (styles + AppShell + ModelWidget)
rails/<name>/             The federated rail apps
deploy/                   docker-compose + Caddyfile + service-install + activate-model-roles.ps1
docs/                     INSTALL.md (lean installer) + architecture.md
```

## Run it

The broker runs as a native Windows service (`platform-broker`, NSSM, `:11500`); the shell and
rails run in containers — **Podman** (Hyper-V provider, default on Windows) or Docker Desktop.

From `deploy/`:

```powershell
# Podman (standalone docker-compose.exe drives the Podman Docker-compat pipe):
docker-compose --env-file .env -f installer/docker-compose.installer.yml --profile recipe-book --profile co-worker up -d --build --no-deps <service>

# Docker Desktop / Docker Engine:
docker compose up -d --build --no-deps <service>

# local: http://localhost:1111
# (platform.localhost is intercepted by some corporate proxies — use the plain localhost form)
```
