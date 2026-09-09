# Architecture

The short, repo-local version. The rail-by-rail contract and the reasoning behind each rule is
[RAIL_CONTRACT.md](RAIL_CONTRACT.md); how to stand the stack up is [INSTALL.md](INSTALL.md).

## The shape

```
                          host :1111
                              |
                        +-----v------+
   browser  ----------> |   Caddy    |  front door, TLS
                        +-----+------+
                              |
                        +-----v-----------------------------+
                        |  GATEWAY  (FastAPI + React shell) |
                        |  auth · entitlements · proxy      |
                        +--+-----------------------------+--+
                           |                             |
              /<rail>/api/*|                             | @role model calls
                        +--v---------+          +--------v-----------------+
                        |   rails    | -------> |  GPU / MODEL BROKER      |
                        | (own image,|          |  FastAPI, :11500, NATIVE |
                        |  own deps) |          |  the ONLY GPU consumer   |
                        +------------+          +--------+-----------------+
                                                         |
                                      Ollama :11434 · SDXL/FLUX · XTTS
                                      Kokoro · faster-whisper · voice engines
```

Caddy publishes host **1111**, not 80/443. That is deliberate: rebinding the privileged ports
churns the Windows Docker/WSL NAT and briefly drops the whole machine's internet, and TLS is
terminated upstream anyway on the deployment this was built for.

The broker runs **native**, not in a container, because it owns the GPU and the media/speech
engines it dispatches to live in their own host virtualenvs. Rails reach it across the container
boundary by URL.

## Principle: everything talks to the broker

Rails never call `localhost:11434`. They import `platform_core.broker_client` and address the
broker by configured URL. That is what makes a rail portable — it finds the broker by config
rather than a hardcoded host — and it is what stops two rails both trying to load 15 GB at once.

Rails also never name a **model**. They ask for a `@role`, and the broker expands it through
`services/broker/roles.json`, which is hot-read: repointing a role is an admin action with no
restart and no rebuild. A rail that pins a concrete model name silently ignores that, which is
why the contract checks the in-code default and not just the compose value.

## VRAM policy (the whole reason the broker exists)

A 24 GB card fits **one heavy generative model (~14-17 GB) at a time**, alongside the ~1.1 GB
embedder.

- Generative models are **heavy**; embedding models are **light**.
- A single async gate serializes heavy operations (chat, model load) so requests queue instead
  of colliding.
- Before loading or serving a heavy model, the broker evicts any *other* heavy model
  (`keep_alive: 0`). Embedders may stay resident alongside one heavy model.
- Belt-and-suspenders: set `OLLAMA_MAX_LOADED_MODELS=1` on the Ollama service so it auto-evicts
  even if something bypasses the broker.

The broker audits its role map against the installed models and the detected VRAM at startup, so
a card too small for the configured roles is reported at boot rather than on the first chat.

## Broker API

The whole control plane sits behind one shared bearer (`BROKER_AUTH_TOKEN`), applied as an
app-wide dependency so no `/v1/*` route is reachable untokened by a rogue container or LAN host.
It is **open when the variable is unset**, for dev and staged rollout, and `/healthz` is always
open for liveness.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness + Ollama reachability + version (never gated) |
| GET | `/v1/status` | Full view: Ollama up, loaded models, GPU VRAM, queue depth, media interpreter paths |
| GET | `/v1/models` | Installed models, classified heavy/embed |
| GET | `/v1/ps` | Currently loaded models + per-model VRAM |
| GET · PUT | `/v1/roles`, `/v1/roles/{role}` | Read and repoint the `@role` map (hot; backs Admin → Rails) |
| GET · PUT | `/v1/disabled` | The reversible availability flag that hides a model from rail pickers |
| POST | `/v1/load`, `/v1/unload` | Warm a model (evicting other heavy ones) / release it |
| POST | `/v1/cancel` | Cancel a queued or running job |
| POST | `/v1/chat`, `/v1/chat/stream` | Chat completion, buffered or token-streamed |
| POST | `/v1/embed`, `/v1/embed_image` | Text and image embeddings |
| POST | `/v1/image` | Image generation (media worker subprocess) |
| POST | `/v1/tts`, `/v1/tts_batch` | Cloned-voice narration with per-segment timings |
| POST | `/v1/tts_light` | Read-aloud (CPU/ONNX) |
| POST | `/v1/transcribe` | Dictation (CPU) |
| GET | `/v1/voice/catalog` | Registered voices across the dispatchable engines |
| POST | `/v1/voice/synthesize` | Synthesize through a named voice engine |

Image and cloned-voice work runs in a **short-lived media worker subprocess** under a separate
CUDA virtualenv, spawned per job and exited to reclaim VRAM, because torch will not release it
in-process. `/v1/status` reports which interpreter each op resolved to, which is the first thing
to read when media returns 502.

## Two speech paths, deliberately

`/v1/tts_light` and `/v1/transcribe` are **ungated and non-evicting** — CPU/ONNX, they never
touch the card. That is the only reason read-aloud and dictation can be offered on *every* rail:
pressing a mic button mid-conversation must not displace the model you are talking to. The
gateway re-exposes both at `/api/platform/*` for any logged-in user, so no rail needs the broker
token or voice plumbing of its own.

`/v1/tts` is not an alternative to be consolidated with it. It is GPU-gated and evicts, and it is
the only path that produces a **cloned** voice and the **per-segment timings** that highlight-sync
narration depends on.

## Identity

The gateway authenticates every request and forwards the verified user as `X-Platform-User`
(plus `X-Platform-Admin`), stripping any client-supplied copy first, so identity cannot be
spoofed. Rails do not trust the network: each re-checks the header and **fails closed with 401**
when it is absent, because in this topology a request without it came from a sibling container
rather than the gateway. Sessions are opaque server-side tokens in an HTTP-only cookie, with
Argon2id password hashing and server-side revocation.
