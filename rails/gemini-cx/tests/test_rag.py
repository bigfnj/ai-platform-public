"""Retrieval: markdown -> chunks -> embeddings -> cosine ranking.

The embedder is faked throughout — a deterministic function of the text, so ranking is exact
and reproducible and nothing reaches the broker at :11500 or the GPU behind it. What is NOT
faked is the numpy path: chunking, L2 normalisation, the matrix multiply, the collection mask
and the top-k slice all run for real, because that is where a wrong citation comes from.

Two behaviours here are the rail's honesty guarantees rather than performance details:

* an empty corpus must return no hits **without embedding the query**, so a rail whose ingest
  has not finished does not burn a broker round-trip per keystroke and then cite nothing;
* a scope that matches no chunk must return an empty list, never a -inf-scored chunk from
  another collection — that is a citation the user has no way to know is wrong.
"""
from __future__ import annotations

import numpy as np
import pytest

from gemini_cx import broker, config, rag

# A one-hot vocabulary: the query embeds to whichever axis its first word names, so the
# expected ranking is readable from the test rather than from a similarity score.
VOCAB = ["pricing", "languages", "guardrails", "telephony"]


def fake_embed(texts, *, model):
    out = []
    for t in texts:
        v = [1.0 if w in t.lower() else 0.0 for w in VOCAB]
        out.append(v if any(v) else [0.25, 0.25, 0.25, 0.25])
    return out


@pytest.fixture()
def embedder(monkeypatch):
    """Records every call so batching and 'was it called at all' are assertable."""
    calls: list[list[str]] = []

    def spy(texts, *, model):
        calls.append(list(texts))
        return fake_embed(texts, model=model)

    monkeypatch.setattr(broker, "embed", spy)
    return calls


