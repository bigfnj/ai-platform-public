"""Every docs/*.md pointer in docs/rail-manifest.schema.json names a section that exists.

The schema is what an editor shows someone writing rail.json, so its descriptions are the
first documentation a new rail author reads. status_route's said "see model_status_envelope
in docs/RAIL_CONTRACT.md", and no such section ever existed: the reader was sent to a name,
searched the page, and found nothing, while the envelope itself was written down only in the
template's docstring.

The rule this holds: a description that mentions a docs/ file says WHICH section, as
`the "<heading>" section of docs/<file>.md`, and that heading is a real heading in that file,
verbatim. A free-text pointer ("the broker-token rule") cannot be checked, so it is refused.

Its own file, not in test_rail_manifest_schema.py: that module skips whole when jsonschema is
missing, and this check needs nothing but the standard library.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO / "docs" / "rail-manifest.schema.json"
DOC = re.compile(r"docs/[\w./-]+\.md")
POINTER = re.compile(r'"([^"]+)" section of (docs/[\w./-]+\.md)')


def descriptions(node, path="$"):
    """(json path, text) for every `description` string, at any depth. A manifest FIELD named
    description would be a dict under properties, not a string, so it is walked, not taken."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "description" and isinstance(v, str):
                yield f"{path}.description", v
            else:
                yield from descriptions(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from descriptions(v, f"{path}[{i}]")


def headings(doc: str) -> set[str]:
    text = (REPO / doc).read_text(encoding="utf-8")
    return {re.sub(r"^#+\s*", "", line).strip() for line in text.splitlines()
            if re.match(r"^#{1,6}\s", line)}


def doc_pointer_problems(schema: dict) -> list[str]:
    problems = []
    for where, text in descriptions(schema):
        mentions = DOC.findall(text)
        if not mentions:
            continue
        pointers = POINTER.findall(text)
        if len(pointers) != len(mentions):
            problems.append(f"{where}: mentions {mentions} but names {len(pointers)} section(s); "
                            f'write the "<heading>" section of docs/<file>.md')
        for heading, doc in pointers:
            if not (REPO / doc).is_file():
                problems.append(f"{where}: {doc} does not exist")
            elif heading not in headings(doc):
                problems.append(f"{where}: {doc} has no heading {heading!r}")
    return problems


def test_the_schema_points_at_docs_at_all():
    """Witness: without it, a schema that lost every pointer (or a walker that found no
    descriptions) would pass the test below by checking nothing."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    with_docs = [w for w, t in descriptions(schema) if DOC.search(t)]
    assert len(with_docs) >= 2, with_docs
    assert any(w.endswith("status_route.description") for w in with_docs)


def test_every_doc_pointer_names_a_real_section():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    problems = doc_pointer_problems(schema)
    assert not problems, "\n".join(problems)


def test_the_old_status_route_pointer_is_refused():
    """The defect itself, fed through the check: a pointer to a section by a name that is not a
    heading, and a free-text pointer with no section at all. Both must be reported."""
    bad = {"properties": {
        "a": {"description": 'see the "model_status_envelope" section of docs/RAIL_CONTRACT.md.'},
        "b": {"description": "see model_status_envelope in docs/RAIL_CONTRACT.md."},
    }}
    problems = doc_pointer_problems(bad)
    assert len(problems) == 2, problems
    assert "has no heading 'model_status_envelope'" in problems[0]
    assert "names 0 section(s)" in problems[1]
