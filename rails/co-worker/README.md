# Co-Worker Rail

A platform rail that surfaces harvested email and calendar intelligence — reminders,
follow-ups, FYIs, and calendar context — into a single dashboard view.

## How it works

An external **harvest process** (a Claude co-work session reading Outlook + Calendar)
writes structured JSON files into the **inbox drop-zone**. The Co-Worker backend reads
those files on demand. The frontend displays them.

The backend is intentionally schema-agnostic — whatever JSON the harvest process writes,
the rail forwards as-is. The frontend evolves alongside the schema as the harvest output
gets refined.

```
[Outlook / Calendar]
        │
        ▼
[Claude co-work harvest process]   ← runs on Windows, writes .json files
        │
        ▼
[inbox drop-zone]                  ← C:\Users\you\ai-platform\data\co-worker\inbox\
        │  (bind-mounted into container)
        ▼
[co-worker backend :8860]          ← /api/inbox lists + serves files
        │
        ▼
[gateway → /co-worker/api/*]
        │
        ▼
[co-worker frontend]               ← displays cards, visuals TBD
```

## Inbox drop-zone

| Context | Path |
|---|---|
| Windows host (write here) | `%USERPROFILE%\ai-platform\data\co-worker\inbox\` |
| WSL / container (read from) | `/data/inbox/` |

Each file is a single JSON object. The harvest process names files by timestamp or ID
(e.g. `2026-08-11T090000_follow-up.json`). The backend sorts by file mtime, newest first.

### Suggested item schema (draft — evolve as needed)

```json
{
  "type": "follow-up | reminder | fyi | calendar",
  "title": "Short subject",
  "body": "Full text or summary",
  "source": "email | calendar",
  "due": "ISO-8601 datetime or null",
  "from": "sender name or email",
  "thread_id": "optional, for grouping related items",
  "tags": ["optional", "labels"]
}
```

## Backend API

```
GET /api/healthz          liveness + inbox item count
GET /api/inbox            all items, newest first
GET /api/inbox/{id}       single item by filename stem
```

## Port

`8860` — next in the platform sequence after ai-playground (8850).

## Status

Scaffold only. No AI model slots — this is a pure data-display rail.
The harvest process and visualization layer are to be built.
