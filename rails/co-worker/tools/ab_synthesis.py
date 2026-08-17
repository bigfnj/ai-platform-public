#!/usr/bin/env python3
"""A/B-test synthesis models against the REAL prompt and the REAL inbox.

WHY THIS EXISTS
The executive brief is produced by a small local model (gemma3:4b via the broker role
@co-worker-synthesis). "Is it good enough?" cannot be answered by reading the prompt —
it has to be measured. This runs the actual prompt the app builds, against one or more
models, N times each, and scores what can be scored objectively.

WHERE TO RUN IT
Inside the co-worker container: the app is importable there AND the broker is reachable
at host.docker.internal:11500, which is the only place both are true.

    docker compose exec co-worker python /app/tools/ab_synthesis.py --help

If tools/ isn't in the image, mount or copy it in:

    docker compose cp rails/co-worker/tools/ab_synthesis.py co-worker:/tmp/ab.py
    docker compose exec co-worker python /tmp/ab.py --models @co-worker-synthesis --reps 3

WHAT IT MEASURES
Objective, no human needed:
  json_ok          parsed as JSON on the first try (format:json should make this ~100%)
  placeholder_echo output contains "<...>" scaffolding — model echoing a template
  id_valid         attention ids that resolve to a real inbox item
  enum_raw_ok      category/urgency already legal BEFORE _normalize coerces them
  over_ten         emitted more than the MAX 10 the prompt asks for
  suppressed_ok    the model's suppressed value matches Python's authoritative formula;
                   the prompt no longer asks for it so this tests whether it infers it
  p1_client_recall of the P1 client items in the payload, how many it surfaced
  determinism      Jaccard overlap of selected id sets across reps (temp is 0.15, not 0)
  latency_s        wall clock per call

WHAT IT CANNOT MEASURE
Whether the ten things it chose are the *right* ten. That needs your eyes. The script
writes every raw brief to disk so you can read them side by side, and prints a compact
diff of which items each model chose that the others did not — which is usually where
the real disagreement lives.

Stdlib only, like the other tools in this directory.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# --- import the app so we test the REAL prompt, not a copy ------------------
try:
    from co_worker_app.config import settings
    from co_worker_app.synthesize import (
        NUM_CTX,
        SYSTEM_PROMPT,
        _build_prompt,
        _condense,
        _dominant_period,
        _extract_json,
        _load_inbox,
        _normalize,
    )
except ImportError:
    sys.exit(
        "cannot import co_worker_app — run this INSIDE the co-worker container:\n"
        "  docker compose exec co-worker python /tmp/ab.py --help"
    )

from datetime import datetime


def call_model(model: str, prompt: str, url: str, token: str, timeout: int = 600) -> tuple[str, float]:
    """One broker call. Returns (raw_text, seconds). Mirrors synthesize._call_broker
    but takes the model verbatim so we can pass a role (@name) or a raw Ollama tag."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "format": "json",
        "options": {"temperature": 0.15, "num_predict": 2048, "num_ctx": NUM_CTX},
    }).encode()

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    t0 = time.time()
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/chat", data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    elapsed = time.time() - t0
    return data.get("message", {}).get("content", ""), elapsed


