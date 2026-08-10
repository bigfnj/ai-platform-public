# Bouquet Builder — Redesign Plan (two-step description flow) · HANDOFF

> **STATUS: SHIPPED 2026-07-31.** The two-step flow below is built, deployed, and
> hosted-E2E-verified (login → upload → identify → edit + add a line → guidance →
> Generate description → 720px card + Copy → History → delete-with-confirm; every
> bouquet endpoint 200). Backend split into `identify`/`generate`/`resolve` +
> `maintenance.py` weekly sweep; writer moved to `@chat-large`; 25 offline tests pass.
> Ongoing ideas (esp. improving vision identification) now live in `docs/BACKLOG.md`.

Pick-up doc for continuing the bouquet rail on another workstation / sandbox. This
describes the **next iteration**, built ON TOP of the already-working v1 (now done).

---

## 0. Where things stand (what's already built + deployed)

The rail exists, is wired into the platform, and passed a live hosted E2E. v1 is a
**one-shot** analyze (upload → identify + write report in a single call). This plan
reworks the Analyze flow into a **two-step, human-in-the-loop** pipeline. Nothing
below throws away v1 — it refactors the analyze path and adds UI.

Already in place (see `docs/PLATFORM_CONVERSION.md`):

- Backend `src/bouquet/`: `config.py`, `broker.py` (sync client, `chat` + `chat_json`),
  `kb.py` (loads 50 profiles, alias-resolves names → slugs, wires reference photos),
  `prompts.py` (vision prompt + `ANALYSIS_SYSTEM` + `FLORIST_SYSTEM` Frenchies persona),
  `analyze.py` (pipeline), `db.py` (single-tenant analyses), `api/app.py` (`create_api`).
- KB baked in at `seed/knowledge-base/` (read-only; `BOUQUET_KB_DIR`).
- Frontend `frontend/`: React module-federation remote, tabs Analyze / Library / History.
- Deploy: `deploy/Dockerfile`, compose service `bouquet` (port 8840, `bouquet_data` volume),
  gateway catalog/config + shell remote wiring.
- 16 offline tests pass; hosted Playwright E2E passed through Caddy → gateway → container.

**Two hard-won model gotchas — do NOT rediscover these:**
1. **Vision images must be ≤896px.** gemma3's encoder is natively 896×896; a larger image
   makes it return EMPTY content. `config.MAX_IMAGE_EDGE = 896`.
2. **Use loose `format="json"`, NOT a strict `format=<schema>`.** gemma3 + a JSON Schema +
   an image returns EMPTY; loose json mode with the shape in the prompt works. `broker.chat_json`.
   Also: guard an empty inventory or the writer confabulates a whole bouquet.

---

## 1. Decisions locked (with the user)

| Decision | Choice |
|---|---|
| Scope | Keep the expert **Analysis report** as a SECOND option alongside the new description flow. |
| Output | **On-screen card**: 720px image + description in a box + **Copy** button. History stores image + description together. |
| Writer model | **qwen3.6:27b** (`@chat-large`) for BOTH description and analysis. |
| Identify model | **gemma3:27b** (`@vision`) — robust first draft; the florist corrects it anyway. |
| Originals | **Do NOT keep full-res originals.** The 720px derivative is the only permanent image. |
| Cleanup | Weekly **Sunday 03:00 America/Los_Angeles**, in-process background task. |
| Delete | **Delete description** action on cards, behind a styled **"Are you sure?"** modal. |

The user accepts the model evict/reload for quality — it now lands during the human edit
pause, so it costs no perceived latency.

---

## 2. The reworked flow (Analyze tab)

The identify + edit steps are shared; at the end the florist picks the output.

1. **Upload a bouquet** → downscale to 896px → **identify** (`@vision`) → draft inventory.
   The full-res upload is saved to `uploads/pending/<token>.jpg`; the token is returned.
2. **Review & correct the flowers.** Each identified flower is an **editable line**:
   flower name (**type-ahead against the 50 KB flowers**, so a correction re-links to a
   profile) + colors, an **in-library ✓ / not-profiled** indicator, a **✕ remove**. A
   **"+ Add flower"** button appends a blank line. **Palette** and **arrangement/form**
   (pre-filled from vision) are editable too — they shape the copy.
3. **Guidance for Description** — a free-text box (e.g. *"For a wedding, tropical theme,
   keep it short."*). Optional. (Optional nicety: one-click chips like wedding / sympathy /
   anniversary that insert text; box stays free-form.)
4. **Generate.** Two buttons:
   - **Generate Description** (primary) → loads the writer (`@chat-large`), sends the
     *corrected* flowers (each resolved to its KB profile) + palette/arrangement + guidance
     + the reference lenses → Frenchies-voice description.
   - **Generate Analysis report** (secondary) → the deep expert report from the same
     corrected inventory.
   - Re-runnable: tweak flowers/guidance and generate again without re-uploading.
5. **Output card** (description): the **720px** image beside the **description in a box**
   with **Copy description** (clipboard). Saved to history.

---

## 3. Backend changes (`src/bouquet/`)

Split the single `POST /api/analyze` so the two model loads straddle the human edit:

- **`POST /api/identify`** (image, optional vision model) → run vision → return
  `{image_token, inventory}` where each flower carries its resolved slug + in-library flag.
  Saves the upload to `uploads/pending/<token>.jpg`. Does NOT persist a final row.
- **`POST /api/generate`** (`{image_token, inventory (edited), palette, arrangement,
  guidance, mode}`) → re-resolve edited flowers to KB profiles → build context → load the
  writer → generate → render the **720px** derivative (`uploads/<analysis_id>.jpg`), delete
  the pending full-res → persist (image, edited inventory, palette/arrangement, guidance,
  output, mode) → return it + the 720px url.
