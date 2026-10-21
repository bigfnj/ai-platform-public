"""Corpus management: seed ingestion, user uploads, and cosine retrieval.

A corpus is a set of documents, chunked and embedded (via the broker) once, with the
vectors cached in SQLite. Retrieval loads a corpus's vectors into a normalized numpy
matrix (memoized per corpus) and cosine-ranks the query. Seed corpora are baked into the
image and ingested on first boot; users can upload their own (owner-scoped).
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict

import numpy as np

from ai_playground import broker, config, db, rag

# corpus_id -> (chunks, normalized matrix), least recently used first. Dropped for a corpus when
# it changes or is deleted, and bounded at config.CORPUS_CACHE_SIZE: it used to keep every
# corpus ever queried, its full chunk text plus an n x 1024 float32 matrix, for process life.
_CACHE: OrderedDict[int, tuple[list[dict], np.ndarray]] = OrderedDict()
_LOCK = threading.Lock()


class QuotaExceeded(Exception):
    """The owner already holds config.MAX_CORPORA_PER_USER uploaded corpora."""


def _cache_put(corpus_id: int, entry: tuple[list[dict], np.ndarray]) -> None:
    """Store under the lock and evict past the bound. Callers hold _LOCK."""
    _CACHE[corpus_id] = entry
    _CACHE.move_to_end(corpus_id)
    while len(_CACHE) > max(1, config.CORPUS_CACHE_SIZE):
        _CACHE.popitem(last=False)


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
        _cache_put(corpus_id, (chunks, matrix))
    return corpus_id


def _seed_order() -> list:
    """Seed folders with the configured default first, then the rest alphabetically.

    Plain ``sorted()`` made ingest order alphabetical, so on a FRESH volume
    ``embedding-concepts`` was ingested before ``nvidia-stack``, took the lower id, and became
    the demo's default under list_corpora's seeds-oldest-first rule — the wrong corpus for
    every one of the demo's sample questions. Existing deployments were unaffected only because
    their database predates the second corpus, which is exactly why it went unnoticed.
    """
    folders = sorted(p for p in config.SEED_CORPORA_DIR.iterdir() if p.is_dir())
    default = config.DEFAULT_CORPUS_SLUG
    return sorted(folders, key=lambda p: (_slugify(p.name) != default, p.name))


def ensure_seeds() -> None:
    """Ingest every seed corpus folder not already present. Needs the broker; safe to run
    in a background thread on startup and idempotent (skips slugs already ingested)."""
    if not config.SEED_CORPORA_DIR.exists():
        return
    con = db.connect()
    try:
        for folder in _seed_order():
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
        # Checked before embedding, so a refused upload costs the GPU nothing.
        if db.count_user_corpora(con, owner) >= config.MAX_CORPORA_PER_USER:
            raise QuotaExceeded(
                f"you already have {config.MAX_CORPORA_PER_USER} uploaded corpora; delete one "
                f"before adding another")
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
            _CACHE.move_to_end(corpus_id)
    if cached is not None:
        return cached
    chunks, vectors = db.get_chunks(con, corpus_id)
    matrix = (np.asarray(vectors, dtype=np.float32) if vectors
              else np.zeros((0, 0), dtype=np.float32))
    with _LOCK:
        _cache_put(corpus_id, (chunks, matrix))
    return chunks, matrix


def retrieve(corpus_id: int, query: str, k: int, owner: str | None) -> list[dict]:
    """Top-k chunks for a query within a corpus (owner-checked for user corpora)."""
    con = db.connect()
    try:
        meta = db.get_corpus(con, corpus_id)
        if meta is None:
            raise ValueError("corpus not found")
        # No `owner is not None` guard: a null owner is a SCOPE (shared corpora), not a skeleton
        # key. Guarding on it meant every caller the rail resolved to a null owner — including
        # anyone who sent a blank X-Platform-User — read past this check into any user's corpus.
        if meta["kind"] == "user" and meta["owner"] not in (None, owner):
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
            # LookupError, not a silent return. The route maps this and PermissionError to
            # the SAME 404, which is what makes a foreign id indistinguishable from an
            # absent one. Returning quietly made an absent id a 200 {"deleted": <id>} while
            # a foreign one was a refusal, so the pair still answered "does this id exist".
            raise LookupError("no such corpus")
        if meta["kind"] == "seed" and not is_admin:
            raise PermissionError("seed corpora are read-only")
        # Same as retrieve(): a null owner owns nothing, so it must not satisfy the test. This
        # one deleted another user's corpus outright.
        if (meta["kind"] == "user" and meta["owner"] not in (None, owner)
                and not is_admin):
            raise PermissionError("not your corpus")
        db.delete_corpus(con, corpus_id)
    finally:
        con.close()
    with _LOCK:
        _CACHE.pop(corpus_id, None)
