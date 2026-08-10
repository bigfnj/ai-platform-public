# Bouquet Builder — backlog

Ideas not yet built. The two-step description flow (see `../PLAN.md`) shipped
2026-07-31; this is what comes next.

## Improving flower identification, on an ongoing basis

The single biggest quality lever is the **vision identify step** (`@vision` →
gemma3:27b at 896px, loose JSON). The two-step redesign we just shipped quietly
created the mechanism to improve it continuously: **the human correction is a label.**
Every time the florist renames a bloom, removes a wrong one, or adds a missed one,
that edit is ground truth about what the model got wrong on a real photo. The plan
below turns that into a flywheel, cheapest/highest-leverage first.

### 1. Capture the correction signal (the flywheel's fuel) — ✅ SHIPPED 2026-07-31
Each analysis now stores the raw `vision_draft` alongside the corrected inventory, so
every florist edit is a labeled `(720px image, vision_draft, corrected_inventory,
guidance)` example. Implementation: `identify` stashes the draft as a JSON sidecar
beside the pending upload; `generate` reads it back and persists it (`db.vision_draft`,
additive migration verified on the live volume). Owner-only, single tenant — no
third-party data.
- NEXT: a loader in the eval harness that turns stored corrections into labels (the
  reference-photo path exists; corrected-analysis labels are still TODO).

### 2. An eval harness + accuracy metric — ✅ SHIPPED 2026-07-31 (v1)
`bouquet.eval_vision` scores the identify step against the KB reference photos (each
`images/<slug>/*.jpg` is a known flower). Broker-free scoring (`evaluate`/`summarize`)
is unit-tested; the CLI runs against the live broker:

    PYTHONPATH=src python -m bouquet.eval_vision --per-flower 1          # 50 photos, 1/flower
    PYTHONPATH=src python -m bouquet.eval_vision --model gemma3:27b --json   # A/B a model swap

Reports **recall** (found the expected flower), **mean extras** (false-positive types
per photo), and a **confusion map** (what a miss got called instead). First 8-sample
smoke: recall 0.625, e.g. amaranthus→aster, anemone/bells-of-ireland→nothing — exactly
the look-alike/miss signal to drive prompt + model tuning. Still TODO: color accuracy,
confidence calibration, and folding in the #1 corrected-analysis labels.

### 3. Tuning results (2026-07-31): recall 0.83 → 0.855

Full 200-photo baseline `@vision`=gemma3:27b. Worked the misses in order:

1. **Resolver** — `fern`→leatherleaf-fern alias: 0.81 → 0.83 (free, offline re-score).
2. **Reference photos** — replaced 5 wide/scene shots with clean close-ups (magnolia,
   amaranthus, wax-flower): the biggest structural win, magnolia 2/4 → 4/4.
3. **Prompt** — added the missing **scabiosa** (pincushion) to the key: a clean, durable
   **0/4 → 4/4** across three runs. That's the only prompt change that stuck.

**What did NOT work — and the lesson:** hand-written *look-alike exemplars* (gerbera/
chrysanthemum, veronica/delphinium, an anemone/ranunculus block, statice/wax-flower)
were **whack-a-mole**: each cue that fixed its target perturbed a neighbour (an anemone
cue fixed anemone but flipped `ranunculus→anemone`; a chrysanthemum-pompom cue caused
`zinnia→chrysanthemum`), for a **net wash**. The eval also exposed a **run-to-run noise
floor of ~±1–2 hits/flower** at temp 0.2, so aggregate recall moves under ~3 points
aren't trustworthy — judge by *repeatable per-flower* signal, not the headline number.
Net: adding a genuinely-missing vocabulary item (scabiosa) helps; nudging the model on
pairs it already knows-but-confuses does not. The eval artifacts are `eval/baseline-*`
(initial 0.83) and `eval/final-*` (0.855, shipped).

- **Next real lever = Model A/B via `roles.json`** (hot-swap, no restart): the look-alike
  errors are a model-capability ceiling, so a stronger local VLM (qwen2.5-VL,
  llama3.2-vision, newer gemma) is more promising than more prompt-poking. Note the
  **896px cap is gemma3-specific** — a different encoder may allow higher resolution,
  which directly helps the small filler blooms.

