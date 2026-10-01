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

The whole control plane sits behind a bearer, applied as an app-wide dependency so no `/v1/*` or
`/openai/v1/*` route is reachable untokened by a rogue container or LAN host. `/healthz` is
always open for liveness.

Two kinds of credential are accepted, and the difference is operational rather than technical.
**`BROKER_AUTH_TOKEN`** is one shared secret at full scope; every rail sends it, and the gateway
authenticates with it in order to reach `/v1/tokens` at all, which is why it is deliberately not
listed or revocable in the admin UI. **Named tokens** are per-host, labelled, individually
revocable and scoped `inference` or `full`. They are minted in Admin → Broker and stored in
`services/broker/tokens.json` as sha256 hashes; the plaintext exists only in the response that
mints it. That file is hot-read on every request, exactly like `roles.json`, so a revoke lands on
the offending host's next call with no restart.

The gate is **open only when nothing is configured at all** — no `BROKER_AUTH_TOKEN` *and* no
token store on disk — which is the dev and staged-rollout case. Enforcement keys on the store
EXISTING, not on it having rows: revoke the last named token and the broker stays closed, because
"I revoked everything" has to mean "nobody is authorised" rather than "authorisation is off". A
store that fails to parse counts as configured and fails closed. Deleting the file is how you
reopen, deliberately.

An `inference` token may run work and read state: chat, embeddings, media, voice, the whole
`/openai/v1` surface, and every GET. It may not `PUT /v1/roles`, `PUT /v1/disabled`, `/v1/load`,
`/v1/unload`, `/v1/cancel`, or touch `/v1/tokens`. That split is about mutation — repointing a
role moves every user's model at once, and unload evicts the model somebody is mid-conversation
with. Reading and minting are both `full`-only because a token that could reach `/v1/tokens`
could mint itself a `full` one.

Both sides also accept **`BROKER_AUTH_TOKEN_FILE`** — a path whose contents are the token, which
wins when both are set — so the shared secret can be delivered as a mounted file and stay out of
`docker inspect`, crash dumps and `/proc/<pid>/environ`. The value form is unchanged and is what
the shipped compose files pass; see the broker-access section of `docs/RAIL_CONTRACT.md` for the
precedence and failure rules, which differ deliberately between a client (empty token → 401, loud)
and the broker (empty token → an open control plane on any box without a token store, so it
refuses to start instead rather than silently disarming the gate it was added to strengthen).

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness + Ollama reachability + version (never gated) |
| GET | `/v1/status` | Full view: Ollama up, loaded models, GPU VRAM, queue depth, live jobs, `fallbacks.count`, media interpreter paths |
| GET | `/v1/models` | Installed models, classified heavy/embed |
| GET | `/v1/ps` | Currently loaded models + per-model VRAM |
| GET · PUT | `/v1/roles`, `/v1/roles/{role}` | Read and repoint the `@role` map (hot; backs Admin → Rails). Reports `resolved` and `serving` separately |
| GET · POST · DELETE | `/v1/tokens`, `/v1/tokens/{id}` | List, mint and revoke named tokens. `full` scope only; the plaintext is returned once, by the mint |
| GET · PUT | `/v1/disabled` | The reversible availability flag that hides a model from rail pickers |
| POST | `/v1/load`, `/v1/unload` | Warm a model (evicting other heavy ones) / release it |
| POST | `/v1/cancel` | Cancel a queued or running job |
| POST | `/v1/chat`, `/v1/chat/stream` | Chat completion, buffered or token-streamed |
| POST | `/v1/embed`, `/v1/embed_image` | Text and image embeddings |
| POST | `/v1/image` | Image generation (media worker subprocess) |
| POST | `/v1/tts`, `/v1/tts_batch` | Cloned-voice narration with per-segment timings |
| POST | `/v1/tts_light` | Read-aloud (CPU/ONNX) |
| POST | `/v1/transcribe` | Dictation (CPU) |
| GET | `/v1/voice/catalog` | Every registered voice, each flagged runnable or not |
| POST | `/v1/voice/synthesize` | Synthesize through a named voice engine |

