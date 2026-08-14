# Recipe Book — Backlog

Known bugs / follow-ups. Newest first.

## DONE 2026-08-14 — surface only fully-makeable recipes + fix ingredient matching
Pantry match now separates **fully-makeable** recipes from **one-away** (sorted makeable-first,
then by fewest missing ingredients), so the grid leads with what you can cook right now. Fixed
generic-vs-specific ingredient matching in `catalog.py` (`ingredient_is_covered` +
`ingredient_words_generic`): a generic staple no longer masks a missing specific ingredient, and
optional ingredients don't make a recipe count as unmakeable. Covered by `tests/test_pantry_match.py`.

## DONE 2026-08-06 — allow editing the recipe title when editing a recipe
The admin edit flow now edits the title alongside content/category/attributes. `PUT
/api/recipes/{id}/title` (behind `require_admin`) stores a **title override** (new
`title_overrides` table) that `ingest` re-applies on every rebuild, and updates the live row.
The recipe `id` is path-derived (not title-derived), so a rename keeps the stable `id`, file
path, and all favorites / ratings / planner links — only the displayed title changes (`rel_path`
is intentionally left alone). Wired into `RecipeModal.tsx` (the title becomes an input in edit
mode; `saveEdit` calls `api.editTitle` when it changed). Covered by `tests/test_title_edit.py`
(override survives a full rebuild + id stays stable) and the permission gate in
`tests/test_permissions.py`.

## DONE 2026-07-28 — admin-editable meal-plan settings (retention + recency)
A gear in the recipe-book header (top-right) opens a **Meal-plan settings** modal to edit
history retention and the AI recency window at runtime. Gear + modal show only for
admin/super-admin (the gateway forwards a trusted `x-platform-admin` header; `GET /api/settings`
reports `is_admin`, `PUT /api/settings` re-checks it → 403 otherwise). Values persist in a new
`app_settings` kv table and override the env defaults; `recipe_book.settings` resolves the
effective value (DB → env → hardcoded), read by `planner_ai` and `maintenance`. Env vars still
work as the default when nothing is saved.

## DONE 2026-07-27 — beverage pairing when manually adding a dinner
Dinner cards in the planner now show a 🍷 button → a small **Cocktail / Wine** menu that
suggests a pairing and drops it into that day's `drink` slot. New endpoint
`POST /api/planner/pair {date, ptype, exclude_ids}` reuses `planner_ai.swap(date,"dinner",…)`
(a real Bar cocktail when the model names one, else a titled wine/cocktail card) and commits
the pick. Frontend: `pairDrink` in `PlannerView.tsx` + `api.pairDrink`.

Possible follow-ups: a **mocktail / non-alcoholic** option (the cocktail pool currently
excludes `Non-Alcoholic`); a re-roll on the drink card that passes the current pick as
`exclude_ids` so it varies; and dedupe if a drink already exists that day.

## DONE 2026-07-28 — rolling plan resolved (absolute dates + nightly purge + recency)
Decided the plan stays **absolute-date**: a meal is pinned to its date and nothing is ever
rewritten. The calendar "rolls" on its own — as time advances, "This week" simply points at
the new current week. No shifting logic (that was the corrupting `_roll_forward`, reverted in
`ca48aac`). Two supporting pieces added:
- **Nightly purge** (`maintenance.py`, scheduled from the `api/app.py` lifespan): once a day
  it deletes plan entries older than `PLAN_RETENTION_DAYS` (~6 months). Never touches tray
  (empty-date) entries; never runs on a read. Recent past stays browsable via ‹ Prev.
- **Recency-aware suggestions** (`planner_ai.py`): the AI planner drops meals planned within
  `PLAN_RECENCY_DAYS` (~6 weeks — recent past *or* already scheduled ahead) from its candidate
  pools, so the same dinners stop resurfacing. Falls back to the full pool if excluding would
  leave too few candidates.

Both windows are env-overridable (`RECIPE_BOOK_PLAN_RETENTION_DAYS` / `RECIPE_BOOK_PLAN_RECENCY_DAYS`).

## FIXED 2026-07-27 — plan scrambled on drag; entries "blanked"; a snack was lost
Dragging cards on desktop blanked the plan and moved everything onto weekdays; an entry
flagged as a snack disappeared from view. Two root causes, both fixed in `ca48aac`:
1. the read-side `_roll_forward` above (destructive on GET); and
2. `weekDates()`/`TODAY` in `PlannerView.tsx` + `ShoppingView.tsx` built date strings with
   `toISOString().slice(0,10)` (UTC) from local-time `Date`s — west of UTC an evening date
   rolled forward a day, so a cell's date string disagreed with its weekday label and the
   stored value; cards failed `e.date === d` and vanished, drops saved the wrong day, and
   the shopping range desynced from the grid. Now formatted from local y/m/d.

Data note: two duplicate clones the corruption left behind (Teriyaki + Caprese on 08-03)
were removed after confirming with the owner; the original Caprese snack was intact. Dates
that were overwritten in place could not be reconstructed (backup was of the corrupted
state), so a few cards may need re-dragging to their intended days — now safe.
