# Co-Worker Rail

A platform rail that surfaces harvested email, calendar, Teams, and Insights
intelligence into a triage dashboard. The landing view is an **executive brief**
— 5–10 prioritised action items synthesised from the week's data by a local 4B
model. The raw item grid is one tab away for when you want the detail.

## How it works

```
[Outlook / Calendar / Teams / SharePoint]
        │
        ▼
[4 Claude co-work scheduled tasks]   ← run on Windows, write .md + .json
        │
        ▼
[inbox drop-zone]                    ← C:\Users\you\ai-platform\data\co-worker\inbox\
        │  (bind-mounted into container)
        ▼
[co-worker backend :8860]            ← /api/inbox serves raw items
        │                              /api/brief serves the synthesised executive brief
        ▼
[broker :11500 → @co-worker-synthesis (gemma3:4b)]  ← one pass per source lane
        │
        ▼
[gateway → /co-worker/api/*]
        │
        ▼
[co-worker frontend]                 ← Brief landing view + All-items grid
```

### The four harvest loops

| Loop | Schedule (PT) | Scope | Output |
|---|---|---|---|
| `teams-monthly-scan` | Mon 3a | Prior 30 days of Teams | `inbox/teams/YYYY-MM-DD-30day.md` + `inbox/*.json` |
| `email-daily-brief` | Daily 4a | Prior day + today | `inbox/email/YYYY-MM-DD.md` + `inbox/*.json` |
| `calendar-weekly-brief` | Mon 5a | Current week Mon–Fri | `inbox/calendar/YYYY-MM-DD-week.md` + `inbox/*.json` |
| `insights-weekly-synthesis` | Mon 6a | Synthesises the other three | `inbox/insights/YYYY-MM-DD-synthesis.md` + `inbox/*.json` |

