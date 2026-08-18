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
| ✅ SME corpus | **67 sourced files across 13 collections** (~1,050 chunks), every file carrying a source URL, an as-of date and a currency warning |
| ✅ Scenario Builder | **Six scenarios**, 6–8 questions each, five-part grounded package, live reasoning trace |
| ✅ Desktop rail | Three tabs + AI status + live mobile preview |
| ✅ Platform wiring | Broker roles, gateway registration, compose service |
| ◐ Mobile surface | Standalone build at `/smb-partner-enablement/m/`; Chat works, the Scenario Builder flow is **not yet wired there** |
| ✅ Server-side voice out | **Kokoro-82M** via the broker's non-evicting `tts_light` — both models stay resident. Every spoken surface, including *Read aloud*, goes through it |
| ✅ Server-side voice in | **faster-whisper** (CPU/int8) via the broker's `/v1/transcribe` — honours the selected microphone and keeps audio on the box |
| ✅ Deployed | Live at `localhost:1111` behind the entitlement gate |

### How the Scenario Builder stays honest

Three mechanisms, each added after watching the model get something wrong:

- **Hard constraints are computed, not inferred.** A rule table maps an answer to a limit the
  model may not contradict — the pooled 300-seat Business-family cap, the sub-300-employee scope
  of the partner-led Copilot trial, the Microsoft-managed-account requirement for deal
  registration, and "foundation before AI" rules that stop a Copilot recommendation when MFA is
  off or there is no CRM.
- **Two output guards.** Any sentence carrying a figure — or asserting an entitlement about a
  product — that is absent from the retrieved context is dropped, and the suppression count is
  shown to the partner rather than hidden.
- **The Scenario Card is assembled in code**, not generated, because nothing in it needs
  judgement and as a model pass it just echoed the partner's own answers back.

## The two resident models

The rail holds two broker models warm at once, which is the platform's normal steady state
rather than a special case: `@smb-partner-rag` (heavy, 3B-class) writes the answer, `@embed`
(light) does retrieval, and the broker's one-heavy-model policy exempts embedders. Measured
co-resident on this workstation at 2.55 GB + 0.66 GB.

**Voice used to be the exception, and no longer is.** The broker's original TTS path (`/v1/tts`,
XTTS) evicts *every* heavy model before running, which would cost a model swap per utterance —
the opposite of an always-on voice agent. Both directions now run on paths that take no GPU gate
and evict nothing:

- **Out — Kokoro-82M** via `/v1/tts_light`. At ~350 MB it coexists with the resident RAG model.
  Every spoken surface routes here, including *Read aloud* (`POST /api/speak`); the browser's
  synthesizer is only a fallback for when the media worker is down.
- **In — faster-whisper `small.en`** via `/v1/transcribe`, on **CPU/int8** so speech input never
  competes for VRAM with the model that is about to answer. ~1.7s for a 4s utterance.

The reason STT moved server-side is not privacy alone. `SpeechRecognition` has **no device API** —
it always listens to the OS default input, so a user who picked a headset in the rail's own device
picker got silence and a `no-speech` error. `getUserMedia({ deviceId })` honours the selection, so
the browser records and the broker transcribes. It also removes the Firefox gap and stops shipping
audio to Google.

`/api/capabilities` reports both directions (`voice.effective` for output, `voice.stt` for input),
so the UI never claims a capability it does not have. Full analysis in [`MODELS.md`](MODELS.md).

## Layout

```
src/smb_partner/
  config.py     env-driven settings (SMB_PARTNER_ prefix), prompts, model roles
  broker.py     chat / chat_stream / embed / tts against the platform broker
  voice.py      the voice seam — TTS backend (browser | broker | auto | off) + transcribe()
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
| `POST /api/speak` | synthesize arbitrary text through Kokoro — backs *Read aloud* |
| `POST /api/transcribe` | faster-whisper STT for one recorded utterance |
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

The corpus is real: 67 files across 13 collections, researched from public Microsoft sources and
audited against them. Two rules matter most when adding to it.

**Never state a rate, margin, threshold or fee you cannot cite.** Much of the incentive detail is
partner-confidential and lives only in the Partner Center rate card. "Check the current rate card"
is a genuinely useful answer; an invented percentage loses a deal. The 15% partner earned credit
and the 60/40 rebate split that circulate are *worked examples* inside Microsoft's docs, not rate
cards.

**Disambiguate near-identical policies in the heading itself.** A heading reading "Can I get a
refund if I renew or purchase by mistake?" retrieved strongly for "how long do I have to cancel an
annual subscription I sold by mistake" — and the assistant answered **30 days** (the MAICPP
membership window) when the correct answer was **7 calendar days** (New Commerce). The chunk was
accurate but unscoped. Retrieval hands over a chunk without its neighbours, so the disambiguation
has to travel inside it.

Four collections — `discovery/`, `objection-handling/`, `solution-plays/`, `customer-stories/` —
are deliberately empty with authoring READMEs. They are for field-earned material that comes from
real partner conversations rather than documentation.
