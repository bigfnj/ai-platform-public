"""Vision identification eval harness.

Scores the identify step against a labeled set so prompt tweaks and ``@vision`` model
swaps can be *measured* instead of guessed (backlog #2). Two modes:

- ``reference`` (default): re-run the model on the KB reference photos — each
  ``images/<slug>/*.jpg`` is a known single flower, so the label is the slug. Needs
  the broker. This is the baseline / regression + model-A/B tool.
- ``corrections``: score the *already stored* ``vision_draft`` against the florist's
  corrected inventory for each saved analysis (backlog #1's captured data). No broker —
  it reads the DB — so it measures real-bouquet accuracy over time as usage accrues.

The scoring functions are broker-free and unit-tested; only ``_default_identify`` and
the ``reference`` run touch the model. Usage (from ``rails/bouquet``):

    PYTHONPATH=src python -m bouquet.eval_vision --per-flower 0 --out eval/baseline.json
    PYTHONPATH=src python -m bouquet.eval_vision --model gemma3:27b        # A/B a swap
    PYTHONPATH=src python -m bouquet.eval_vision --mode corrections        # DB-backed, no GPU
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from bouquet import analyze as analyze_mod
from bouquet import broker, config, db, kb


# --- shared: turn free-text flower names into comparable keys ---------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _keys(names: list[str]) -> list[str]:
    """A comparable key per flower name: its KB slug if profiled (alias-robust), else
    the normalized raw name — so unprofiled flowers still count, de-duped, order kept."""
    out: list[str] = []
    for n in names:
        key = kb.resolve(n) or _norm(n)
        if key and key not in out:
            out.append(key)
    return out


def _all_names(inv: dict) -> list[str]:
    """Every identified name — flowers AND greenery. Foliage (eucalyptus, ruscus, fern)
    is profiled in the KB but the model reports it in the separate ``greenery`` list, so
    scoring only ``flowers`` would count a correctly-identified leaf as a miss."""
    names = [(f.get("name") or "").strip() for f in inv.get("flowers", [])]
    names += [g.strip() for g in inv.get("greenery", []) if isinstance(g, str)]
    return [n for n in names if n]


# --- reference-photo mode (re-runs the model) -------------------------------

@dataclass
class Sample:
    expected: str        # the KB slug the photo is labeled with
    path: Path


@dataclass
class Result:
    expected: str
    names: list[str]              # raw flower names the model returned
    resolved: list[str]           # those names as KB slugs (de-duped)
    hit: bool                     # was the expected slug found
    extras: list[str] = field(default_factory=list)   # resolved slugs other than expected
    error: str | None = None      # set if identify failed for this sample


def load_reference_samples(per_flower: int | None = None) -> list[Sample]:
    """Labeled samples from the KB reference photos (``images/<slug>/*.jpg`` -> ``slug``).
    ``per_flower`` caps photos per flower (None/0 = all)."""
    root = config.KB_DIR / "images"
    samples: list[Sample] = []
    if not root.is_dir():
        return samples
    for slug in sorted(p.name for p in root.iterdir() if p.is_dir()):
        if kb.get_flower(slug) is None:
            continue
        imgs = sorted((root / slug).glob("*.jpg"))
        chosen = imgs[:per_flower] if per_flower else imgs
        samples.extend(Sample(expected=slug, path=p) for p in chosen)
    return samples


def _default_identify(path: Path, attempts: int = 3, backoff: float = 3.0) -> dict:
    """Run the real vision pipeline on one image, retrying a transient broker error
    (a busy/loading model can 500 briefly — don't let one blip abort a long run)."""
    data = path.read_bytes()
    for attempt in range(1, attempts + 1):
        try:
            return analyze_mod.identify(analyze_mod.prepare_image(data))
        except broker.BrokerError:
            if attempt == attempts:
                raise
            time.sleep(backoff)
    return {"flowers": []}  # unreachable


def evaluate(samples: list[Sample], identify_fn=None, on_progress=None) -> list[Result]:
    identify_fn = identify_fn or _default_identify
    results: list[Result] = []
    for i, s in enumerate(samples, 1):
        error = None
        try:
            inv = identify_fn(s.path)
        except Exception as exc:  # noqa: BLE001 — record + continue; one failure != lost run
            inv, error = {"flowers": []}, str(exc)
        names = _all_names(inv)
        resolved: list[str] = []
        for n in names:
            slug = kb.resolve(n)
            if slug and slug not in resolved:
                resolved.append(slug)
        hit = (error is None) and (s.expected in resolved)
        extras = [r for r in resolved if r != s.expected]
        r = Result(s.expected, names, resolved, hit, extras, error)
        results.append(r)
        if on_progress:
            on_progress(i, len(samples), r)
    return results


def summarize(results: list[Result]) -> dict:
    """Top-line accuracy (over non-errored samples) + a confusion map."""
    n = len(results)
    scored = [r for r in results if r.error is None]
    errors = n - len(scored)
    hits = sum(1 for r in scored if r.hit)
    extra_total = sum(len(r.extras) for r in scored)
    confusion: dict[str, dict[str, int]] = {}
    for r in scored:
        if not r.hit:
            got = r.extras[0] if r.extras else (r.resolved[0] if r.resolved else "(nothing)")
            confusion.setdefault(r.expected, {})
            confusion[r.expected][got] = confusion[r.expected].get(got, 0) + 1
    d = len(scored)
    return {
        "n": n,
        "errors": errors,
        "hits": hits,
        "misses": d - hits,
        "recall": round(hits / d, 3) if d else 0.0,           # found the expected flower
        "mean_extras": round(extra_total / d, 3) if d else 0.0,   # avg extra types/photo
        "confusion": confusion,
    }


# --- corrections mode (scores stored drafts, no broker) ---------------------

@dataclass
class CorrectionSample:
    truth: set[str]        # keys of the florist-corrected inventory (ground truth)
    predicted: set[str]    # keys of the stored vision draft


def load_correction_samples() -> list[CorrectionSample]:
    out: list[CorrectionSample] = []
    for row in db.iter_labeled():
        truth = set(_keys(_all_names(row["inventory"])))
        pred = set(_keys(_all_names(row.get("vision_draft") or {})))
        out.append(CorrectionSample(truth, pred))
    return out


def score_corrections(samples: list[CorrectionSample]) -> dict:
    """Micro precision/recall/F1 of the raw vision drafts vs the human corrections."""
    tp = sum(len(s.truth & s.predicted) for s in samples)
    fp = sum(len(s.predicted - s.truth) for s in samples)
    fn = sum(len(s.truth - s.predicted) for s in samples)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"n": len(samples), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3)}


# --- CLI --------------------------------------------------------------------

def _progress(i: int, n: int, r: Result) -> None:
    tag = "ERR " if r.error else ("hit " if r.hit else "miss")
    print(f"[{i:>3}/{n}] {tag} {r.expected:22s} -> {', '.join(r.resolved) or '(nothing)'}",
          file=sys.stderr)


def _print_reference(summary: dict) -> None:
    print(f"\nvision model: {config.VISION_MODEL}")
    print(f"samples:      {summary['n']}  (errors: {summary['errors']})")
    print(f"recall:       {summary['recall']}  "
          f"({summary['hits']}/{summary['n'] - summary['errors']} identified the expected flower)")
    print(f"mean extras:  {summary['mean_extras']}  (avg extra flower types per photo)")
    if summary["confusion"]:
        print("\nmisses (expected -> what it was called instead):")
        for exp in sorted(summary["confusion"]):
            for got, c in sorted(summary["confusion"][exp].items(), key=lambda kv: -kv[1]):
                print(f"  {exp:22s} -> {got}  x{c}")


def _save(path: str, payload: dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\nsaved -> {out}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bouquet vision identification eval.")
    ap.add_argument("--mode", choices=["reference", "corrections"], default="reference")
    ap.add_argument("--per-flower", type=int, default=1,
                    help="reference mode: photos per flower (default 1; 0 = all)")
    ap.add_argument("--limit", type=int, default=0, help="cap total samples (0 = no cap)")
    ap.add_argument("--model", default=None,
                    help="override the vision model (e.g. gemma3:27b) to A/B a swap")
    ap.add_argument("--out", default=None, help="write the full JSON result to this path")
    ap.add_argument("--json", action="store_true", help="also print JSON to stdout")
    args = ap.parse_args(argv)

    if args.mode == "corrections":
        config.ensure_dirs()
        db.init()               # idempotent: opens the live DB, or creates an empty one
        samples = load_correction_samples()
        summary = score_corrections(samples)
        print(f"\ncorrections scored: {summary['n']} saved analyses")
        if summary["n"]:
            print(f"precision {summary['precision']}  recall {summary['recall']}  f1 {summary['f1']}"
                  f"  (tp {summary['tp']} fp {summary['fp']} fn {summary['fn']})")
        else:
            print("no analyses with a stored vision draft yet - the DB fills as bouquets are run.")
        payload = {"mode": "corrections", "summary": summary}
        if args.out:
            _save(args.out, payload)
        if args.json:
            print(json.dumps(payload, indent=2))
        return 0

    if args.model:
        config.VISION_MODEL = args.model
    samples = load_reference_samples(per_flower=args.per_flower or None)
    if args.limit:
        samples = samples[:args.limit]
    print(f"evaluating {len(samples)} reference photo(s) with {config.VISION_MODEL} ...",
          file=sys.stderr)
    results = evaluate(samples, on_progress=_progress)
    summary = summarize(results)
    _print_reference(summary)
    payload = {
        "mode": "reference", "model": config.VISION_MODEL, "summary": summary,
        "results": [{"expected": r.expected, "resolved": r.resolved, "names": r.names,
                     "hit": r.hit, "extras": r.extras, "error": r.error} for r in results],
    }
    if args.out:
        _save(args.out, payload)
    if args.json:
        print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
