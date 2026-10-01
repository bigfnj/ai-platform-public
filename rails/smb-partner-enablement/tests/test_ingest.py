"""Seed ingest — fingerprinting, and the three ways content leaves the corpus.

The interesting behaviour is all in the removal cases. A collection whose folder was emptied,
and one whose folder was deleted outright, both used to leave their chunks indexed forever:
placeholder scaffolding deleted from four folders kept being retrieved and cited for weeks of
edits afterwards, because nothing ever removed it. Those two paths and the fingerprint that
makes a restart free are what this file guards.
"""
from __future__ import annotations

import json
import shutil

import pytest

from smb_partner import broker, config, ingest, store

CAPS = """# Seat caps

The Microsoft 365 Business family caps at 300 seats pooled across Basic, Standard and Premium.
"""

TRIAL = """# Copilot trial

The partner-led Copilot trial covers 25 seats and is scoped to customers under 300 employees.
"""

DESIGNATIONS = """# Solutions Partner

A Solutions Partner designation is earned against a capability score and must be renewed.
"""

#: Two headed sections, so the chunker produces two retrieval units rather than merging them.
TWO_SECTIONS = CAPS + "\n" + TRIAL


@pytest.fixture()
def kb(isolated_rail):
    """A miniature seed knowledge base under the redirected ``SEED_KB_DIR``."""
    root = config.SEED_KB_DIR
    (root / "csp-licensing").mkdir()
    (root / "csp-licensing" / "caps.md").write_text(CAPS, encoding="utf-8")
    (root / "csp-licensing" / "trial.md").write_text(TRIAL, encoding="utf-8")
    (root / "designations").mkdir()
    (root / "designations" / "solutions-partner.md").write_text(DESIGNATIONS, encoding="utf-8")
    return root


def _status(report):
    return {c["collection"]: c["status"] for c in report["collections"]}


def test_a_missing_knowledge_base_is_reported_not_raised(isolated_rail, fake_broker):
    """The container mounts the seed read-only; a bad mount must not stop the rail booting."""
    shutil.rmtree(config.SEED_KB_DIR)
    report = ingest.ingest_seed()
    assert report["found"] is False
    assert report["collections"] == []


def test_each_subfolder_becomes_a_collection(kb, fake_broker):
    report = ingest.ingest_seed()
    assert report["found"] is True
    assert _status(report) == {"csp-licensing": "ingested", "designations": "ingested"}
    listed = {c["name"]: c for c in store.collections()}
    assert listed["csp-licensing"]["chunks"] == 2
    assert listed["csp-licensing"]["origin"] == "seed"
    assert listed["csp-licensing"]["label"] == "Csp Licensing"
    assert store.stats()["chunks"] == 3


def test_an_unchanged_corpus_is_not_re_embedded(kb, fake_broker):
    """A container restart on an existing volume must be free, not a full re-embed."""
    ingest.ingest_seed()
    fake_broker.embeds.clear()
    report = ingest.ingest_seed()
    assert set(_status(report).values()) == {"unchanged"}
    assert fake_broker.embeds == []


def test_force_re_embeds_everything(kb, fake_broker):
    ingest.ingest_seed()
    fake_broker.embeds.clear()
    report = ingest.ingest_seed(force=True)
    assert set(_status(report).values()) == {"ingested"}
    assert fake_broker.embeds


def test_changed_content_is_re_ingested(kb, fake_broker):
    ingest.ingest_seed()
    (kb / "csp-licensing" / "caps.md").write_text(
        CAPS + "\nCopilot in Microsoft 365 is scoped to customers under 300 employees.\n",
        encoding="utf-8")
    report = ingest.ingest_seed()
    assert _status(report) == {"csp-licensing": "ingested", "designations": "unchanged"}


def test_a_renamed_file_counts_as_a_change(kb, fake_broker):
    """The fingerprint hashes the path as well as the bytes, so moving content between files
    still re-ingests — the source is part of the citation a partner reads."""
    ingest.ingest_seed()
    (kb / "csp-licensing" / "caps.md").rename(kb / "csp-licensing" / "seat-caps.md")
    report = ingest.ingest_seed()
    assert _status(report)["csp-licensing"] == "ingested"
    assert "seat-caps.md" in {c["source"] for c in store.snapshot()[0]}


def test_authoring_scaffolding_is_not_content(kb, fake_broker):
    """READMEs and _TEMPLATE.md are instructions to an author. They must be invisible to both
    the chunker and the fingerprint, or editing the README triggers a corpus re-embed."""
    ingest.ingest_seed()
    (kb / "csp-licensing" / "README.md").write_text("# How to author\n\n" + CAPS, encoding="utf-8")
    (kb / "csp-licensing" / "_TEMPLATE.md").write_text(CAPS, encoding="utf-8")
    report = ingest.ingest_seed()
    assert set(_status(report).values()) == {"unchanged"}
    assert store.collections()[0]["chunks"] == 2


def test_an_underscore_folder_is_not_a_collection(kb, fake_broker):
    (kb / "_drafts").mkdir()
    (kb / "_drafts" / "wip.md").write_text(CAPS, encoding="utf-8")
    report = ingest.ingest_seed()
    assert "_drafts" not in _status(report)


