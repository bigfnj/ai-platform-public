"""Semantic search over the catalog using broker embeddings (bge-m3).

Each recipe is embedded once and the vectors cached to disk
(``DATA_DIR/semantic_index.json``, on the volume); queries embed just the query
string and cosine-rank against the cache in pure Python (835 vectors is trivial to
score per request — no numpy). All embedding goes through the broker; if it's
offline the index just can't be (re)built and search falls back to lexical.
"""
from __future__ import annotations

import json
import logging
import math
import operator
import threading
from pathlib import Path

from recipe_book import broker, config

# module-level index: {"model", "ids", "vectors", "norms"} or None until built/loaded
_INDEX: dict | None = None
# Why the loaded index cannot answer the embedder as it resolves NOW, or "" when it can. Set by
# the first query or add that sees the mismatch; cleared by a rebuild.
_STALE = ""
# The last background build's failure, or "" (cleared by a successful build).
_LAST_ERROR = ""
log = logging.getLogger("recipe_book.semantic")
# Held around every change to the in-memory index and its write to disk, never across an
# embed. add() runs as a BackgroundTask per edit, so two quick edits are two threads: both
# appended to the same lists and both wrote the one fixed .json.tmp name, and the second
# replace() either found the file gone or moved the other writer's half-written bytes into
# place (the audit's "unconfirmed" .json.tmp race; load() then drops to lexical search).
_LOCK = threading.Lock()


def _norm_tag(name: str) -> str:
    name = (name or "").strip().lower()
    return name if ":" in name else name + ":latest"


def _mismatch(model: str, vec: list[float]) -> str:
    """Why vectors from `model` cannot be scored against the loaded index, or "".

    The index records the model it was built with and nothing compared it: repoint the
    embedder and zip(qvec, vec) silently truncated to the shorter vector while the norms used
    the full lengths, so scores still cleared the floor and the route answered
    "semantic": true over noise. Width is checked always; the model whenever both names are
    concrete (an index built before the model was recorded resolved, or a broker that does not
    say, leaves width to decide). bouquet's retrieval.py has the same guard for the same reason.
    """
    vectors = (_INDEX or {}).get("vectors") or []
    if vectors and len(vec) != len(vectors[0]):
        return f"the embedder now returns {len(vec)}-d vectors; the index holds {len(vectors[0])}-d"
    built = (_INDEX or {}).get("model", "")
    if model and built and not built.startswith("@") and not model.startswith("@") \
            and _norm_tag(model) != _norm_tag(built):
        return f"the index was built with {built}; the embedder now resolves to {model}"
    return ""


def _path() -> Path:
    return Path(config.DATA_DIR) / "semantic_index.json"


def _recipe_text(r) -> str:
    bits = [r.title, r.category]
    if r.meta:
        bits.append(r.meta)
    if r.base_spirits:
        bits.append("Spirits: " + ", ".join(r.base_spirits))
    if r.ingredients:
        bits.append("Ingredients: " + ", ".join(r.ingredients[:40]))
    return ". ".join(bits)


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vec)) or 1.0


def load() -> bool:
    """Load the cached index if present. Returns True if an index is now in memory."""
    global _INDEX, _STALE
    _STALE = ""
    p = _path()
    if not p.exists():
        _INDEX = None
        return False
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        d["norms"] = [_norm(v) for v in d["vectors"]]
        _INDEX = d
        return True
    except (OSError, KeyError, ValueError):
        _INDEX = None
        return False


def built() -> bool:
    return _INDEX is not None and bool(_INDEX.get("ids"))


def status() -> dict:
    out = {"built": built(),
           "count": len(_INDEX["ids"]) if built() else 0,
           "model": (_INDEX or {}).get("model", broker.EMBED_MODEL)}
    if _LAST_ERROR:
        out["last_error"] = _LAST_ERROR
    if _STALE:
        # Search answers lexically until an admin rebuilds (POST /api/search/reindex, or the
        # nightly scheduler): the index is kept, because a rebuild is the expensive operation.
        out["stale"] = _STALE
    return out


