# GPU / Model Broker (v0)

The single owner of the GPU. Apps never touch Ollama directly — they call this.
Ollama is the only **text** backend — chat, streaming chat and embeddings all
forward to it. Images (SDXL / FLUX), cloned-voice and read-aloud speech (XTTS /
Kokoro) and transcription (faster-whisper) are the broker's too, run as short-lived
worker subprocesses under their own interpreters, which exit to reclaim VRAM because
torch will not give it back in-process. See `../../docs/architecture.md` for the why.

## Run (Windows PowerShell, from the repo root `D:\.ai-work\projects\ai-platform`)

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
GET    /v1/roles         role -> pattern -> resolved (what the admin set) + serving (what runs)
PUT    /v1/roles/{r}     repoint a role (hot; no restart)
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

The three `/v1/tokens` routes, `PUT /v1/roles`, `PUT /v1/disabled`, `/v1/load`, `/v1/unload` and
`/v1/cancel` are the `full`-scope set; everything else above is reachable with an `inference`
token. See **Access tokens** below.

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
four are the reason this table exists: until it did, "where does this box keep its tokens" was
answerable only by reading `config.py`.

| Variable | Default | Secret | Notes |
|---|---|---|---|
| `BROKER_HOST` | `127.0.0.1` | | `0.0.0.0` puts the GPU on the LAN. Set a token in the same change. |
| `BROKER_PORT` | `11500` | | |
| `BROKER_AUTH_TOKEN` | *(unset — open)* | **yes** | Shared control-plane secret; every `/v1/*` route requires it as `Authorization: Bearer` or `X-Broker-Token`. Empty = open, which is a deliberate choice on a single-user box, not an oversight. |
| `BROKER_AUTH_TOKEN_FILE` | *(unset)* | **yes (path)** | The same secret read from a file, and the form that WINS when both are set — it keeps the token out of `docker inspect` and `/proc/<pid>/environ`. Contents are stripped, so a trailing newline from `echo` is not an auth failure. |
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
| `inference` | chat, streaming chat, embeddings, media, voice, the whole `/openai/v1` surface, and every GET | `PUT /v1/roles`, `PUT /v1/disabled`, `/v1/load`, `/v1/unload`, `/v1/cancel`, anything under `/v1/tokens` |
| `full` | everything | — |

`inference` is what a remote workstation on the LAN wants: it can use the GPU and read state, and
it cannot repoint a rail's model for every user or evict the model somebody is mid-conversation
with. A token that could reach `/v1/tokens` could mint itself a `full` one, which is why reading
and minting are both `full`-only.

```powershell
# issue one, then on the remote host:
BROKER_AUTH_TOKEN=bt_...            # or the api_key of an OpenAI client -> /openai/v1
```

Three properties worth knowing before relying on this:

- **Revocation is immediate.** `tokens.json` is hot-read on every request, exactly like
  `roles.json`, so a revoked host stops working on its next call with no restart.
- **Enforcement keys on the token store EXISTING, not on it having rows.** Revoke the last named
  token on a box with no `BROKER_AUTH_TOKEN` and the broker stays closed — "I revoked everything"
  means nobody is authorised, not that authorisation is off. Delete the file to reopen
  deliberately. A file that fails to parse also counts as configured, and fails closed.
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
the fifteen rail containers on the compose network, which reach the broker at
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
