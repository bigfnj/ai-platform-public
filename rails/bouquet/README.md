# Bouquet Builder 💐 — platform rail

Hand it a bouquet photo, get back a full report: every flower identified, with its
history, meaning, color symbolism, native region, typical pairings, and the
occasion the arrangement implies — or the same knowledge rewritten as polished
florist copy.

## How it works

Two broker round-trips **straddling a human edit** (all GPU work goes through the
platform broker, never Ollama directly). Splitting the two model loads around the
review means the vision→writer evict/reload lands during the edit pause, so it costs
no perceived latency.

1. **Identify** (`POST /api/identify`) — the uploaded photo is downscaled to 896px and
   sent to the broker's vision model (`@vision` → gemma3:27b) in loose JSON mode,
   returning a structured **draft inventory** (distinct flower types + colors +
   confidence, greenery, palette, arrangement). The full-res upload is parked under
   `uploads/pending/<token>`; no analysis is saved yet.
2. **Review & correct** — the florist edits the draft: rename a flower (type-ahead over
   the 50 KB flowers, alias-aware `GET /api/resolve` re-links it to a profile), fix
   colors, remove a wrong bloom, `+ Add flower` a missed one, tweak palette/arrangement,
   and optionally add free-text **guidance** ("for a wedding, keep it short").
3. **Generate** (`POST /api/generate`) — each *corrected* flower is resolved to its KB
   profile; the profiles + the cross-cutting references (color symbolism, occasions,
   arrangement types, floriography) + the guidance go to the writer (`@chat-large` →
   qwen3.6:27b), which writes in one of two voices:
   - **Description** — Frenchies Flowers customer copy (a description paragraph + a fun
     fact), shown on a card beside the 720px image with a **Copy** button.
   - **Analysis** — an expert breakdown (at-a-glance, per-flower detail, palette,
     occasion, cultural notes, confidence).

   A permanent **720px derivative** is rendered and the full-res original deleted (no
   originals kept). Re-runnable: tweak and regenerate without re-uploading. A weekly
   in-process sweep (Sunday 03:00 America/Los_Angeles) mops up abandoned pending uploads.

**Long calls are polled jobs.** A cold 27B model load can take longer than
Cloudflare's ~100s edge timeout, which would 524 the public URL if a single request
were held open for it. So `identify` and `generate` return a `job_id` immediately and
the browser polls `GET /api/jobs/{id}` (each request stays short); the work runs in a
background thread (`bouquet.jobs`). Identify also pre-warms the writer model so the
vision→writer swap overlaps the review pause (`BOUQUET_WARM_WRITER`, default on).

## Layout

```
bouquet/
├── src/bouquet/
│   ├── config.py     env-driven paths + model roles
│   ├── broker.py     thin sync client to the platform broker (chat + vision)
│   ├── kb.py         load/index the flower profiles + references + image manifest
│   ├── prompts.py    the vision schema + the two report system prompts
│   ├── analyze.py    identify + generate steps + image helpers (fake-broker testable)
│   ├── db.py         SQLite store for saved analyses (single-tenant)
│   ├── jobs.py       in-process job store for the polled identify/generate calls
│   ├── maintenance.py  weekly sweep of abandoned pending uploads
│   ├── eval_vision.py  offline vision-accuracy harness (see docs/vision-eval.md)
│   └── api/app.py    the FastAPI surface (identify/generate/resolve/jobs + create_api factory)
├── seed/knowledge-base/   the flower KB (50 profiles, 4 references, 200 licensed photos)
├── eval/             committed eval baselines (recall + confusion, tracked over time)
├── deploy/Dockerfile
└── frontend/         React module-federation remote (the shell loads /bouquet/module)
```

The knowledge base is read-only reference data baked into the image; only the
analyses DB and uploaded photos are mutable (the `/srv/var` volume). Single-tenant,
owner-only: access is gated by the platform entitlement.

## Vision accuracy

The identify step is measured offline by `bouquet.eval_vision` against the 50 KB
reference photos (each a known single flower) — reporting recall, mean false-positive
types, and a confusion map so changes can be judged, not guessed. Runbook:
`docs/vision-eval.md`; full tuning history in `docs/BACKLOG.md`.

**Retrieval-grounding** (`retrieval.py`) is the biggest lever: identify embeds the
uploaded photo with the broker's SigLIP encoder (`POST /v1/embed_image`, CPU-side, never
evicts the GPU model), nearest-neighbours it against a baked 200-vector index of the KB
reference photos (`seed/knowledge-base/images/reference-index.npz`), and injects the
resulting short candidate list into the vision prompt. It fixes the out-of-vocabulary
confusions that prompt/model tuning could not (ruscus→"holly", statice→"solidago"):
**recall 0.855 → ~0.91** on the reference eval. Best-effort — any failure falls back to
ungrounded identify. Toggle `BOUQUET_GROUNDING`; regenerate the index with
`tools/build_reference_index.py` when reference photos change.

## Run standalone (dev)

```bash
pip install -e .
# broker on :11500 (or set BOUQUET_BROKER_URL)
uvicorn --factory bouquet.api:create_api --port 8840
```

The KB is extracted from the standalone `bouquet-builder` authoring repo (the
flower profiles, references, and licensed reference photos); the Frenchies Flowers
persona lives in `prompts.py` so the rail is self-contained.