def _persist(ids: list[str], vectors: list[list[float]], model: str) -> None:
    """Write the cache atomically. ``add`` rewrites this file on every new recipe, not
    just on a full rebuild, so a crash mid-write would otherwise leave a truncated index
    behind; ``load`` would then quietly fall back to lexical search."""
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"model": model, "ids": ids, "vectors": vectors}),
                   encoding="utf-8")
    tmp.replace(p)


def build(catalog, batch: int = 48) -> dict:
    """Embed every recipe (batched) and cache the vectors. Reloads into memory."""
    recipes = catalog.recipes
    ids: list[str] = []
    vectors: list[list[float]] = []
    model = ""
    for i in range(0, len(recipes), batch):
        chunk = recipes[i:i + batch]
        vecs, used = broker.embed_resolved([_recipe_text(r) for r in chunk])
        if len(vecs) != len(chunk):
            raise broker.BrokerError(f"embed count mismatch: {len(vecs)} != {len(chunk)}")
        model = model or used
        ids.extend(r.id for r in chunk)
        vectors.extend(vecs)
    # The CONCRETE model when the broker names it, so a later repoint of @embed is visible.
    model = model or broker.EMBED_MODEL
    global _INDEX, _STALE, _LAST_ERROR
    with _LOCK:
        _INDEX = {"model": model, "ids": ids, "vectors": vectors,
                  "norms": [_norm(v) for v in vectors]}
        _STALE = ""
        _LAST_ERROR = ""
        _persist(ids, vectors, model)
    return {"built": True, "count": len(ids), "model": model}


def record_failure(exc: BaseException) -> None:
    """A background build failed: log it and keep the reason for status()."""
    global _LAST_ERROR
    _LAST_ERROR = f"{type(exc).__name__}: {exc}"[:300]
    log.warning("semantic index build failed: %s", _LAST_ERROR)


def add(recipe) -> bool:
    """Fold ONE recipe into the cached index, replacing any vector already held for its
    id. Returns False when there is no index to keep in sync.

    Adding a recipe used to leave the index untouched, so a newly contributed card was
    invisible to semantic search until someone re-embedded the WHOLE catalog — a job
    measured in minutes, which is why it only ever ran from the admin button or the
    nightly scheduler. One recipe costs one embed call, so it can just happen inline.

    A missing index is deliberately NOT built here: ``build`` is the expensive, admin-gated
    operation, and quietly promoting a contributor's upload into a full catalog re-embed
    would hand any signed-in user a way to occupy the shared GPU.
    """
    global _STALE
    if not built():
        return False
    vecs, used = broker.embed_resolved(_recipe_text(recipe))
    vec = vecs[0]
    with _LOCK:
        why = _mismatch(used, vec)
        if why:
            # Folding a vector from another model in would corrupt the index for every query.
            _STALE = why
            return False
        ids, vectors, norms = _INDEX["ids"], _INDEX["vectors"], _INDEX["norms"]
        if recipe.id in ids:
            i = ids.index(recipe.id)
            vectors[i], norms[i] = vec, _norm(vec)
        else:
            ids.append(recipe.id)
            vectors.append(vec)
            norms.append(_norm(vec))
        _persist(ids, vectors, _INDEX.get("model", "") or broker.EMBED_MODEL)
    return True


def query(text: str, top_k: int = 400) -> list[tuple[str, float]]:
    """Return [(recipe_id, cosine_score)] ranked best-first, or [] if not built."""
    global _STALE
    if not built():
        return []
    qvecs, used = broker.embed_resolved(text)
    qvec = qvecs[0]
    why = _mismatch(used, qvec)
    if why:
        _STALE = why
        return []          # the route falls back to lexical and says "semantic": false
    qnorm = _norm(qvec)
    # sum(map(mul)) rather than a generator over zip: same arithmetic, no numpy in this
    # image, and measured 34.1 ms against 74.7 ms per query over 900 x 1024 (median of 15
    # queries per process, 5 fresh processes per variant, interleaved; 2026-10-08).
    mul = operator.mul
    scored = [(rid, sum(map(mul, qvec, vec)) / (qnorm * vnorm))
              for rid, vec, vnorm in zip(_INDEX["ids"], _INDEX["vectors"], _INDEX["norms"])]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
