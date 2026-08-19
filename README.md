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
gateway, entitlement-gated, with all model work routed through the broker. Each rail declares itself in `rails/<id>/rail.json` and every contract it has to honour is
machine-checked by `python tools/rail_conformance.py` — see
[`docs/RAIL_CONTRACT.md`](docs/RAIL_CONTRACT.md). The shipped rails:

| Rail | Icon | What it does |
|---|---|---|
| **edu-suite** | 🎓 | Bilingual (EN / es-MX) classroom content: translation, CVC worksheets, TeachTown units. |
| **IEP** | 📝 | A second instance of the edu-suite dashboard, isolated to drafting IEP Present Levels narratives (its own library/DB so student data stays separate). |
| **recipe-book** | 🍳 | Multi-tenant cooking assistant: meal plans, recipe help, pantry and bar reasoning, plus generated recipe-card icons. |
| **co-worker** | 💼 | Exec-brief dashboard: harvests email, calendar, and Teams activity and synthesizes it into prioritized attention items (client interactions, open threads, missed responses, agenda-less meetings). Attention only — not another inbox. |
| **workstation** | 💻 | A browser terminal into the host over SSH. Highest-privilege rail, entitlement-gated to a single owner. |
| **terminal-fun** | 🕹️ | Self-hosted terminal games and toys with an in-terminal AI helper. Sandboxed, no host access. |
| **ai-playground** | 🛝 | A multi-demo rail: a RAG-over-documents demo (local or NVIDIA NIM generation, WebSocket token streaming) plus an Embedding Lab that benchmarks embedders head-to-head (GPU vs CPU-ONNX) with optional CPU cross-encoder reranking. |
| **smb-partner-enablement** | 🤝 | Microsoft Partner Network SME assistant for the SMB segment: a grounded knowledge base (CSP licensing, designations, MCEM, incentives, Partner Center) behind RAG, plus a Scenario Builder that turns a short diagnostic into a meeting kit. Includes a standalone mobile build. |
| **gemini-cx** | ✨ | Subject-matter assistant for Google Cloud's Gemini Enterprise for Customer Experience: a 17-collection corpus behind RAG, fronted by a curated question deck rather than a chat box. Marks GA / Preview / coming-soon separately and refuses figures it cannot cite. |

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

**Rails must reference models as `@role`, never as a pinned model name.** Admin → Rails repoints a
role in `roles.json` (hot-read, no restart), so a rail that pins a concrete name silently ignores
the panel: the admin repoints the role, the rail keeps using the pin, and nothing reports the
disagreement. Each rail's header chips show the reference the rail *actually* sends, which is what
makes that drift visible. The one deliberate exception is documented where it occurs
(`RECIPE_BOOK_LLM_MODEL`, a fallback with no role of its own).

Speech is served two ways, and the distinction matters: `/v1/tts` (XTTS) takes the full GPU gate
and evicts every resident heavy model per utterance, while `/v1/tts_light` (Kokoro-82M, ~350 MB in
a short-lived worker) takes neither the gate nor an eviction. Rails that hold a model resident use
`tts_light`. They are not alternatives to consolidate: XTTS clones a voice from a reference clip
and returns per-segment timings for highlight-sync; Kokoro does neither.

## Voice

Speech in and out is a **platform capability**, not a per-rail one. The browser talks to the
gateway, which holds the broker token and enforces the session, so no rail needs a broker client,
a broker URL, or the token:

```
browser ──► gateway /api/platform/{tts_light,transcribe} ──► broker /v1/{tts_light,transcribe}
            (any logged-in user; 502 on broker error)         (no GPU gate, no eviction)
```

Both broker paths call the media worker directly rather than through the gated path, so a mic
press never queues behind a chat completion and speaking never unloads the model the user is
talking to. That property is asserted structurally (`services/broker/tests/test_voice_ungated.py`)
rather than by round-trip, because a round-trip passes either way.

Rails consume it as chips from `@web-core`:

| Component | Where | Why |
|---|---|---|
| `DictateButton` | a rail | Takes `onTranscript`, so the transcript goes through the rail's own state setter — no DOM write |
| `SpeakButton` | a rail | Takes the text **explicitly**; only the rail knows which passage it means |
| `VoiceControls` | the shell top bar | Fallback mic for rails without a chip, plus the shared voice/speed/device settings |

STT is `faster-whisper` on CPU. **The model must be multilingual, never a `.en` build** — those
silently ignore the `language` parameter and turn other languages into nonsense rather than
erroring. Dictation deliberately has **no browser `SpeechRecognition` fallback**: that API streams
the microphone to a cloud service, so a silent fallback would exfiltrate exactly the audio that
must stay local. Broker down means dictation reports itself down.

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
web/                      Shared @web-core design system (styles + AppShell + ModelWidget + voice)
rails/<name>/             The federated rail apps, each declaring itself in rail.json
deploy/                   docker-compose + Caddyfile + service-install + activate-model-roles.ps1
docs/                     INSTALL.md + architecture.md + RAIL_CONTRACT.md + BACKLOG.md + schema
tools/                    rail_conformance.py — checks every rail against its manifest
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

You do not normally run any of this by hand. On Windows the **`platform-watchdog`** service
(NSSM, LocalSystem, auto-start) owns the lifecycle: it brings the stack up at boot and restarts
it if health checks fail. It deliberately starts the platform *in the logged-on user's session*
so the podman machine — and its API pipe — belongs to you and your `docker-compose` keeps
working. See [`deploy/installer/WATCHDOG.md`](deploy/installer/WATCHDOG.md).
