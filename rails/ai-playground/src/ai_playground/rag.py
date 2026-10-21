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


# The ceiling on one chunk, the same number both sibling RAG rails use. Paragraphs split only on
# blank lines and had NO maximum, so a hard-wrapped .txt, an exported transcript or a CSV (single
# newlines, no blank line) ingested as ONE chunk: measured 2026-09-11, a 207,499-character file
# became 1 chunk whose vector represents only its head, returned for every question, while
# /api/rag/corpora reported the corpus healthy. The shipped seeds are unaffected: their longest
# paragraph is 895 characters (measured 2026-10-08).
MAX_CHARS = 1400
# Headers and tiny fragments. Applied to the PARAGRAPH, never to the pieces a long one is cut
# into, so splitting cannot drop the short tail of a paragraph that was kept.
MIN_CHARS = 40


def _pieces(para: str) -> list[str]:
    """A paragraph as chunks of at most MAX_CHARS, cut at line breaks, then at spaces, and only
    then mid-word. Nothing is dropped: a sibling rail TRUNCATES at this length instead."""
    if len(para) <= MAX_CHARS:
        return [para]
    words: list[str] = []
    for line in para.splitlines():
        while len(line) > MAX_CHARS:
            cut = line.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            words.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        words.append(line)
    out: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CHARS:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur}\n{w}" if cur else w
    if cur.strip():
        out.append(cur)
    return out


def _paragraph_chunks(source: str, text: str) -> list[dict]:
    return [{"source": source, "text": piece}
            for para in (p.strip() for p in text.split("\n\n")) if len(para) > MIN_CHARS
            for piece in _pieces(para)]


def load_chunks(folder: str) -> list[dict]:
    """Split every *.md in ``folder`` into ~paragraph chunks (blank-line delimited, each at
    most MAX_CHARS)."""
    chunks: list[dict] = []
    for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
        with open(path, encoding="utf-8") as fh:
            chunks.extend(_paragraph_chunks(os.path.basename(path), fh.read()))
    return chunks


def chunk_text(source: str, text: str) -> list[dict]:
    """Paragraph-chunk a single raw document (used for user uploads), each at most MAX_CHARS."""
    return _paragraph_chunks(source, text)


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