The gateway re-exposes the token routes to **super-admins only**, at `GET` and `POST
/api/platform/admin/broker/tokens` and `DELETE /api/platform/admin/broker/tokens/{id}`. A plain
admin manages users and rooms; a broker token is not scoped to a rail or a user, so handing one
out is a different kind of authority.

Image and cloned-voice work runs in a **short-lived media worker subprocess** under a separate
CUDA virtualenv, spawned per job and exited to reclaim VRAM, because torch will not release it
in-process. `/v1/status` reports which interpreter each op resolved to, which is the first thing
to read when media returns 502.

## When a role's model cannot run

A `@role` whose model is disabled or not installed is served by a capability-preserving
**substitute** rather than an error. `@vision` means "whatever the admin points vision at", so
re-pointing it under failure is inside the promise the role already makes. A **concrete** model
name is never substituted: you named that model, and answering with another is the same lie as a
wav labelled `audio/mpeg`, so a disabled one stays `403 model_disabled`.

This is on the generative paths — `/v1/chat`, `/v1/chat/stream`, `/v1/load` and the whole
`/openai/v1` surface, all of which now resolve through one function so the tray cannot warm the
model a role names while chat serves a different one. `/v1/embed` and `/v1/image` still resolve
without substituting.

Capability is asked of the backend (Ollama's `/api/show`), not of the model catalog's category,
which is editorial and wrong in both directions — this repo files `gemma4:12b` as chat while the
installer catalog files it under vision and the lean role map points `@vision` at `gemma3:4b`.
The category ranks candidates; the backend decides. With nothing installed that shares the
capability the broker refuses, `409 no_substitute` naming the category, and an `@role` nothing
defines is `400 unknown_role` listing the known ones rather than reaching Ollama as a model
literally named `nosuchrole`.

Every substitution announces, because one that let a broken role map look healthy would be worse
than the error it replaces: `X-Model-Fallback*` headers on the OpenAI dialect, a `fallback`
object on the `/v1/chat` body, a leading NDJSON frame before the first token on
`/v1/chat/stream`, a log line on **every** call, a marker on the job in the queue view, and a
persistent `fallbacks.count` on `/v1/status`. `/v1/roles` keeps `resolved` (what the admin set)
and `serving` (what would actually run) as separate fields, so the Rails picker cannot start
reporting a substitute as the setting and invite someone to "fix" a role that was never wrong.

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
spoofed. The strip is `startswith("x-platform-")`, so it covers every header below.

**Four trusted headers, not two.** The last two carry *sharing* and are sent **only when
non-empty**, so their absence is the common case and a rail's un-shared path stays the default:

| Header | Shape | Consumed by | Answers |
|---|---|---|---|
| `X-Platform-User` | username | every rail | who is calling |
| `X-Platform-Admin` | `0` / `1` | every rail | may they administer |
| `X-Platform-Peers` | JSON array of usernames | edu-suite | whose work may I see |
| `X-Platform-Rooms` | JSON array of workspace ids | iep-goals | which rooms am I in |

Entitlements say which **apps** you may reach; a workspace says whose **data** you see inside one.
Two shapes because the rails ask different questions: edu-suite has only jobs, so ownership follows
the creator; iep-goals hangs everything off a child, and a child belongs to a *classroom* that
outlives any one teacher. JSON rather than comma-separated because usernames are `String(64)` with
no charset validation, so a comma is legal in one and would split it into two names that do not
exist. Peers never contains the caller, and admins get no peer list because they are unrestricted
already. Rails do not trust the network: each re-checks the header and **fails closed with 401**
when it is absent, because in this topology a request without it came from a sibling container
rather than the gateway. Sessions are opaque server-side tokens in an HTTP-only cookie, with
Argon2id password hashing and server-side revocation.
