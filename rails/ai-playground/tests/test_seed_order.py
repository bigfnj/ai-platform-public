"""The RAG demo must land on the corpus its sample questions are written for.

This is a regression test for a bug that survived its own fix. Commit 355441d corrected
``db.list_corpora``'s ORDER BY after a second seed corpus silently became the demo's default,
but left ``ensure_seeds`` iterating the seed directory with a plain ``sorted()``. Alphabetically
``embedding-concepts`` precedes ``nvidia-stack``, so on a FRESH volume the wrong corpus was
ingested first, took the lower id, and became the default again under the new ordering.

It went unnoticed because the running deployment's database predates the second corpus, so only
a clean install was affected — and the symptom was a 200 with a plausible-looking answer
("the context does not cover that"), not an error.
"""
import pytest

from ai_playground import config, corpora


@pytest.fixture
def seed_dir(tmp_path, monkeypatch):
    """A seed tree whose alphabetical order is the WRONG order."""
    for name in ("embedding-concepts", "nvidia-stack", "zzz-later-addition"):
        d = tmp_path / name
        d.mkdir()
        (d / "doc.md").write_text(f"# {name}\n\nSome text.\n", encoding="utf-8")
    monkeypatch.setattr(config, "SEED_CORPORA_DIR", tmp_path)
    return tmp_path


def test_default_corpus_is_ingested_first(seed_dir, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CORPUS_SLUG", "nvidia-stack")
    assert [p.name for p in corpora._seed_order()][0] == "nvidia-stack"


def test_remaining_seeds_keep_a_stable_alphabetical_order(seed_dir, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CORPUS_SLUG", "nvidia-stack")
    assert [p.name for p in corpora._seed_order()] == [
        "nvidia-stack", "embedding-concepts", "zzz-later-addition"]


def test_every_seed_is_still_ingested(seed_dir, monkeypatch):
    """Ordering must not drop a corpus — the default is promoted, not filtered to."""
    monkeypatch.setattr(config, "DEFAULT_CORPUS_SLUG", "nvidia-stack")
    assert len(corpora._seed_order()) == 3


def test_unknown_default_slug_degrades_to_alphabetical(seed_dir, monkeypatch):
    """A typo'd or removed default must not crash startup, just lose the preference."""
    monkeypatch.setattr(config, "DEFAULT_CORPUS_SLUG", "no-such-corpus")
    assert [p.name for p in corpora._seed_order()] == [
        "embedding-concepts", "nvidia-stack", "zzz-later-addition"]


def test_the_shipped_default_corpus_actually_exists():
    """Guards the pairing the demo depends on: RagDemo's sample questions are written for
    DEFAULT_CORPUS_SLUG, so renaming the seed folder without updating config breaks them."""
    if not config.SEED_CORPORA_DIR.exists():   # not present in a container-less checkout
        pytest.skip("seed corpora not present")
    names = {corpora._slugify(p.name) for p in config.SEED_CORPORA_DIR.iterdir() if p.is_dir()}
    assert config.DEFAULT_CORPUS_SLUG in names