def score(
    raw: str,
    valid_ids: set[str],
    idx_to_id: dict[int, str],
    payload: list[dict],
    n_total: int,
    n_triaged: int,
) -> dict:
    """Objective scoring of one raw model response."""
    s: dict[str, Any] = {"json_ok": False, "error": None}

    # Placeholder echo: the schema example no longer uses <angle brackets>, but a model
    # that echoes its own scaffold still produces them.
    s["placeholder_echo"] = "<" in raw and ">" in raw

    try:
        brief = _extract_json(raw)
        if not isinstance(brief, dict):
            raise ValueError("not an object")
        s["json_ok"] = True
    except Exception as exc:
        s["error"] = str(exc)[:120]
        return s

    att = brief.get("attention") or []
    att = [a for a in att if isinstance(a, dict)]
    s["n_attention"] = len(att)
    s["over_ten"] = len(att) > 10

    # Model emits integer _idx; resolve to actual _id strings for validity checks.
    ids_resolved: list[str] = []
    for a in att:
        raw_id = a.get("id")
        try:
            ids_resolved.append(idx_to_id.get(int(raw_id), ""))
        except (TypeError, ValueError):
            ids_resolved.append(str(raw_id or "").strip())
    s["ids"] = ids_resolved
    resolved = [i for i in ids_resolved if i in valid_ids]
    s["id_valid"] = round(len(resolved) / len(ids_resolved), 3) if ids_resolved else None

    # Enum legality BEFORE _normalize silently coerces to a default. A model whose
    # enums are always repaired looks fine in the UI while being wrong.
    CATS = {"client", "dangling", "missed", "agenda-gap", "other"}
    URGS = {"today", "this-week", "soon"}
    ok = sum(
        1 for a in att
        if str(a.get("category") or "") in CATS and str(a.get("urgency") or "") in URGS
    )
    s["enum_raw_ok"] = round(ok / len(att), 3) if att else None

    # The prompt no longer instructs the model to compute suppressed — Python does it.
    # This checks whether the model infers the right value anyway.
    expected = n_total - n_triaged - len(att)
    got = brief.get("suppressed")
    s["suppressed_got"] = got
    s["suppressed_expected"] = expected
    s["suppressed_ok"] = (isinstance(got, int) and got == expected)

    # Recall on the items that matter most: client-flagged, priority 1.
    # Payload rows carry _idx not _id; resolve via idx_to_id.
    p1c = {
        idx_to_id[row["_idx"]]
        for row in payload
        if row.get("client") and row.get("priority") == 1 and row.get("_idx") in idx_to_id
    }
    s["p1_client_total"] = len(p1c)
    s["p1_client_recall"] = round(len(p1c & set(resolved)) / len(p1c), 3) if p1c else None

    for k in ("client_pulse", "synthesis_note"):
        v = brief.get(k)
        s[f"len_{k}"] = len(v) if isinstance(v, str) else 0
    for k in ("dangling", "missed", "agenda_gaps"):
        v = brief.get(k)
        s[f"n_{k}"] = len(v) if isinstance(v, list) else 0

    s["_brief"] = brief
    return s