def corpus(*pairs):
    """(collection, text) pairs -> the (chunks, matrix) shape store.snapshot() hands back."""
    chunks = [{"collection": coll, "source": f"{i}.md", "title": "T", "text": text}
              for i, (coll, text) in enumerate(pairs)]
    matrix = np.asarray(fake_embed([c["text"] for c in chunks], model="x"), dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
    return chunks, (matrix / norms).astype(np.float32)


# --- chunking -------------------------------------------------------------------------------

def test_chunk_carries_its_nearest_heading_as_the_title():
    """The title is what the user sees in the citation, and this corpus is heavily sectioned,
    so losing it turns a recognisable source into a filename."""
    md = ("# How is GECX priced?\n\n"
          + "GECX pricing is not published anywhere Google will stand behind. " * 4)
    chunk = rag.chunk_markdown(md, source="01-what-is-published.md",
                               collection="pricing-and-licensing")[0]
    assert chunk["title"] == "How is GECX priced?"
    assert chunk["text"].startswith("How is GECX priced?\n\n")
    assert chunk["source"] == "01-what-is-published.md"
    assert chunk["collection"] == "pricing-and-licensing"


def test_a_new_heading_closes_the_previous_chunk():
    md = ("## Text languages\n\n" + "Forty plus languages are supported for text. " * 6
          + "\n\n## Audio languages\n\n" + "Ten languages are supported audio to audio. " * 6)
    titles = [c["title"] for c in rag.chunk_markdown(md, source="langs.md")]
    assert titles == ["Text languages", "Audio languages"]


def test_short_paragraphs_merge_up_to_the_floor():
    """A one-line bullet must not become its own retrieval unit competing with a paragraph."""
    para = "x" * 120
    chunks = rag.chunk_markdown(f"{para}\n\n{para}", source="a.md")
    assert len(chunks) == 1
    assert len(chunks[0]["text"]) >= rag.MIN_CHARS


def test_the_tail_below_the_floor_is_still_kept():
    """Merging stops at the floor, but the leftover is flushed rather than dropped — the last
    paragraph of a file is often the one that states the status."""
    para = "x" * 120
    chunks = rag.chunk_markdown(f"{para}\n\n{para}\n\n{para}", source="a.md")
    assert [len(c["text"]) for c in chunks] == [242, 120]


def test_a_long_section_is_capped():
    chunks = rag.chunk_markdown("y" * 4000, source="a.md")
    assert [len(c["text"]) for c in chunks] == [rag.MAX_CHARS]


def test_scraps_below_the_floor_are_dropped():
    """40 characters of prose is a stray line, not a retrieval unit."""
    assert rag.chunk_markdown("# Heading\n\ntiny", source="a.md") == []
    assert rag.chunk_markdown("", source="a.md") == []


def test_a_heading_with_body_on_the_same_block_keeps_both():
    md = "# Status\n" + "Commerce agents are coming soon per the documentation. " * 5
    chunk = rag.chunk_markdown(md, source="a.md")[0]
    assert chunk["title"] == "Status"
    assert "coming soon" in chunk["text"]


# --- loading a collection off disk -------------------------------------------------------------

def test_load_collection_skips_the_authoring_scaffolding(tmp_path):
    """_TEMPLATE.md and per-folder READMEs are instructions to the author. Indexing them makes
    the assistant quote its own style guide as GECX fact."""
    body = "Guardrails block a response before it reaches the caller. " * 5
    (tmp_path / "01-guardrails.md").write_text(f"# Guardrails\n\n{body}", encoding="utf-8")
    (tmp_path / "_TEMPLATE.md").write_text(f"# Template\n\n{body}", encoding="utf-8")
    (tmp_path / "README.md").write_text(f"# Readme\n\n{body}", encoding="utf-8")
    got = rag.load_collection(tmp_path, "cx-agent-studio")
    assert {c["source"] for c in got} == {"01-guardrails.md"}
    assert {c["collection"] for c in got} == {"cx-agent-studio"}


def test_load_collection_recurses_and_uses_posix_relative_sources(tmp_path):
    """The source string is a citation the user reads, and it must not vary with the OS."""
    nested = tmp_path / "voice"
    nested.mkdir()
    body = "AudioCodes is a validated Google partner for telephony integration. " * 4
    (nested / "03-telephony.md").write_text(f"# Telephony\n\n{body}", encoding="utf-8")
    got = rag.load_collection(tmp_path, "deployment-and-channels")
    assert [c["source"] for c in got] == ["voice/03-telephony.md"]


def test_load_collection_of_an_empty_folder_is_empty_not_an_error(tmp_path):
    assert rag.load_collection(tmp_path, "empty") == []


# --- embedding -----------------------------------------------------------------------------

def test_embed_texts_l2_normalises_so_a_dot_product_is_a_cosine(embedder):
    arr = rag.embed_texts(["pricing", "languages and pricing"])
    assert arr.shape == (2, len(VOCAB))
    assert np.allclose(np.linalg.norm(arr, axis=1), 1.0, atol=1e-5)


def test_embed_texts_batches_rather_than_sending_the_corpus_in_one_request(embedder):
    rag.embed_texts([f"chunk {i}" for i in range(70)], batch=32)
    assert [len(c) for c in embedder] == [32, 32, 6]


def test_embed_texts_asks_the_configured_embedder(monkeypatch):
    seen = {}

    def spy(texts, *, model):
        seen["model"] = model
        return fake_embed(texts, model=model)

    monkeypatch.setattr(broker, "embed", spy)
    rag.embed_texts(["pricing"])
    assert seen["model"] == config.EMBED_MODEL


def test_embed_texts_raises_rather_than_indexing_garbage(monkeypatch):
    """An embedder that answers with nothing must not produce a zero-row matrix that then
    silently desynchronises from the rows it was supposed to describe."""
    monkeypatch.setattr(broker, "embed", lambda texts, *, model: [])
    with pytest.raises(broker.BrokerError):
        rag.embed_texts(["pricing"])


# --- ranking --------------------------------------------------------------------------------

def test_rank_puts_the_matching_chunk_first(embedder):
    chunks, matrix = corpus(("pricing-and-licensing", "pricing is not published"),
                            ("models-and-languages", "languages for audio"),
                            ("cx-agent-studio", "guardrails and what they block"))
    hits = rag.rank("pricing", chunks, matrix)
    assert hits[0]["collection"] == "pricing-and-licensing"
    assert hits[0]["score"] == pytest.approx(1.0)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_rank_returns_a_citable_row_for_every_hit(embedder):
    chunks, matrix = corpus(("pricing-and-licensing", "pricing is not published"))
    hit = rag.rank("pricing", chunks, matrix)[0]
    assert {"collection", "source", "title", "text", "score"} <= set(hit)


def test_rank_honours_k_and_falls_back_to_the_configured_top_k(embedder):
    pairs = [("c", f"pricing chunk {i}") for i in range(10)]
    chunks, matrix = corpus(*pairs)
    assert len(rag.rank("pricing", chunks, matrix, k=3)) == 3
    assert len(rag.rank("pricing", chunks, matrix)) == config.TOP_K


def test_rank_scopes_to_the_requested_collections(embedder):
    chunks, matrix = corpus(("pricing-and-licensing", "pricing is not published"),
                            ("models-and-languages", "pricing of languages"))
    hits = rag.rank("pricing", chunks, matrix, collections={"models-and-languages"})
    assert {h["collection"] for h in hits} == {"models-and-languages"}


def test_a_scope_that_matches_nothing_returns_nothing(embedder):
    """The masked-out chunks score -inf. They must be dropped, not cited with a bad score."""
    chunks, matrix = corpus(("pricing-and-licensing", "pricing is not published"))
    assert rag.rank("pricing", chunks, matrix, collections={"renamed-away"}) == []


def test_an_empty_corpus_returns_no_hits_without_calling_the_embedder(embedder):
    """The state the rail is in for the whole of its first boot, while ingest is still
    running. It must degrade to an ungrounded answer, not to a broker round-trip per ask."""
    assert rag.rank("pricing", [], np.zeros((0, 0), dtype=np.float32)) == []
    assert rag.rank("pricing", [], np.zeros((0, 4), dtype=np.float32)) == []
    assert embedder == []


def test_rank_propagates_a_broker_failure_rather_than_citing_nothing(monkeypatch):
    """Silently returning [] here would look identical to "the corpus does not cover this",
    which is the one thing this rail must never say untruthfully."""
    def boom(_texts, *, model):
        raise broker.BrokerError("embedder down")

    monkeypatch.setattr(broker, "embed", boom)
    chunks, matrix = corpus(("c", "pricing is not published"))
    with pytest.raises(broker.BrokerError):
        rag.rank("pricing", chunks, matrix)


# --- the context block the model cites against ---------------------------------------------------

def test_build_context_numbers_from_one_and_names_the_source():
    hits = [{"source": "01-what-is-published.md", "text": "pricing is not published"},
            {"source": "02-figures.md", "text": "never state a per-seat figure"}]
    block = rag.build_context(hits)
    assert block.startswith("[1] (01-what-is-published.md) pricing is not published")
    assert "[2] (02-figures.md)" in block


def test_build_context_of_no_hits_is_empty_so_the_caller_can_say_so():
    assert rag.build_context([]) == ""