def test_a_collection_that_becomes_empty_is_cleared(kb, fake_broker):
    """Not skipped — cleared. Skipping is how deleted placeholder content stayed retrievable."""
    ingest.ingest_seed()
    (kb / "csp-licensing" / "caps.md").unlink()
    (kb / "csp-licensing" / "trial.md").unlink()
    report = ingest.ingest_seed()
    entry = {c["collection"]: c for c in report["collections"]}["csp-licensing"]
    assert entry["status"] == "empty"
    assert entry["removed"] == 2
    assert "csp-licensing" not in {c["name"] for c in store.collections()}
    assert store.stats()["chunks"] == 1


def test_a_deleted_folder_is_removed_from_the_index(kb, fake_broker):
    """The loop never iterates a folder that is gone, so orphan detection is the only thing
    standing between a retired collection and permanent citation."""
    ingest.ingest_seed()
    shutil.rmtree(kb / "csp-licensing")
    report = ingest.ingest_seed()
    entry = {c["collection"]: c for c in report["collections"]}["csp-licensing"]
    assert entry["status"] == "removed"
    assert entry["removed"] == 2
    assert [c["name"] for c in store.collections()] == ["designations"]
    # The fingerprint goes with it, so re-adding the folder later re-ingests rather than
    # reporting "unchanged" against an index that no longer holds the chunks.
    assert "csp-licensing" not in json.loads(store.get_meta("seed_fingerprints", "{}"))


def test_an_upload_is_never_mistaken_for_a_retired_seed_collection(kb, fake_broker):
    """Orphan detection is scoped to ``origin = 'seed'``. If it were not, the first seed
    ingest after an upload would delete the partner's document."""
    ingest.ingest_seed()
    ingest.ingest_upload("client-notes", CAPS, source="notes.md")
    ingest.ingest_seed(force=True)
    assert "client-notes" in {c["name"] for c in store.collections()}


def test_one_failing_collection_does_not_stop_the_others(kb, monkeypatch, fake_broker):
    """An unreachable embedder on one folder must not stop the rail booting with the corpus it
    already has."""
    def flaky(text, *, model):
        texts = [text] if isinstance(text, str) else list(text)
        if any("capability score" in t for t in texts):
            raise broker.BrokerError("embedder unreachable")
        return fake_broker.embed(texts, model=model)

    monkeypatch.setattr(broker, "embed", flaky)
    report = ingest.ingest_seed()
    by_name = {c["collection"]: c for c in report["collections"]}
    assert by_name["csp-licensing"]["status"] == "ingested"
    assert by_name["designations"]["status"] == "error"
    assert "unreachable" in by_name["designations"]["detail"]
    assert [c["name"] for c in store.collections()] == ["csp-licensing"]


def test_a_failed_collection_is_retried_on_the_next_pass(kb, monkeypatch, fake_broker):
    """It must not be fingerprinted as done — otherwise a transient broker outage costs the
    corpus that collection until someone notices and forces a re-ingest."""
    def broken(text, *, model):
        raise broker.BrokerError("embedder unreachable")

    monkeypatch.setattr(broker, "embed", broken)
    ingest.ingest_seed()
    assert json.loads(store.get_meta("seed_fingerprints", "{}")) == {}

    monkeypatch.setattr(broker, "embed", fake_broker.embed)
    report = ingest.ingest_seed()
    assert set(_status(report).values()) == {"ingested"}
    assert store.stats()["chunks"] == 3


def test_fingerprints_survive_a_restart(kb, fake_broker):
    ingest.ingest_seed()
    prints = json.loads(store.get_meta("seed_fingerprints"))
    assert set(prints) == {"csp-licensing", "designations"}
    assert all(len(v) == 64 for v in prints.values())  # sha256 hexdigest


def test_a_corrupt_fingerprint_blob_does_not_stop_ingest(kb, fake_broker):
    store.set_meta("seed_fingerprints", "{not json")
    report = ingest.ingest_seed()
    assert set(_status(report).values()) == {"ingested"}


# --- uploads ----------------------------------------------------------------------------

def test_ingest_upload_indexes_as_its_own_collection(isolated_rail, fake_broker):
    count = ingest.ingest_upload("client-notes", TWO_SECTIONS, source="notes.md")
    assert count == 2
    listed = store.collections()[0]
    assert listed["name"] == "client-notes"
    assert listed["origin"] == "upload"
    assert listed["label"] == "Client Notes"
    assert {c["source"] for c in store.snapshot()[0]} == {"notes.md"}


def test_ingest_upload_of_unchunkable_text_indexes_nothing(isolated_rail, fake_broker):
    """Below the chunker's floor there is no content, and an empty collection row would show
    up in the UI as a source a partner could scope a question to."""
    assert ingest.ingest_upload("stub", "hi", source="notes.md") == 0
    assert store.collections() == []
    assert fake_broker.embeds == []


def test_ingest_upload_replaces_a_previous_upload_of_the_same_name(isolated_rail, fake_broker):
    ingest.ingest_upload("client-notes", TWO_SECTIONS, source="notes.md")
    assert ingest.ingest_upload("client-notes", CAPS, source="notes.md") == 1
    assert store.collections()[0]["chunks"] == 1
