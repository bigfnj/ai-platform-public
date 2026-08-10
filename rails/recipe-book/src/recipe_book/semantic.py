"""Semantic search over the catalog using broker embeddings (bge-m3).

Each recipe is embedded once and the vectors cached to disk
(``DATA_DIR/semantic_index.json``, on the volume); queries embed just the query
string and cosine-rank against the cache in pure Python (835 vectors is trivial to
score per request — no numpy). All embedding goes through the broker; if it's
offline the index just can't be (re)built and search falls back to lexical.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from recipe_book import broker, config

# module-level index: {"model", "ids", "vectors", "norms"} or None until built/loaded
_INDEX: dict | None = None


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
    global _INDEX
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
    return {"built": built(),
            "count": len(_INDEX["ids"]) if built() else 0,
            "model": (_INDEX or {}).get("model", broker.EMBED_MODEL)}


def build(catalog, batch: int = 48) -> dict:
    """Embed every recipe (batched) and cache the vectors. Reloads into memory."""
    recipes = catalog.recipes
    ids: list[str] = []
    vectors: list[list[float]] = []
    for i in range(0, len(recipes), batch):
        chunk = recipes[i:i + batch]
        vecs = broker.embed([_recipe_text(r) for r in chunk])
        if len(vecs) != len(chunk):
            raise broker.BrokerError(f"embed count mismatch: {len(vecs)} != {len(chunk)}")
        ids.extend(r.id for r in chunk)
        vectors.extend(vecs)
    global _INDEX
    _INDEX = {"model": broker.EMBED_MODEL, "ids": ids, "vectors": vectors,
              "norms": [_norm(v) for v in vectors]}
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"model": broker.EMBED_MODEL, "ids": ids, "vectors": vectors}),
                 encoding="utf-8")
    return {"built": True, "count": len(ids), "model": broker.EMBED_MODEL}


def query(text: str, top_k: int = 400) -> list[tuple[str, float]]:
    """Return [(recipe_id, cosine_score)] ranked best-first, or [] if not built."""
    if not built():
        return []
    qvec = broker.embed(text)[0]
    qnorm = _norm(qvec)
    scored: list[tuple[str, float]] = []
    for rid, vec, vnorm in zip(_INDEX["ids"], _INDEX["vectors"], _INDEX["norms"]):
        dot = sum(a * b for a, b in zip(qvec, vec))
        scored.append((rid, dot / (qnorm * vnorm)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
