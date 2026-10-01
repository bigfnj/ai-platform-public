"""The chunk index: SQLite rows plus the in-memory matrix they are projected into.

store.py holds two representations of the same corpus, and its own docstring says why that is
the dangerous part: "a partial rebuild that silently desynchronises the matrix from the rows is
far worse than a second of work". A desync is not an exception — it is a citation pointing at
the wrong document, which reads exactly like a correct answer. So most of what follows is about
the rows and the matrix staying the same length and the same order through every mutation the
rail performs.

Nothing here touches the real data volume: DB_PATH, DATA_DIR and UPLOADS_DIR are all redirected
under tmp_path before init(), and the module-level matrix is rebuilt from that empty database.
"""
from __future__ import annotations

import numpy as np
import pytest

from gemini_cx import config, store


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    store.init()
    yield tmp_path / "var"
    # The chunk list and matrix are module globals; leave them empty for the next test.
    store.reload_matrix()


def rows(*pairs):
    return [{"source": src, "title": src.rsplit(".", 1)[0].upper(), "text": text}
            for src, text in pairs]


def unit(*vectors) -> np.ndarray:
    return np.asarray(vectors, dtype=np.float32)


# --- schema -------------------------------------------------------------------------------

def test_init_creates_the_data_directories(db):
    assert db.is_dir()
    assert (db / "uploads").is_dir()


def test_init_creates_the_three_tables():
    with store.connect() as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"collections", "chunks", "meta"} <= names


def test_chunks_are_indexed_by_collection():
    """Every retrieval scope and every re-ingest filters on this column."""
    with store.connect() as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "idx_chunks_collection" in idx


def test_init_is_idempotent():
    """The container re-runs init() on every boot against a volume that already has rows."""
    store.replace_collection("c", "C", "seed", rows(("a.md", "alpha")), unit([1, 0]))
    store.init()
    assert store.stats()["chunks"] == 1


# --- round trip ---------------------------------------------------------------------------

def test_replace_collection_round_trips_rows_and_vectors():
    n = store.replace_collection(
        "gecx-overview", "Gecx Overview", "seed",
        rows(("01-what.md", "GECX is a CX solution"), ("02-four.md", "four components")),
        unit([1, 0, 0], [0, 1, 0]))
    assert n == 2
    chunks, matrix = store.snapshot()
    assert [c["source"] for c in chunks] == ["01-what.md", "02-four.md"]
    assert [c["collection"] for c in chunks] == ["gecx-overview"] * 2
    assert [c["title"] for c in chunks] == ["01-WHAT", "02-FOUR"]
    assert matrix.shape == (2, 3)
    assert matrix.dtype == np.float32
    assert np.allclose(matrix, [[1, 0, 0], [0, 1, 0]])


def test_vectors_survive_the_float32_blob_exactly():
    """Vectors go to disk as raw float32 bytes. A dtype slip here changes every score."""
    vec = unit([0.1234567, -0.7654321, 0.5])
    store.replace_collection("c", "C", "seed", rows(("a.md", "alpha")), vec)
    _, matrix = store.snapshot()
    assert np.array_equal(matrix, vec)


def test_replace_collection_swaps_rather_than_appends():
    """Re-ingesting a changed collection must not leave the old chunks retrievable — they
    would keep being cited alongside their own replacements."""
    store.replace_collection("c", "C", "seed", rows(("a.md", "old")), unit([1, 0]))
    store.replace_collection("c", "C", "seed", rows(("b.md", "new")), unit([0, 1]))
    chunks, matrix = store.snapshot()
    assert [c["text"] for c in chunks] == ["new"]
    assert matrix.shape == (1, 2)


def test_replace_collection_leaves_other_collections_alone():
    store.replace_collection("a", "A", "seed", rows(("a.md", "alpha")), unit([1, 0]))
    store.replace_collection("b", "B", "seed", rows(("b.md", "beta")), unit([0, 1]))
    store.replace_collection("a", "A", "seed", rows(("a2.md", "alpha2")), unit([1, 1]))
    assert {c["collection"] for c in store.snapshot()[0]} == {"a", "b"}
    assert store.stats()["chunks"] == 2


def test_replace_collection_refuses_a_length_mismatch():
    """The one input that would desynchronise rows from vectors, rejected before it lands."""
    with pytest.raises(ValueError):
        store.replace_collection("c", "C", "seed",
                                 rows(("a.md", "alpha"), ("b.md", "beta")), unit([1, 0]))


