"""The shipped GECX corpus, against its own authoring contract.

The corpus is the product here: the rail answers ONLY from what is in ``seed/knowledge-base``,
so a content mistake is a wrong answer with a citation attached, which is worse than no answer.
seed/knowledge-base/README.md states the rules that make retrieval work, and three of them are
mechanical enough to check on every run:

* **Headings are load-bearing.** A chunk carries its nearest heading as the title, and that
  title is what the user sees in the citation. A file that opens with prose produces chunks
  titled "" — a citation to nowhere.
* **Mark status explicitly on every capability.** GA, Preview, Coming soon and Announced-only
  are four different answers to anyone planning a build, and the ``Status:`` front-matter
  field is where that lives. It is the rule the corpus exists to enforce.
* **A file must actually chunk.** Content below the 40-character floor is dropped silently, so
  a file can be present, correct, and completely unretrievable.

This suite reads the seed tree (read-only, as the container mounts it) and touches nothing
else — no database, no broker, no embeddings.
"""
from __future__ import annotations

import re

from gemini_cx import config, questions, rag

SEED = config.SEED_KB_DIR
COLLECTIONS = sorted(p for p in SEED.iterdir() if p.is_dir()) if SEED.is_dir() else []
# Ingest's own skip rules: instructions to the author are not GECX facts.
CONTENT = [p for c in COLLECTIONS for p in sorted(c.rglob("*.md"))
           if not p.name.startswith("_") and p.name.upper() != "README.MD"]

_STATUS = re.compile(r"^>.*Status:\s*(\S.*)$", re.M)
_COLLECTION_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")


def test_the_seed_tree_ships_with_the_image():
    """A missing corpus is a rail that answers "the context does not cover this" to
    everything, and it is the single failure the deck cannot survive."""
    assert SEED.is_dir(), f"no seed knowledge base at {SEED}"
    assert len(COLLECTIONS) >= 17
    assert CONTENT


def test_collection_ids_are_url_and_scope_safe():
    """The folder name IS the collection id: it is what a deck question scopes to and what
    the UI title-cases. A space or a capital there silently breaks the scoping."""
    for folder in COLLECTIONS:
        assert _COLLECTION_ID.fullmatch(folder.name), folder.name


def test_every_collection_has_content_that_survives_chunking():
    """An empty collection is worse than an absent one: ingest deletes its chunks, the deck
    still offers questions scoped to it, and every one of them answers nothing."""
    for folder in COLLECTIONS:
        assert rag.load_collection(folder, folder.name), f"{folder.name} indexes nothing"


def rel(path):
    return f"{path.parent.name}/{path.name}"


def test_every_content_file_declares_a_status():
    """The corpus's own rule — "mark status explicitly on every capability" — and the one the
    system prompt then forbids the model from smoothing away."""
    missing = [rel(p) for p in CONTENT
               if not (_STATUS.search(p.read_text(encoding="utf-8")) or [None])[0]]
    assert missing == [], "no 'Status:' field in the front-matter block"


def test_every_content_file_opens_with_a_heading():
    """The first chunk of a file has no earlier heading to inherit, so prose at the top of a
    file is cited with a blank title."""
    headless = [rel(p) for p in CONTENT
                if not p.read_text(encoding="utf-8").lstrip().startswith("#")]
    assert headless == []


def test_every_content_file_produces_at_least_one_chunk():
    """Present, correct, and unretrievable is a real state: everything under 40 characters is
    dropped without a word."""
    silent = [rel(p) for p in CONTENT
              if not rag.chunk_markdown(p.read_text(encoding="utf-8"),
                                        source=p.name, collection=p.parent.name)]
    assert silent == []


def test_every_chunk_is_citable():
    """A citation carries source, collection and title. A blank title reads to the user as a
    source the assistant could not name."""
    for folder in COLLECTIONS:
        for chunk in rag.load_collection(folder, folder.name):
            assert chunk["title"].strip(), f"{folder.name}/{chunk['source']} has an untitled chunk"
            assert chunk["source"]
            assert chunk["collection"] == folder.name


def test_the_authoring_scaffolding_never_enters_the_index():
    """_TEMPLATE.md tells an author how to write a file. Indexed, it becomes the assistant
    quoting its own style guide as a GECX fact."""
    sources = {c["source"] for f in COLLECTIONS for c in rag.load_collection(f, f.name)}
    assert not [s for s in sources if s.rsplit("/", 1)[-1].startswith("_")]
    assert not [s for s in sources if s.upper().endswith("README.MD")]


def test_the_corpus_marks_more_than_one_status_level():
    """The whole point of the Status field: GECX marketing collapses four states into
    "available", and a corpus that only ever says GA has collapsed them too."""
    values = " ".join(_STATUS.search(p.read_text(encoding="utf-8")).group(1)
                      for p in CONTENT if _STATUS.search(p.read_text(encoding="utf-8")))
    lowered = values.lower()
    assert "ga" in lowered
    assert "preview" in lowered
    assert "coming soon" in lowered


def test_every_collection_the_deck_scopes_to_holds_content():
    """questions.validate() only checks the folder exists. A folder that exists and indexes
    nothing passes validation and still answers nothing."""
    scoped = {c for q in questions.all_questions() for c in q["collections"]}
    indexable = {f.name for f in COLLECTIONS if rag.load_collection(f, f.name)}
    assert scoped - indexable == set()
