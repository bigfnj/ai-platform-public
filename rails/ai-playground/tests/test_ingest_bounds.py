"""Bounds on what one caller can make this rail hold: chunk size, upload size, corpus count,
and how many corpora stay in memory. Four items the 2026-09-10/11 audits recorded and nobody
closed, each measured:

  * a hard-wrapped 207,499-character .txt ingested as ONE chunk (no maximum chunk size);
  * a 40 MB upload was read whole, decoded and embedded (25.3 s), and a 40 MB query set parsed;
  * every upload is a new corpus forever, with no per-owner ceiling;
  * every corpus ever queried stayed in the retrieval cache for the life of the process.

Hermetic: the embedder is a stub (no broker, no GPU), the database is a temp file.
"""
from __future__ import annotations

import io
import re

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ai_playground import config, corpora, rag
from conftest import make_corpus

USER = {"X-Platform-User": "owner-one"}


def _words(text: str) -> list[str]:
    return re.findall(r"\S+", text)


# --- the chunker ---------------------------------------------------------------------------

def test_a_hard_wrapped_file_is_no_longer_one_chunk():
    """The recorded case's shape: thousands of single newlines, no blank line."""
    text = "\n".join(f"line {i} " + "word " * 15 for i in range(2500))
    chunks = rag.chunk_text("transcript.txt", text)
    assert len(chunks) > 50
    assert max(len(c["text"]) for c in chunks) <= rag.MAX_CHARS
    assert _words(" ".join(c["text"] for c in chunks)) == _words(text), "content was lost"


def test_a_single_huge_line_is_cut_at_spaces_and_loses_nothing():
    text = " ".join(f"w{i}" for i in range(3000))
    chunks = rag.chunk_text("one-line.txt", text)
    assert max(len(c["text"]) for c in chunks) <= rag.MAX_CHARS
    assert _words(" ".join(c["text"] for c in chunks)) == _words(text)


def test_a_line_with_no_spaces_is_still_cut():
    text = "x" * 5000
    chunks = rag.chunk_text("blob.txt", text)
    assert [len(c["text"]) for c in chunks] == [1400, 1400, 1400, 800]


def test_the_short_tail_of_a_long_paragraph_is_kept():
    """The 40-character floor is for headers, applied to the paragraph: a long paragraph's last
    piece must not be dropped for being short."""
    text = "a " * 700 + "\n" + "tail."
    chunks = rag.chunk_text("t.txt", text)
    assert chunks[-1]["text"].endswith("tail.")


def test_ordinary_paragraphs_are_unchanged():
    text = "Header\n\n" + "A normal paragraph of a reasonable length, well over the floor." \
           + "\n\nAnother one, also long enough to be kept as a chunk on its own."
    assert [c["text"] for c in rag.chunk_text("d.md", text)] == [
        "A normal paragraph of a reasonable length, well over the floor.",
        "Another one, also long enough to be kept as a chunk on its own."]


def test_the_shipped_seeds_chunk_exactly_as_before():
    """Every seed paragraph is under the cap (longest 895), so a fresh volume ingests the same
    chunks it always did and the demo's sample answers do not move."""
    import glob
    import os
    seed_root = os.path.join(os.path.dirname(__file__), "..", "seed", "corpora")
    folders = sorted(glob.glob(os.path.join(seed_root, "*")))
    assert folders, "no seed corpora found, so this proves nothing"
    for folder in folders:
        old = []
        for path in sorted(glob.glob(os.path.join(folder, "*.md"))):
            with open(path, encoding="utf-8") as fh:
                old += [p.strip() for p in fh.read().split("\n\n") if len(p.strip()) > 40]
        assert [c["text"] for c in rag.load_chunks(folder)] == old


# --- upload ceilings -----------------------------------------------------------------------

@pytest.fixture
def uploads(api, monkeypatch):
    ingested: list = []

    def fake_ingest(owner, name, docs):
        ingested.append(docs)
        return {"id": 1, "slug": "x", "name": name, "chunks": 1}

    monkeypatch.setattr(corpora, "ingest_upload", fake_ingest)
    return TestClient(api), ingested