def test_a_refused_write_leaves_the_index_untouched():
    store.replace_collection("c", "C", "seed", rows(("a.md", "alpha")), unit([1, 0]))
    with pytest.raises(ValueError):
        store.replace_collection("c", "C", "seed", rows(("x.md", "x"), ("y.md", "y")),
                                 unit([1, 0]))
    assert [c["text"] for c in store.snapshot()[0]] == ["alpha"]


# --- deletion -----------------------------------------------------------------------------

def test_delete_collection_removes_chunks_and_the_registration():
    store.replace_collection("c", "C", "seed", rows(("a.md", "alpha"), ("b.md", "beta")),
                             unit([1, 0], [0, 1]))
    assert store.delete_collection("c") == 2
    assert store.stats() == {"chunks": 0, "dims": 0, "collections": 0}
    assert store.collections() == []


def test_delete_collection_on_an_unknown_name_is_zero_not_an_error():
    assert store.delete_collection("never-existed") == 0


# --- the rows/matrix invariant --------------------------------------------------------------

def test_reload_matrix_skips_rows_with_no_vector():
    """An unembedded row must not enter the chunk list either, or every index past it points
    at the wrong document and the citations silently shift."""
    store.replace_collection("c", "C", "seed", rows(("a.md", "alpha")), unit([1, 0]))
    with store.connect() as conn:
        conn.execute("INSERT INTO chunks(collection, source, title, text, vec) "
                     "VALUES('c', 'ghost.md', 'GHOST', 'no vector', NULL)")
    store.reload_matrix()
    chunks, matrix = store.snapshot()
    assert [c["source"] for c in chunks] == ["a.md"]
    assert matrix.shape[0] == len(chunks) == 1


def test_snapshot_is_aligned_after_every_mutation():
    store.replace_collection("a", "A", "seed", rows(("a.md", "alpha")), unit([1, 0]))
    store.replace_collection("b", "B", "seed", rows(("b.md", "beta"), ("c.md", "gamma")),
                             unit([0, 1], [1, 1]))
    store.delete_collection("a")
    chunks, matrix = store.snapshot()
    assert len(chunks) == matrix.shape[0] == 2


def test_snapshot_of_an_empty_corpus_is_a_zero_matrix_not_a_crash():
    chunks, matrix = store.snapshot()
    assert chunks == []
    assert matrix.size == 0


# --- registry and stats ---------------------------------------------------------------------

def test_collections_reports_label_origin_and_a_live_chunk_count():
    store.replace_collection("pricing-and-licensing", "Pricing And Licensing", "seed",
                             rows(("01.md", "unpublished"), ("02.md", "never quote")),
                             unit([1, 0], [0, 1]))
    store.replace_collection("my-notes", "My Notes", "upload", rows(("u.md", "note")),
                             unit([1, 1]))
    got = {c["name"]: c for c in store.collections()}
    assert got["pricing-and-licensing"]["origin"] == "seed"
    assert got["pricing-and-licensing"]["chunks"] == 2
    assert got["my-notes"]["origin"] == "upload"
    assert got["my-notes"]["label"] == "My Notes"
    assert got["pricing-and-licensing"]["ingested_at"]


def test_collections_are_returned_in_name_order():
    for name in ("zeta", "alpha", "mu"):
        store.replace_collection(name, name.title(), "seed", rows(("a.md", name)), unit([1, 0]))
    assert [c["name"] for c in store.collections()] == ["alpha", "mu", "zeta"]


def test_stats_counts_chunks_dimensions_and_collections():
    store.replace_collection("a", "A", "seed", rows(("a.md", "alpha")), unit([1, 0, 0, 0]))
    store.replace_collection("b", "B", "seed", rows(("b.md", "beta")), unit([0, 1, 0, 0]))
    assert store.stats() == {"chunks": 2, "dims": 4, "collections": 2}


def test_stats_counts_a_registered_but_empty_collection():
    """delete_collection is the only thing that unregisters; an empty ingest keeps the row."""
    store.replace_collection("a", "A", "seed", [], np.zeros((0, 0), dtype=np.float32))
    assert store.stats()["collections"] == 1
    assert store.stats()["chunks"] == 0


# --- meta -------------------------------------------------------------------------------------

def test_meta_round_trips_and_defaults():
    assert store.get_meta("absent") == ""
    assert store.get_meta("absent", "fallback") == "fallback"
    store.set_meta("seed_fingerprints", '{"gecx-overview": "abc"}')
    assert store.get_meta("seed_fingerprints") == '{"gecx-overview": "abc"}'


def test_set_meta_upserts_rather_than_duplicating():
    store.set_meta("k", "one")
    store.set_meta("k", "two")
    assert store.get_meta("k") == "two"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM meta WHERE key = 'k'").fetchone()[0] == 1
