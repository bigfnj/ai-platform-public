# ai-platform

A self-hosted AI platform for a single Windows workstation with one GPU. It converges a set of
loose local dashboards onto one shared core: **one way to host, one way to authenticate, one way to
talk to the GPU**. Each app is a federated **rail** behind a gateway, and every model call goes
through a single **broker** that owns the card.

Everything runs on your own hardware. No API keys, no cloud inference, nothing leaves the box.

> **This repository is a generated subset.** It is built from a larger private monorepo and
> committed as a single fresh snapshot, so it has no incremental history. Rails that hold personal
> records — financial documents, a job search, and a special-education rail containing real student
> data — are withheld, which is why a design doc or a code comment will occasionally reference a
> rail that is not present here. The platform core and the nine rails below are complete and run
> as-is.
>
> It is also **one-way**. `tools/publish.py`, which generates this repository, is not in it, and
> every publish force-replaces the branch — so this repo cannot regenerate itself, and a commit
> pushed here is erased by the next run. Report anything you fix as an issue upstream instead;
> patches carried locally need re-applying after each snapshot.

## Layout

```
packages/platform_core/   Shared Python (config base, BrokerClient)
services/broker/          GPU / Model Broker — the ONLY thing that touches the GPU
apps/platform/            Gateway (reverse proxy + auth + entitlements) + React shell host + Admin
web/                      Shared @web-core design system (styles + AppShell + RailHeader)
rails/<name>/             The federated rail apps (below)
deploy/                   docker-compose + Caddyfile + the installer
docs/                     INSTALL.md, RAIL_CONTRACT.md, architecture.md
```

## The rails

Each rail = a FastAPI `/api` backend + a React module-federation remote, containerized behind the
gateway, **entitlement-gated**, models via the broker:

| Rail | What it is |
|---|---|
| **recipe-book** 🍳 | ~900-card recipe + cocktail book with per-recipe generated icons, an AI meal planner and a pantry |
| **ai-playground** 🛝 | RAG over documents (cited, token-streamed) plus an **Embedding Lab** that benchmarks embedders head-to-head — GPU vs int8 CPU-ONNX — with optional cross-encoder reranking |
| **terminal-fun** 🕹️ | Browser terminal into ~22 sandboxed games and toys, plus a broker-backed assistant |
| **workstation** 💻 | Browser terminal over SSH into the host, and **RemoteApp launchers** that open a host desktop app as a single seamless window |
| **co-worker** 🧭 | Harvested email/calendar/chat items rolled into a prioritized executive brief |
| **smb-partner-enablement** 🤝 | Grounded RAG over partner enablement content, with a voice surface and a mobile build |
| **gemini-cx** ✨ | Grounded RAG over a Google Cloud Gemini Enterprise CX corpus, fronted by a curated question deck |
| **meeting-atlas** 🗓️ | Indexes meeting recordings and rolls them up by day/week/month — and treats every generated summary as a **claim**, locating each action item's cited quote in the real transcript and flagging invented dates and reused evidence |

Rails enforce identity in-rail too, not just at the gateway: data is owner-scoped where a rail is
multi-user, and requests fail closed without the gateway's trusted identity header.

**Every rail declares itself once** in `rails/<id>/rail.json`, and
[`tools/rail_conformance.py`](tools/rail_conformance.py) asserts that the places those facts get
restated — the rail's source, its vite config, its Dockerfile, its compose service, the gateway
registries, the admin model panel — actually agree. Stdlib only, ~2 s on a bare checkout:

```bash
python tools/rail_conformance.py     # exit 1 on any violation; --rules is the authoritative list
```

The contract, and the reasoning behind each rule, is in [`docs/RAIL_CONTRACT.md`](docs/RAIL_CONTRACT.md).

## Tests

```powershell
.\run-tests.ps1                                            # core + every rail, ~90 s
.\deploy\installer\smoke-test.ps1 -Stage all -FullStack    # the container stack itself
```

`run-tests.ps1` puts each rail on `PYTHONPATH` rather than installing every rail package into one
venv; `smoke-test.ps1` is the only out-of-process coverage (everything else is in-process pytest).

## The GPU / Model Broker

The broker (`services/broker`, native at `:11500`) is the single owner of the GPU. No rail touches
Ollama / SDXL / FLUX / XTTS directly — they call the broker, which serves chat / embed / **vision** /
**image** / **tts**, resolves **`@role` → model** (per-rail roles in `roles.json`, hot-read so a
model can be repointed live), and enforces **one heavy model at a time** (a ~1 GB embedder may
co-reside). It exposes a live job queue, and its control plane is **token-authenticated** — every
`/v1/*` route requires a shared bearer, so only platform components can drive the card.

The `@role` indirection is the point: a rail asks for `@recipe-vision`, not `gemma4:26b`. Swapping
the model behind a role is an admin action, not a code change.

## Voice, on every rail

Two broker endpoints run **CPU/ONNX with no GPU gate and no eviction**, which is what lets voice be
offered platform-wide: pressing a mic or a read-aloud button mid-conversation cannot displace the
model you are using. `/v1/tts_light` is Kokoro-82M read-aloud; `/v1/transcribe` is faster-whisper
dictation (**multilingual** — the `.en` models silently ignore the language argument and mangle
Spanish). The gateway re-exposes both for any logged-in user, so a rail just drops in a chip:

```tsx
import { DictateButton, SpeakButton } from '@web-core'
<SpeakButton   text={section} small />
<DictateButton small onText={(t) => setNotes(n => n + t)} />
```

Dictation prefers the browser's **on-device** speech API (Chrome 139+ `processLocally`) and falls
back to the local broker. It is used on student writing, so audio stays on the machine.

## Admin

An admin-only console: **Users** (accounts + per-app entitlements), **Rails** (per-rail model
picker, capability-filtered, applied live), **Models** (the whole model pool — in-use / loaded /
enabled, with a delete-block while a role depends on one), and **Schedule** (a central scheduler
with Outlook-style recurrence per rail task, run-now, next/last run).

## Install

**One line, no clone needed.** From any PowerShell:

```powershell
irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex
```

It ensures git, enables Windows long paths, clones to `%USERPROFILE%\ai-platform`, and opens a
menu that drives the installer. It runs non-elevated; only the provisioning step self-elevates.
*(Piping a remote script to `iex` runs whatever is at that URL — read it first at the raw link,
and pin a tag with `$env:AIPLATFORM_REF` rather than tracking `main`.)*

**Already cloned:**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1
```

The installer auto-detects your container runtime — **Podman** (Hyper-V, daemonless), **Docker
Desktop**, or **Docker Engine in WSL2** — and provisions accordingly. Add `-Console` to stay in the
terminal, or `-Check` to run just the prerequisite doctor.

See [`docs/INSTALL.md`](docs/INSTALL.md) for the tri-mode detail, the Podman/Hyper-V startup-RAM
gotcha, and what the installer actually writes.

## Requirements

An NVIDIA GPU (8 GB is enough for the lean profile), Windows 10/11, a container runtime, Ollama,
Python 3.11+, and about 20 GB of disk. No Node.js on the host — frontends build inside the image.