Loops run in dependency order on Monday so the synthesis sees fresh input from the
other three. Task prompts live in `%USERPROFILE%\Claude\Scheduled\<taskId>\SKILL.md`
(machine-local, never committed — they embed the owner's real name and email).

## Two artifacts per run

| Artifact | Path | Consumer |
|---|---|---|
| Narrative markdown | `inbox/<source>/*.md` | Humans — the full brief with reasoning |
| Item JSON | `inbox/*.json` (**flat root**) | This rail — one file per finding, rendered as a card |

**JSON must be flat in `inbox/`.** The backend globs `inbox/*.json` non-recursively;
markdown lives one level down so the backend never sees it.

## Executive Brief

The landing tab is not a list of items — it is a synthesised **executive brief** produced
by one model call per source lane, then merged into a single ranked output.

`GET /api/brief` → `brief.json`  
`GET /api/brief?source=email|calendar|teams|insights` → per-lane brief  
`POST /api/brief/refresh[?source=X]` → trigger re-synthesis (returns 409 if already running)  
`GET /api/brief/status` → running / last error / timestamps

**Why per-lane, not combined:** a single combined pass over 200 items at ~450 chars/item
is ~23K tokens. A 4B model reading 23K tokens attends to a fraction of the middle. Split
by lane each payload is 2.5K–8K tokens — inside the range where the model actually reads
everything. This is a correctness fix, not an optimisation.

The brief schema is documented in [`BRIEF_SCHEMA.md`](./BRIEF_SCHEMA.md).

### Self-authored items

Items where `from` matches `CO_WORKER_USER_NAME` / `CO_WORKER_USER_EMAIL` are relabelled
`from=(self)` before the model sees them. Combined with a prompt guard, this prevents
the model from generating "Reply to yourself" attention items from the user's own sent
messages, calendar events, or synthesised insight notes.

Set `CO_WORKER_USER_NAME` and `CO_WORKER_USER_EMAIL` in `deploy/.env` (gitignored).

## Item schema

**See [`SCHEMA.md`](./SCHEMA.md)** — the contract between harvest and rail. Schema v1.

Every item: required `why`, `priority` (1–5), `client` boolean, explicit-offset datetimes,
`doc` pointer back to the narrative markdown. Frontend mirrors the contract in
`frontend/src/types.ts` and warns on schema-version mismatch.

Schema changes: bump `SCHEMA_VERSION` in `types.ts`, update `SCHEMA.md`, update all four
task prompts.

## Backend API

```
GET  /api/healthz                liveness + inbox item count
GET  /api/inbox                  all items, newest first by file mtime
GET  /api/inbox/{id}             single item by filename stem
PATCH /api/inbox/{id}            set triage state (open / done / dismissed)

GET  /api/brief[?source=X]       executive brief (merged or per-lane)
POST /api/brief/refresh[?source=X]  trigger synthesis; 409 if running
GET  /api/brief/status           synthesis job status
```

Brief writes are atomic (`mkstemp` + `os.replace`). One synthesis run at a time;
second concurrent request returns 409.

## Frontend

Federated React remote (`co_worker/module`).

```
src/types.ts         item + brief contract (mirrors SCHEMA.md and BRIEF_SCHEMA.md)
src/api.ts           same-origin fetch helper
src/theme.css        token-derived styles, scoped under .co-worker
src/BriefView.tsx    executive brief tab: lens tabs, attention rows, source badges
src/module.tsx       the all-items dashboard
src/prompts.example.ts  sanitised template (real prompts.ts is gitignored)
```

**Brief view features:** lens tabs (★ Top 10 / Email / Calendar / Teams / Insights — lanes
ordered by data freshness, since insights is a weekly synthesis of the other three and never
newer than them), a "top N of M surfaced across lanes" line so the 10-item cap accounts for
what it holds back,
attention count badge per lane, a leading icon that shows whichever field discriminates in the current view (source lane on Top 10, category on a lane view — it was always the category, which made it a column of identical handshakes since client work is ranked first by construction and the category was already on the row as a chip), "Partial pass" banner only
on genuine context truncation (not deliberate noise filtering), failed-source warning,
re-synthesize button (single lane or all), auto-refresh on staleness.

**All-items view features:** KPI strip, source tabs, type filter, free-text search,
sort by priority / when / newest, client-only + hide-noise toggles, collapsible card
bodies with markdown-lite rendering, deep links back into Outlook.

**Build:**
```bash
cd rails/co-worker/frontend
npm install
npm run build    # → frontend/dist, mounted into gateway at /co-worker/*
npm run dev      # standalone on :5260, proxies /co-worker/api to :8860
```

The gateway image bakes the dist at build time from the source in the repo —
run `docker compose build gateway` after a frontend change.

## Environment variables

All prefixed `CO_WORKER_` (set in `deploy/.env`, forwarded by the compose file):

| Variable | Default | Notes |
|---|---|---|
| `CO_WORKER_INBOX_DIR` | `/data/inbox` | Container path to the bind-mount |
| `CO_WORKER_BROKER_URL` | `http://host.docker.internal:11500` | Ollama broker |
| `CO_WORKER_BROKER_AUTH_TOKEN` | `""` | Optional bearer token |
| `CO_WORKER_USER_NAME` | `""` | Your display name — suppresses self-reply items |
| `CO_WORKER_USER_EMAIL` | `""` | Your email — suppresses self-reply items |
| `CO_WORKER_AUTO_SYNTHESIZE` | `true` | Re-synthesize when inbox is stale |
| `CO_WORKER_AUTO_SYNTHESIZE_MIN_INTERVAL_S` | `900` | Minimum gap between auto-triggered passes |

## Inbox drop-zone

| Context | Path |
|---|---|
| Windows host (harvest writes here) | `%USERPROFILE%\ai-platform\data\co-worker\inbox\` |
| Container (backend reads) | `/data/inbox/` |

Wired via `CO_WORKER_INBOX_MOUNT` in `deploy/.env`; falls back to a named volume
(`co_worker_inbox`) if unset.

## Tooling

```
tools/ab_synthesis.py    A/B-test synthesis models against the real prompt and inbox
tools/validate_inbox.py  Schema validation for inbox items
tools/prune_inbox.py     Age out old items
```

Run inside the container:
```bash
docker compose exec co-worker python /app/tools/ab_synthesis.py --source email --reps 3
```

## Port

`8860` — next in the platform sequence after ai-playground (8850).

## Status

Working end to end. Per-source synthesis, executive brief, per-lane lens tabs, triage
state (open / done / dismissed) via `PATCH /api/inbox/{id}`, archive view, and all four
harvest loops are in place.
