"""Retrieval: markdown -> chunks -> broker embeddings -> cosine ranking.

Deliberately a transparent numpy cosine search rather than a vector-store framework. The
corpus is SME enablement content measured in thousands of chunks, not millions, so an
in-memory matrix multiply is both faster and far easier to reason about when a partner
asks why a particular source was cited.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from smb_partner import broker, config

# Chunks are paragraph-shaped but merged up to a floor, so a one-line bullet does not become
# its own retrieval unit competing with a full paragraph.
MIN_CHARS = 220
MAX_CHARS = 1400

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def chunk_markdown(text: str, *, source: str, collection: str = "") -> list[dict]:
    """Split markdown into retrieval chunks, carrying the nearest heading as a title.

    The heading matters: SME content is heavily sectioned ("Objection: price", "MCEM stage 3"),
    and keeping that label on the chunk both improves retrieval and gives the answer a
    citation a human recognises.
    """
    chunks: list[dict] = []
    title = ""
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        body = "\n\n".join(buf).strip()
        if len(body) >= 40:
            chunks.append({
                "collection": collection,
                "source": source,
                "title": title,
                "text": (f"{title}\n\n{body}" if title else body)[:MAX_CHARS],
            })
        buf.clear()

    for block in (b.strip() for b in (text or "").split("\n\n")):
        if not block:
            continue
        head = _HEADING.match(block.splitlines()[0])
        if head:
            flush()
            title = head.group(2).strip()
            rest = block.split("\n", 1)[1].strip() if "\n" in block else ""
            if rest:
                buf.append(rest)
            continue
        buf.append(block)
        if sum(len(b) for b in buf) >= MIN_CHARS:
            flush()
    flush()
    return chunks


def load_collection(folder: Path, collection: str) -> list[dict]:
    """Chunk every *.md under ``folder`` (recursively), skipping authoring scaffolding."""
    out: list[dict] = []
    for path in sorted(folder.rglob("*.md")):
        name = path.name
        if name.startswith("_") or name.upper() == "README.MD":
            continue  # _TEMPLATE.md and per-folder READMEs are instructions, not content
        rel = path.relative_to(folder).as_posix()
        out.extend(chunk_markdown(path.read_text(encoding="utf-8"),
                                  source=rel, collection=collection))
    return out


def embed_texts(texts: list[str], *, batch: int = 32) -> np.ndarray:
    """Embed via the broker and L2-normalize, so a dot product IS cosine similarity."""
    vecs: list[list[float]] = []
    for i in range(0, len(texts), batch):
        vecs.extend(broker.embed(texts[i:i + batch], model=config.EMBED_MODEL))
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim != 2 or not arr.size:
        raise broker.BrokerError("embedder returned no usable vectors")
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


def rank(query: str, chunks: list[dict], matrix: np.ndarray,
         k: int = 0, *, collections: set[str] | None = None) -> list[dict]:
    """Top-k chunks for a query by cosine similarity, optionally scoped to collections."""
    if not chunks or matrix.size == 0:
        return []
    k = k or config.TOP_K
    qv = embed_texts([query])[0]
    scores = matrix @ qv
    if collections:
        mask = np.array([c.get("collection") in collections for c in chunks])
        scores = np.where(mask, scores, -np.inf)
    top = np.argsort(scores)[::-1][:k]
    return [{**chunks[i], "score": float(scores[i])}
            for i in top if np.isfinite(scores[i])]


def build_context(hits: list[dict]) -> str:
    """Render ranked hits as the numbered context block the system prompt cites against."""
    return "\n\n".join(
        f"[{i}] ({h['source']}) {h['text']}" for i, h in enumerate(hits, start=1)
    )
