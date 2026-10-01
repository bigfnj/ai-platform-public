"""Seed ingest: fingerprinting, and the three ways a collection can go away.

Ingest is the only writer the rail has, and its two hardest cases are both *removals* that
produce no error when they are skipped. ingest.py says so in a comment that asks not to be
simplified away: a folder that has been emptied, and a folder that has been deleted outright,
both leave their old chunks indexed forever, and those chunks keep being retrieved and cited
through every later edit because nothing ever removes them. That defect ran for weeks in the
sibling smb-partner-enablement rail. It is the reason this file exists.

Every tree here is invented under tmp_path and the embedder is a stub — nothing reads the real
seed corpus, writes the real volume, or reaches the broker.
"""
from __future__ import annotations

import json

import pytest

from gemini_cx import broker, config, ingest, store

BODY = ("GECX pricing is not published anywhere Google will stand behind, so quote nothing. "
        "Price it through your Google Cloud account team instead. ")


@pytest.fixture()
def seed(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    root = tmp_path / "knowledge-base"
    root.mkdir()
    monkeypatch.setattr(config, "SEED_KB_DIR", root)
    store.init()
    yield root
    store.reload_matrix()


@pytest.fixture()
def embedder(monkeypatch):
    """A stub embedder that records how many texts it was asked for. A zero here is the whole
    point of fingerprinting: a container restart must not re-embed an unchanged corpus."""
    calls: list[int] = []

    def spy(texts, *, model):
        calls.append(len(texts))
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(broker, "embed", spy)
    return calls


def collection(root, name, *, files=("01-what.md",), body=BODY):
    folder = root / name
    folder.mkdir(exist_ok=True)
    for f in files:
        (folder / f).write_text(f"# {name}\n\n{body * 3}", encoding="utf-8")
    return folder


def statuses(report):
    return {c["collection"]: c["status"] for c in report["collections"]}


# --- the happy path -------------------------------------------------------------------------

def test_every_subfolder_becomes_a_collection(seed, embedder):
    collection(seed, "pricing-and-licensing")
    collection(seed, "gecx-overview")
    report = ingest.ingest_seed()
    assert report["found"] is True
    assert statuses(report) == {"pricing-and-licensing": "ingested", "gecx-overview": "ingested"}
    assert {c["name"] for c in store.collections()} == {"pricing-and-licensing", "gecx-overview"}
    assert store.stats()["chunks"] > 0


def test_a_collection_is_registered_as_seed_with_a_readable_label(seed, embedder):
    collection(seed, "pricing-and-licensing")
    ingest.ingest_seed()
    row = store.collections()[0]
    assert row["origin"] == "seed"
    assert row["label"] == "Pricing And Licensing"


def test_chunks_carry_their_collection_and_relative_source(seed, embedder):
    collection(seed, "cx-agent-studio", files=("06-guardrails.md",))
    ingest.ingest_seed()
    chunk = store.snapshot()[0][0]
    assert chunk["collection"] == "cx-agent-studio"
    assert chunk["source"] == "06-guardrails.md"


def test_a_missing_seed_tree_is_reported_not_raised(seed, embedder, tmp_path, monkeypatch):
    """The container mounts the seed tree read-only; a bad mount must leave the rail booting
    and answering /api/health rather than crashing at startup."""
    monkeypatch.setattr(config, "SEED_KB_DIR", tmp_path / "absent")
    report = ingest.ingest_seed()
    assert report["found"] is False
    assert report["collections"] == []
    assert embedder == []


def test_underscore_folders_are_not_collections(seed, embedder):
    collection(seed, "_scratch")
    collection(seed, "discovery")
    assert set(statuses(ingest.ingest_seed())) == {"discovery"}


# --- fingerprinting --------------------------------------------------------------------------

def test_an_unchanged_corpus_is_not_re_embedded(seed, embedder):
    """A container restart is meant to be free. Without this the whole corpus is re-embedded
    on every boot, which on an 8 GB card is minutes of GPU the user is waiting through."""
    collection(seed, "discovery")
    ingest.ingest_seed()
    embedder.clear()
    report = ingest.ingest_seed()
    assert statuses(report) == {"discovery": "unchanged"}
    assert embedder == []


def test_only_the_changed_collection_is_re_embedded(seed, embedder):
    collection(seed, "discovery")
    collection(seed, "solution-plays")
    ingest.ingest_seed()
    embedder.clear()
    (seed / "discovery" / "01-what.md").write_text(f"# discovery\n\n{BODY * 4}",
                                                   encoding="utf-8")
    report = ingest.ingest_seed()
    assert statuses(report) == {"discovery": "ingested", "solution-plays": "unchanged"}
    assert len(embedder) == 1


def test_force_re_embeds_everything(seed, embedder):
    """POST /api/ingest?force=true, the admin's escape hatch when the embedder itself moved."""
    collection(seed, "discovery")
    ingest.ingest_seed()
    embedder.clear()
    assert statuses(ingest.ingest_seed(force=True)) == {"discovery": "ingested"}
    assert embedder


def test_the_fingerprint_covers_filenames_not_just_bytes(seed, embedder):
    """Renaming a file changes what a citation points at, so it has to count as a change."""
    collection(seed, "discovery", files=("01-what.md",))
    ingest.ingest_seed()
    embedder.clear()
    (seed / "discovery" / "01-what.md").rename(seed / "discovery" / "02-what.md")
    assert statuses(ingest.ingest_seed()) == {"discovery": "ingested"}


def test_fingerprints_are_persisted_as_meta(seed, embedder):
    collection(seed, "discovery")
    ingest.ingest_seed()
    assert "discovery" in json.loads(store.get_meta("seed_fingerprints"))


def test_a_corrupt_fingerprint_blob_re_ingests_rather_than_crashing(seed, embedder):
    collection(seed, "discovery")
    ingest.ingest_seed()
    store.set_meta("seed_fingerprints", "not json")
    embedder.clear()
    assert statuses(ingest.ingest_seed()) == {"discovery": "ingested"}


# --- the two removals ---------------------------------------------------------------------------

def test_emptying_a_folder_clears_its_chunks(seed, embedder):
    """Do not simplify this away: without it the old chunks stay indexed and keep being cited
    long after the content that justified them was deleted."""
    folder = collection(seed, "commerce-agents")
    ingest.ingest_seed()
    assert store.stats()["chunks"] > 0
    for f in folder.iterdir():
        f.unlink()
    report = ingest.ingest_seed()
    entry = next(c for c in report["collections"] if c["collection"] == "commerce-agents")
    assert entry["status"] == "empty"
    assert entry["removed"] > 0
    assert store.stats()["chunks"] == 0


def test_deleting_a_folder_retires_the_collection(seed, embedder):
    """Never iterated, so nothing else would ever remove it. Renaming a collection is exactly
    this case, and it is how a deck question comes to cite a document that no longer exists."""
    collection(seed, "old-name")
    collection(seed, "discovery")
    ingest.ingest_seed()
    for f in (seed / "old-name").iterdir():
        f.unlink()
    (seed / "old-name").rmdir()
    report = ingest.ingest_seed()
    entry = next(c for c in report["collections"] if c["collection"] == "old-name")
    assert entry["status"] == "removed"
    assert entry["removed"] > 0
    assert {c["name"] for c in store.collections()} == {"discovery"}


def test_a_retired_collection_drops_its_fingerprint(seed, embedder):
    """Otherwise re-adding the folder later reads as unchanged and is never re-embedded."""
    collection(seed, "old-name")
    ingest.ingest_seed()
    for f in (seed / "old-name").iterdir():
        f.unlink()
    (seed / "old-name").rmdir()
    ingest.ingest_seed()
    assert "old-name" not in json.loads(store.get_meta("seed_fingerprints"))


def test_an_upload_is_not_retired_by_a_seed_ingest(seed, embedder):
    """Only seed-origin collections are the seed tree's to remove. A user document lives in
    the same index and must survive a re-ingest of the corpus around it."""
    ingest.ingest_upload("my-notes", f"# Notes\n\n{BODY * 3}", source="notes.md")
    collection(seed, "discovery")
    ingest.ingest_seed()
    assert "my-notes" in {c["name"] for c in store.collections()}


# --- failure -----------------------------------------------------------------------------------

def test_an_embedder_failure_is_reported_per_collection(seed, embedder, monkeypatch):
    """One unreachable embedder must not stop the rail booting with the corpus it already
    has, and must not take the other collections down with it."""
    collection(seed, "discovery")
    collection(seed, "solution-plays")

    def boom(texts, *, model):
        raise broker.BrokerError("embedder down")

    monkeypatch.setattr(broker, "embed", boom)
    report = ingest.ingest_seed()
    assert set(statuses(report).values()) == {"error"}
    assert store.stats()["chunks"] == 0


def test_a_failed_collection_is_retried_on_the_next_boot(seed, embedder, monkeypatch):
    """The fingerprint must NOT be recorded for a collection that failed, or a transient
    broker outage leaves that collection permanently unindexed and silently uncitable."""
    collection(seed, "discovery")

    def boom(texts, *, model):
        raise broker.BrokerError("embedder down")

    monkeypatch.setattr(broker, "embed", boom)
    ingest.ingest_seed()
    monkeypatch.setattr(broker, "embed",
                        lambda texts, *, model: [[1.0, 0.0, 0.0] for _ in texts])
    assert statuses(ingest.ingest_seed()) == {"discovery": "ingested"}
    assert store.stats()["chunks"] > 0


# --- ad-hoc uploads ------------------------------------------------------------------------------

def test_upload_indexes_a_document_as_its_own_collection(seed, embedder):
    count = ingest.ingest_upload("my notes", f"# Notes\n\n{BODY * 3}", source="notes.md")
    assert count > 0
    row = next(c for c in store.collections() if c["name"] == "my notes")
    assert row["origin"] == "upload"
    assert row["label"] == "My Notes"
    assert store.snapshot()[0][0]["source"] == "notes.md"


def test_upload_replaces_a_document_of_the_same_name(seed, embedder):
    ingest.ingest_upload("notes", f"# One\n\n{BODY * 3}", source="a.md")
    ingest.ingest_upload("notes", f"# Two\n\n{BODY * 3}", source="b.md")
    assert {c["source"] for c in store.snapshot()[0]} == {"b.md"}


def test_upload_of_unchunkable_text_indexes_nothing_and_asks_for_no_embedding(seed, embedder):
    assert ingest.ingest_upload("notes", "hi", source="a.md") == 0
    assert embedder == []
    assert store.collections() == []
