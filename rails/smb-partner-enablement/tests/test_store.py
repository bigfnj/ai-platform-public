"""The SQLite chunk index and the in-memory matrix it feeds.

``store`` is the one piece of this rail that keeps state, and the invariant that matters is not
"the rows are there" but "the chunk list and the vector matrix are the same length and in the
same order". Everything a partner sees as a citation is an index into that pair, so a
desynchronised rebuild does not crash — it silently attributes an answer to the wrong document.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from smb_partner import config, store

from conftest import chunk, unit_matrix

ROWS = [
    chunk("The Business family caps at 300 seats pooled across Basic, Standard and Premium.",
          source="caps.md", title="Seat caps", collection="csp-licensing"),
    chunk("Solutions Partner designations are earned against a capability score.",
          source="designations.md", title="Designations", collection="csp-licensing"),
    chunk("Deal registration requires a Microsoft-managed account.",
          source="cosell.md", title="Co-sell", collection="csp-licensing"),
]


def _seed(collection="csp-licensing", rows=None, origin="seed", label="CSP Licensing"):
    rows = ROWS if rows is None else rows
    return store.replace_collection(collection, label, origin, rows,
                                    unit_matrix([r["text"] for r in rows]))


def test_init_creates_the_schema_under_the_configured_path(isolated_rail):
    """The autouse fixture redirects every writable path; if it ever stopped working this is
    the test that notices, before the suite starts writing into the real data volume."""
    db = Path(config.DB_PATH)
    assert db.is_file()
    assert db.is_relative_to(isolated_rail)
    assert Path(config.DATA_DIR).is_relative_to(isolated_rail)
    assert Path(config.UPLOADS_DIR).is_dir()

    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"collections", "chunks", "meta", "idx_chunks_collection"} <= names


def test_init_is_idempotent():
    """Every ``create_api()`` calls it, and the container restarts on an existing volume."""
    _seed()
    store.init()
    assert store.stats()["chunks"] == len(ROWS)


def test_meta_round_trip():
    assert store.get_meta("seed_fingerprints") == ""
    assert store.get_meta("seed_fingerprints", "{}") == "{}"
    store.set_meta("seed_fingerprints", '{"csp-licensing": "abc"}')
    assert store.get_meta("seed_fingerprints") == '{"csp-licensing": "abc"}'
    store.set_meta("seed_fingerprints", '{"csp-licensing": "def"}')
    assert store.get_meta("seed_fingerprints") == '{"csp-licensing": "def"}'


def test_replace_collection_round_trips_rows_and_vectors():
    assert _seed() == 3
    chunks, matrix = store.snapshot()
    assert [c["source"] for c in chunks] == ["caps.md", "designations.md", "cosell.md"]
    assert [c["title"] for c in chunks] == ["Seat caps", "Designations", "Co-sell"]
    assert {c["collection"] for c in chunks} == {"csp-licensing"}
    assert matrix.dtype == np.float32
    assert matrix.shape == (3, 64)
    # The blob survives the sqlite round trip bit for bit, not merely approximately.
    assert np.array_equal(matrix, unit_matrix([r["text"] for r in ROWS]))


def test_collections_reports_origin_label_and_size():
    _seed()
    _seed("uploads-notes", ROWS[:1], origin="upload", label="Uploads Notes")
    listed = {c["name"]: c for c in store.collections()}
    assert [c["name"] for c in store.collections()] == ["csp-licensing", "uploads-notes"]
    assert listed["csp-licensing"]["chunks"] == 3
    assert listed["csp-licensing"]["origin"] == "seed"
    assert listed["csp-licensing"]["label"] == "CSP Licensing"
    assert listed["uploads-notes"]["origin"] == "upload"
    assert listed["csp-licensing"]["ingested_at"]  # datetime('now'), not NULL


def test_replace_collection_swaps_rather_than_appends():
    """A re-ingest of changed content must leave no trace of the previous version — stale
    chunks that keep being retrieved and cited is exactly the failure ingest guards against."""
    _seed()
    replacement = [chunk("Copilot in Microsoft 365 is scoped to customers under 300 employees.",
                         source="copilot.md", collection="csp-licensing")]
    assert _seed(rows=replacement) == 1
    chunks, matrix = store.snapshot()
    assert [c["source"] for c in chunks] == ["copilot.md"]
    assert matrix.shape[0] == 1
    assert store.collections()[0]["chunks"] == 1


def test_replace_collection_leaves_other_collections_alone():
    _seed()
    _seed("smb-segment", ROWS[:2])
    _seed(rows=ROWS[:1])
    sizes = {c["name"]: c["chunks"] for c in store.collections()}
    assert sizes == {"csp-licensing": 1, "smb-segment": 2}


def test_replace_collection_rejects_a_length_mismatch():
    """The guard exists because the mismatch is silent otherwise: the rows land, the matrix is
    a different height, and every citation index after the short one points at the wrong text."""
    with pytest.raises(ValueError, match="same length"):
        store.replace_collection("csp-licensing", "CSP", "seed", ROWS,
                                 unit_matrix([r["text"] for r in ROWS[:2]]))
    assert store.stats()["chunks"] == 0


def test_delete_collection_reports_what_it_removed():
    _seed()
    _seed("smb-segment", ROWS[:2])
    assert store.delete_collection("csp-licensing") == 3
    assert [c["name"] for c in store.collections()] == ["smb-segment"]
    assert store.stats()["chunks"] == 2


def test_delete_collection_of_an_absent_name_is_zero_not_an_error():
    assert store.delete_collection("never-existed") == 0


def test_rows_without_a_vector_stay_out_of_the_matrix():
    """``reload_matrix`` selects ``WHERE vec IS NOT NULL``. A row that slipped in unembedded
    must be dropped from BOTH sides, or the chunk list and the matrix stop lining up."""
    _seed()
    with store.connect() as conn:
        conn.execute("INSERT INTO chunks(collection, source, title, text, vec) "
                     "VALUES('csp-licensing', 'orphan.md', '', 'no vector here', NULL)")
    store.reload_matrix()
    chunks, matrix = store.snapshot()
    assert len(chunks) == matrix.shape[0] == 3
    assert "orphan.md" not in {c["source"] for c in chunks}
    # The row is still in SQLite and still counted by collections(); only retrieval skips it.
    assert store.collections()[0]["chunks"] == 4


def test_snapshot_hands_back_an_aligned_pair():
    _seed()
    chunks, matrix = store.snapshot()
    assert len(chunks) == matrix.shape[0]
    # Same objects on a second call: retrieval must not pay for a rebuild per query.
    assert store.snapshot()[0] is chunks


def test_stats_on_an_empty_index():
    assert store.stats() == {"chunks": 0, "dims": 0, "collections": 0}
    assert store.snapshot()[1].shape == (0, 0)


def test_stats_after_ingest():
    _seed()
    _seed("smb-segment", ROWS[:1])
    assert store.stats() == {"chunks": 4, "dims": 64, "collections": 2}


def test_emptying_the_index_returns_dims_to_zero():
    _seed()
    store.delete_collection("csp-licensing")
    assert store.stats() == {"chunks": 0, "dims": 0, "collections": 0}
    assert store.snapshot()[0] == []
