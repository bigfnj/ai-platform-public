"""Retrieval: markdown to chunks, chunks to vectors, vectors to a ranked, citable shortlist.

The embedder is faked (a deterministic bag-of-words hash), the numpy path is real. That is the
right seam: what can actually go wrong here is the ranking, the collection scoping and the
degradation when there is nothing to retrieve — none of which needs a GPU to exercise.
"""
from __future__ import annotations

import numpy as np
import pytest

from smb_partner import broker, config, rag, store

from conftest import chunk, unit_matrix

MARKDOWN = """# CSP licensing

An opening paragraph long enough to clear the forty character floor comfortably.

## Seat caps

The Microsoft 365 Business family caps at 300 seats pooled across Basic, Standard and Premium.

## Copilot trial

The partner-led Copilot trial covers 25 seats and is scoped to customers under 300 employees.
"""

CORPUS = [
    chunk("Seat caps\n\nThe Business family caps at 300 seats pooled across the plans.",
          source="caps.md", title="Seat caps", collection="csp-licensing"),
    chunk("Copilot trial\n\nThe partner-led Copilot trial covers 25 seats.",
          source="copilot.md", title="Copilot trial", collection="csp-licensing"),
    chunk("Designations\n\nSolutions Partner designations are earned on a capability score.",
          source="designations.md", title="Designations", collection="designations"),
]


@pytest.fixture()
def corpus(seed_corpus):
    seed_corpus("csp-licensing", CORPUS[:2])
    seed_corpus("designations", CORPUS[2:])
    return CORPUS


# --- chunking ---------------------------------------------------------------------------

def test_chunk_markdown_carries_the_nearest_heading():
    chunks = rag.chunk_markdown(MARKDOWN, source="csp.md", collection="csp-licensing")
    assert [c["title"] for c in chunks] == ["CSP licensing", "Seat caps", "Copilot trial"]
    assert all(c["source"] == "csp.md" for c in chunks)
    assert all(c["collection"] == "csp-licensing" for c in chunks)
    # The heading is prepended to the body, so the citation and the embedding both carry it.
    assert chunks[1]["text"].startswith("Seat caps\n\n")
    assert "300 seats pooled" in chunks[1]["text"]


def test_a_new_heading_closes_the_previous_chunk():
    """Without the flush on a heading, two unrelated sections would merge into one retrieval
    unit and the citation would name the wrong one."""
    chunks = rag.chunk_markdown(MARKDOWN, source="csp.md")
    assert "Copilot" not in chunks[1]["text"]
    assert "300 seats pooled" not in chunks[2]["text"]


def test_short_paragraphs_merge_up_to_the_floor():
    """A one-line bullet must not become its own retrieval unit competing with a paragraph."""
    para = "A paragraph of roughly sixty characters, give or take a few. "
    chunks = rag.chunk_markdown("\n\n".join([para] * 5), source="x.md")
    assert len(chunks) == 2
    assert len(chunks[0]["text"]) >= rag.MIN_CHARS


def test_a_fragment_under_forty_characters_is_dropped():
    assert rag.chunk_markdown("Too short.", source="x.md") == []
    assert rag.chunk_markdown("# Heading only", source="x.md") == []


def test_chunk_text_is_capped_at_max_chars():
    chunks = rag.chunk_markdown("word " * 600, source="x.md")
    assert chunks
    assert all(len(c["text"]) <= rag.MAX_CHARS for c in chunks)


def test_chunk_markdown_tolerates_empty_input():
    assert rag.chunk_markdown("", source="x.md") == []
    assert rag.chunk_markdown(None, source="x.md") == []


def test_load_collection_skips_authoring_scaffolding(tmp_path):
    folder = tmp_path / "csp-licensing"
    (folder / "nested").mkdir(parents=True)
    (folder / "caps.md").write_text(MARKDOWN, encoding="utf-8")
    (folder / "nested" / "more.md").write_text(MARKDOWN, encoding="utf-8")
    (folder / "_TEMPLATE.md").write_text(MARKDOWN, encoding="utf-8")
    (folder / "README.md").write_text(MARKDOWN, encoding="utf-8")
    (folder / "notes.txt").write_text(MARKDOWN, encoding="utf-8")

    rows = rag.load_collection(folder, "csp-licensing")
    # Relative, posix-separated — the citation a partner sees must not depend on the host OS.
    assert sorted({r["source"] for r in rows}) == ["caps.md", "nested/more.md"]
    assert {r["collection"] for r in rows} == {"csp-licensing"}


