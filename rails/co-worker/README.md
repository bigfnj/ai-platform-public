# Co-Worker Rail

A platform rail that surfaces harvested email, calendar and Teams intelligence — client
prep, dangling commitments, scheduling conflicts, ranked mail and workflow insights — into
a single triage dashboard.

## How it works

Four **Claude co-work scheduled tasks** run on the Windows host, read Microsoft 365, and
write into the inbox drop-zone. The Co-Worker backend reads those files on demand. The
frontend renders them as a priority-ordered dashboard.

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
[co-worker backend :8860]            ← /api/inbox lists + serves JSON items
        │
        ▼
[gateway → /co-worker/api/*]
        │
        ▼
[co-worker frontend]                 ← KPI strip, filters, priority-ranked cards
```

### The four loops

| Loop | Schedule (PT) | Scope | Narrative output |
|---|---|---|---|
| `teams-monthly-scan` | Mon 3a | Prior 30 days of Teams | `inbox/teams/YYYY-MM-DD-30day.md` |
| `email-daily-brief` | Daily 4a | Prior day + today's mail | `inbox/email/YYYY-MM-DD.md` |
| `calendar-weekly-brief` | Mon 5a | Current week Mon–Fri | `inbox/calendar/YYYY-MM-DD-week.md` |
| `insights-weekly-synthesis` | Mon 6a | Synthesizes the other three | `inbox/insights/YYYY-MM-DD-synthesis.md` |

Task prompts live in `%USERPROFILE%\Claude\Scheduled\<taskId>\SKILL.md`. They run in
dependency order on Monday so the synthesis sees fresh input from the other three.

## Two artifacts per run

Each loop emits **both** a human artifact and a machine artifact:

| Artifact | Path | Consumer |
|---|---|---|
| Narrative markdown | `inbox/<source>/*.md` | Humans — the full brief, with reasoning and a Method & limits section |
| Item JSON | `inbox/*.json` (**flat**) | This rail — one file per finding, rendered as a card |

**The JSON must be flat in `inbox/`.** The backend globs `inbox/*.json`
non-recursively (`main.py: inbox_list`), so items in subfolders are never found. The
markdown deliberately lives one level down, which is what keeps it invisible to the
backend.

## Item schema

**See [`SCHEMA.md`](./SCHEMA.md)** — the authoritative contract between the harvest
process and this rail. Currently **schema version 1**.

Every item carries a required one-line `why`, a `priority` (1–5, client work outranks
internal), a `client` boolean, explicit-offset datetimes, and a `doc` pointer back to the
narrative markdown. The frontend mirrors the contract in `frontend/src/types.ts` and shows
a visible warning if it encounters an item declaring a different schema version.

When the schema changes: bump `SCHEMA_VERSION` in `types.ts`, update `SCHEMA.md`, and
update all four task prompts in the same change.

## Inbox drop-zone

| Context | Path |
|---|---|
| Windows host (harvest writes here) | `%USERPROFILE%\ai-platform\data\co-worker\inbox\` |
| WSL / container (backend reads) | `/data/inbox/` |

Wired via `CO_WORKER_INBOX_MOUNT` in `deploy/.env`; falls back to a named volume
(`co_worker_inbox`) if unset, which yields an empty dashboard rather than a startup
failure.

## Backend API

```
GET /api/healthz          liveness + inbox item count
GET /api/inbox            all items, newest first by file mtime
GET /api/inbox/{id}       single item by filename stem
```

Schema-agnostic by design: unknown fields are forwarded as-is, and malformed files are
skipped with a logged warning rather than failing the whole request.

## Frontend

Federated React remote (`co_worker/module`), no runtime dependencies beyond React.
Follows the platform theming contract in [`web/THEMING.md`](../../web/THEMING.md): local
tokens are derived from the shared design tokens, action surfaces use `--grad-accent`, and
the priority/status scale stays **semantic** (`--critical` / `--warning` / `--good`) so
triage signal reads identically on every palette.

```
src/types.ts    item contract + display metadata (mirrors SCHEMA.md)
src/api.ts      same-origin fetch helper
src/theme.css   token-derived styles, scoped under .co-worker
src/module.tsx  the dashboard
```

Features: KPI strip (P1 count, dangling promises, conflicts, client items, run count),
source tabs with per-loop counts, type filter, free-text search, sort by
priority / when-it-happens / newest-harvested, client-only and hide-noise toggles,
collapsible card bodies with markdown-lite rendering, and deep links back into Outlook.

Build (the compose stack mounts the host-built dist read-only):

```bash
cd rails/co-worker/frontend
npm install
npm run build          # -> frontend/dist, mounted as /app/co-worker-dist
npm run dev            # standalone on :5260, proxies /co-worker/api to :8860
```

## Port

`8860` — next in the platform sequence after ai-playground (8850).

## Status

Working end to end. Backend, schema, frontend dashboard and all four harvest loops are in
place.

Triage state (`open` / `done` / `dismissed`) persists in `inbox/.state.json` via an atomic
sidecar write — harvest files are never mutated. `PATCH /api/inbox/{id}` sets triage status;
the dashboard updates optimistically and rolls back on failure. The archive view surfaces
pruned items for trend analysis.
