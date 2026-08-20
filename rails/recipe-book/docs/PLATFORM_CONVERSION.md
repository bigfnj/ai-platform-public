# Recipe Book — Platform Rail Build Ledger

**STATUS: DEPLOYED LIVE 2026-07-25 (P0–P4 done).** Running as container
`platform-recipe-book-1` (port 8830) on the platform; gateway serves the remote at
`/recipe-book/`, catalog 835 (721 meals + 114 cocktails, 24 categories, 11 spirits).
Icons: full 835 SDXL batch generated server-side into the `recipe_book_data` volume.
Repo is **local-only** (not pushed) per the owner. Semantic search = the one fast-follow.

Fresh-built rail (not a port). Reuses the **835-card markdown corpus** and mines
the **deleted recipe-book** app's backend logic from platform git history
(`git show b80e871^:apps/recipe-book/...`), but the plumbing and the entire UI
are new, aligned to the current rail conventions (finance is the reference).

## Locked decisions (2026-07-25)
- **Supersede the old recipe-book.** The prior app was built then *deleted* from
  the platform on 2026-07-21 (commit `b80e871`); it predates module federation and
  is not live. This is a clean-slate rebuild — nothing to retire at runtime.
- **Name:** Recipe Book. **Rail id:** `recipe-book`. **Icon:** 🍳.
- **Beverages are first-class:** a co-equal **Bar** half (114 cocktails is the
  largest category), with a saved **bar inventory** + "what can I pour now" match.
- **Design:** editorial / cookbook aesthetic (serif, warm cream Kitchen, dark-gold
  Bar) **+ app affordances** (checkbox ingredients, inventory ring, action buttons).
  Approved from two rendered mockups → see `docs/design/`.
- **Per-recipe icon:** local **SDXL** generates a small clipart glyph per card
  (martini glass, wok, …), cached to `data/icons/<id>.png`, shown beside the title.
- **DB-as-source-of-truth**, **broker-buffered AI**, **module-federation remote**,
  entitlement-gated (owner + admin `admin`).

## Addressing / ports
- Backend **8830** (next in the 88x0 rail sequence: edu 8800 · job-aid 8810 ·
  finance 8820 · **recipe-book 8830**). Vite dev **5220**. Gateway path `/recipe-book/`.
- Container service `recipe-book:8830`; named volume `recipe_book_data:/srv/var`.

## Reuse map (from `b80e871^:apps/recipe-book/`)
Recovered to a scratch reference. Adapt, don't copy wholesale:
- `catalog.py` — markdown parser + search + pantry-match + shopping-list aggregation.
- `models.py` — User / Favorite / Rating / Tag / RecipeTag / MealPlanEntry / PantryItem / ShoppingCheck.
- `semantic.py` — bge-m3 embeddings via broker, cosine rank in pure Python.
- routers `recipes / planner / pantry / personalization / assistant`.
New surface to build: the **Bar** domain (base-spirit parse, glassware, technique,
bar inventory + match) and the **icon** pipeline, plus all views/design.

## Phases
- **P0 — Scaffold** ✅ repo skeleton (pyproject, package, config, broker facade,
  create_api health, Dockerfile, this ledger, design mockups).
- **P1 — Backend:** SQLite (WAL, one-conn/req); ingest the 835 `.md` → recipes
  table + parsed sections + bar enrichment; routers (recipes/planner/pantry/bar/
  personalization/assistant); semantic index via broker; import/rebuild + health;
  SDXL icon batch (broker image endpoint) cached to `icons/`.
- **P2 — Frontend:** federation remote (`base:/recipe-book/`, `exposes ./module`,
  adopts shell theme); the approved design; Kitchen + Bar + Plan + Shopping +
  Pantry/Bar-inventory + Assistant; per-recipe icons; buffered-AI UX.
- **P3 — Platform wiring:** catalog (`ready`), gateway `config.py`
  (`app_recipe_book_url` 8830 / `enabled_apps` / dist), shell vite remote +
  `remotes.d.ts` + `App.tsx` branch. Entitlement is automatic via the access gate.
- **P4 — Containerize + deploy:** compose service + gateway `depends_on` +
  read-only dist bind-mount + `recipe_book_data` named volume; seed corpus →
  rebuild → DB; `docker compose up -d --build recipe-book gateway`; E2E via gateway.
- **P5 — Docs/memory:** finalize this ledger, correct the stale recipe-book memory,
  add the rail memory, commit + push.

## Conventions to hold (from finance/job-aid)
- **Named volume, never a Windows bind mount** for SQLite (WAL corrupts on drvfs/9p).
- Frontend **dists host-built + mounted read-only**; gateway serves `remoteEntry.js`
  / `index.html` `no-cache`, so a redeploy shows on a normal refresh.
- **All AI through the broker, buffered** — no direct Ollama, no SSE to the browser.
- Deploy only the changed service: `docker compose up -d --build recipe-book`
  (never a full-stack `up`); restart the gateway after a shell dist change.

## FLUX icons (2026-07-27) — icons upgraded SDXL-Turbo → FLUX.1-schnell
Per-recipe icons now render with **FLUX.1-schnell (nf4)** instead of SDXL-Turbo — much better prompt
adherence (real ribs/ramen/martini vs SDXL blobs). What changed:
- `recipe_book/broker.py`: `ICON_MODEL` (env `RECIPE_BOOK_ICON_MODEL`, default `flux-schnell`), threaded
  into `generate_images(..., model=...)` → broker `/v1/image` `model` field.
- `recipe_book/icons.py`: default `size` 512→768; subject map tuned so **0 recipes fall to the generic
  "plated dish"** — added `_CATEGORY_SUBJECT` fallbacks (Smoothies/Entrees/Blue Apron/Marley and Spoon/
  Dinnerly/Thai/To Try), ~40 keywords, and a `_TITLE_SUBJECT` exact-title override dict (17 user-reviewed
  one-offs). Coverage: 761 keyword-specific / 133 category / 0 generic.
- The GPU side (broker `model` routing, `edu_media_core.images.get_flux`, the media CUDA venv) is in the
  broker + edu-suite; the broker service was repointed to a new media venv (`D:\.ai-work\venvs\media-venv`) and
  HF cache (`D:\.ai-work\cache\hf-cache`) after the monorepo cutover deleted the old edu-suite venv.

**Re-running a full regen** (all 894): `docker exec -e RECIPE_BOOK_BROKER_TIMEOUT=1600
platform-recipe-book-1 python /tmp/full_regen.py` (chunk 250). **The `-e` timeout override is required** —
the client default is 600s and a big chunk (cold FLUX load + 250 renders ≈ 600-1000s) overruns it, which
silently marks the whole chunk failed. Keep a chunk's total under the broker's 1200s `media_timeout`. Each
`/v1/image` call spawns a fresh worker that loads FLUX once (~200s) then exits to reclaim VRAM, so bigger
chunks amortize the load. Last full run: 894 made / 0 failed in 46.5 min. New recipes auto-get a FLUX icon
on save. New icons overwrite `<id>.png` in the `recipe_book_data` volume → a normal refresh shows them.