- **`GET /api/resolve?name=…`** → `{slug|null, title}` (reuse `kb.resolve`) so edited/added
  lines show the correct in-library status with the same alias logic. Autocomplete list =
  existing `GET /api/flowers`.
- **`analyze.py`**: keep `identify()`; add `generate(inventory, palette, arrangement,
  guidance, mode)`. Adapt `prompts.build_context` to take the EDITED inventory. Inject the
  guidance as a high-priority "Florist's direction for this description" block, bounded by
  the persona's factual + sensitivity rules (so "for a funeral" still triggers the grief
  handling). Keep the `_strip_md_fence` post-process.
- **`db.py`**: additive migration — add `guidance TEXT` (and `palette`/`arrangement` if not
  folded into the stored inventory JSON). Store the EDITED inventory. Only the 720px image
  is referenced; recompute `matched`/`unprofiled` from the edited inventory at generate.
- **Models/env**: `BOUQUET_VISION_MODEL=@vision`, new `BOUQUET_DESCRIPTION_MODEL=@chat-large`
  (reuse for analysis, or a separate `BOUQUET_ANALYSIS_MODEL=@chat-large`). One evict/reload
  per generate (vision → qwen3.6).

### Scheduled cleanup (in-process, matches job-aid's nightly loop pattern)

- A background asyncio task started in the FastAPI lifespan, TZ-aware: sleep until the next
  **Sunday 03:00 local**, run the sweep, reschedule (recompute next run on startup so a
  restart never double-runs/misses).
- Sweep deletes: (a) `uploads/pending/*` older than `BOUQUET_ORPHAN_MAX_AGE_HOURS` (default
  48h) — abandoned identifies; (b) defensively, any `uploads/<id>.jpg` with no DB row.
- Config: `BOUQUET_CLEANUP_ENABLED=1`, `BOUQUET_CLEANUP_DOW=6` (Sun), `BOUQUET_CLEANUP_HOUR=3`,
  `BOUQUET_ORPHAN_MAX_AGE_HOURS=48`. Set `TZ=America/Los_Angeles` on the compose service.
- Normal use self-cleans (generate deletes its own pending file); the weekly job only mops
  up abandoned sessions.

---

## 4. Frontend changes (`frontend/`)

- Rebuild the **Analyze** tab as a stepper: `Uploader → InventoryEditor → GuidanceBox →
  [Generate Description | Generate Analysis report] → output`.
- New components: `InventoryEditor` (editable lines + palette/arrangement), `FlowerLineInput`
  (type-ahead over the 50 flowers, via `GET /api/flowers`), `GuidanceBox`, `DescriptionCard`
  (720px image + description box + **Copy** via `navigator.clipboard`), and a reusable
  **`ConfirmDialog`** (styled "Are you sure?" modal).
- **Delete description**: on each history card AND the opened card. Click → `ConfirmDialog`
  ("Delete this description? This can't be undone." · Cancel / Delete) → `DELETE
  /api/analyses/{id}` (already removes the row + image) → refresh. Wire it to both the
  description cards and the Analysis-report cards.
- **History**: cards show the 720px thumbnail + mode + snippet. Opening a *description* shows
  `DescriptionCard`; an *analysis* shows the existing `ReportView`.
- **Library** tab: unchanged.
- Reuse the existing theming (contract-compliant, derives shared tokens; see `theme.css`).

---

## 5. Testing & deploy

- Extend the offline suite (fake broker): `identify` → editable inventory → `generate` for
  both modes; `resolve`; edited-inventory grounding; guidance reaches the writer; 720px
  derivative produced + pending deleted; delete removes row + image; the cleanup sweep
  (orphan pending deleted, live analysis image kept).
- Rebuild only the changed services (NEVER a bare `docker compose up` — it churns the WSL NAT
  and drops host internet). From `deploy/`, with docker on PATH
  (`$env:Path = "$env:ProgramFiles\Docker\Docker\resources\bin;$env:Path"`):
  - `npm run build` in `rails/bouquet/frontend` (and the shell if its remote list changed).
  - `docker compose up -d --build --no-deps bouquet`
  - `docker compose up -d --build --no-deps gateway` (recreates with new env/mounts + re-mounts remotes)
- Re-run the Playwright E2E adapted to the new flow: login admin → upload → edit a line +
  add one → guidance → Generate Description → card + Copy → delete w/ confirm → History.
  Playwright is in the DevToolbox venv; `*.localhost` is a secure context so the http:1111
  login works. admin pw is in `deploy/.env` (gitignored).

---

## 6. Pickup checklist (do this first on the other box)

1. `git pull` on `main`. The rail is at `rails/bouquet/`.
2. Read this file + `docs/PLATFORM_CONVERSION.md` + the platform `CLAUDE.md` (rails table,
   deploy runbook, the vision gotchas in the model section) + `CLAUDE.local.md` on that box
   (live container/ports, admin, broker).
3. `frontend/dist` and `node_modules` are gitignored — run `npm install` in
   `rails/bouquet/frontend` before building.
4. Backend tests: root `.venv` has fastapi/httpx/pytest; add `pillow` + `python-multipart`.
   From `rails/bouquet`: `PYTHONPATH=src python -m pytest -q`.
5. Broker must be reachable (`BOUQUET_BROKER_URL`, native :11500 on the GPU box). Models
   needed: `@vision` (gemma3:27b), `@chat-large` (qwen3.6:27b) — both already installed.
6. Build in the order in §5.

---

## 7. Not changing

The broker, the KB content (`seed/`), the Library tab, and the platform auth/gateway plumbing.
Single-tenant (owner-only, gated by the entitlement) stays; no per-user scoping.