def jaccard(sets: list[set]) -> float | None:
    """Mean pairwise Jaccard — how repeatable the model's *selection* is."""
    pairs = [
        len(a & b) / len(a | b) if (a | b) else 1.0
        for i, a in enumerate(sets) for b in sets[i + 1:]
    ]
    return round(statistics.mean(pairs), 3) if pairs else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["@co-worker-synthesis"],
                    help="broker role (@name) or raw Ollama tag, e.g. qwen3:8b granite4:tiny")
    ap.add_argument("--reps", type=int, default=3, help="runs per model (temp is 0.15, not 0)")
    ap.add_argument("--inbox", type=Path, default=None, help="default: settings.inbox_dir")
    ap.add_argument("--out", type=Path, default=Path("/tmp/ab-synthesis"),
                    help="where raw briefs are written for side-by-side reading")
    ap.add_argument("--broker", default=None, help="default: settings.broker_url")
    args = ap.parse_args()

    inbox = args.inbox or Path(settings.inbox_dir)
    broker = args.broker or settings.broker_url
    args.out.mkdir(parents=True, exist_ok=True)

    items, state = _load_inbox(inbox)
    if not items:
        return print(f"no items in {inbox} — nothing to synthesize against") or 2

    now = datetime.now().astimezone()
    period = _dominant_period(items, now)
    n_triaged = sum(
        1 for i in items
        if state.get(str(i.get("_id", "")), "open") in ("done", "dismissed")
    )
    payload, idx_to_id = _condense(items, state, today=now.date())
    valid_ids = set(idx_to_id.values())
    prompt = _build_prompt(len(items), n_triaged, payload, now.isoformat(), now.date().isoformat(), period)

    print(f"inbox     : {inbox}")
    print(f"items     : {len(items)} total, {n_triaged} triaged, {len(payload)} in payload")
    print(f"prompt    : {len(prompt):,} chars (~{len(prompt)//4:,} tok) · num_ctx {NUM_CTX:,}")
    n_unresolved = len(items) - n_triaged
    if len(payload) < n_unresolved:
        print(f"  ⚠ budget/filter trimmed {n_unresolved - len(payload)} unresolved items")
    print(f"p1 client : {sum(1 for i in payload if i.get('client') and i.get('priority') == 1)} "
          f"in payload (recall denominator)")
    print()

    results: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"── {model}")
        runs = []
        for rep in range(1, args.reps + 1):
            try:
                raw, secs = call_model(model, prompt, broker, settings.broker_auth_token)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                print(f"   rep {rep}: TRANSPORT FAIL {exc}")
                runs.append({"json_ok": False, "error": f"transport: {exc}", "latency_s": None})
                continue

            sc = score(raw, valid_ids, idx_to_id, payload, len(items), n_triaged)
            sc["latency_s"] = round(secs, 1)
            runs.append(sc)

            tag = "ok " if sc["json_ok"] else "BAD"
            print(f"   rep {rep}: {tag} {secs:5.1f}s  "
                  f"att={sc.get('n_attention','-'):>2}  "
                  f"id_valid={sc.get('id_valid')}  "
                  f"enum={sc.get('enum_raw_ok')}  "
                  f"sup_ok={sc.get('suppressed_ok')}  "
                  f"p1recall={sc.get('p1_client_recall')}")

            slug = model.replace("@", "").replace(":", "-").replace("/", "-")
            (args.out / f"{slug}.rep{rep}.raw.json").write_text(raw, encoding="utf-8")
            if sc.get("_brief") is not None:
                norm = _normalize(json.loads(json.dumps(sc["_brief"])), idx_to_id)
                (args.out / f"{slug}.rep{rep}.normalized.json").write_text(
                    json.dumps(norm, indent=2), encoding="utf-8")
        results[model] = runs
        print()

    # --- summary ------------------------------------------------------------
    print("=" * 78)
    print(f"{'model':<26} {'json':>5} {'id':>5} {'enum':>5} {'sup':>5} {'p1':>5} {'det':>5} {'sec':>6}")
    print("-" * 78)

    def pct(vals: list) -> str:
        v = [x for x in vals if x is not None]
        return f"{statistics.mean(v):.2f}" if v else "  -  "

    for model, runs in results.items():
        okr = [r for r in runs if r.get("json_ok")]
        det = jaccard([set(r.get("ids", [])) for r in okr]) if len(okr) > 1 else None
        lat = [r["latency_s"] for r in runs if r.get("latency_s") is not None]
        print(f"{model:<26} "
              f"{len(okr)/len(runs):>5.2f} "
              f"{pct([r.get('id_valid') for r in okr]):>5} "
              f"{pct([r.get('enum_raw_ok') for r in okr]):>5} "
              f"{sum(1 for r in okr if r.get('suppressed_ok'))/len(okr) if okr else 0:>5.2f} "
              f"{pct([r.get('p1_client_recall') for r in okr]):>5} "
              f"{det if det is not None else '  -  ':>5} "
              f"{statistics.mean(lat):>6.1f}" if lat else "     -")

    print()
    print("json=valid JSON rate · id=ids that resolve · enum=enums legal pre-coercion")
    print("sup=suppressed arithmetic correct · p1=P1-client recall · det=selection repeatability")
    print()

    # Where the models actually disagree — usually the interesting part.
    if len(results) > 1:
        print("── selection disagreement (ids chosen by one model and not another)")
        chosen = {
            m: set().union(*[set(r.get("ids", [])) for r in runs if r.get("json_ok")] or [set()])
            for m, runs in results.items()
        }
        common = set.intersection(*chosen.values()) if chosen else set()
        print(f"   agreed by all: {len(common)}")
        for m, ids in chosen.items():
            uniq = ids - common
            if uniq:
                print(f"   only {m}: {sorted(uniq)[:6]}")
        print()

    print(f"raw + normalized briefs: {args.out}")
    print("Read them side by side — the objective scores cannot tell you whether the ten")
    print("items chosen were the RIGHT ten. That judgement is still yours.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
