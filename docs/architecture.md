# Architecture

The short, repo-local version. The rail-by-rail contract and the reasoning behind each rule is
[RAIL_CONTRACT.md](RAIL_CONTRACT.md); how to stand the stack up is [INSTALL.md](INSTALL.md).

## The shape

```
                          host :1111
                              |
                        +-----v------+
   browser  ----------> |   Caddy    |  front door, plain HTTP (public TLS ends at Cloudflare)
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
`/openai/v1` surface, and every GET. It may not `PUT /v1/roles`, `PUT /v1/cli/roles/{role}`,
`PUT /v1/disabled`, `/v1/load`, `/v1/unload`, `/v1/cancel`, or touch `/v1/tokens`. That split is about mutation — repointing a
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
| GET | `/v1/upstreams` | Registered remote brokers a role may be delegated to, and whether each answers; never a token |
| GET · PUT | `/v1/roles`, `/v1/roles/{role}` | Read and repoint the `@role` map (hot; backs Admin → Rails). Reports `resolved` and `serving` separately |
| PUT | `/v1/cli/roles/{role}` | Switch one role's Claude CLI eligibility and choose its Claude model (hot; backs Admin → Rails) |
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

**A second text backend, opt-in: the Claude CLI.** Chat calls, buffered or streamed, may be
answered by `claude -p` instead of Ollama, translated back into the same Ollama-shaped response so
no rail learns a second shape. It serves only the platform owner's own requests, because that is
what subscription credentials permit: a call qualifies only when its `requester` is in
`BROKER_CLI_OWNERS` (and was sent with a `full`-scope credential), it names an `@role`, and that
role is eligible, per the admin's switches. Every failure falls back to Ollama, so a rail gains
latency rather than a new way to fail. Every default is off. The eligibility rules, the
configuration and the `cli` block in `/v1/status` are in
[`services/broker/README.md`](../services/broker/README.md) (*The Claude CLI backend*).

## When a role's model cannot run

A `@role` whose model is disabled or not installed is served by a capability-preserving
**substitute** rather than an error. `@vision` means "whatever the admin points vision at", so
re-pointing it under failure is inside the promise the role already makes. A **concrete** model
name is never substituted: you named that model, and answering with another is the same lie as a
wav labelled `audio/mpeg`. What a disabled concrete name gets depends on the surface. The OpenAI
surface refuses it, `403 model_disabled` (`services/broker/app/broker.py`
`resolve_for_external` raises, `openai_compat.py` answers). The platform paths do not: for a
concrete name `resolve_ref` returns it unchanged before it ever reads the disabled list, so
`/v1/chat`, `/v1/chat/stream` and `/v1/load` run a disabled model that a caller names directly.

Substitution is on the generative paths — `/v1/chat`, `/v1/chat/stream`, `/v1/load` and the whole
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
non-empty, and only to the tile that reads each** (the allowlists `PEER_TILES` and `ROOM_TILES` in
the gateway's `config.py`), so their absence is the common case and a rail's un-shared path stays
the default. A tile on neither list costs no peer or room query, and a new rail that reads one is
sent nothing until it is listed:

| Header | Shape | Consumed by | Answers |
|---|---|---|---|
| `X-Platform-User` | username | every rail | who is calling |
| `X-Platform-Admin` | `0` / `1` | every rail | may they administer |
| `X-Platform-Peers` | JSON array of usernames | edu-suite's content instance | whose work may I see |
| `X-Platform-Rooms` | JSON array of workspace ids | iep-goals | which rooms am I in |

Entitlements say which **apps** you may reach; a workspace says whose **data** you see inside one.
Two shapes because the rails ask different questions: edu-suite's content work has only jobs, so
ownership follows the creator; iep-goals hangs everything off a child, and a child belongs to a
*classroom* that outlives any one teacher. The **iep** tile (IEP Present Levels, edu-suite's image
run with `IEP_ONLY=1`) reads neither: each of its jobs IS a child's record, named after the child
with name, birthdate and IEP date in its header, and peers are the union across every workspace
the caller is in, so an aide in two classrooms bridged both rooms' children. It is owner-only
(admins see all), and it is on neither list, so the gateway sends it neither header. JSON rather
than comma-separated because usernames are `String(64)` with
no charset validation, so a comma is legal in one and would split it into two names that do not
exist. Peers never contains the caller. Admins are not special-cased: an admin who belongs to a
workspace gets a peer list like anyone else (`peer_usernames` in
`apps/platform/backend/platform_gateway_app/auth.py`, called by the access gate in `main.py`).
Each rail re-checks the header and **fails closed with 401** when it is absent, because in this
topology a request without it came from a sibling container rather than the gateway. A header
that IS present is believed from whoever sent it, so identity is trusted from the network. That
is why terminal-fun, the one rail that hands its users a shell, runs on a compose network shared
with the gateway alone and honours the headers only from the gateway's address
(`docs/RAIL_CONTRACT.md` § Identity). Sessions are opaque server-side tokens in an HTTP-only cookie, with
Argon2id password hashing and server-side revocation.

## The relay

`/<rail>/api/*` is relayed by one function, `_forward` in
`apps/platform/backend/platform_gateway_app/main.py`, which every rail shares, for every method
including `HEAD`. What reaches the rail is the client's request as sent, less what is the
platform's:

- **Stripped:** the client's `x-platform-*` headers (identity is set only by the gateway, above);
  the platform's own cookies (the session's, under its configured name and its default one, and
  Cloudflare Access's `CF_Authorization`); and the front door's credentials in headers
  (`Authorization`, `Proxy-Authorization`, `Cf-Access-Jwt-Assertion`, `Cf-Access-Client-*`). No rail
  reads `Authorization` from a relayed request: the bearer a rail sends (its broker token) goes
  rail to service, never through the gateway.
- **Kept as sent:** the query string, byte for byte (repeated keys in order), and the client's
  `Accept-Encoding`. A client that names none is asked of the rail as `identity`, because the rail's
  bytes come back under the rail's own `Content-Encoding` and httpx would otherwise ask for gzip on
  its behalf.
- **The answer:** each response header is copied line by line (repeated `Set-Cookie` lines survive),
  except a `Set-Cookie` naming one of the platform's own cookies: a rail cannot set, replace or
  clear the session or Access's cookie.

The shell (`/`, any client-side route) and `/api/platform/healthz` answer `HEAD` too. A `HEAD` for
another `/api/platform/*` route is the 405 naming that route's methods, and for no route a 404.

**Request bodies are streamed, never read whole, and each rail has a ceiling.** The ceiling is the
rail's `max_request_bytes` (mirrored in the gateway's `MAX_REQUEST_BYTES`, because the gateway
container cannot see `rails/`), else the platform default of 26 MiB (`PLATFORM_MAX_REQUEST_BYTES`).
A tile in a rail's `also_serves` takes the rail's figure unless `also_serves_default_ceiling` holds
it to the default, as edu-suite's iep tile is: it takes one 25 MiB PDF, not a folder.
A `Content-Length` over it is a 413 before the body is read and before the rail is contacted;
anything else is relayed as it arrives and cut off with a 413 once it passes the ceiling, so the
gateway holds one chunk at a time whatever the client sends. Nothing replays a body: the relay's
httpx client makes no retries and follows no redirects. The figures and RC035, which keeps the
mirror honest, are in `docs/RAIL_CONTRACT.md` § Request bodies.

**Responses are streamed too, never read whole.** Each is relayed as the rail sends it
(`_RelayedResponse`): the rail's raw bytes under its own `Content-Length` and `Content-Encoding`,
which describe them exactly because the gateway never decodes them, with one chunk in flight and
back-pressure running from the client's socket to the rail's. A request that asks for
`text/event-stream` is asked of the rail uncompressed and has no read timeout (a live job log must
not arrive in lumps, or die in a quiet stage); everything else keeps the 600 s read timeout, so a
hung rail cannot hold a request for ever. A client that goes away closes the rail's response. A rail
that breaks off mid-body can no longer be answered with a 502, because the status line has gone, so
the response is left unfinished instead (`_CutShortGuard`, outside the http middlewares, which
would otherwise end the body cleanly): the browser sees a failed download, never a finished shorter
file. (Streamed answers in the rails travel over the gateway's WebSocket proxy, a choice made while
ordinary responses were still buffered.)

**Two upstream clients, so long transfers cannot starve page loads.** Streaming both ways means a
request holds its rail connection for as long as its slower end takes: an upload until its last
byte, a download until its client has read it, an event stream for its whole life. An **upload**
(a body over 1 MiB, or of unknown length) goes out on its own client, capped at 32 connections for
the whole box; one that cannot get a connection within 30 s is a 503 with `Retry-After`, before
any of its body has been read. **Everything else**, event streams and small JSON writes included,
goes out on a client with no cap on connections: the relay holds one rail connection per request
in flight, as nginx and Caddy do, so a cap would only add a queue. Both are built and closed by the
lifespan (`_relay_clients`, `_RELAY_LIMITS`, `_UPLOAD_LIMITS` in `main.py`). Both drop a connection
idle for 2 s (`_KEEPALIVE_EXPIRY`) rather than reuse it, well inside the 5 s a rail's uvicorn keeps
one open, so a request never goes out on a connection the rail is closing (a 502 for a request it
never read). An expiry, not a retry: a relayed POST may be a generation or a write.

**The platform's own routes are not relayed, and have a ceiling of their own.** Every
`/api/platform/*` body is a small JSON document, read whole by FastAPI before any dependency runs,
login's included, so `_PlatformBodyCeiling` holds each to 1 MiB before a route sees it: a larger
`Content-Length` is a 413 unread, and a chunked body is cut off one chunk past the ceiling.
Dictation (`/api/platform/transcribe`, up to 15 MB of base64 audio) has a larger room, for a
signed-in caller only (`_PLATFORM_BODY_ROOM`).
