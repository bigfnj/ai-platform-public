# SMB Partner Enablement

A grounded enablement assistant for Microsoft SMB partners, with a voice surface and a
standalone mobile build. Rebuild of the SME&C Account Planning Hackathon prototype whose source
was lost — see [`reference/README.md`](reference/README.md) for the spec it is rebuilt from.

The premise: a partner walking into a customer meeting gets, in two minutes, what to ask, what
to position, how to handle the objections, and what to do in Partner Center afterwards —
answered only from curated SME material, with citations.

## What is built, and what is not

| | |
|---|---|
| ✅ Backend | Retrieval, grounded ask (buffered + streamed), ingest, capabilities |
| ✅ SME corpus | Six collections with an authoring contract — **content is placeholder** |
| ✅ Desktop rail | Three tabs + AI status + live mobile preview |
| ✅ Mobile surface | Standalone voice-first build at `/smb-partner-enablement/m/` |
| ✅ Platform wiring | Broker roles, gateway registration, compose service |
| ⛔ Scenario Builder | The four-question diagnostic and generated package are **not wired** |
| ⛔ Server-side voice | Kokoro is unavailable on this platform — see [`MODELS.md`](MODELS.md) |

## The two resident models

The rail holds two broker models warm at once, which is the platform's normal steady state
rather than a special case: `@smb-partner-rag` (heavy, 3B-class) writes the answer, `@embed`
(light) does retrieval, and the broker's one-heavy-model policy exempts embedders. Measured
co-resident on this workstation at 2.55 GB + 0.66 GB.

**Voice is the exception.** The broker's TTS path evicts *every* heavy model before running, so
server-side speech would cost a model swap per utterance — the opposite of an always-on voice
agent. Until a non-evicting Kokoro path exists, `voice.py` resolves to browser speech synthesis,
which costs no VRAM and works on a phone. `/api/capabilities` reports which backend is actually
live, so the UI never claims a capability it does not have. Full analysis in [`MODELS.md`](MODELS.md).

## Layout

```
src/smb_partner/
  config.py     env-driven settings (SMB_PARTNER_ prefix), prompts, model roles
  broker.py     chat / chat_stream / embed / tts against the platform broker
  voice.py      the TTS backend seam — browser | broker | auto | off
  rag.py        markdown -> heading-aware chunks -> embeddings -> cosine ranking
  store.py      SQLite chunk index + in-memory normalized vector matrix
  ingest.py     fingerprinted seed ingest (re-embeds only what changed)
  api.py        FastAPI factory — /api/ask, /ws/ask, /api/capabilities, /api/ingest
seed/knowledge-base/   the SME corpus — one folder per collection (see its README)
frontend/src/          the federated desktop remote
frontend/mobile/       the standalone mobile SPA -> dist/m/
reference/             the lost prototype's spec + screenshots
```

## Endpoints

All served by the gateway under `/smb-partner-enablement/`, behind the entitlement gate.

| | |
|---|---|
| `GET /api/health` | liveness + corpus size |
| `GET /api/capabilities` | model residency, voice backend, corpus stats |
| `GET /api/collections` | the SME corpus per collection |
| `POST /api/ask` | grounded answer; `speak:true` adds a voice payload |
| `WS /ws/ask` | the same, streamed (the gateway buffers plain HTTP) |
| `POST /api/ingest?force=` | re-ingest the seed corpus (admin) |
| `POST /api/upload` | index an ad-hoc document (admin) |

## Two frontend builds, one package

`npm run build` runs the federation build first (it owns and empties `dist/`), then the mobile
build into `dist/m/` with `emptyOutDir: false`. **Order matters** — running the mobile config
alone after a clean will produce a `dist/` with no `remoteEntry.js` and the shell will fail to
load the rail.

The mobile app is deliberately not a federation remote: the platform shell is a fixed
two-column desktop grid, so the phone experience is served standalone by the gateway's existing
per-rail `StaticFiles` mount (`html=True`) at `/smb-partner-enablement/m/`. No gateway routing
change was needed, and the entitlement gate still applies because the path's first segment is
the rail id.

## Local development

```bash
# backend (needs the broker on :11500)
pip install -e .
SMB_PARTNER_STANDALONE=1 uvicorn --factory smb_partner.api:create_api --port 8870

# desktop remote            # mobile surface
npm run dev                 npm run dev:mobile
```

`SMB_PARTNER_STANDALONE=1` is required outside the platform: identity fails closed on a missing
`X-Platform-User` header, because in the deployed topology a request without one is a sibling
container rather than a logged-in user.

## Adding SME content

Drop markdown into a collection under `seed/knowledge-base/` and re-ingest. Headings become
citation titles, so write them the way a partner would ask. The full contract — including why
every file carries a source and a date — is in
[`seed/knowledge-base/README.md`](seed/knowledge-base/README.md).

Everything currently in there is scaffolding that says so. Until it is replaced, answers will be
correctly reported as ungrounded, which is the intended behaviour rather than a bug.
