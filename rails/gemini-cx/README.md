# Gemini Enterprise CX rail

A grounded question-answering rail over a purpose-built subject-matter corpus on **Google
Cloud's Gemini Enterprise for Customer Experience (GECX)**. Retrieval-augmented, cited, and
fronted by a **curated question deck** rather than a bare chat box.

Loosely modelled on `rails/smb-partner-enablement` — same retrieval architecture (broker
embeddings, numpy cosine, SQLite chunk index), different subject and a different front door.

## Why a question deck instead of a chat box

Because a blank prompt over an unfamiliar corpus produces a bad first experience. The user does
not yet know what GECX *is*, so they cannot know what to ask, and their opening question is
usually one the corpus cannot ground — which teaches them the tool is broken on the first click.

The deck inverts that: it makes the corpus's own strengths clickable. Its first group is
deliberately **"Get it right"** — the questions where Google's launch announcement and Google's
product documentation give different answers. That is where a grounded assistant beats reading
the product page, so it leads.

Three rules govern the deck (`src/gemini_cx/questions.py`):

1. **Every deck question must be answerable from the corpus.** `questions.validate()` checks each
   question's declared collections exist on disk, `GET /api/health` reports the result, and the
   UI disables any question that fails. A deck entry that answers "the context does not cover
   this" is worse than no deck.
2. **Lead with the disambiguation traps**, not the basics.
3. **Scope each question to the collections that answer it.** A deck click carries its scope; a
   free-typed question is deliberately unscoped, because guessing a scope for the user retrieves
   worse than not guessing.

## The corpus

`seed/knowledge-base/` — 17 collections, ~38 markdown files. Authoring contract and the
disambiguation rules are in [`seed/knowledge-base/README.md`](seed/knowledge-base/README.md).
Every file carries a `Source` / `As of` / `Verified` / `Status` front-matter block, because GECX
launched in January 2026 and is still moving.

The corpus splits into **platform** collections (what GECX is and how it works) and **motion**
collections (how a practitioner scopes, sells, and delivers it).

Three things the corpus is deliberately careful about, because they are where GECX gets
over-sold:

- **Status is never smoothed.** GA, Preview, coming soon, and announced-only are four different
  answers. Commerce Agents were announced at NRF 2026 and their documentation still says "coming
  soon".
- **40+ languages (text) and 10 languages (audio-to-audio) are different numbers** for different
  modalities.
- **Unpublished figures are refused, not approximated.** No containment rates, no latency
  benchmarks, no invented per-session prices. `pricing-and-licensing/02-figures-never-to-state.md`
  is the standing list.

The system prompt (`config.SYSTEM_PROMPT`) enforces all three at answer time.

## API

```
GET  /api/health        liveness, corpus stats, resident models, deck validation
GET  /api/capabilities  what the rail can do right now, so the UI renders honestly
GET  /api/questions     the curated question deck
GET  /api/collections   the corpus, per collection
POST /api/ingest        re-ingest the seed knowledge base (admin) — ?force=true
POST /api/upload        index an ad-hoc document
POST /api/ask           grounded answer, buffered
WS   /ws/ask            the same, streamed token-by-token
```

`POST /api/ask` and `WS /ws/ask` accept either `question` (free prose) or `question_id` (a deck
id, which brings its own collection scoping).

Streaming is not a flourish: on an 8 GB card a 4B-class model emits an 800-token answer over
roughly twenty seconds, and a spinner for twenty seconds reads as a hang. The buffered POST is
kept because it is trivially scriptable and because a corporate proxy may refuse a WebSocket
upgrade — the frontend falls back to it automatically.

## Models

Two broker models held concurrently: `@gemini-cx-rag` (heavy, writes the answer) and `@embed`
(light, retrieval). See [MODELS.md](MODELS.md) for the VRAM arithmetic and the swap-avoidance
tradeoff against the SMB Partner rail.

## Running it standalone

```bash
cd rails/gemini-cx
pip install -e .
GEMINI_CX_STANDALONE=1 uvicorn --factory gemini_cx.api:create_api --port 8880 \
  --reload --reload-dir src
```

`GEMINI_CX_STANDALONE=1` is required outside the gateway: identity fails closed by design, so
without it every request is a 401. The broker must be reachable at
`GEMINI_CX_BROKER_URL` (default `http://127.0.0.1:11500`) for ingest and answering.

Frontend:

```bash
cd rails/gemini-cx/frontend
npm install && npm run dev      # http://localhost:5280
```

**`pip install .` does not reload a running uvicorn** — it keeps the old module in memory. Kill
the PID (`netstat -ano | findstr :8880`; a failed rebind shows `WinError 10013`) and restart with
`--reload --reload-dir src`.

## Deploying a content change

The seed tree is baked into the image read-only, so editing a knowledge-base file needs a
container rebuild. To iterate without one, bind-mount the directory over
`/srv/seed/knowledge-base` and call `POST /api/ingest?force=true`.

Ingest is fingerprinted per collection, so a restart only re-embeds what actually changed.

A **frontend** change needs the gateway image rebuilt, not a host `npm run build` — the running
gateway bakes rail dists in. See the platform's deploy notes.
