# Analysis Playbook — From Bouquet Photo to Full Report

The procedure Claude follows when handed a bouquet photo. The goal is a report
that names every flower and tells you everything about it and the arrangement.

## Step 1 — Inventory the distinct flowers
Look at the photo and separate the arrangement into **distinct flower types**
(not individual stems). For each, work the visual key:

**Bloom-form key** (narrow the bucket first — see flower-index.md):
- **Cupped, many layered petals** → rose, ranunculus, peony, garden rose,
  lisianthus, camellia. Distinguish: rose has a tight spiraled center; peony is
  larger and looser with a fluffy center; ranunculus has thin, tightly stacked
  concentric petals and a green/dark button eye; lisianthus is smaller with a
  more open, rose-like face on wiry stems.
- **Ray petals around a central disc (daisy-form)** → gerbera (large, single
  disc), daisy (small white), sunflower (large, dark disc), aster (small, many
  thin rays), chrysanthemum (dense, disc often hidden), marigold (dense
  orange/yellow ruffle).
- **Trumpet / star, large** → lily (6 tepals, prominent stamens, often spotted
  throat), amaryllis (huge, hollow stem), alstroemeria (smaller, clustered,
  streaked/spotted throat).
- **Ruffled, fringed edges** → carnation (dense ruffle, notched petals),
  dianthus.
- **Tall vertical spike** → snapdragon (pouched florets), delphinium/larkspur
  (open florets up a stalk), gladiolus (large funnel florets), stock (dense,
  fragrant), liatris (fuzzy).
- **Tiny blooms in a cloud/cluster (filler)** → baby's breath (white specks),
  wax flower (tiny 5-petal), statice (papery clusters), solidago (yellow plume).
- **Distinct architectural silhouette** → orchid (bilateral, "face"), calla
  lily (single curled spathe), protea (spiky cone), bird of paradise (crane
  shape), anthurium (glossy heart + spike).

**Then record for each type:** color(s), size, petal texture, center, and any
foliage. Note your **confidence** — photos are ambiguous and cultivars vary.

## Step 2 — Identify greenery & fillers
Eucalyptus (round silver leaves / seeded), ruscus (glossy pointed), fern, salal,
ivy. Greenery choice signals style (silver-dollar eucalyptus → modern/organic).

## Step 3 — Pull each flower's profile (and compare against reference images)
For every identified flower, load `flowers/<name>.md` and extract: native
region, history, general + **color-specific** meaning, typical pairings,
seasonality, and cautions. If a flower isn't profiled yet, log it in
flower-index.md and profile it.

**Verify the ID against the reference library.** Each profiled flower has 4
licensed photos in `images/<slug>/` (embedded in its profile). Compare the
bouquet's bloom against them — especially for the classic look-alikes below —
before committing to an identification. This is the guard against confidently
mislabeling a ranunculus as a rose or an alstroemeria as a lily.

## Step 4 — Apply the cross-cutting references across the whole bouquet
- **color-symbolism.md** → resolve each flower's color meaning; read the whole
  palette; check for culture-flipping meanings.
- **occasions-and-events.md** → match the flower mix + palette + form to a
  likely occasion; check birth-month / anniversary hits.
- **bouquet-types.md** → name the arrangement form and each flower's design role.
- **floriography-and-history.md** → add the historical/coded meaning layer.

## Step 5 — Infer occasion, style & message
Synthesize: what is this bouquet *for*? Cross-check signals — e.g. all-white
lilies + chrysanthemums + gladioli in a standing form → sympathy; red roses +
baby's breath in a round ribbon-tied posy → romance/Valentine's; blush peonies
+ garden roses + ranunculus + eucalyptus, loose hand-tied → wedding.

## Step 6 — Write the report
Recommended structure (save to `analyses/<descriptor>.md`):
1. **At a glance** — one-paragraph read: likely occasion, style, overall message.
2. **Flowers identified** — table: flower · color · confidence · one-line meaning.
3. **Per-flower detail** — for each: native region, history, this-color meaning,
   typical pairings.
4. **Palette & symbolism** — what the color scheme says as a whole.
5. **Occasion & style** — arrangement form, design roles, inferred purpose.
6. **Cultural notes / cautions** — any meaning that flips by culture; anything to
   avoid.
7. **Confidence & caveats** — what's certain vs. a best guess; look-alike risks.

## Guardrails
- **State confidence.** Distinguish "this is a rose" from "likely a ranunculus,
  possibly a small peony."
- **Resolve color before meaning.** Never state a flower's meaning without its
  color.
- **Name the cultural frame.** Meaning is not universal; say whose meaning it is.
- **Watch classic look-alikes:** rose ↔ ranunculus ↔ peony ↔ garden rose;
  mum ↔ aster ↔ dahlia ↔ zinnia; lily ↔ alstroemeria ↔ amaryllis;
  gerbera ↔ daisy ↔ sunflower; carnation ↔ dianthus ↔ ruffled peony.
