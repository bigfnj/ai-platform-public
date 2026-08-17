# Executive brief contract (`inbox/brief.json`)

The harvest loops produce raw items; a **synthesis pass** reduces them to what actually
needs attention. That output is `brief.json` and this is its contract.

Why this exists: a week of harvested email + calendar + Teams runs to 100+ items. Reading
100 cards to decide what matters costs more time than it saves. The brief is the landing
view; the raw grid is a second tab for when you want the detail.

Written by `backend/co_worker_app/synthesize.py`. Read by `GET /api/brief`.
Mirrored in `frontend/src/types.ts` — keep both in sync.

## Shape

```json
{
  "generated": "2026-08-13T21:00:00+00:00",
  "period": "2026W33",
  "items_considered": 147,
  "items_triaged": 23,

  "attention": [
    {
      "id": "teams-20260811-a3f2",
      "category": "client",
      "headline": "Reply to Priya Sharma re: SOW milestone 3 — asked Tuesday, no response yet",
      "urgency": "today",
      "why": "Milestone sign-off gates the next invoice cycle."
    }
  ],

  "client_pulse": "Two of three client threads are waiting on you...",
  "dangling": ["Promised the architecture diagram to Dan by EOW — not sent"],
  "missed": ["Priya Sharma (client) — SOW milestone 3, last message Tuesday"],
  "agenda_gaps": ["Delivery sync Thu 2pm — you organised it, no agenda set"],

  "suppressed": 114,
  "synthesis_note": null
}
```

## Fields

| Field | Type | Notes |
|---|---|---|
| `generated` | ISO 8601 | When the pass ran. The API derives `age_hours` / `stale` from file mtime, not this. |
| `period` | string | Dominant period across the items read (`2026W33` or `20260811`). |
| `items_considered` | int | Every item in `inbox/*.json` (excluding `brief.json` and dotfiles). |
| `items_triaged` | int | How many were already `done`/`dismissed` and therefore ignored. |
| `attention` | array | **Max 10.** The whole point of the brief. See below. |
| `client_pulse` | string | 2–3 sentences on the overall state of client threads. |
| `dangling` | string[] | Commitments made with no resolution signal. |
| `missed` | string[] | Threads where someone is waiting on a response. |
| `agenda_gaps` | string[] | Future meetings the user organised with no agenda/purpose. |
| `suppressed` | int | `items_considered - items_triaged - len(attention)`. Shown so suppression is visible, never silent. |
| `synthesis_note` | string \| null | Anything unusual the model wants to flag. |

### `attention[]`

| Field | Type | Notes |
|---|---|---|
| `id` | string | **Must match an inbox item `_id`** — this is what makes Done/Dismiss round-trip via `PATCH /api/inbox/{id}`. An `id` that doesn't resolve renders but its triage buttons no-op. |
| `category` | enum | `client` \| `dangling` \| `missed` \| `agenda-gap` \| `other` |
| `headline` | string | A direct instruction, not a description. "Reply to X re: Y — asked Tuesday" not "Email from X about Y". |
| `urgency` | enum | `today` \| `this-week` \| `soon` — drives the left border colour and sort order. |
| `why` | string | One sentence on the consequence of not acting. |

## Priority order

The synthesis prompt ranks in this order, and the frontend sorts by urgency then category:

1. **client** — anything involving a client contact, deliverable, or meeting
2. **dangling** — explicit commitments ("I'll send", "by EOW") with no resolution
3. **missed** — someone is waiting; last message is not from the user
4. **agenda-gap** — future meetings the user owns with no stated purpose
5. **other** — anything else a senior consultant would need to act on this week

## Suppression rules

The pass deliberately drops:
- Items already `done` / `dismissed`
- FYIs, newsletters, automated digests, status emails with no implied action
- Recurring meetings with established purpose (standups, syncs that already have agendas)
- Internal items older than 14 days (client items have no age limit)

`suppressed` makes the volume of that filtering visible. If it looks wrong, the raw
grid is one tab away — nothing is ever hidden, only deprioritised.

## Backend-injected fields

These are added by `synthesize.py` or `main.py` and are never written by the harvest loops.
They are safe to ignore by anything that reads `brief.json` directly.

| Field | Written by | Notes |
|---|---|---|
| `_source_signature` | `synthesize.py` | `[item_count, newest_item_mtime]` — the identity of the inbox when this brief was produced. Used by `_brief_is_stale()` in `main.py` to detect genuine staleness without a mtime comparison that misses deletions. |
| `_mtime` | `GET /api/brief` | File mtime of `brief.json` — added at read time, not stored. |
| `age_hours` | `GET /api/brief` | `(now - _mtime) / 3600`. |
| `stale` | `GET /api/brief` | `age_hours > 12` (time-based, advisory). |
| `stale_source` | `GET /api/brief` | True when `_source_signature` says the inbox changed since synthesis (and `CO_WORKER_AUTO_SYNTHESIZE` is on). The frontend auto-triggers a refresh when this is true. |
| `stale_reason` | `GET /api/brief` | Human-readable reason — "item count changed (209 -> 211)", "an item was rewritten after the last synthesis", "brief predates staleness tracking". |

## Operational notes

- **Atomic write.** `mkstemp` + `os.replace`, same as `.state.json`. A torn brief would
  break the landing view for every subsequent load.
- **`brief.json` is NOT an item.** It lives in `inbox/` beside the items and is explicitly
  excluded from the item glob in both `main.py` and `synthesize.py`. Forgetting that
  exclusion renders it as a phantom card.
- **One run at a time.** `synthesize_background()` holds a lock; a second request gets
  HTTP 409 rather than racing on the file.
- **Failure preserves the old brief.** If the model call fails, the previous `brief.json`
  stays in place and the age indicator goes red. Stale-but-present beats empty.
