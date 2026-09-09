# meeting-atlas backlog

Rail-local, deferred work. Cross-cutting platform items live in the repo's `docs/BACKLOG.md`.

This rail is deliberately read-only over a mounted recordings tree: no broker client, no model
slots, no persistence. Several things that look like gaps against other rails are not — see
"Not gaps" at the bottom before proposing to add them.

## Open

- **Indirect reindex on read.** `POST /api/reindex` is admin-only, but `_maybe_refresh()` still
  fires on `GET /api/meetings` and `/api/healthz`, throttled to
  `MEETING_ATLAS_AUTOREINDEX_SECONDS` (default 300) and only when the mount fingerprint changed.
  Concurrent rebuilds are now serialised by `_build_lock`, so the cost is bounded, but any
  identified reader can still cause a walk. Fine at this size; revisit if the tree gets large.
- **DONE (2026-08-24): deskpet quip bank.** 111 quips, registered in `deskpet/lines.ts`. The
  same pass covered co-worker, gemini-cx and smb-partner-enablement, so RC024 is clean across
  all fourteen rails.
- **DONE: one operator variable for the Meetily database.** `MEETING_ATLAS_MEETILY_DB_MOUNT`
  now points at the database FILE and the container-side env is derived from it with compose's
  `:+` substitution. Setting one without the other is no longer possible, which was the whole
  failure mode.
- **`indexer.py` coverage beyond the first pass.** The citation verifier, timezone
  reconciliation and week bucketing are pinned. Not yet covered: the Meetily SQLite read path
  (needs a fixture database) and the keyword/IDF ranking beyond a smoke assertion.

## Not gaps — verified, do not "fix"

- No `broker.py`, no `modelstate.py`, no `/api/capabilities`, no `ModelChips`. The rail runs no
  inference: `backend/pyproject.toml` declares no HTTP client at all, and every model-derived
  field arrives as a JSON sidecar on disk. `status_route: null` and `model_slots: []` are
  therefore correct, unlike the eight rails whose manifests said the same while serving chips.
- No `BROKER_AUTH_TOKEN` in compose. Handing a platform-wide shared secret to a container that
  only reads a read-only mount would be strictly negative.
- `/api/healthz` rather than `/api/health`. The repo splits by layout, and all four
  `layout: backend` rails use `healthz`.
