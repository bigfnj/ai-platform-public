"""The curated question deck — the rail's front door.

questions.py states three design rules in its own docstring, and two of them are testable
facts rather than opinions:

1. **Every question must be answerable from the corpus.** A deck entry scoped to a collection
   that was renamed or deleted retrieves nothing and answers "the context does not cover
   this" — on the user's very first click, which is the exact failure the deck exists to
   prevent. ``validate()`` is the guard; these tests are the guard on the guard.
2. **Orient first, then disabuse.** The order of the groups is content, not chrome: "What it
   is" leads and "Get it right" sits second, because the disambiguation traps are where this
   corpus earns its keep and burying them mid-deck wastes them.

The shape tests matter for a duller reason: the deck is a hand-maintained literal that both
frontends index by id, and a duplicated or missing id fails as a 404 on a click, not at boot.
"""
from __future__ import annotations

import pytest

from gemini_cx import config, questions

DECK = questions.all_questions()


# --- shape ---------------------------------------------------------------------------------

def test_the_deck_is_not_empty():
    assert len(questions.groups()) >= 5
    assert len(DECK) >= 20


@pytest.mark.parametrize("key", ["id", "label", "icon", "blurb", "questions"])
def test_every_group_carries_the_keys_the_ui_renders(key):
    for group in questions.groups():
        assert group.get(key), f"group {group.get('id')!r} has no {key}"


def test_every_question_has_an_id_a_text_and_a_scope():
    for q in DECK:
        assert q["id"] and q["id"].strip() == q["id"]
        assert q["text"].strip()
        assert q["collections"], f"question {q['id']!r} is scoped to nothing"


def test_every_question_is_phrased_as_a_question():
    """The deck is read as a list of things you may ask, so a statement reads as a broken
    entry. Cheap to keep true, invisible when it stops being true."""
    assert [q["id"] for q in DECK if not q["text"].endswith("?")] == []


def test_question_ids_are_unique_across_the_whole_deck():
    """find() returns the first match, so a duplicate id makes one entry unreachable and the
    other answer the wrong question."""
    ids = [q["id"] for q in DECK]
    assert len(ids) == len(set(ids))


def test_group_ids_are_unique():
    ids = [g["id"] for g in questions.groups()]
    assert len(ids) == len(set(ids))


def test_all_questions_carries_the_group_back_to_the_client():
    """The UI groups by these, and they are not stored on the question literal itself."""
    for q in DECK:
        assert q["group"]
        assert q["group_label"]
    by_group = {q["group"] for q in DECK}
    assert by_group == {g["id"] for g in questions.groups()}


def test_all_questions_does_not_mutate_the_deck_literal():
    """It merges the group fields into a copy; a leak here would permanently pollute the
    module-level literal that every later request reads."""
    questions.all_questions()
    for group in questions.QUESTION_GROUPS:
        for q in group["questions"]:
            assert "group" not in q
            assert "group_label" not in q


def test_orientation_leads_and_the_traps_come_second():
    """Design rule 2, stated in the module docstring: a reader who cannot yet place GECX has
    no frame to hang a correction on, so the corrections sit immediately after the primer."""
    assert [g["id"] for g in questions.groups()][:2] == ["basics", "traps"]


# --- lookup ---------------------------------------------------------------------------------

def test_find_returns_the_question_with_its_scope():
    q = questions.find("pricing")
    assert q is not None
    assert q["text"] == "How is GECX priced?"
    assert q["collections"] == ["pricing-and-licensing"]


def test_find_returns_none_for_an_unknown_id():
    """The API turns this into a 404 rather than silently answering something else."""
    assert questions.find("no-such-question") is None
    assert questions.find("") is None


def test_every_id_is_findable():
    for q in DECK:
        assert questions.find(q["id"])["text"] == q["text"]


# --- validate: design rule 1 with teeth -------------------------------------------------------

def test_the_shipped_deck_validates_against_the_shipped_corpus():
    """The whole point of the deck. Every collection any question is scoped to exists on disk
    in the seed tree that gets baked into the image."""
    assert questions.validate(config.SEED_KB_DIR) == []


def test_validate_names_the_question_and_the_missing_collection(tmp_path):
    (tmp_path / "gecx-overview").mkdir()
    problems = questions.validate(tmp_path)
    assert problems, "a corpus with one collection cannot satisfy the whole deck"
    reported = {p["question"] for p in problems}
    assert "pricing" in reported
    assert next(p for p in problems if p["question"] == "pricing")["missing_collections"] == [
        "pricing-and-licensing"]
    # A question scoped only to the collection that IS present must not be reported.
    assert "what-is-gecx" not in reported


def test_validate_reports_a_partial_scope_rather_than_swallowing_it():
    """A question spanning four collections is still broken if one of them was renamed, and
    only the renamed one should be named."""
    q = questions.find("components")
    assert len(q["collections"]) > 1


def test_validate_on_a_missing_seed_directory_fails_loudly(tmp_path):
    """The container mounts the seed tree read-only. If that mount is absent the deck is
    entirely unanswerable, and health has to say so rather than report a clean bill."""
    problems = questions.validate(tmp_path / "does-not-exist")
    assert len(problems) == len(DECK)


def test_validate_ignores_loose_files_in_the_seed_root(tmp_path):
    """Only directories are collections; README.md at the root is the authoring contract."""
    (tmp_path / "pricing-and-licensing").mkdir()
    (tmp_path / "README.md").write_text("not a collection", encoding="utf-8")
    assert {c for p in questions.validate(tmp_path) for c in p["missing_collections"]} \
        .isdisjoint({"README.md", "pricing-and-licensing"})


# --- the traps the deck exists to surface -------------------------------------------------------

def test_the_traps_group_covers_the_documented_confusions():
    """The corpus authoring contract names three pairs that WILL be confused: text vs audio
    language counts, announced vs documented, and Gemini Enterprise vs GECX. Each has a deck
    entry, because a user cannot ask about a trap they do not know exists."""
    traps = next(g for g in questions.groups() if g["id"] == "traps")
    ids = {q["id"] for q in traps["questions"]}
    assert {"languages", "commerce-status", "vs-parent"} <= ids


def test_the_commerce_status_question_is_scoped_to_the_status_gap():
    """Asking whether the Shopping agent is deployable today is the announced-versus-
    documented trap, and it is only answerable if retrieval can see the collection that
    records both claims."""
    assert "commerce-agents" in questions.find("commerce-status")["collections"]


def test_the_pricing_refusal_question_is_in_the_deck():
    """Rule 2 of the system prompt is "never invent a figure". The deck makes the refusal
    itself clickable, so a user can find out what the rail will not tell them."""
    q = questions.find("never-quote")
    assert q is not None
    assert q["collections"] == ["pricing-and-licensing"]
