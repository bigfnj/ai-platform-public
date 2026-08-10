"""Corpus management: seed ingestion, user uploads, and cosine retrieval.

A corpus is a set of documents, chunked and embedded (via the broker) once, with the
vectors cached in SQLite. Retrieval loads a corpus's vectors into a normalized numpy
matrix (memoized per corpus) and cosine-ranks the query. Seed corpora are baked into the
image and ingested on first boot; users can upload their own (owner-scoped).
"""
from __future__ import annotations

import re
import threading

import numpy as np

from ai_playground import broker, config, db, rag

# corpus_id -> (chunks, normalized matrix). Dropped for a corpus when it changes/deletes.
_CACHE: dict[int, tuple[list[dict], np.ndarray]] = {}
_LOCK = threading.Lock()


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "corpus"


def _ingest(con, *, slug: str, name: str, kind: str, owner: str | None,
            chunks: list[dict]) -> int:
    """Embed chunks via the broker and persist a new corpus + its (normalized) vectors."""
    matrix = rag.embed_texts([c["text"] for c in chunks])           # (n, d) normalized
    corpus_id = db.add_corpus(con, slug=slug, name=name, kind=kind, owner=owner,
                              embed_model=broker.EMBED_MODEL)
    db.add_chunks(con, corpus_id, chunks, matrix.tolist())
    with _LOCK:
        _CACHE[corpus_id] = (chunks, matrix)
    return corpus_id


def ensure_seeds() -> None:
    """Ingest every seed corpus folder not already present. Needs the broker; safe to run
    in a background thread on startup and idempotent (skips slugs already ingested)."""
    if not config.SEED_CORPORA_DIR.exists():
        return
    con = db.connect()
    try:
        for folder in sorted(p for p in config.SEED_CORPORA_DIR.iterdir() if p.is_dir()):
            slug = _slugify(folder.name)
            if db.corpus_exists(con, slug, None):
                continue
            chunks = rag.load_chunks(str(folder))
            if not chunks:
                continue
            name = folder.name.replace("-", " ").replace("_", " ").title()
            _ingest(con, slug=slug, name=name, kind="seed", owner=None, chunks=chunks)
    finally:
        con.close()


def ingest_upload(owner: str | None, name: str, docs: list[tuple[str, str]]) -> dict:
    """docs = [(filename, text)]. Chunk (paragraph split) + embed + store as a user corpus."""
    chunks: list[dict] = []
    for fname, text in docs:
        chunks.extend(rag.chunk_text(fname, text))
    if not chunks:
        raise ValueError("no usable text found in the upload")
    con = db.connect()
    try:
        base = _slugify(name)
        slug, n = base, 2
        while db.corpus_exists(con, slug, owner):
            slug, n = f"{base}-{n}", n + 1
        cid = _ingest(con, slug=slug, name=name or base, kind="user", owner=owner, chunks=chunks)
        return {"id": cid, "slug": slug, "name": name or base, "chunks": len(chunks)}
    finally:
        con.close()


def _matrix(con, corpus_id: int) -> tuple[list[dict], np.ndarray]:
    with _LOCK:
        cached = _CACHE.get(corpus_id)
    if cached is not None:
        return cached
    chunks, vectors = db.get_chunks(con, corpus_id)
    matrix = (np.asarray(vectors, dtype=np.float32) if vectors
              else np.zeros((0, 0), dtype=np.float32))
    with _LOCK:
        _CACHE[corpus_id] = (chunks, matrix)
    return chunks, matrix


def retrieve(corpus_id: int, query: str, k: int, owner: str | None) -> list[dict]:
    """Top-k chunks for a query within a corpus (owner-checked for user corpora)."""
    con = db.connect()
    try:
        meta = db.get_corpus(con, corpus_id)
        if meta is None:
            raise ValueError("corpus not found")
        if meta["kind"] == "user" and owner is not None and meta["owner"] not in (None, owner):
            raise PermissionError("not your corpus")
        chunks, matrix = _matrix(con, corpus_id)
    finally:
        con.close()
    if matrix.shape[0] == 0:
        return []
    return rag.rank(query, chunks, matrix, k)


def delete(corpus_id: int, owner: str | None, is_admin: bool) -> None:
    con = db.connect()
    try:
        meta = db.get_corpus(con, corpus_id)
        if meta is None:
            return
        if meta["kind"] == "seed" and not is_admin:
            raise PermissionError("seed corpora are read-only")
        if (meta["kind"] == "user" and owner is not None
                and meta["owner"] not in (None, owner) and not is_admin):
            raise PermissionError("not your corpus")
        db.delete_corpus(con, corpus_id)
    finally:
        con.close()
    with _LOCK:
        _CACHE.pop(corpus_id, None)