def test_an_oversized_document_upload_is_413_and_nothing_is_ingested(uploads, monkeypatch):
    client, ingested = uploads
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    r = client.post("/api/rag/upload", headers=USER, data={"name": "big"},
                    files={"files": ("big.txt", io.BytesIO(b"a" * 1001), "text/plain")})
    assert r.status_code == 413, r.text
    assert ingested == []


def test_the_cap_is_on_the_total_not_per_file(uploads, monkeypatch):
    client, ingested = uploads
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    files = [("files", (f"f{i}.txt", io.BytesIO(b"a" * 400), "text/plain")) for i in range(3)]
    r = client.post("/api/rag/upload", headers=USER, data={"name": "three"}, files=files)
    assert r.status_code == 413, r.text
    assert ingested == []


def test_an_upload_under_the_cap_still_ingests_every_file(uploads, monkeypatch):
    client, ingested = uploads
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    files = [("files", (f"f{i}.txt", io.BytesIO(b"word " * 50), "text/plain")) for i in range(3)]
    r = client.post("/api/rag/upload", headers=USER, data={"name": "ok"}, files=files)
    assert r.status_code == 200, r.text
    assert [name for name, _ in ingested[0]] == ["f0.txt", "f1.txt", "f2.txt"]
    assert all(text == "word " * 50 for _, text in ingested[0])


def test_an_oversized_query_set_is_413_before_it_is_parsed(api, monkeypatch):
    monkeypatch.setattr(config, "MAX_QUERYSET_BYTES", 100)
    body = b'{"queries": [' + b'{"q": "x", "targets": ["a.md"]},' * 20 + b'{"q": "y"}]}'
    r = TestClient(api).post("/api/bench/querysets/upload", headers=USER, data={"name": "qs"},
                             files={"file": ("qs.json", io.BytesIO(body), "application/json")})
    assert r.status_code == 413, r.text


def test_the_ceilings_are_configured():
    assert config.MAX_UPLOAD_BYTES >= 1024 * 1024
    assert config.MAX_QUERYSET_BYTES >= 64 * 1024


# --- the per-owner corpus ceiling ----------------------------------------------------------

def test_the_corpus_ceiling_refuses_before_embedding(api, con, monkeypatch):
    embeds: list = []

    def fake_embed(texts):
        embeds.append(len(texts))
        return np.ones((len(texts), 2), dtype=np.float32)

    monkeypatch.setattr(rag, "embed_texts", fake_embed)
    monkeypatch.setattr(config, "MAX_CORPORA_PER_USER", 2)
    client = TestClient(api)

    def upload(name):
        return client.post("/api/rag/upload", headers=USER, data={"name": name},
                           files={"files": ("d.txt", io.BytesIO(b"long enough text " * 5),
                                            "text/plain")})

    assert upload("one").status_code == 200
    assert upload("two").status_code == 200
    third = upload("three")
    assert third.status_code == 409, third.text
    assert "delete one" in third.json()["detail"]
    assert len(embeds) == 2, "the refused upload was still embedded on the GPU"
    # Another owner is unaffected: the ceiling is per owner.
    other = client.post("/api/rag/upload", headers={"X-Platform-User": "bob"},
                        data={"name": "b"},
                        files={"files": ("d.txt", io.BytesIO(b"long enough text " * 5),
                                         "text/plain")})
    assert other.status_code == 200, other.text


# --- the retrieval cache -------------------------------------------------------------------

def test_the_retrieval_cache_is_bounded_and_least_recently_used(con, monkeypatch):
    monkeypatch.setattr(config, "CORPUS_CACHE_SIZE", 2)
    monkeypatch.setattr(rag, "embed_texts", lambda texts: np.ones((len(texts), 2), np.float32))
    ids = [make_corpus(con, None, name=f"Docs {i}", kind="seed") for i in range(3)]
    corpora._CACHE.clear()
    corpora.retrieve(ids[0], "q", 1, None)
    corpora.retrieve(ids[1], "q", 1, None)
    corpora.retrieve(ids[0], "q", 1, None)      # touch 0: 1 is now the least recently used
    corpora.retrieve(ids[2], "q", 1, None)
    assert list(corpora._CACHE) == [ids[0], ids[2]]
