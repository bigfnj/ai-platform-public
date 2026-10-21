# GPU / Model Broker (v0)

The single owner of the GPU. Apps never touch Ollama directly — they call this.
Ollama is the local **text** backend — chat, streaming chat and embeddings all
forward to it. An opt-in Claude CLI backend can also answer the platform owner's own
chat calls, buffered or streamed (see **The Claude CLI backend** below). Images (SDXL / FLUX),
cloned-voice and read-aloud speech (XTTS / Kokoro) and transcription (faster-whisper) are the broker's too, run as short-lived
worker subprocesses under their own interpreters, which exit to reclaim VRAM because
torch will not give it back in-process. See `../../docs/architecture.md` for the why.

## Run (Windows PowerShell, from the repo root)

```powershell
# one-time: create a venv and install
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e packages\platform_core
pip install -e "services\broker[dev]"

# run the broker
uvicorn app.main:app --app-dir services\broker --host 127.0.0.1 --port 11500
```

## Endpoints

Two dialects, deliberately on separate prefixes. `/v1/*` is what the rails speak.
`/openai/v1/*` is the OpenAI-compatible surface for outside clients.

```
GET    /healthz          liveness + Ollama reachability. The one route the token never gates
GET    /v1/status        reachable, loaded models, GPU VRAM, queue depth, live jobs,
                         fallbacks.count, and which interpreter each media op resolved to
GET    /v1/models        installed models, each classified heavy | embed
GET    /v1/upstreams     registered remote brokers and whether each answers; never a token
                         (see Delegating a role to another broker)
GET    /v1/roles         role -> pattern -> resolved (what the admin set) + serving (what runs)
                         ?inventory=true adds every box's health + models from the SAME probe
                         (the Rails tab's one read; each remote is dialled once per load)
                         ?local_only=true: this card's roles only, no remote dialled (Models tab)
PUT    /v1/roles/{r}     repoint a role (hot; no restart)
PUT    /v1/cli/roles/{r} {"enabled": bool, "model": "<id>"|null}: one role's Claude CLI switch and
                         model, either or both (hot; see The Claude CLI backend)
GET    /v1/tokens        named tokens without their hashes, + whether the shared secret is set
POST   /v1/tokens        {"label": "...", "scope": "inference"|"full"} -> the plaintext, ONCE
DELETE /v1/tokens/{id}   revoke; effective on the next request, no restart
GET    /v1/disabled      the admin-disabled model names
PUT    /v1/disabled      {"names": [...]} replaces the whole set (hot)
GET    /v1/ps            currently loaded models + per-model VRAM
POST   /v1/load          {"model": "...", "keep_alive": -1}  -> evicts other heavy models first
POST   /v1/unload        {"model": "..."}
POST   /v1/cancel        {"seq": N}  -> drop a waiting job / abort an active one
POST   /v1/chat          {"model": "...", "messages": [{"role":"user","content":"hi"}]}
POST   /v1/chat/stream   NDJSON token stream, same gate
POST   /v1/embed         {"model": "bge-m3", "input": "text or [texts]"}
POST   /v1/embed_image   {"images": [<b64>]} SigLIP, CPU, un-gated — retrieval grounding
POST   /v1/image         {"prompts": [...], "model": "sdxl-turbo"}
POST   /v1/tts           XTTS voice-clone, per-segment timings, GPU-gated
POST   /v1/tts_batch     many clips -> separate wavs, XTTS loaded once
POST   /v1/tts_light     Kokoro read-aloud, CPU, un-gated, evicts nothing
POST   /v1/transcribe    faster-whisper, CPU, un-gated
GET    /v1/voice/catalog every registered voice, flagged runnable or not (ai-voice rail)
POST   /v1/voice/synthesize
                         one named voice through its engine's own venv; evicts everything
```

The three `/v1/tokens` routes, `PUT /v1/roles`, `PUT /v1/cli/roles/{role}`, `PUT /v1/disabled`,
`/v1/load`, `/v1/unload` and `/v1/cancel` are the `full`-scope set; everything else above is
reachable with an `inference` token. See **Access tokens** below.

