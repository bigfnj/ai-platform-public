# Gemini CX rail — handoff

Read this first when resuming. Built and deployed 2026-08-18.

## State: live and verified in production

Running at `http://localhost:1111` → **Gemini CX**. Not a prototype — it is deployed, ingested and
answering.

| Check | Result |
|---|---|
| Corpus | **307 chunks / 17 collections / 1024 dims**, ingested in-container |
| Question deck | **37 questions / 7 groups**, `validate()` clean |
| Models | `@gemini-cx-rag` → `gemma3:4b` and `@embed` → `bge-m3`, both resident together |
| Voice | Kokoro `af_heart` via `tts_light`; 24 kHz RIFF/WAVE, ~7 s to first word |
| Identity gate | 401 without the gateway header, on `/api/ask` and `/api/speak` |
| Build | `py_compile`, `tsc --noEmit`, `vite build` all clean |

## The three things that make this rail what it is

**1. It exists to stop GECX being over-sold.** Google's January 2026 launch and its August 2026
documentation contradict each other. The corpus marks GA / Preview / coming-soon / announced-only
as four different answers, refuses figures it cannot cite, and disambiguates confusable pairs in
the heading itself. `config.SYSTEM_PROMPT` enforces all three at answer time.

**If a future change makes the model willing to fill a gap, that is a regression, not a style
change.** Verified working: asked how GECX is priced, `gemma3:4b` answered "not verifiable"
instead of inventing a number.

**2. The question deck is the front door, not decoration.** A blank prompt over an unfamiliar
corpus produces a bad first experience. `questions.validate()` checks every deck question's
collections exist on disk, `/api/health` reports it, and the UI disables any that fail — a deck
entry that answers "not covered" teaches the user the tool is broken on click one. Order is
*What it is* → *Get it right* → the rest: orientation before correction.

**3. Answers stream, because they have to.** 2.6–9.2 s per answer on `gemma3:4b`. A spinner that
long reads as a hang. `WS /ws/ask` with a buffered `POST /api/ask` fallback for proxies that
refuse the upgrade.

## Things that will trip you up

- **`config.ensure_dirs()` is load-bearing.** `store.init()` calls it before touching SQLite and
  the container mounts an empty volume at `/srv/var`. It was missing in the first draft and would
  have crashed the rail at startup.
- **`_same()` in `modelstate.py` must not become `==`.** Ollama reports an untagged pull as
  `:latest`, so `@embed` resolves to `bge-m3` while the loaded list says `bge-m3:latest`. The
  broker's own `roles` payload has this bug and reports `installed: false` for a resident model.
- **The empty- and orphan-collection purges in `ingest.py` are not redundant.** Without them,
  emptying or renaming a collection leaves its chunks indexed and cited forever. This cost the
  sibling SMB rail weeks.
- **Voice must use `/v1/tts_light`, never `/v1/tts`.** The latter takes the GPU gate and evicts
  every heavy model per utterance, which would destroy this rail's co-residency.
- **`pip install .` does not reload a running uvicorn.** Kill the PID, use `--reload --reload-dir src`.
- **`GEMINI_CX_STANDALONE=1`** is required outside the gateway or identity fails closed (401).
- **The live broker runs from the INSTALL clone.** Editing the Grimoire copy's `roles.json` does
  nothing to the running system. It is hot-read, so a pull is enough — no broker restart.

## Deploying a change

Frontend or corpus changes need the **gateway image rebuilt** (dists are baked in; a host
`npm run build` deploys nothing) plus the rail container rebuilt for backend changes:

```powershell
docker-compose --env-file .env -f installer\docker-compose.installer.yml `
  --profile recipe-book --profile co-worker --profile smb-partner-enablement --profile gemini-cx `
  up -d --no-deps --build gemini-cx gateway
```

Hard-refresh afterwards — the shell caches the federated `remoteEntry.js`.

## Open items

`BACKLOG.md` has the ordered list. The two that matter:

1. **Three facts the corpus deliberately refuses to state**, each needing a human with a browser:
   base per-session CX Agent Studio prices (the pricing page defeats automated extraction — only
   the **$0.0025/sec voice overage after 300 s** is sourced), the actual 40+ and 10-language
   enumerations, and whether "Personal Intelligence" is real (Forrester cites it, Google's own
   pages do not).
2. **A staleness signal.** Every file carries `As of:` / `Verified:` front matter and nothing
   surfaces it. GECX is moving; Commerce Agents flipping from "coming soon" to GA would invalidate
   several answers at once and nothing would say so.

## Cross-rail work that landed alongside this

- **Four-state model chips** (`modelstate.py`) on gemini-cx, co-worker and terminal-fun: red
  missing / blue cold / orange warming / green loaded, polling every 6 s. Duplicated per rail on
  purpose — independent deployables, different dependency sets. Keep the state names and the
  resolution order identical.
- **Admin → Rails is now authoritative for every rail.** terminal-fun and recipe-book pinned
  concrete model names and silently ignored the panel; co-worker was not in the panel at all. All
  now reference `@roles`. **Still pending: SMB's chips are the old on/off dot** — converting them
  is a small change to `AiStatus` + `.dot` in its `theme.css`, left undone because those files had
  uncommitted work in them.
