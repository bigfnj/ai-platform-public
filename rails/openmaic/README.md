# OpenMAIC — interactive classroom rail

Turns a topic or an uploaded document into a class: generated slides, quizzes and simulations
delivered by an AI teacher with narration and a live whiteboard.

## What this rail actually is

**A wrapper, not the application.** OpenMAIC itself is an upstream Next.js 16 app
([THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)) that cannot be a federated remote —
it owns its own router, middleware and SSR. It runs as a second container and this rail gives it
a contract-shaped presence on the platform: one catalog tile, one identity gate, one set of model
chips, and one place where `@role` becomes a real model.

```
browser ─► caddy :1111 ─► gateway :8700 ─┬─► /openmaic/assets/*   federated remote (baked into the gateway image)
                                         └─► /openmaic/api/*  ─►  openmaic :8900
                                                                  ├─ /api/capabilities   chips
                                                                  ├─ /api/llm/v1/*       OpenAI shim ─► broker :11500
                                                                  └─ /api/app/*          reverse proxy ─► openmaic-app :3000
```

Two containers, and the names matter: the **rail** must be the compose service `openmaic`
(RC026 derives its env-var stem from it); the app is `openmaic-app`.

## The two decisions worth knowing

**1. The shim exists because the broker is not OpenAI-compatible.** The broker speaks Ollama —
`POST /v1/chat`, NDJSON frames, text at `message.content`. OpenMAIC speaks OpenAI —
`/v1/chat/completions`, SSE, `choices[0].delta.content`. [`api/llm.py`](backend/openmaic_app/api/llm.py)
translates.

Pointing OpenMAIC straight at Ollama would work and would need none of this. It is the wrong
answer here: generating one course is a burst of dozens of LLM calls, and bypassing the broker's
gate means those calls evict whatever another rail had resident, repeatedly, on a card with room
for one heavy model.

**2. The app is proxied same-origin, not iframed cross-origin.** That keeps the gateway's session
cookie and `X-Platform-User` in front of every request the app makes, and lets the iframe keep
`allow-same-origin` — an opaque origin withholds the cookie and every gated subresource 401s.

The price: OpenMAIC must be **built** with a matching `basePath`. That is a build argument, not a
runtime setting — an image built without it cannot be re-pointed later.

## Building the app image

The app source is not in this repo. From an OpenMAIC checkout:

```bash
python local-patches/platform-basepath.py
```

then build with the prefix baked in:

```bash
podman build -t openmaic-app:latest --build-arg NEXT_BASE_PATH=/openmaic/api/app .
```

`platform-basepath.py` is idempotent and has a `--check` dry run. It makes `basePath`/`assetPrefix`
env-driven, so the same tree still builds standalone when `NEXT_BASE_PATH` is unset.

## Root-origin assets

OpenMAIC's source carries ~124 hand-written absolute asset paths (`<img src="/logos/openai.svg">`).
Next's `basePath` does **not** rewrite those — it only touches URLs Next itself generates — so the
browser resolves them against the origin root and they leave this rail's namespace. `rail.json`
declares the prefixes, the gateway routes them here, and RC028 checks nobody else claims them.

**Deriving the set — do not do it by eye.** It was got wrong the first time: `/vendor/` was
missed, and it is the one that matters most, because `lib/import/use-import-pptx.ts` loads
`/vendor/maic-importer/index.js` at runtime — so PPTX import breaks, not a logo. From an OpenMAIC
checkout:

```bash
grep -rohE "['\"\`]/[A-Za-z0-9._-]+(/[^'\"\`]*)?" app lib components --include=*.ts --include=*.tsx | sort -u
```

Cross-check the result against `ls public/`: every top-level entry there that the source
references absolutely needs a prefix. Two traps in the output — bare SVG names like
`/openai.svg` appear only inside doc comments (the real files are under `/logos/`), and a
`.json` hit can be a regex artefact rather than a reference. Confirm each with `grep -F`.

After changing the set, update **three** places or RC028 fails: `rail.json`, the gateway's
`ROOT_ASSETS` mirror, and `OPENMAIC_ROOT_ASSETS` in this rail's config.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `OPENMAIC_LLM_MODEL` | `@openmaic` | Keep it an `@role` — a pinned name makes Admin → Rails decorative. |
| `OPENMAIC_EMBED_MODEL` | `@embed` | Shared with the RAG rails, so it stays resident. |
| `OPENMAIC_APP_URL` | `http://openmaic-app:3000` | The app container. |
| `OPENMAIC_SHIM_TOKEN` | `openmaic-local-dev` | Shared secret for app→rail calls. **Change it on any box that is not single-user.** |
| `OPENMAIC_LLM_BASE_URL` | *(unset)* | External OpenAI-compatible endpoint to use **instead** of the broker — e.g. a larger-VRAM host on the LAN. |
| `BROKER_AUTH_TOKEN` | *(unset)* | Unprefixed, platform-wide. |

On the app container, `DEFAULT_MODEL` is **mandatory** and needs a provider prefix
(`ollama:@openmaic`). OpenMAIC has no hardcoded fallback: unset, generation fails outright rather
than picking something.

### Pointing the LLM slot at another machine

Set `OPENMAIC_LLM_BASE_URL` to any OpenAI-compatible endpoint. The reasoning chip then reports the
override host rather than broker residency — this rail has no visibility into what is resident on
the far side, and reporting `loaded` for a model it cannot see would be exactly the lie the
four-state contract exists to prevent.

## Model slots

| Slot | Role | Kind | In Admin → Rails |
|---|---|---|---|
| `reasoning` | `@openmaic` | chat | yes |
| `embed` | `@embed` | embed | no — that panel is chat/vision/image only |

## Tests

```bash
.venv\Scripts\python.exe -m pytest rails/openmaic/tests -q
```

`test_auth.py` covers the fail-closed gate, including the shim mount — a mount is not a route, so
the app-level dependency never runs for it, and those tests are what keep "has its own gate" from
quietly becoming "has no gate". `test_llm_shim.py` covers the translation, which has no visible
failure mode: a wrong `options` key does not raise, it silently uses the model's default.
