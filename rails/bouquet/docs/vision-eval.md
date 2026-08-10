# Vision identification eval — runbook

How we measure and improve the identify step (`@vision` → gemma3:27b) over time. The
harness is `bouquet.eval_vision`; run everything from `rails/bouquet` with the broker
reachable (`BOUQUET_BROKER_URL`, native :11500 on the GPU box).

## Two modes

- **reference** (needs the GPU): re-runs the model on the KB reference photos — each
  `seed/knowledge-base/images/<slug>/*.jpg` is a known single flower, so the label is
  the slug. This is the baseline, the regression check, and the model-A/B tool.
- **corrections** (no GPU): scores the *already stored* `vision_draft` against the
  florist's corrected inventory for each saved analysis (the labeled data captured on
  every real run). Reads the DB; grows more meaningful as bouquets are analyzed.

## Metrics

- reference: **recall** (fraction of photos where the expected flower was identified),
  **mean extras** (avg false-positive flower types per photo), and a **confusion map**
  (for a miss, what it was called instead) — the actionable "what to fix" list.
- corrections: micro **precision / recall / F1** of the raw drafts vs the corrections.

## Run the baseline (do this when the GPU is free)

All ~200 reference photos (4 per flower), saved as a durable artifact:

```
PYTHONPATH=src python -m bouquet.eval_vision --per-flower 0 --out eval/baseline-YYYY-MM-DD.json
```

~200 vision calls; ~10-20 min once the model is warm. Progress prints to stderr; a
transient broker 500 is retried 3× so one blip doesn't abort the run. A quicker
smoke is `--per-flower 1` (50 photos) or `--limit N`.

## A/B a model swap

`@vision` is hot-swappable via `services/broker/roles.json` (no restart). To compare a
candidate against the baseline without touching the role, override per-run:

```
PYTHONPATH=src python -m bouquet.eval_vision --per-flower 0 --model <candidate> \
    --out eval/<candidate>-YYYY-MM-DD.json
```

Then diff the two `summary` blocks (recall, mean_extras, confusion). Only repoint the
role if the candidate wins.

## Corrections (in-container, against live data)

```
docker exec platform-bouquet-1 python -m bouquet.eval_vision --mode corrections
```

Empty until analyses accumulate a `vision_draft` (backlog #1). Over time this is the
truest signal: how often the model matched what the florist actually kept.

## Artifacts

Baselines and A/B results are written under `rails/bouquet/eval/` and committed, so the
recall trend + confusion history are tracked alongside the code.