The media routes (`/v1/image`, the three tts routes, `/v1/transcribe`, `/v1/embed_image`) each run
one short-lived worker (`app/media_worker.py`) under the media interpreter. A job has a directory
of its own in the broker's temp (`broker-media-*`) holding its spec, its result and a `scratch`
directory the worker makes its own temp files in (a transcribe's clip, a tts job's wav), and the
directory goes when the job ends however it ends: an answer, a failure, a timeout or a
`/v1/cancel`, the worker killed first. One left by a broker that stopped mid-job (or held past the
removal's tries by a file filter) goes at the next start, once nothing in it has changed for an
hour or three `BROKER_MEDIA_TIMEOUT`s, whichever is longer. The spec (up to 15 MB for a
transcribe) and the result (an image batch's runs to several MB) are written and read in a
thread, so neither stalls the routes around them. A voice job (`/v1/voice/synthesize`) has a
directory of its own too (`broker-voice-*`, the text being read aloud and the engine's wav),
removed when it ends; one a broker that stopped mid-job left goes at the next start by the same
rule, against `BROKER_VOICE_TIMEOUT`.

`/v1/image` draws with `sdxl-turbo` or `flux-schnell`, named in any case (`FLUX-Schnell` is
flux-schnell, and the job is labelled that way), or an `@role` set to one, in any case too (and
/v1/roles and the startup audit read such a role the way it renders); any other model is a
400 before anything is evicted. The OpenAI images route takes the same names and maps every other
OpenAI model name (`dall-e-3`) to `sdxl-turbo`.

The settings writes (`PUT /v1/roles/{r}`, `PUT /v1/cli/roles/{r}`, `PUT /v1/disabled`, `POST` and
`DELETE /v1/tokens`) go through a temp file and an atomic rename, retried for about 0.6 s while
something on the host holds the file (an endpoint scanner, an editor). A file still held after
that, or one the broker cannot write at all, is a **409** naming the file: nothing changed (a
token is not minted, a revoke has not happened) and no `<name>.tmp` is left behind. Try again.

**Every request body has a ceiling**, checked before a route reads it (`app/main.py`
`_TokenAndBodyGate`): a `Content-Length` over it is a **413** before a byte is read, and a
chunked body is counted and cut off one chunk past it. On `/openai/v1/*` the 413 is in OpenAI's
error shape (`code: "request_too_large"`).

| ceiling | routes | why that size |
|---|---|---|
| 32 MiB | `/v1/chat`, `/v1/chat/stream`, `/v1/embed`, `/v1/embed_image`, `/v1/tts`, `/v1/tts_batch`, `/openai/v1/chat/completions`, `/openai/v1/embeddings` | a chat carrying several photos; a 500-item tts batch of 8,000-character texts, every character JSON-escaped (24 MB) |
| 15 MB + 1 MiB | `/v1/transcribe` | the 15,000,000-character base64 bound `TranscribeRequest` holds, plus the rest of the JSON |
| 11.25 MB + 1 MiB | `/openai/v1/audio/transcriptions` | the largest file whose base64 fits that bound, plus the multipart framing |
| 1 MiB | everything else | the control plane, the 8,000-character speech routes, image prompts |

`/v1/chat` and `/v1/chat/stream` speak **Ollama's** dialect: `model`, `messages`, `options`,
`keep_alive`, `format`, `think`, `requester`. `requester` counts only from a `full`-scope
credential: both routes drop it from an `inference` token before the Claude CLI decision (see
**Access tokens**). Sampling goes inside `options` (`num_predict`,
`temperature`, …). Any other top-level field (`max_tokens`, `temperature`, `stream`: the
OpenAI spellings) is accepted and ignored, and the broker logs
`WARNING: <route> ignored unknown field(s) ...` naming it (`app/main.py`
`_warn_unknown_fields`; names only, never values), so a setting that never reached the
model shows up in the service log instead of nowhere. For the OpenAI shape, use the
`/openai/v1` surface below.

**Before Ollama is up.** Where the broker runs as a service and Ollama starts at logon, the
broker comes up first. Until Ollama answers, `/v1/models` and `/v1/ps` return 503
`{"detail": "Ollama is not reachable at <url>"}`, logged once per outage rather than once per
request. The startup role audit is deferred, not skipped: the broker says so, retries in the
background (2, 4, 8 … 60 s apart, for up to 30 minutes), and prints its `ROLE WARNING` lines
once Ollama answers, or one `role audit skipped: Ollama never answered` line if it never does.

## OpenAI-compatible surface

Point any OpenAI client at the broker and it gets the GPU queue for free:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:11500/openai/v1",
                api_key="<BROKER_AUTH_TOKEN>")     # sent as the bearer token
```

```
GET  /openai/v1/models                 {"object":"list","data":[...]}
POST /openai/v1/chat/completions       streaming + buffered
POST /openai/v1/embeddings
POST /openai/v1/audio/speech           -> raw audio bytes (Kokoro)
POST /openai/v1/audio/transcriptions   multipart (faster-whisper)
POST /openai/v1/images/generations     -> b64_json (SDXL / FLUX)
```

**Why not just point at Ollama's own `/v1`?** Because this adds the GPU gate
(requests queue FIFO instead of racing two 18 GB loads onto a 24 GB card),
one-heavy-model eviction, `@role` indirection, the admin disable flag, and the
broker token.

`model` accepts a concrete name, a glob, or an `@role` — so a client can ask for
`@chat` and the admin repoints it live. A model the admin has **disabled** is absent
from `/models`, and the check is on the *resolved* name, so disabling `gemma4:26b`
reaches `@vision` too.

What happens then depends on how you asked, and the asymmetry is deliberate:

| you asked for | the model cannot run | why |
|---|---|---|
| an `@role` | a capability-compatible **substitute** runs, announced in whichever dialect asked | `@vision` means "whatever the admin points vision at". Re-pointing it is inside that promise |
| a concrete name | `403 model_disabled` | you named that model. Answering with another is the same lie as a wav labelled `audio/mpeg` |
| an `@role` with no substitute | `409 no_substitute`, naming the category | the admin fixes it by pulling or re-enabling. `openai-python` maps 409 to `ConflictError` |
| an `@role` nothing defines | `400 unknown_role`, listing the known ones | it used to reach Ollama as a model literally named `nosuchrole` |

**A substitution is a degraded state and is never silent.** The caller hears it in the
dialect it asked in: `X-Model-Fallback` / `-Original` / `-Reason` / `-Category` response
headers on `/openai/v1/chat/completions` (headers, because that body belongs to somebody
else's spec and the `model` field in it already names the substitute), a `fallback` object
added to the `/v1/chat` body, and a leading `{"fallback": {...}}` frame before the first
token on `/v1/chat/stream` — a shape all three rail stream readers already skip, and
deliberately not keyed `error`, which they all raise on. The operator hears it four more
ways: a broker log line on *every* call, a marker on the job in the queue view, a
persistent `fallbacks.count` on `/v1/status`, and a `will fall back to X` clause from the
startup role audit. A fallback that let a broken role map look healthy would be worse than
the error it replaces.

`/v1/roles` keeps the two apart rather than collapsing them: `resolved` stays what the
admin configured, so the Rails picker goes on showing their own choice, and `serving` is
what would actually run. One field for both is how an admin ends up "fixing" a role that
was never wrong.

⚠ Capability is asked of the BACKEND (`/api/show`), not of the model catalog's
category, which is editorial. This repo files `gemma4:12b` as `chat` while the
installer catalog lists it under vision and the lean role map points `@vision` at
`gemma3:4b`. Verified live: with `gemma4:26b` disabled, `@vision` falls back to
`gemma4:12b` — a substitution a category-only rule would have refused.

Chat and embeddings are **forwarded** to Ollama's own OpenAI layer, not translated,
so `tools`, `tool_choice`, vision content parts, `response_format` and
`reasoning_effort` work without the broker knowing they exist.

### ⚠ Thinking models: use `reasoning_effort`, not `think`

`think` is a `/v1/chat` parameter and does **nothing** on the OpenAI path. A
reasoning model will spend its whole output budget thinking and return empty
content with `finish_reason: "length"`. Measured on `qwen3:1.7b` at 30 max_tokens:
`think: false` → `""`, `reasoning_effort: "none"` → `"pong"`.

### What this server refuses, and why

Audio and images run on the broker's own workers, so unsupported options are
rejected rather than approximated — a wav returned as `audio/mpeg` is a corrupt
file with no error anywhere.

| request | answer |
|---|---|
| `response_format` mp3 / opus / aac / flac | `400` — only `wav` and `pcm`; no encoder ships here. Omitted defaults to `wav` |
| `speed` outside 0.5–2.0 | `400` — Kokoro's real range, not silently clamped |
| OpenAI voice names (`alloy`, `nova`, …) | accepted, fall back to `BROKER_KOKORO_VOICE`. Kokoro ids (`af_heart`, `ef_dora`) pass through |
| transcription `srt` / `vtt` | `400` — no per-segment timings to build cues from |
| images `response_format: "url"` | `400` — only `b64_json`; nothing hosts a URL here |
| non-square image `size` | `400` — square only |
| images `n` > 4 | `400` — each one evicts every heavy model and holds the card |
| `model: "dall-e-3"` / `"whisper-1"` | accepted and ignored; falls back to the configured backend |

## Configuration (`BROKER_*`)

Every setting is one field of `BrokerSettings` (`app/config.py`) under `env_prefix="BROKER_"`,
so the env name is the field name upper-cased with that prefix. A `.env` in the process's
working directory is read too, and unknown `BROKER_*` names are ignored rather than rejected —
a typo is silent, so check the spelling against this table. `deploy/install-services.ps1`
injects the host-specific paths into the NSSM service environment.

**Secret** marks a value that is itself a credential, or a path to a file holding one. Those
rows are the reason this table exists: until it did, "where does this box keep its tokens" was
answerable only by reading `config.py`.

There is no host or port setting. The bind is uvicorn's `--host`/`--port` on the service command
line, and the two installers choose it differently:

- `deploy/install-services.ps1` (the full stack) passes `0.0.0.0` and `11500`, which puts the GPU
  on the LAN: set a token alongside.
- The lean installer (`deploy/installer/install.ps1`, which registers the service through
  `install-native.ps1`) binds port `11500` on what `Get-BrokerBindHost` in
  `deploy/installer/lib-runtime.ps1` returns for the runtime: `0.0.0.0` in `wsl` mode, whose
  containers arrive over a WSL adapter with no fixed address, together with firewall rules that
  admit only the WSL range and loopback; `127.0.0.1` otherwise (podman and Docker Desktop deliver
  container traffic from loopback). It also writes a token into `deploy\.env` and the service's
  environment.

| Variable | Default | Secret | Notes |
|---|---|---|---|
| `BROKER_AUTH_TOKEN` | *(unset — open)* | **yes** | Shared control-plane secret; every `/v1/*` route requires it as `Authorization: Bearer` or `X-Broker-Token`. Empty = open, which is a deliberate choice on a single-user box, not an oversight. |
| `BROKER_AUTH_TOKEN_FILE` | *(unset)* | **yes (path)** | The same secret read from a file, and the form that WINS when both are set — it keeps the token out of `docker inspect` and `/proc/<pid>/environ`. Contents are stripped, so a trailing newline from `echo` is not an auth failure. Write it as UTF-8 (a BOM is fine): a file that is not, UTF-16 with or without a BOM included, or whose token holds a control character, stops the broker at start saying so. |
| `BROKER_TOKENS_FILE` | `services/broker/tokens.json` | **yes (path)** | The named per-host token store (sha256 hashes, plus a label per host you issued to). Relocating it moves security-relevant state off the repo tree. Enforcement keys on the file EXISTING — see *Access tokens* below. |
| `BROKER_UPSTREAMS_FILE` | `services/broker/upstreams.json` | **yes (path)** | Registry of remote brokers to delegate roles to, `{name: {url, token}}`. Holds one bearer token per remote, which is why it is a file and not env. |
| `BROKER_ROLES_FILE` | `services/broker/roles.json` | | `{role: glob}` overlay on `DEFAULT_ROLES`, hot-read on every resolve (no restart). |
| `BROKER_DISABLED_FILE` | `services/broker/disabled.json` | | JSON list of admin-disabled model names. |
| `BROKER_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | | The text backend. |
| `BROKER_OLLAMA_TIMEOUT` | `600.0` | | Seconds. Generous because a cold heavy load is slow. |
| `BROKER_DEFAULT_LOAD_KEEP_ALIVE` | `30m` | | Applied when `/v1/load` names none. `-1` = resident indefinitely. |
| `BROKER_EMBED_NAME_HINTS` | `embed,bge,nomic-embed,mxbai,gte,e5,minilm` | | Comma-separated substrings that mark a model *light* (exempt from the one-heavy-model policy). |
| `BROKER_MEDIA_ENABLED` | `true` | | `false` on a box with no torch venv. |
| `BROKER_MEDIA_PYTHON` | `<ai-work>/venvs/media-venv/Scripts/python.exe` | | Interpreter with torch/diffusers + `edu_media_core`. Shared media venv OUTSIDE the repo. |
| `BROKER_TTS_PYTHON` | *(unset → `MEDIA_PYTHON`)* | | Separate venv for XTTS: `coqui-tts` and the image stack want incompatible `transformers`. |
| `BROKER_MEDIA_CORE_SRC` | `packages/edu-media-core/src` | | Prepended to the worker's `sys.path`. |
| `BROKER_MEDIA_VOICES_DIR` | `packages/edu-media-core/voices` | | XTTS reference clips. |
| `BROKER_MEDIA_TIMEOUT` | `1200.0` | | Seconds, per media job. |
| `BROKER_IMAGE_EMBED_MODELS` | `google/siglip2-base-patch16-384` | | Hugging Face ids `/v1/embed_image` may load, comma-separated; the first is the default. Any other `model` is a `400`, because the id is downloaded into the HF cache on first use. |
| `BROKER_LIGHT_MEDIA_CONCURRENCY` | `2` | | How many ungated CPU media jobs (read-aloud, dictation, image embedding) run at once. Each is its own 350-500 MB interpreter; past this they wait for one another, never for the GPU gate. |
| `BROKER_KOKORO_PYTHON` | *(unset → `MEDIA_PYTHON`)* | | Own venv for a hard reason: `kokoro-onnx` wants numpy ≥ 2, the image path's inpainting wants numpy < 2. |
| `BROKER_KOKORO_MODEL_PATH` | *(unset — `/v1/tts_light` disabled)* | | `kokoro-v1.0*.onnx`. |
| `BROKER_KOKORO_VOICES_PATH` | *(unset — same)* | | `voices-v1.0.bin`. |
| `BROKER_KOKORO_VOICE` | `af_heart` | | Platform-wide default read-aloud voice. |
| `BROKER_KOKORO_LANG_CODE` | `a` | | `a` US English, `b` British, `e` Spanish. Must AGREE with the voice or the audio is garbled. |
| `BROKER_WHISPER_PYTHON` | *(unset → Kokoro's, then media)* | | Torch-free (CTranslate2), so it shares the light-speech venv. |
| `BROKER_WHISPER_MODEL` | `small` | | Multilingual on purpose. A `.en` model ignores the language parameter and turns Spanish into nonsense. |
| `BROKER_WHISPER_DEVICE` | `cpu` | | CPU so dictation never queues behind the GPU gate. |
| `BROKER_WHISPER_COMPUTE_TYPE` | `int8` | | |
| `BROKER_VOICE_ENABLED` | `true` | | The ai-voice studio dispatch. |
| `BROKER_VOICE_ENGINES_DIR` | *(unset — voice reports unavailable)* | | Root of the host-native engine venvs, supplied by the deployment. |
| `BROKER_VOICE_REGISTRY` | *(unset → `<engines dir>/voices/registry.json`)* | | `voice_id` → engine + assets. |
| `BROKER_VOICE_ENGINES_FILE` | *(unset → `services/broker/voice_engines.json`)* | | Overlay on the built-in engine table. |
| `BROKER_VOICE_TIMEOUT` | `1200.0` | | Seconds, per synthesis job. |
| `BROKER_CLI_ENABLED` | `false` | | The Claude CLI backend at all. Inert until the owners, an eligible role and the workdir are set too (see *The Claude CLI backend*). |
| `BROKER_CLI_EXE` | *(unset → PATH, then npm's global root)* | | Absolute path to `claude.exe`; a `.cmd`/`.ps1` shim is refused. |
| `BROKER_CLI_OWNERS` | *(unset — nobody)* | | Platform usernames whose requests may use the CLI, comma-separated, exact. Every one must be the ONE subscriber's own account. |
| `BROKER_CLI_ROLES` | *(unset — none)* | | Eligible roles, comma-separated, until a switch file exists. |
| `BROKER_CLI_ROLES_FILE` | *(unset)* | | The Rails tab's per-role switches and models, JSON. No default path, on purpose; once the file exists it decides. |
| `BROKER_CLI_TIMEOUT` | `180.0` | | Seconds per call, the wait for a slot included; past it the call falls back to Ollama. |
| `BROKER_CLI_CONCURRENCY` | `2` | | CLI calls that may run at once; the rest wait for a slot. |
| `BROKER_CLI_WORKDIR` | *(unset — a blocker)* | | An EMPTY directory outside the repo, so no project instructions reach a rail's prompt. Required. |
| `BROKER_CLI_TEMP_ROOT` | *(unset → `cli-tmp` beside the workdir)* | | Where each CLI call's own TEMP directory is made and removed (see *What a call leaves on disk*). Outside the repo and OneDrive, no `CLAUDE.md` above it, never inside the workdir. |
| `BROKER_CLI_OAUTH_TOKEN` | *(unset)* | **yes** | A `claude setup-token` token, for a service account with no login of its own. Prefer the `_FILE` form. |
| `BROKER_CLI_OAUTH_TOKEN_FILE` | *(unset)* | **yes (path)** | The same token read from a file (UTF-8). Unreadable is a blocker, not a refusal to start. |
| `BROKER_CLI_MODEL` | *(unset — the CLI's own default)* | | The model for a role the switch file gives none. |
| `BROKER_CLI_MODELS` | `claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5` | | The models a role may be given, in the order the Rails tab offers them. |
| `BROKER_CLI_SETTINGS_JSON` | excludes the user-scope `CLAUDE.md` and rules | | JSON handed to `claude --settings`. Blank it only to send the account's own instructions with every call. |

The Claude CLI rows belong to the box and are set on the service rather than in a tracked file;
`install-services.ps1 -BrokerCliEnvFile` takes them from a file outside the checkout (see
*Setting them with install-services.ps1*).

## Access tokens

Two kinds, and the difference is operational rather than technical.

**`BROKER_AUTH_TOKEN`** is one shared secret, full scope, set in `deploy/.env` and the broker's
NSSM service environment. Every rail sends it. To stop trusting one machine you have to rotate it
and then re-deploy every rail, which is why the second kind exists.

**Named tokens** are per-host, labelled, individually revocable and scoped. Create them in
**Admin → Broker** (super-admin only). Stored in `services/broker/tokens.json` as sha256
**hashes**; the plaintext is returned exactly once, by the request that mints it, and there is no
endpoint that can show it again.

| scope | may | may not |
|---|---|---|
| `inference` | chat, streaming chat, embeddings, media, voice, the whole `/openai/v1` surface, and every GET | `PUT /v1/roles`, `PUT /v1/cli/roles/{role}`, `PUT /v1/disabled`, `/v1/load`, `/v1/unload`, `/v1/cancel`, anything under `/v1/tokens`, or name who is asking (`requester`) |
| `full` | everything | — |

`inference` is what a remote workstation on the LAN wants: it can use the GPU and read state, and
it cannot repoint a rail's model for every user or evict the model somebody is mid-conversation
with. A token that could reach `/v1/tokens` could mint itself a `full` one, which is why reading
and minting are both `full`-only.

Nor can it say whose request it is. `requester` on `/v1/chat` decides one thing, whether a
platform owner's Claude subscription may answer (see the Claude CLI section), and the broker
cannot check it. A `full`-scope credential could repoint every role anyway, so its `requester` is
honoured. An `inference` token's is dropped before that decision (`app/main.py`
`_trusted_requester`): the call is answered as background work, locally, and counted under
`no_requester`, and the broker logs the token's label, once per token. A broker with no token configured
at all is open, and treats every caller as `full` there as on every other route.

```powershell
# issue one, then on the remote host:
BROKER_AUTH_TOKEN=bt_...            # or the api_key of an OpenAI client -> /openai/v1
```

Properties worth knowing before relying on this:

- **The token is checked before the body is read.** With either kind of credential configured,
  a request with no token or a wrong one is a 401, and an `inference` token on a `full`-scope
  route a 403, before the broker reads a byte of its body (`app/main.py` `_TokenAndBodyGate`,
  which makes `require_token`'s own decision). That covers every path but `/healthz`: FastAPI's
  `/docs` and `/openapi.json`, which the per-route check never reached, and a path no route
  serves (401, not 404). An open broker (nothing configured) is unchanged, bar the body ceiling.
- **Revocation is immediate.** `tokens.json` is hot-read, like `roles.json`, so a revoked host
  stops working on its next call with no restart. It is parsed once per version of the file: a
  stat on each request decides whether it changed (a replaced file is a new file), and the
  broker's own mint or revoke drops the parse it held, so that next call sees it whatever the
  file system's timestamps say.
- **Enforcement keys on the token store EXISTING, not on it having rows.** Revoke the last named
  token on a box with no `BROKER_AUTH_TOKEN` and the broker stays closed — "I revoked everything"
  means nobody is authorised, not that authorisation is off. Delete the file to reopen
  deliberately. A file that fails to parse also counts as configured, and fails closed: that
  includes one saved as UTF-16 (Windows PowerShell 5.1's `>`), which reads as no tokens, while
  `/healthz` keeps answering.
- **The FIRST named token needs `BROKER_AUTH_TOKEN` set.** On a broker with no shared secret and
  no store, `POST /v1/tokens` answers `409` and writes nothing (`app/main.py` `create_token`):
  creating the store would switch enforcement on for the gateway and every rail, none of which
  hold a token, and the console could not reach `/v1/tokens` again to undo it. Set the shared
  secret for the broker and the gateway first.
- **The shared secret is not listed in the UI and cannot be revoked there.** The gateway
  authenticates to the broker WITH it in order to reach `/v1/tokens` at all, so revoking it from
  that screen would lock the console out of its own token manager on the first click. Change it in
  `deploy/.env` and the service environment.

`last seen` in the UI is tracked **in memory** by the broker and resets when it restarts, so
"never" can mean "not since the last restart" rather than "never used". It is there to answer "is
this workstation still using its token" without a disk write on every request.

⚠ `tokens.json` is gitignored, and listed in `docs/gitignore.public` too because `services/` is a
wholesale include in the publish pipeline. It holds hashes rather than secrets, but its labels
name every host you issued a credential to.

⚠ A firewall is not a substitute for this on a box running the full stack. The token also gates
the rail containers on the compose network, which reach the broker at
`host.docker.internal:11500`, and anything else running on the machine.

## Delegating a role to another broker

One broker owns one card. When a rail needs a bigger card than the box it is installed on, a
role may name a **registered upstream** and this broker forwards the call. The rail is
unchanged and does not know: it still asks for `@openmaic`, and the answer happens to have been
produced somewhere else.

Register the box in `services/broker/upstreams.json` (gitignored — it holds that box's bearer
token; copy `upstreams.example.json`), then point a role at it:

```json
{ "offsite": { "url": "http://gpu-box.example.internal:11500", "token": "" } }
```

```json
{ "openmaic": "offsite::mistral-small3*:24b" }
```

The separator is a **double** colon, because a single one is already the model/tag separator —
`gemma3:4b` would otherwise parse as an upstream named `gemma3`. The glob is resolved by the
UPSTREAM against its own inventory, never here.

Both files are hot-read, so adding a box or repointing a role takes effect on the next request.
`GET /v1/upstreams` lists what is registered and whether each answers;
`GET /v1/models?upstream=<name>` returns that box's inventory. Admin → Rails has a per-slot
broker dropdown that does all of this for you.

What delegation deliberately does **not** do:

- **Take the local GPU gate.** A delegated call never touches this card, so holding the
  single-slot gate for it would serialise local work behind something that cannot contend with
  it. One consequence is visible in the UI: a delegated role has no local queue entry and
  therefore **no WARMING state** — its chip reads missing / cold / loaded only. `/v1/roles`
  resolves the delegated glob against the inventory the **remote** reports, so `resolved` names
  what that box will run. `installed` means the remote reported a model the pattern resolves to:
  a reachable box without the model fails every call, so it reads missing. `loaded` comes from
  the remote's resident set. A `box::@role` value is looked up in the remote's own `/v1/roles`.
  Each remote read is capped at 8 s and every box is probed at once, so a slow remote cannot
  push `/v1/roles` past the rails' 30 s timeout.
- **Fall back.** A delegated role that cannot reach its upstream is an error. Quietly running a
  same-named model on the wrong box and reporting success is the failure this avoids.
- **Work on the OpenAI surface.** `/openai/v1/*` refuses a delegated role with a 400 naming the
  reason: `disabled` is a local policy with no meaning remotely, and that response shape has
  nowhere to say the answer came from elsewhere.
- **Apply to image roles.** A media backend is loaded by this box's own media worker out of its
  HF cache, so there is nothing to forward.

**A concrete ref delegates too.** `{"model": "offsite::llama3.2:3b"}` forwards with no role
involved, so a caller holding an inference-scoped token can address a registered upstream
directly. That is intended — it is how you try a box before committing a role to it — and it is
bounded by the registry: a caller can only reach boxes the operator already registered, and
cannot introduce one. Point a role at the box instead if you want the choice to be an admin's.

## The Claude CLI backend (the OWNER's own requests only)

A second **chat** backend beside Ollama (text, and images over stream-json, see **Images**
below), and the only one that leaves the box. It shells out to `claude -p` once per call and
translates the answer back into the broker's ordinary Ollama-shaped response, so no rail learns
a second shape.

**It may only ever serve the platform owner's own requests.** That is a licence condition, not
a preference. Anthropic permits subscription (OAuth) credentials for *ordinary use* by the
subscriber and does **not** permit routing requests "through Free, Pro, or Max plan
credentials on behalf of their users"
([legal-and-compliance](https://code.claude.com/docs/en/legal-and-compliance)). So the broker
asks one question before every call: is the person waiting on this answer the subscriber?

**Every name in `BROKER_CLI_OWNERS` must be one of that ONE subscriber's own platform
accounts.** The broker holds a single Claude credential (one `BROKER_CLI_OAUTH_TOKEN[_FILE]`, or
else the login of the account it runs as), so every listed username spends the same
subscriber's plan. A subscriber with two platform accounts lists both. Listing anyone else, a
family member or a colleague, serves that person on the subscriber's credentials, which is
exactly what the terms forbid; the broker has no way to serve a second subscriber.

```powershell
# the platform-broker service environment
BROKER_CLI_ENABLED   = true
BROKER_CLI_OWNERS    = owner-one           # the subscriber's OWN platform usernames, exact (case too); empty = nobody
BROKER_CLI_ROLES     = recipe              # eligible @roles until a switch file exists; empty = nothing
BROKER_CLI_WORKDIR   = C:\platform\var\cli-work   # an EMPTY directory outside the repo (see below)
BROKER_CLI_TEMP_ROOT =                     # each call's own TEMP goes under this; empty = C:\platform\var\cli-tmp, beside the workdir
BROKER_CLI_TIMEOUT   = 180                 # seconds per call, the wait for a slot included; past it the call falls back to Ollama
BROKER_CLI_MODEL     =                     # the model for a role the Rails tab gave none; empty = the CLI's own default
BROKER_CLI_MODELS    = claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5 # the default: the models a role may be given
BROKER_CLI_ROLES_FILE = <repo>\deploy\data\cli-roles.json # the Rails tab's per-role switches; unset = BROKER_CLI_ROLES alone
BROKER_CLI_CONCURRENCY = 2                 # CLI calls that may run at once; the rest wait for a slot
```

Every default is off or empty, so the feature is inert until the owners, at least one eligible
role and the workdir are set. `GET
/v1/status` carries a `cli` block reporting `ready` plus **every** blocker, not just the
first — fixing one of three and seeing no change is the same dead end one step further on.

**What makes a call eligible**, all of which must hold (`Broker.cli_decision`):

| | why |
|---|---|
| the backend is enabled and `claude.exe` resolves | a `.cmd`/`.ps1` shim is refused: the broker launches without a shell |
| the request carries a `requester`, sent with a `full`-scope credential | background work (the scheduler, a boot warm) has nobody waiting on it and nobody who consented to it paying. An `inference` token's `requester` is dropped, so its calls are background work too (see **Access tokens**) |
| that requester is in `BROKER_CLI_OWNERS`, spelled exactly as listed | the licence condition: every entry is one of the subscriber's own accounts, because the broker holds one credential (above). Exact, case included: gateway usernames are unique ignoring case and the gateway forwards the stored spelling, so `Owner-One` is a different account, never the owner (`app/config.py` `cli_owner_set`) |
| the model ref is an **`@role`**, never a concrete name | roles are set by an admin. Unlike `upstream::model`, a token holder cannot name this backend and spend the owner's subscription |
| that role is eligible, spelled as the role is: in `BROKER_CLI_ROLES`, or switched on in `BROKER_CLI_ROLES_FILE` once that file exists (see below) | an allowlist, so enabling it for the recipe assistant does not also send every other rail's calls off the box. `@Recipe` is not `@recipe`: the local fallback could not serve it either |
| `BROKER_CLI_WORKDIR` is set and exists | see below; unset is `workdir_unset`, missing is `workdir_missing` |

**Every failure is a fallback, never an error.** A missing CLI, an expired login, a usage
limit, a timeout, an image it will not send, prose where JSON was asked for, a reply that did not
begin with the prefill it was asked to continue (see **Prefills** below): each degrades to Ollama,
logs a line, bumps `cli.fallbacks` and marks the answer with `cli_fallback`. The worst case
for a rail is latency. That is deliberate: a rail must not acquire a new way to fail because
the owner switched this on. The one exception is a caller that has already hung up (below):
nothing is left to answer, so no local answer runs and none is counted. A streamed answer
that fails AFTER its first token is the other: the rail has shown the text, so the stream ends
with an error line instead (see **Streaming** below). Before the first token it falls back like
any other call.

**It takes no GPU gate and evicts nothing**, for the same reason a delegated role does not.

**At most `BROKER_CLI_CONCURRENCY` calls (default 2) run at once.** Each call is its own
`claude.exe`, so without a cap every owner request in flight would be a process. A call over the
limit waits for a slot, and the wait comes out of the SAME `BROKER_CLI_TIMEOUT` budget as the call:
a call that waited 60 s of 180 gets 120 s to answer, so its fallback still starts no later than
`BROKER_CLI_TIMEOUT` after it arrived. A call that cannot get a slot in time falls back with
the reason `busy`. A waiting call is not in `active` (nothing runs for it yet);
`cli.concurrency` reports `{limit, waiting}`.

**A call that waited is decided again when it gets its slot.** `cli_decision` runs when a call
arrives and again once it holds a slot: on fresh reads of the switch file and the token file for
a call that waited, and on its arrival's reads for one that took a free slot (it takes it without
yielding to the event loop, so nothing can have changed). A role switched off while the call
waited (the chips say local at once), or a switch file that turned invalid, stops it there: it
falls back under the new reason (`role_not_eligible`, `cli_roles_file_invalid`).

**A caller that hung up costs nothing more.** Uvicorn does not cancel a handler whose client has
gone, so the CLI path asks the connection (Starlette's `request.is_disconnected`) at the three
points where a rail that already gave up would still cost something: when a call that waited gets
its slot, every 2 s while the CLI answers (`Broker._while_there`), and before the local fallback
after a CLI failure. In the first case the CLI call never starts; in the second it is stopped and
its process tree killed, and its slot comes free once the tree is dead (until 2026-10-14 it ran on
for nobody until it answered or `BROKER_CLI_TIMEOUT` ran out); in the third the local answer never
starts. None of them is a fallback: none bumps `cli.fallbacks` or sets `fallbacks.last`, the
broker logs a line that says the call was skipped or stopped (never `CLI FALLBACK`), and it answers
499, which nobody receives. The first two are counted as the refusal `client_gone`. In the third
the CLI path failed, and `last_by_role` records that failure's reason. Usually that is the CLI's own failure, but the same path also covers calls the
CLI never ran, among them one that got no slot in time (`busy`), one refused when its slot came
free (the re-decision above, under its new reason), and the client's own refusals before it
spawns anything: no user content (`empty`, a reason the CLI also gives when it ran and printed
nothing), an image it will not send (`vision_unsupported`, see **Images**) and no executable to
start (`not_installed`).

**Each CLI-wired rail's own client timeout has to outlast `BROKER_CLI_TIMEOUT` plus one local
answer**, because the fallback runs inside the same broker request; a rail that gives up first
shows its user an error where the broker promised a fallback. The broker cannot enforce that, and
`BROKER_CLI_TIMEOUT` is usually set only in the broker's service environment, where no rail can
read it, so `/v1/status` reports it (`cli.timeout_s`). Whoever wires a rail to the CLI checks
that rail's client timeout against this figure plus room for one local answer. The maintainers'
fleet sweep (`tools/rail_smoke.py`, maintainer tooling that is not in the public snapshot)
automates the check, allowing 120 s for the local answer. A call whose request budget is shorter
than that ceiling either carries no requester, so it never waits on the CLI at all, or moves off
the request: watch-builder's design and variations run as background jobs, so its `ai._ask`
carries the requester on every call and waits the full `broker.CHAT_TIMEOUT` (see
`docs/RAIL_CONTRACT.md`).

### Switching roles from the Rails tab (`BROKER_CLI_ROLES_FILE`)

Which roles are eligible, and which Claude model each one's calls use, can be changed per role,
hot, from the admin Rails tab, through `PUT /v1/cli/roles/{role}` with `{"enabled": true|false}`,
`{"model": "<id>"|null}` or both (see **Choosing the Claude model per role** below). Super-admin only is the GATEWAY's
rule, for the tab: the broker route itself accepts any `full`-scope credential, the same posture
as `PUT /v1/roles`, so the shared `BROKER_AUTH_TOKEN`, which `deploy/docker-compose.yml` gives
the gateway and most rail containers (not meeting-atlas, workstation, or openmaic-app, the
upstream OpenMAIC app that runs beside the openmaic rail), can call it directly
and skip the gateway's checks (wired rails only, text and vision slots only, and a vision role ON
only on a broker whose `cli` block says `vision`). Only the ROLES move:
`BROKER_CLI_OWNERS` (who may use the CLI, the licence boundary) and `BROKER_CLI_ENABLED` stay
environment settings that no route can change.

The switches live in the JSON file `BROKER_CLI_ROLES_FILE` names, `{"recipe": true,
"terminal-fun": true, "watch-builder-designer": false}`; a role given a Claude model is written
as an object instead, `{"recipe": {"enabled": true, "model": "claude-haiku-5-5"}}`, and a role
without one stays a bare `true` or `false`. The setting has **no default path**, on purpose:
this directory is published as it stands, and a default under it would put a live switch file in
a public tree (and make every test that builds the settings without paths read the machine's
live switches). Point it at a path git ignores, such as `deploy\data\cli-roles.json`
(`deploy/.gitignore` ignores `deploy/data/`), and keep the CLI token file somewhere equally
private. Setting it costs one broker restart; every switch after that is live. The rules
(`BrokerSettings.cli_effective`, read by both the routing and the status block):

- **unset, or set to a file that does not exist yet**: `BROKER_CLI_ROLES` decides, exactly as
  before. `cli.roles_source` is `env`.
- **set, then deleted**: the same, and that is the documented reset: every role goes back to
  `BROKER_CLI_ROLES` and every switch the file held is lost, which can switch back on a role
  somebody switched off. It is visible rather than silent: `roles_file_set` is true with
  `roles_source` `env`, and the Rails tab says the service defaults decide.
- **present and valid**: the file decides, for adding a role and for removing one. A role it does
  not switch on is not eligible, whatever `BROKER_CLI_ROLES` says. `roles_source` is `file`.
- **present but malformed**: NO role is eligible (fail closed), the blocker is
  `cli_roles_file_invalid`, and `cli.roles_problem` says why. It never falls back to
  `BROKER_CLI_ROLES`, which could switch back on a role a super-admin switched off. Malformed is
  anything but a JSON object whose every value (annotations aside, below) is a switch, `true` or
  `false`, or an object holding one as `"enabled"` beside an optional `"model"` (a string, or
  `null` for none): text that does not parse (nesting too deep for the parser included), a list,
  a string or number value, or an object with any other key, no `"enabled"`, an `"enabled"` that
  is not `true` or `false`, or a `"model"` that is not a string or `null`. A BOM is fine.
- **present with a model `BROKER_CLI_MODELS` does not allow** (hand-written, or allowed once and
  since taken off the list): the role's switch stands and its calls use the default model, as if
  it had none. `roles_problem` names it, WITHOUT a blocker, and the next switch writes the role
  back as a bare switch. Only an allowed id ever reaches `--model` from this file.
- **present with a key no role here answers to** (a role renamed or removed in `roles.json`, a
  typo): the key is ignored, because it cannot route a call either way, and every other switch
  stands. `roles_problem` names it, WITHOUT a blocker, and the next switch writes the file without
  it.
- **present with an annotation**, a key starting with `_` (`"_comment": "switched on for the
  pilot"`, the convention `roles.json` uses): ignored whatever its value, never named in
  `roles_problem`, and kept by the next switch. No role name starts with `_`, so it cannot switch
  anything. Every other key is held to the true-or-false rule above.
- **while `roles.json` cannot be read** (saved as UTF-16 by Windows PowerShell 5.1's `>` or
  `Out-File`, not valid JSON, or nested too deep to parse): every role resolves to the built-in
  defaults, so a role only `roles.json` defines is invisible meanwhile. `roles_problem` names
  `roles.json` and why, WITHOUT a blocker (the Rails tab shows it). The switch route REFUSES every
  switch ON, and every model chosen or cleared, with a 409 until the file is fixed: written the
  usual way, a switch keeps only the roles the broker can see and would drop the ON switch of
  every role only `roles.json` defines. A switch OFF still works, because it is the kill switch:
  it changes its one switch and carries every key in the file forward as it was, known or not,
  models included, so nothing is dropped (with no
  file yet, it seeds from every `BROKER_CLI_ROLES` entry that is a legal role name, for the same
  reason). Its role must be a built-in one or a key the file (or that seed) already names; any
  other name is not on anywhere, so the route answers 400 as for any unknown role. A repoint
  (`PUT /v1/roles/{role}`, the Rails tab's model picker) is refused with a 409 as well: it would
  rewrite `roles.json` from nothing and lose every mapping the file holds.
- **every role switched off**: the blocker is `cli_all_roles_off`, distinct from
  `no_roles_configured` (nothing was ever configured).

The route switches ONE role per call (never a full-set write), under a lock, through a temp file
and an atomic replace. The first write seeds the file from the current effective set, so switching
one role on keeps the ones `BROKER_CLI_ROLES` already had (an env role the broker does not know is
left out of the seed). It needs a **full-scope** token; an inference token gets 403. It answers 400
for a role the broker does not know or a model `BROKER_CLI_MODELS` does not allow, 422 for a body
that sends neither `enabled` nor `model`, and 409 when `roles.json` cannot be read and the switch
is ON or a model is chosen, or the file is not configured, is invalid (left alone, never
overwritten; the refusal says what deleting it does) or cannot be written. The reply
is `{"cli": {ready, blockers, enabled_roles, roles_source, roles_file_set, roles_problem, models,
default_model, role_models, vision}}`, the same block `GET /v1/roles?inventory=true` (the Rails
tab's one read) carries as `cli`. `vision` is `true` on a broker whose CLI path sends images (see
Images below), whatever `ready` says; an older broker that refused every image call (one from
before the CLI path could send images) sends no `vision` key at all.

### Choosing the Claude model per role (`BROKER_CLI_MODELS`)

The Rails tab sets which Claude model each role's CLI calls use, beside that role's switch. A
call passes `--model` with the role's own model (from the switch file), else
`BROKER_CLI_MODEL`, else no `--model` at all, which leaves the CLI's own default
(`BrokerSettings.cli_role_model`). The model is read when the call starts, once it holds a slot,
like the second decision.

- **Only an id `BROKER_CLI_MODELS` allows** can be chosen: comma-separated, in the order the tab
  offers them, default `claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5`. Any other id is
  refused on write (400, naming the list) and ignored with a note when it is in the file. Empty:
  no role can be given one. The list is an environment setting, so changing it takes a broker
  restart.
- **`{"model": null}` clears** the role's own model, so its calls use the default again.
  **Leaving `model` out** of a body leaves the model as it is, so a switch never touches it.
- **Choosing a model leaves the switch as it is.** For a role the file does not name, that is
  off: the file decides alone once it exists, and the first write seeds it from
  `BROKER_CLI_ROLES` as a switch does.
- **The model changes which Claude model answers, nothing else.** Who may use the CLI
  (`BROKER_CLI_OWNERS`) and whether it runs (`BROKER_CLI_ENABLED`) stay environment settings, and
  a model in the file never makes a role eligible.
- **Before rolling the broker back** to a version without per-role models (one that reads only
  bare `true`/`false` switches), set every role's Claude model back to Default in the Rails tab,
  which writes each role as a bare `true`/`false` again, or rewrite each object to its `enabled`
  value by hand. An
  older broker reads an object as an invalid switch file: every role goes off (fail closed) and
  its tab refuses every switch, OFF included, until the file is edited or deleted (deleting it
  falls back to `BROKER_CLI_ROLES`).

Translation refuses or defers, never approximates silently. A JSON **schema** is handed to the
CLI's own `--json-schema`, which enforces the shape. A bare `format="json"` has no schema to
enforce, so it becomes a system instruction and the answer is then *validated* — if no JSON
comes back, the call falls back rather than handing a rail prose it will reject two layers
away. The value taken is the largest one in the answer (`extract_json`, one pass over the text,
in a thread for an answer of 64 KiB or more);
none, and so a fallback, when the only candidates are nested past what the parser reads. Either
kind of JSON call whose result object says `stop_reason: "max_tokens"` falls back too (`bad_json`):
the largest value left in an answer cut off at the token limit is an inner fragment of the
wrong shape. A text answer cut off there is still served, with `done_reason: "length"` as Ollama
reports it. A message's `images` are sent as images (next section) or the call falls back, never
silently stripped. A trailing assistant turn (a prefill) becomes an instruction and the reply is
verified to begin with it (see **Prefills** below). Sampling options the
CLI has no equivalent for (`temperature`, `num_ctx`, `think`, …) are reported on the answer as
`cli.dropped` and in the log, because running at a default the caller did not ask for is
acceptable but hiding it is not.

### Images (`--input-format stream-json`)

A call whose messages carry `images` (Ollama's field: a list of bare base64 strings) goes to the
CLI as **one stream-json user message on stdin** (`cli_backend.vision_message`). The argv swaps
`--output-format json` for `--input-format stream-json --output-format stream-json --verbose`
(`-p` prints stream-json only with `--verbose`) and keeps everything else: every isolation flag
above, `--settings`, `--model`, `--system-prompt`, `--json-schema`, the allowlisted environment
and the empty workdir. The text is flattened exactly as the text path flattens it (system turns
to `--system-prompt`; one turn verbatim, several labelled `User:` / `Assistant:`), one text block
per turn, with that turn's images as base64 image blocks just before it, because Claude reads an
image best ahead of the words about it. A call **without** images builds byte for byte the argv
and stdin it did before images could be sent; `tests/test_cli_backend_images.py` pins both as
literal goldens recorded by running that earlier version.

The answer is the transcript's **last `result` line**, which carries the same `result`,
`is_error` and `usage` the json format's one object does, so `cli` on the answer, `last_by_role`,
`fallbacks` and `cli.dropped` mean exactly what they mean for a text call. `is_error` there is
classified like any CLI failure (`rate_limited`, `not_signed_in`, …); a transcript that ends
without a result line falls back as `bad_stream`, never pieced together from the assistant lines
before it; no output at all is `empty`. A **nonzero exit** falls back whatever the transcript
says, a result line that reads as a success included. Its recorded detail (the `CLI FALLBACK` log
line and `fallbacks.last.detail`) is the CLI's own words only: the sentence on an `is_error`
result line, else stderr. With neither, it is a fixed sentence, `the CLI exited with code N and
printed no error sentence; its transcript (M lines) is not recorded`, with the reason `failed`,
because the other lines of a transcript can hold rail content: the assistant line is the
model's description of the picture, a result line that is not an error carries the same
answer, and a line cut off by a kill can be either (`cli_backend._stream_exit_failure`). Who may
send an image call, its slot under
`BROKER_CLI_CONCURRENCY`, the second decision once it holds one and the hung-up caller are the
text call's rules unchanged (`tests/test_cli_routing.py` runs those tests for both shapes).

Structured output works as on the text path: `--json-schema` is honoured with stream-json input
(measured below; the result line carries the JSON as text and as `structured_output`), and a bare
`format="json"` becomes the system instruction plus validation.

**What is refused, and why.** Each of these falls back with the reason `vision_unsupported`, so
the local vision model answers the question exactly as it was put. `vision_unsupported` now
means "an image this path will not send", never "a call that carries images".

| refused | why |
|---|---|
| a format other than PNG, JPEG, GIF or WebP | the four Claude reads. The media type is **sniffed from the decoded bytes** and only ever one of those four; Ollama's field carries no type, and nothing a caller declares is passed on |
| a data URL, a character outside the base64 alphabet, wrong padding | decoded as strictly as Ollama's own decoder reads the field (Go's standard base64, which skips CR and LF and nothing else). A repaired image is a different image |
| one image over **5,000,000** base64 characters | the 5 MB per-image limit on Bedrock and Google Cloud (10 MB on the direct API). The strictest figure, because the child inherits `CLAUDE_*` and so a `CLAUDE_CODE_USE_*` variable could point it at either |
| more than **20** images in one call | past 20, the API holds every image in the request to 2000 px; at 20 or fewer the limit is 8000 px |
| more than **24,000,000** base64 characters of images in one call | leaves the prompt and the CLI's envelope 8 MB of the API's 32 MB request limit |
| an image on a **system** turn | `--system-prompt` is text only, and moving the picture into the user turn would change what was asked |
| an image on any other **non-user** turn ("assistant", "model", "tool", any case) | the same reason: the call is ONE user message, so the picture would arrive as one the person asking had shown. Dropping it instead would leave the model answering about a picture it never saw. Images on every user turn are sent, not only the last one's |

Source for every figure: the Vision guide, <https://platform.claude.com/docs/en/build-with-claude/vision>
("Request limits", "Supported formats"), read 2026-10-09. **No image is ever written to disk**: it
is decoded, sniffed and re-encoded in memory and reaches the CLI on stdin only (a test makes every
file write fail for the length of an image call).

**Only the header is checked.** The sniff reads an image's magic bytes (PNG's 8-byte signature,
JPEG's `FF D8 FF`, `GIF87a`/`GIF89a`, `RIFF....WEBP`) to choose the media type, and nothing
checks that the bytes after them decode as a picture: the PNG signature alone, or a valid header
over arbitrary bytes, is sent as that type (`test_only_the_header_is_checked`). Pixel dimensions
are not checked either. Such an image is the CLI's or the API's to reject, and the call then
falls back only if the CLI exits nonzero or marks its result line `is_error`; if it ever did
neither, the API's error sentence would reach the rail as its answer. What the CLI does with a
rejected image (a non-image behind a valid header, or one over 8000 px) is **not measured**:
every real call so far sent a valid image. Decoding with Pillow's `Image.verify` is not used:
Pillow is not a broker dependency (`pyproject.toml`), although the broker's service interpreter
here, the repo `.venv`, can import it because other packages require it.

Measured 2026-10-09 with claude 2.1.293, `--model claude-haiku-5-5`, generated 64 px
squares, through `ClaudeCliClient.chat` with the argv above: a red PNG answered `Red`; a blue PNG
with `--json-schema` returned `{"colour": ...}` (two turns, the result line carrying
`structured_output`); a red and a green PNG in one message answered `Red green`; a yellow JPEG
`Yellow`; a purple WebP with `format="json"` returned parseable JSON. Each took 5.2 to 6.4 s end
to end, stdout was 7.4 to 10.8 KB (the image is not echoed back without
`--replay-user-messages`), and the workdir was still empty afterwards.

**Which calls reach it.** The same ones as for text: a `/v1/chat` or `/v1/chat/stream` call
whose `requester` the broker trusts and names an owner, on an eligible `@role` (a streamed call
carrying images is answered buffered inside the stream, see **Streaming** below). Images widen
what the CLI can carry, never who may use it. So a photo leaves the box only once its rail's call site
passes the person asking on that role and the role is eligible (`BROKER_CLI_ROLES`, or switched
on in the switch file); a call site that sends its images with no requester stays local
whatever this path can do. Which call sites do so is each rail's own decision, listed in
`docs/RAIL_CONTRACT.md` (`cli_slots`).

### Streaming (`/v1/chat/stream`)

An owner's streamed call on an eligible `@role` reaches the CLI under exactly the buffered
call's rules: `requester` from a `full`-scope credential, the same `cli_decision`, a slot under
`BROKER_CLI_CONCURRENCY` taken within the same `BROKER_CLI_TIMEOUT` budget, the second decision
once it holds one, and the hung-up checks (`Broker.chat_stream` runs them through the same
helpers as `Broker.chat`). It takes no GPU gate, and it holds its CLI slot until its last frame.

**The frames**, NDJSON as always, in this order:

- `{"cli": {"backend": "claude", "model": "<id>"}}`, before the first token: who answered. It
  has no `message.content`, so every rail's reader skips it.
- Ollama's content frames, `{"model", "created_at", "message": {"role": "assistant",
  "content": "<text>"}, "done": false}`, as the CLI writes them.
- The done frame: `"done": true`, empty content, `done_reason` (`"length"` when the model ran
  out of tokens, else `"stop"`) and `cli`, the same usage block a buffered answer carries
  (`input_tokens`, `output_tokens`, `duration_ms`, `total_cost_usd`, `num_turns`, `dropped`).

**The argv** is the text path's with `--output-format stream-json --verbose
--include-partial-messages` in place of `--output-format json`, which adds one `stream_event`
line per token (claude 2.1.296's own `--help`: partial messages need `--print` and stream-json,
and `-p` prints stream-json only with `--verbose`). Every isolation flag above, the allowlisted
environment and the empty workdir are the same; the prompt goes on stdin as plain text
(`ClaudeCliClient.chat_stream`). Only `text_delta`s are content: a thinking model's reasoning,
its signature and a tool call's arguments never are. The transcript's last `result` line is
still the verdict, read as an image call's is (a nonzero exit is refused whatever it says, with
the CLI's own words or the fixed sentence as the detail, never a transcript line; no result line
is `bad_stream`; `is_error` is classified), and its text must agree with what was streamed: with
no delta it is sent whole, a longer one sends the rest, outer whitespace aside anything else is
`stream_mismatch`. A second message beginning after text went out is `bad_stream`. Measured on
2026-10-13 with claude 2.1.296 and Haiku 5.5: a count to thirty arrived as 22 frames, all while
the process ran, whose join was exactly the result line.

**A `format` or images go buffered inside the stream.** A JSON answer is validated whole (a bare
`format="json"`) or enforced by the CLI (a schema), and an image call is stream-json in both
directions, so these are asked for with the buffered client and sent as one content frame plus
the done frame. Nothing is yielded before that call succeeds, so every failure of it is a
transparent fallback.

**Before the first token, a failure is a fallback**, exactly as on `/v1/chat`: the stream goes on
with `{"cli_fallback": {"backend": "ollama", "reason": "<slug>"}}` (no `message.content`, and
deliberately never keyed `error`, on which every reader raises), then the local answer, with
its own leading `fallback` frame if a model was substituted, or a delegated role's upstream
answer. It counts in `cli.fallbacks` and is logged as `CLI FALLBACK`, like any other.

**After the first token, a failure is an interruption, and no local answer starts.** The rail
has already shown the text; a second answer appended to half of the first would read as one
answer that changed course. The stream ends with

```json
{"error": "the Claude CLI stopped mid-answer (stalled); the text above is incomplete",
 "done": true, "done_reason": "cli_interrupted",
 "cli_interrupted": {"reason": "stalled", "chars": 412}}
```

keyed `error` on purpose, because every reader raises on it: a plain done frame would be read as
a complete answer. It counts in `cli.interruptions` (never in `cli.fallbacks`), `last_by_role`
records `reason: "interrupted"` with the failure's slug as `cause`, and the log line is `CLI
INTERRUPTED`, with no word of the answer in it.

**Deadlines.** The first text within what is left of the `BROKER_CLI_TIMEOUT` budget (a model
that thinks first can be silent until then), else `timeout`, which is still before the first
token and so a fallback. Once text flows, a byte at least every 60 s of waiting, else `stalled`;
and the whole answer within `BROKER_CLI_TIMEOUT` of its first token, else `timeout`. Either of
those two is an interruption. A rail's stream reader must therefore wait for its first frame for
longer than `BROKER_CLI_TIMEOUT` plus the local model's time to its first token (the fallback's
first frame can arrive that late), the streamed form of the buffered rule above.

**A caller who disconnects takes the CLI's process tree with it, and its slot comes free only
once the tree is dead.** uvicorn reports ASGI spec 2.3, for which Starlette's `StreamingResponse`
watches for `http.disconnect` and cancels the stream through anyio. anyio's cancellation is
level-triggered: it is delivered again at every await until the task leaves the cancelled scope,
which would stop the tree kill's own spawn of `taskkill` and leave the CLI's node child running.
So the kill runs as a task of its own (`cli_backend._reap`), the answer is produced in a task of
its own besides (the next frame of a text answer, `Broker._watch`; the whole of a buffered one,
`Broker._while_there`), and the cancelled stream waits for that task, kill included, inside a
shielded anyio scope (`cli_backend.settle`, at most `_REAP_TIMEOUT` once the cancellation came)
before it gives its CLI slot back. Until 2026-10-14 the slot came back in the cancellation
itself, while taskkill was still running, so for a moment one more CLI call could start than
`BROKER_CLI_CONCURRENCY` allows. Under spec 2.4 Starlette never listens for the disconnect, so
while it waits on the CLI the broker asks the connection every 2 s (`request.is_disconnected`)
and stops the stream itself, a buffered answer for a `format` or images included. Either way the
tree is killed, nothing more is written to the caller, not even an error line, and the hang-up
counts as the refusal `client_gone`, whether the broker's check found it or Starlette's
cancellation delivered it (then the connection is asked once whether it has gone, so a server
shutting down is not counted). A stream that ends in Starlette's `send()` instead, a 2.4 write that
fails or a cancellation that lands while a write is blocked, is one `async for` would leave
suspended until a garbage collection got to it; the route closes it itself before the request
ends (`app/streaming.py`, `ClosingStreamingResponse`, which the OpenAI-dialect stream uses too, for
the GPU gate it holds), and that close is counted the same way. `tests/test_cli_stream.py` proves
both spec versions against real process trees through the real app.

**A caller that stops reading** but keeps its connection open is the one case none of that
notices: every deadline the stream has runs while it waits for its next frame, never while it
waits behind a write, and the server's own write has no timeout. So each write of a streamed
response is bounded (`streaming.SEND_TIMEOUT_S`, a minute): one still blocked after that ends the
stream, the body is closed as on any other exit (the CLI's tree killed and its slot back, or the
GPU gate given back), the broker logs `WARNING: ... a send waited 60s for the client to read; the
stream was closed`, and the server closes the connection of the response left incomplete
(uvicorn's own ERROR line for that, `ASGI callable returned without completing response.`, is
dropped for such a stream, and only for one: the WARNING says what happened). It is not counted
as `client_gone`: the connection never said it had gone. A slow reader is not cut off for being
slow: a write blocks only once the server's buffers are full, it frees once the reader has taken
48 KiB more, and the minute starts again at every write, so only a reader slower than about
0.8 KiB/s with that much waiting for it is stopped. Until 2026-10-14 such a caller held its CLI
slot (two of them used up `BROKER_CLI_CONCURRENCY=2`) or the gate for as long as it liked.

**On the status block**: `cli.streams` is `true` on a broker whose streamed calls can reach the
CLI (what the code carries, like `vision`; an older broker sends no such key), on `cli_summary`
too; `cli.interruptions` is `{count, last}` with `last` `{requested, reason, chars}`; and a
streamed call's `active` entry carries `"stream": true`.

**The OpenAI-compatible surface stays local on purpose.** Its callers are outside clients, which
the CLI may not serve, and its wire shape (somebody else's spec) has no place for `cli_fallback`
or `cli_interrupted`.

### Prefills (a trailing assistant turn)

Ollama reads a call whose last turn (system turns aside) is the assistant's as a **prefill**:
the start of the answer, which the model continues, and its answer is the continuation alone. A
rail joins the two (smb-partner-enablement's Scenario Builder opens a pass with `Your next move: `
and builds `prefill + answer`). `claude -p` has no such turn, so the CLI path **emulates** it on
every call, buffered or streamed, text or images (`cli_backend.split_prefill`):

- **The turn becomes an instruction.** It leaves the prompt, and the system prompt gains, after
  the caller's own, an instruction to begin the reply with exactly that text, shown between two
  marker lines (`_PREFILL_INSTRUCTION`). Every other part of the argv is the plain call's.
- **The reply is verified, then cut.** It must begin with the prefill: the reply's leading
  whitespace is skipped, then the prefill must match exactly, case and punctuation included.
  The prefill's trailing whitespace is a boundary rather than text, since the instruction cannot
  show it: ending in a space, the reply must break there too (`Dear ` is not begun by
  `Dearest`); ending in a line break, it must break the line there (a heading is not begun by the
  same heading extended along its line). What follows is the answer (`cli_backend.continuation`),
  so `prefill + answer` reads as the reply did, without a doubled space.
- **A reply that does not begin with it falls back**, `prefill_mismatch`, like any other CLI
  failure: counted in `cli.fallbacks`, recorded in `last_by_role`, logged as `CLI FALLBACK` with a
  detail that quotes neither text, and the local model is sent the call as it was put, prefill
  included. A reply that is the prefill and nothing more is `empty`.
- **A stream holds every frame back until it is decided**: no lead `cli` frame and no content
  until the reply is seen to begin with the prefill (and to carry something past it), and then
  only what follows it. A reply that leaves the prefill is refused as soon as it does, often at
  its first words, so the fallback is the transparent kind: nothing reached the rail. A reply
  still undecided when the deltas end is judged on the result line.
- **Still refused, `prefill_unsupported`, before anything starts**: a last turn whose role is
  neither `user` nor exactly `assistant` (`Assistant`, `model`, `tool`: the role is a free string
  on the wire, and Ollama's spelling is the one it continues); a prefill carrying images, or whose
  content is blank or not a string; one that does not follow a user turn; and any prefill with a
  `format`, because a schema is enforced on the whole reply and a bare `json` answer is rebuilt
  from the largest JSON value in it, so neither leaves an opening to verify and cut off.

Until 2026-10-14 every trailing assistant turn was refused as `prefill_unsupported`, so a
prefilled call always ran locally. Measured 2026-10-14 with claude 2.1.296 and the CLI's default
model, through `ClaudeCliClient` (`tests/test_cli_backend.py`, `LiveTests.test_prefill_live`):
the Scenario Builder's three shapes, `Your next move: ` buffered and `**"` and
`## Where the value comes from\n` streamed, each began with its opening and was served; the
three calls took 44.6 s together.

### Keeping the owner's environment out of a rail's prompt

Three layers, and the first does most of the work:

- **`--setting-sources ""`** — load no user, project or local settings. Without it the owner's
  hooks, `env` block, model pin and plugins apply to a call the platform makes on a rail's
  behalf, and a hook would run under the **broker's service account**, not theirs.
- **`--settings {"claudeMdExcludes": …}`** (`BROKER_CLI_SETTINGS_JSON`) — belt and braces for
  the memory files, since `--settings` applies even when setting *sources* are off. Note that
  `claude -p` **silently ignores** a settings blob that fails validation, so the broker
  parses it first and reports `settings_json_invalid` rather than letting the exclusion
  quietly stop applying.
- **`BROKER_CLI_WORKDIR`** — Claude Code reads any `CLAUDE.md` *above* its working directory.
  Leave this unset and it runs in the repo root and reads **this repo's** agent guide into a
  rail's prompt; `claudeMdExcludes` does not cover that file, because it is not under
  `.claude/`. Point it at an empty directory. Unset is the `workdir_unset` blocker and a
  configured-but-missing directory is `workdir_missing`: either one keeps the CLI off.

Measured on claude 2.1.292, one cold call per variant, same two-word prompt:

| | input tokens | wall |
|---|---|---|
| no isolation | 14,183 | 3.4 s |
| `claudeMdExcludes` only | 5,085 | 5.0 s |
| **`--setting-sources ""`** | **820** | **1.4 s** |
| both | 816 | 1.4 s |

The tokens are the small half. A `UserPromptSubmit` hook running as LocalSystem on every rail
call is the real one.

### What a call leaves on disk

Nothing, once it has ended. The workdir stays empty (`--no-session-persistence`, no tools), but
the CLI writes under its TEMP on every call: measured on claude 2.1.296, one buffered call,
`claude\<workdir slug>\<session id>\scratchpad\`, four empty directories. The session id is new
each call, so until 2026-10-14, when the child inherited the broker's TEMP, one of these was left
in the service account's temp per call for as long as the broker ran.

- **Each call has a TEMP of its own.** `TEMP` and `TMP` (and `TMPDIR` on POSIX) point at
  `<root>\call-<32 hex digits>`, made as the call starts, and the broker's own are never passed
  on. `<root>` is `BROKER_CLI_TEMP_ROOT`, else `cli-tmp` beside `BROKER_CLI_WORKDIR`
  (`C:\platform\var\cli-tmp` for the workdir above): beside, so the workdir stays empty, and in
  the place install-services already checked. Each call's directory is made with mode `0o700`,
  as `tempfile.mkdtemp` makes one: on Windows (Python 3.12.4 and later) a protected ACL for the
  broker's account, SYSTEM and Administrators that inherits nothing from the root, on POSIX the
  owner's alone. A root that cannot be made is a fallback (`failed`, the detail names it).
- **It is removed once the call's tree is dead**, whatever ended the call: an answer, a refusal,
  a timeout, a caller who hung up, a stream cancelled or closed. The removal runs in the same
  task as the tree's kill (`cli_backend._reap`), after it, so a cancellation delivered again
  cannot skip it, and the call (and its slot) ends only once it is done.
- **A removal a file filter refuses is retried**, five tries over about 1.5 s. One that outlasts
  them is logged once (`CLI temp directory ... could not be removed`) and left.
- **The startup sweep removes what was left**: at boot, before any call, every `call-*`
  directory under the root that nothing has changed for an hour, or for three
  `BROKER_CLI_TIMEOUT`s if that is longer (a younger one can be another broker's call in flight
  on the same root). Nothing else under the root is touched. It logs what it removed.

### Running under a service account

The broker runs as **LocalSystem**, which has no Claude login of its own; credentials live in
`%USERPROFILE%\.claude\.credentials.json` and are readable only by that user. Generate a
one-year token as the owner and give it to the service:

```powershell
claude setup-token          # browser login; prints the token, saves it nowhere
```

Put it in `BROKER_CLI_OAUTH_TOKEN_FILE` (a path) rather than `BROKER_CLI_OAUTH_TOKEN`, so it
stays out of the service registry and `/proc`-equivalents.

⚠ **Write that file as UTF-8.** PowerShell 5.1's `>` and `Out-File` produce **UTF-16LE**, so
`claude setup-token > token.txt` gives a file the broker has to decode as UTF-16 (it does, and
also strips a BOM and rejects a token containing control characters — a UTF-16 file with no
BOM otherwise decodes into `s\0k\0…`, survives a `.strip()`, and reaches the child process as
an env value with embedded NULs). `Set-Content -Encoding utf8` is the unambiguous form.

Unlike `BROKER_AUTH_TOKEN_FILE`, an unreadable file here does **not** stop the broker: an
empty CLI token means this backend is off, which is the safe state, whereas an empty
`BROKER_AUTH_TOKEN` would leave the control plane open. The cost of degrading quietly is paid
by `cli.blockers` naming `token_unreadable`.

### Setting them with install-services.ps1

These settings belong to the box (the owner's usernames, the path to their `claude.exe`, the token
file), so the tracked installer does not hold them. A reinstall carries forward whatever the
installed service already has; to set or change them, keep them in a file **outside the checkout**
and pass it, elevated:

```powershell
# C:\platform\broker-cli.env  (deploy\.env's rules: quotes, `# comments`, the last line wins)
BROKER_CLI_ENABLED=true
BROKER_CLI_OWNERS=owner-one
BROKER_CLI_EXE=C:\Users\<owner>\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe
BROKER_CLI_OAUTH_TOKEN_FILE=C:\platform\claude-token.txt
BROKER_CLI_WORKDIR=C:\platform\var\cli-work
# An empty value REMOVES the setting from the service. A comment goes on a line of its own:
# after `KEY=` it would be the value, and BROKER_CLI_MODEL's value is passed as `--model`.
BROKER_CLI_MODEL=

.\deploy\install-services.ps1 -BrokerCliEnvFile C:\platform\broker-cli.env
```

The file is checked before anything is torn down (`Test-BrokerCliEnv` in
`deploy/installer/lib-runtime.ps1`): besides the paths, a `BROKER_CLI_MODEL` that starts with
`#` is refused, and so is one that `BROKER_CLI_MODELS` does not list when the file sets both.

The file's keys are merged over the carried-forward ones. It may set only the CLI backend's own
settings (the ones in the block at the top of this section); any other key (a stray
`BROKER_AUTH_TOKEN`, a `PATH`) refuses the whole file, by key name and never by value. Before
anything is torn down the paths are checked, and a failure changes nothing: `BROKER_CLI_EXE` must be
an absolute path to a real `.exe` (no `.cmd`/`.ps1` shim), `BROKER_CLI_OAUTH_TOKEN_FILE` must exist,
`BROKER_CLI_WORKDIR` must exist outside the repo and any OneDrive folder with **no `CLAUDE.md` in it
or in any folder above it** (so not under a user profile that keeps one),
`BROKER_CLI_TEMP_ROOT` follows the workdir's rules but need not exist yet, may not be a file, and
may not be the workdir or inside it, and `BROKER_CLI_ROLES_FILE` must be absolute and outside the
repo. Every service's environment is
written without a blank element, which NSSM would otherwise read as the end of the list.

### Reading the status block

`GET /v1/status` → `cli`:

- `ready` + `blockers` — configuration faults. Every one that applies, not the first.
- `vision`: `true` when this broker's CLI path sends a message's images (over stream-json, see
  Images above), set by the code (`cli_backend.SENDS_IMAGES`), not by configuration, and the
  same whether or not the backend is ready. An older broker that refused every image call sends
  no `vision` key. The Rails tab's read carries it too.
- `streams`: the same for `/v1/chat/stream` (`cli_backend.STREAMS_TEXT`, see Streaming above):
  `true` when a streamed call can reach the CLI. An older broker, whose streamed calls always
  stayed local, sends no `streams` key. The Rails tab's read carries it too, and the gateway
  turns a streaming role's switch on only while it is `true` (as it does a vision role's with
  `vision`).
- `roles` + `roles_source` + `roles_file_set` + `roles_problem`: the eligible roles right now,
  where they came from (`env` or `file`), whether a switch file is configured, and what the
  broker made of a present one: why it was refused (with the blocker `cli_roles_file_invalid`),
  or, with no blocker, which keys and models it ignored. Either way `roles_problem` leads with why
  `roles.json` cannot be read, when it cannot. Every rail's chip keys on `roles`, and the routing
  reads the same list.
- `models` + `default_model` + `role_models` + `call_models`: which Claude model answers.
  `models` is `BROKER_CLI_MODELS`, the ids a role may be given; `default_model` is
  `BROKER_CLI_MODEL` (`""` is the CLI's own default); `role_models` is `{role: model}` for every
  role the switch file gives an allowed model, switched on or off; `call_models` is
  `{role: model}` for every role in `roles`, the `--model` a call on it passes right now (`""`
  passes none).
- `timeout_s`: `BROKER_CLI_TIMEOUT`, seconds per call with the wait for a slot included. Every
  CLI-wired rail's own client timeout must be above it plus a local answer, and this is the
  figure to check each wired rail against (see above).
- `concurrency`: `{limit, waiting}`, the cap and the calls waiting for a slot.
- `calls` — served by the CLI.
- `refusals` — per reason, for calls that were **not**. This is the one that answers "it says
  ready and nothing changed": a mistyped role shows as `role_not_eligible`, and a rail that
  was never wired shows as `no_requester`. Without it both look like "nobody has asked yet".
  `client_gone` counts calls skipped because the caller hung up while waiting for a slot, calls
  stopped because it hung up while the CLI answered, and streams ended by a hang-up however it
  arrived: the broker's own check, or Starlette's cancellation under ASGI 2.3.
- `fallbacks`: count plus the last `{requested, reason, detail}`, of the local answers that
  started after a CLI failure. A failure whose caller had hung up starts none and is not counted.
  `detail` is the CLI's own error text; for an image call that exited nonzero with no error
  sentence it is the fixed sentence described under Images, never a transcript line.
- `interruptions`: count plus the last `{requested, reason, chars}`, of the streamed answers that
  failed after their first token and ended with a `cli_interrupted` line (see Streaming above).
  None of them started a local answer, so none is in `fallbacks`.
- `active` — CLI calls in flight right now, oldest first, each
  `{role, rail, since, requester}`: `role` is the ref exactly as the rail passed it (`@recipe`),
  `rail` the friendly name from the same map the GPU queue uses (`null` for a role that
  belongs to no rail), `since` a Unix timestamp in seconds, `requester` the platform username,
  stripped and otherwise exactly as the owner check matched it. This exists because a CLI call
  takes no GPU gate and so never appears in `jobs`; without it the UI reads idle while the
  owner's call runs. An entry is removed on every exit, cancellation included. A streamed call's
  entry also carries `"stream": true`, and stays until the stream's last frame.
- `last_by_role`: the latest outcome per role, `{ok: true, at, requester, model}` or
  `{ok: false, at, reason, requester[, model]}`, keyed by the same ref as `role`. `reason` is the
  fallback slug (`timeout`, `rate_limited`, …), never the CLI's error text, which stays in
  `fallbacks.last` and the log. A stream interrupted after its first token records
  `reason: "interrupted"` and the failure's slug as `cause`. `requester` is whose call it was, normalised exactly as in
  `active`. `model` is the `--model` the call passed (`""` for none); a failure has it only once
  a model was chosen, so a call that got no slot, or was refused when it got one, has none.
  A cancelled call records nothing, since it neither served nor fell back. Per
  role because `fallbacks.last` is one slot for the whole backend that only failures write:
  it cannot say `@recipe` has recovered since, and the next `@terminal-fun` failure overwrites
  it. Still ONE slot per role, so with two owner accounts the latest outcome may be the other
  account's.
- `last_by_requester` — `{requester: {role: outcome}}`, each outcome in exactly the
  `last_by_role` shape: every requester's OWN latest per role. This is what a per-owner view
  reads. Filtering `last_by_role` by `requester` instead loses one account's outcome the moment
  another owner account calls the same role. Capped at 16 requesters and 32 roles each, least
  recently updated dropped first (`_CLI_HISTORY_REQUESTERS` / `_CLI_HISTORY_ROLES` in `broker.py`); owners
  and eligible roles are small configured sets, but both are hot-read.

⚠ **`requester` is a username, and the broker does not filter it.** `/v1/status` is
token-gated, so it is reported as-is, in `active`, `last_by_role` and `last_by_requester`
(whose keys are usernames too). The **gateway** is
responsible for filtering on it and stripping it (or reducing it to "you" / "someone else")
before this block reaches a browser.

**Under LocalSystem, set `BROKER_CLI_EXE` explicitly.** Verified by running the broker's own
CLI path as SYSTEM (PsExec `-s`): auto-resolution finds nothing, because SYSTEM's
`PATH` has no npm and its `%APPDATA%` is `C:\Windows\system32\config\systemprofile\AppData\Roaming`.
With the path set to the subscriber's
`%APPDATA%\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe` and the token file in place,
a listed owner's call was served and anyone else's was refused. The CLI
then keeps its own state under SYSTEM's profile (`systemprofile\.claude`, `.claude.json`), not
the owner's, and never self-updates (`DISABLE_AUTOUPDATER=1`), so a broker call cannot rewrite
the owner's install as SYSTEM.

Codex cannot follow this route: it has no personal long-lived token, and its `~/.codex/auth.json`
refresh tokens are single-use, so a copy signs the owner out everywhere else.

### Not covered

The OpenAI-compatible surface never reaches the CLI, buffered or streamed (see Streaming above
for why). Embeddings, speech, image generation and voice have no CLI equivalent and never will
here.

## Smoke test

```powershell
curl http://127.0.0.1:11500/v1/status
curl http://127.0.0.1:11500/v1/upstreams
curl -X POST http://127.0.0.1:11500/v1/chat -H "content-type: application/json" `
  -d '{"model":"@chat","messages":[{"role":"user","content":"say hi in 3 words"}]}'

# the OpenAI surface (add -H "authorization: Bearer $env:BROKER_AUTH_TOKEN" if set)
curl http://127.0.0.1:11500/openai/v1/models
```

## VRAM policy

At most one **heavy** (generative) model resident at a time; loading/serving one
evicts any other heavy model. **Embedding** models (name matches a hint like
`bge`/`embed`) are light and may stay resident alongside, so `/v1/embed` is not
gated. Recommended belt-and-suspenders on the Ollama service:
`OLLAMA_MAX_LOADED_MODELS=1`.