### 4. Architectural accuracy levers (more work, bigger ceiling)
- **Retrieval-grounded identify — ✅ SHIPPED 2026-08-02.** Recall **0.855 → ~0.91** on
  the reference eval (leave-one-out prototype), fixing the out-of-vocabulary look-alikes
  nothing else could: **ruscus 0/4 → 4/4**, **spirea 1/4 → 4/4**, freesia 1/4 → 3/4.
  Live end-to-end (verified: a ruscus photo that used to read "holly" now reads "Ruscus").
  Architecture: a generic **broker** `POST /v1/embed_image` (SigLIP, CPU-only in the
  media worker, *un-gated* so it never evicts the resident vision model) + a **rail-side**
  `retrieval.py` that numpy-nearest-neighbours the upload's embedding against a baked
  200-vector index (`seed/.../reference-index.npz`, ~630 KB) and injects a short candidate
  list into the vision prompt. Best-effort (any failure → ungrounded); toggle
  `BOUQUET_GROUNDING`; index rebuilt by `tools/build_reference_index.py`. The rail stays
  torch-free (only numpy); the embedder lives in the broker per the platform principle.
  - **Known tradeoff / next tune:** grounding roughly doubles `mean_extras` (the model
    lists shortlist items it half-sees). The two-step review flow absorbs it (the florist
    prunes the draft — removing an extra is easier than adding a missed flower), and it's
    tunable via `BOUQUET_GROUNDING_MAX` (shortlist length). Worth an A/B of max=3 vs 5.
  - **Superseded proxy test below** (kept for the reasoning):

  A cheap proxy test
  (2026-08-02) put the *whole* 50-flower vocabulary in the identify prompt and told the
  model to prefer it. It fixed exactly the out-of-vocab errors — **ruscus 0→3** (stopped
  saying "holly"/"salal"), **amaranthus 2→4** — proving that grounding to our known set
  helps. But dumping all 50 names made the model **over-call** (`mean_extras` 0.29 →
  0.455) and collapsed a few flowers (**stock 3→0**), for a net wash — so it was reverted.
  The lesson: grounding works, but it must be **surgical**. Build the real version: embed
  the uploaded bouquet (or crops) with a CLIP-class image embedder, nearest-neighbour
  against the 200 licensed KB reference photos, and feed the VLM a **short per-image
  shortlist** ("this looks like ruscus / eucalyptus / salal — which, or none?") instead of
  the full list. Needs a CLIP/SigLIP image embedder added to the broker/media stack, plus
  the 200 references embedded once. This is the **most promising remaining lever**.
- **Tile / two-pass identify.** A single 896px downscale loses small filler (baby's
  breath, wax flower, statice). Do a coarse whole-bouquet pass, then targeted crops of
  dense/low-confidence regions, and merge. **Caveat:** the current eval is single-flower
  reference photos, so tiling (which helps *multi-flower* bouquets) can't be measured on
  it — build a small multi-flower labeled set first, or land backlog #1's corrected-
  analysis labels, so the gain is verifiable rather than blind.
- **Deterministic color cross-check.** k-means the palette from the image and reconcile
  it with the VLM's color claims; flag likely-dyed blooms more reliably.

### 5. Close the loop back into the KB (active learning)
Track **frequently-unprofiled** names (the `unprofiled` list, aggregated) and
**frequently-corrected** flowers. A name that keeps showing up but has no profile is a
signal to author a new KB flower (the rail's intended growth path); a flower that's
repeatedly mis-identified is a signal for a better reference photo or a prompt note.

### 6. Fine-tune (longest horizon)
Once #1 has accumulated enough corrected pairs, a local LoRA on the corrected
inventories is on the table. Only worth it after the harness (#2) proves the cheaper
levers have plateaued.

**Status:** #1–#3 are shipped (current recall **0.855**, gemma3:27b). The durable gains
were structural (a missing `fern` alias, adding `scabiosa` to the key, replacing weak
reference photos); the look-alike prompt exemplars and a model A/B both washed out.
Remaining, ordered by where the eval shows misses: **#4** (tile / retrieval-grounded
identify / color cross-check) → **#5** (active learning) → **#6** (fine-tune). #4 is the
next real lever now that prompt + model levers are spent.

## Bouquet Recommendation Engine

The rail runs **photo → report** today. The inverse — **intent → bouquet** — is a
natural, high-value feature that reuses the KB we already built, with almost no new
knowledge work.

- **Input (a form or free text):** occasion (wedding / sympathy / anniversary / birthday /
  apology / get-well / thank-you), recipient + relationship, palette preference, season,
  budget/size, and a vibe. Occasion + relationship + a grief/sympathy flag drive the same
  sensitivity rules the writer persona already enforces.
- **Selection, grounded in the KB (no confabulation):** pick flowers by cross-referencing
  the profiles' **Occasions & events**, **Symbolism & meaning** (color-resolved via
  `reference/color-symbolism.md`), **Typical pairings**, and **Seasonality**, and assign
  each a design role (focal / line / form / filler / greenery) from
  `reference/bouquet-types.md`. Everything is chosen from the 50 profiled flowers, so the
  recommendation is always defensible — the same grounding discipline as the report path.
- **Output:** a recommended arrangement — focal + supporting + filler + greenery with a
  one-line rationale per pick (why this flower, this colour, for this occasion), plus the
  Frenchies-voice description of the finished bouquet. Optionally: 2–3 alternates, a
  seasonal-substitution note, and (later) a generated mockup image via the broker's image
  pipeline.
- **Shape:** a `POST /api/recommend` (intent → KB-grounded selection → `@chat-large` writes
  the rationale + copy), a new **Recommend** tab beside Analyze/Library/History, and its
  own saved-history rows. Reuses the broker, the writer, the persona, and the entitlement
  gate — no broker or auth changes.
- **Why it's cheap:** the hard part (a florist-grade KB with meanings, pairings,
  occasions, seasonality, and design roles) already exists; this is mostly a
  selection-and-prompt layer over it. A good "flagship" companion to the analyze flow.

## Smaller items
- One-click guidance chips already insert a starter phrase; consider occasion presets
  that also nudge palette/arrangement defaults.
- History filter/search by mode (description vs analysis) once the library grows.
- Surface the weekly-sweep result (counts) somewhere admin-visible.