# --- embedding --------------------------------------------------------------------------

def test_embed_texts_normalises_so_a_dot_product_is_cosine(fake_broker):
    arr = rag.embed_texts(["seat caps", "copilot trial"])
    assert arr.dtype == np.float32
    assert np.allclose(np.linalg.norm(arr, axis=1), 1.0, atol=1e-5)


def test_embed_texts_batches_but_keeps_input_order(fake_broker):
    texts = [f"document number {i}" for i in range(5)]
    arr = rag.embed_texts(texts, batch=2)
    assert [len(call) for call in fake_broker.embeds] == [2, 2, 1]
    assert arr.shape[0] == 5
    assert np.allclose(arr, unit_matrix(texts), atol=1e-6)


def test_embed_texts_raises_when_the_embedder_returns_nothing(monkeypatch, fake_broker):
    monkeypatch.setattr(broker, "embed", lambda text, *, model: [])
    with pytest.raises(broker.BrokerError, match="no usable vectors"):
        rag.embed_texts(["anything"])


def test_embed_texts_uses_the_configured_embedder(monkeypatch, fake_broker):
    seen = {}

    def spy(text, *, model):
        seen["model"] = model
        return fake_broker.embed(text, model=model)

    monkeypatch.setattr(broker, "embed", spy)
    rag.embed_texts(["seat caps"])
    assert seen["model"] == config.EMBED_MODEL


# --- ranking ----------------------------------------------------------------------------

def test_rank_puts_the_matching_chunk_first(corpus, fake_broker):
    chunks, matrix = store.snapshot()
    hits = rag.rank("what does the partner-led Copilot trial cover", chunks, matrix)
    assert hits[0]["source"] == "copilot.md"
    assert hits[0]["title"] == "Copilot trial"
    assert hits[0]["score"] > hits[-1]["score"]
    assert all(isinstance(h["score"], float) for h in hits)


def test_rank_returns_hits_in_descending_score_order(corpus, fake_broker):
    chunks, matrix = store.snapshot()
    scores = [h["score"] for h in rag.rank("seats and designations", chunks, matrix)]
    assert scores == sorted(scores, reverse=True)


def test_rank_honours_top_k(corpus, fake_broker):
    chunks, matrix = store.snapshot()
    assert len(rag.rank("seats", chunks, matrix, k=1)) == 1
    assert len(rag.rank("seats", chunks, matrix, k=99)) == 3


def test_rank_with_k_zero_falls_back_to_the_configured_default(corpus, fake_broker,
                                                               monkeypatch):
    monkeypatch.setattr(config, "TOP_K", 2)
    chunks, matrix = store.snapshot()
    assert len(rag.rank("seats", chunks, matrix, k=0)) == 2


def test_rank_scopes_to_the_requested_collections(corpus, fake_broker):
    chunks, matrix = store.snapshot()
    hits = rag.rank("capability score", chunks, matrix, collections={"csp-licensing"})
    assert {h["collection"] for h in hits} == {"csp-licensing"}
    assert "designations.md" not in {h["source"] for h in hits}


def test_rank_with_a_collection_nobody_indexed_returns_nothing(corpus, fake_broker):
    """Masked-out rows are scored ``-inf`` and then filtered. If the filter were dropped the
    partner would get three citations to documents that were explicitly excluded."""
    chunks, matrix = store.snapshot()
    assert rag.rank("seats", chunks, matrix, collections={"not-a-collection"}) == []


def test_rank_on_an_empty_corpus_degrades_without_calling_the_embedder(fake_broker):
    """First boot, or a knowledge base that failed to ingest. Answering ungrounded is the
    designed behaviour; spending an embed round-trip to discover there is nothing to rank
    is not."""
    chunks, matrix = store.snapshot()
    assert chunks == []
    assert rag.rank("anything at all", chunks, matrix) == []
    assert fake_broker.embeds == []


# --- context ----------------------------------------------------------------------------

def test_build_context_numbers_hits_from_one_and_names_the_source(corpus, fake_broker):
    chunks, matrix = store.snapshot()
    hits = rag.rank("seat caps", chunks, matrix, k=2)
    context = rag.build_context(hits)
    assert context.startswith("[1] (")
    assert "[2] (" in context
    assert f"({hits[0]['source']})" in context
    assert hits[0]["text"] in context


def test_build_context_of_no_hits_is_empty():
    assert rag.build_context([]) == ""
