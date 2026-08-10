"""RAG pipeline: markdown -> paragraph chunks -> broker embeddings -> cosine retrieval.

Ported from the standalone enablement kit, with the one change that matters for the
platform: embeddings go through the broker (bge-m3 via @embed), not a direct Ollama call.
Retrieval is a transparent numpy cosine search — no framework, no hidden magic.
"""
from __future__ import annotations

import glob
import os

import numpy as np

from ai_playground import broker, config


def load_chunks(folder: str) -> list[dict]:
    """Split every *.md in ``folder`` into ~paragraph chunks (blank-line delimited)."""
    chunks: list[dict] = []
    for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
        name = os.path.basename(path)
        with open(path, encoding="utf-8") as fh:
            for para in (p.strip() for p in fh.read().split("\n\n")):
                if len(para) > 40:  # skip headers / tiny fragments
                    chunks.append({"source": name, "text": para})
    return chunks


def chunk_text(source: str, text: str) -> list[dict]:
    """Paragraph-chunk a single raw document (used for user uploads)."""
    return [{"source": source, "text": para}
            for para in (p.strip() for p in text.split("\n\n")) if len(para) > 40]


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed via the broker and L2-normalize for cosine (dot of normalized == cosine)."""
    vecs = np.asarray(broker.embed(texts), dtype=np.float32)
    return vecs / (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)


def rank(query: str, chunks: list[dict], matrix: np.ndarray, k: int = config.TOP_K) -> list[dict]:
    """Top-k chunks for a query by cosine similarity against a normalized matrix."""
    qv = embed_texts([query])[0]
    scores = matrix @ qv
    top = np.argsort(scores)[::-1][:k]
    return [{**chunks[i], "score": float(scores[i])} for i in top]
