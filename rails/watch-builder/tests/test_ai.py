"""The AI half, tested without touching the GPU.

Nothing here runs a prompt. The GPU may be shared with other work and the broker evicts
rather than queues, so a test suite that quietly spends inference time is a
test suite that gets switched off. The broker call is stubbed; what is tested is everything
around it, which is where the failures actually are:

  * getting JSON out of a reply that may or may not be JSON;
  * rejecting a document that would produce a broken face;
  * repairing ONCE and then giving up rather than looping on a shared card;
  * reporting a partial variation pass as a partial success.
"""
from __future__ import annotations

import re

from typing import get_args
from watch_builder.ai import _SCHEMA_BRIEF
from watch_builder.design import ElementType

import json

import pytest

from watch_builder import ai, broker
from watch_builder.design import Design


# --- JSON extraction ------------------------------------------------------------------


def test_bare_json_object():
    assert ai.extract_json('{"a": 1}') == {"a": 1}


def test_fenced_json():
    assert ai.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert ai.extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_json_with_prose_around_it():
    text = 'Sure! Here is the design:\n\n{"a": {"b": 2}}\n\nHope that helps.'
    assert ai.extract_json(text) == {"a": {"b": 2}}


def test_brace_matching_handles_nesting_and_strings():
    """A regex cannot balance braces.

    Greedy swallows trailing prose, lazy stops at the first inner closing brace, and a brace
    inside a string literal defeats naive counting. All three shapes occur.
    """
    text = 'prefix {"outer": {"inner": "}"}, "n": 1} suffix'
    assert ai.extract_json(text) == {"outer": {"inner": "}"}, "n": 1}


def test_empty_and_unparseable_replies_raise():
    for bad in ("", "   ", "no json here at all", "{not json}"):
        with pytest.raises(ai.AIError):
            ai.extract_json(bad)


# --- generation, with the broker stubbed ----------------------------------------------


GOOD = {
    "schema": 1, "name": "Stub", "background": "#000000", "platforms": ["emery"],
    "elements": [
        {"type": "time", "x": 0, "y": "30%", "w": "100%", "color": "#FFFFFF"},
        {"type": "date", "x": 0, "y": "60%", "w": "100%", "color": "#AAAAAA",
         "font": "auto-small"},
    ],
}

# Two text elements 4% apart: passes the schema, overlaps on every screen. This is the
# document class the layout audit exists to reject, and the reason validation is not just
# pydantic.
OVERLAPPING = {
    "schema": 1, "name": "Bad", "background": "#000000", "platforms": ["emery"],
    "elements": [
        {"type": "time", "x": 0, "y": "30%", "w": "100%"},
        {"type": "date", "x": 0, "y": "34%", "w": "100%"},
    ],
}


class Stub:
    """Returns a scripted sequence of replies and records the prompts it was given."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, *, model, max_tokens, temperature=0.7, **_kw):
        # `requester` and `timeout` are test_cli_requester.py's to assert.
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("the code asked for more completions than the test scripted")
        return self.replies.pop(0)


@pytest.fixture
def stub(monkeypatch):
    def install(*replies):
        s = Stub(*replies)
        monkeypatch.setattr(broker, "chat", s)
        return s
    return install


def test_a_valid_reply_is_accepted_first_time(stub):
    s = stub(json.dumps(GOOD))
    design = ai.design_from_prompt("a minimal face", ["emery"])
    assert isinstance(design, Design)
    assert design.name == "Stub"
    assert len(s.calls) == 1


def test_an_overlapping_document_is_rejected_and_repaired_once(stub):
    """The audit runs on the model's output, not just the schema.

    OVERLAPPING is schema-valid, so without the layout audit this would be accepted and would
    render as a face with the time through the date.
    """
    s = stub(json.dumps(OVERLAPPING), json.dumps(GOOD))
    design = ai.design_from_prompt("a face", ["emery"])
    assert design.name == "Stub"
    assert len(s.calls) == 2
    # The repair prompt must carry the actual problems, or the model is guessing.
    repair = s.calls[1][-1]["content"]
    assert "rejected" in repair
    assert "overlap" in repair.lower()


def test_repair_happens_once_and_then_gives_up(stub):
    """ONE repair, not a loop.

    A model that produced an invalid document twice given explicit field-level errors will
    not converge on the third try, and an unbounded loop on a shared GPU is the rudest thing
    this rail could do.
    """
    s = stub(json.dumps(OVERLAPPING), json.dumps(OVERLAPPING))
    with pytest.raises(ai.AIError) as exc:
        ai.design_from_prompt("a face", ["emery"])
    assert len(s.calls) == 2
    assert "could not produce a valid design" in str(exc.value)


def test_non_json_reply_gets_one_reformat_attempt(stub):
    s = stub("I'd be delighted to help with that!", json.dumps(GOOD))
    design = ai.design_from_prompt("a face", ["emery"])
    assert design.name == "Stub"
    assert "JSON document only" in s.calls[1][-1]["content"]


def test_platforms_are_forced_onto_the_document(stub):
    """The caller chose the platforms; a model must not quietly retarget them."""
    without = {k: v for k, v in GOOD.items() if k != "platforms"}
    stub(json.dumps(without))
    design = ai.design_from_prompt("a face", ["gabbro"])
    assert design.platforms == ["gabbro"]


def test_unknown_platform_from_the_caller_falls_back_to_defaults(stub):
    stub(json.dumps(GOOD))
    design = ai.design_from_prompt("a face", ["granite"])
    assert design.platforms == ["emery"]


def test_empty_prompt_is_refused_without_calling_the_model(stub):
    s = stub()
    with pytest.raises(ai.AIError):
        ai.design_from_prompt("   ", ["emery"])
    assert s.calls == []


def test_broker_failure_is_reported_as_unavailable(monkeypatch):
    def boom(*a, **k):
        raise broker.BrokerError("connection refused")
    monkeypatch.setattr(broker, "chat", boom)
    with pytest.raises(ai.AIError) as exc:
        ai.design_from_prompt("a face", ["emery"])
    assert "unavailable" in str(exc.value)


# --- variations -----------------------------------------------------------------------


def test_variations_are_separate_calls_along_named_axes(stub):
    """One call per variant, not one call asking for N.

    Asking for nine in one completion produces nine near-identical documents, because the
    model is completing a list rather than exploring. The named axis is also what makes the
    grid legible afterwards.
    """
    s = stub(*[json.dumps(GOOD)] * 3)
    base = Design.model_validate(GOOD)
    out = ai.variations(base, 3)
    assert len(out) == 3
    assert len(s.calls) == 3
    axes = {v["axis"] for v in out}
    assert len(axes) == 3, "each variant should vary a different axis"
    assert all(v["ok"] for v in out)


def test_a_failed_variant_does_not_fail_the_pass(stub):
    """A grid with two good cells is useful; an exception is not."""
    s = stub(json.dumps(GOOD), "garbage", "still garbage", json.dumps(GOOD))
    base = Design.model_validate(GOOD)
    out = ai.variations(base, 3)
    assert [v["ok"] for v in out] == [True, False, True]
    assert "error" in out[1]
    assert len(s.calls) == 4  # the failed one used its repair attempt


def test_variation_count_is_bounded(stub):
    stub()
    base = Design.model_validate(GOOD)
    with pytest.raises(ai.AIError):
        ai.variations(base, 999)
    with pytest.raises(ai.AIError):
        ai.variations(base, 0)


def test_the_base_design_is_in_the_prompt(stub):
    s = stub(json.dumps(GOOD))
    base = Design.model_validate(GOOD)
    ai.variations(base, 1)
    assert "Stub" in s.calls[0][-1]["content"]


# --- cost reporting -------------------------------------------------------------------


def test_estimate_scales_with_the_call_count_and_names_the_model_ref():
    one = ai.estimate("design", 1)
    nine = ai.estimate("variations", 9)
    assert nine["approx_seconds"] > one["approx_seconds"]
    assert nine["calls"] == 9
    assert one["holds_gpu"] is True
    # The REF, not a resolved name: Admin -> Rails repoints the role, and reporting a
    # resolved name here would go stale the moment someone did.
    assert one["model_ref"].startswith("@")

# --------------------------------------------------------------------------------------
# The brief must describe the schema the validator enforces
# --------------------------------------------------------------------------------------

# Types deliberately withheld from the model, and why. An entry here is a claim that the
# omission is intentional, and the test below requires the brief to say so in prose too.
_WITHHELD = {
    "image": "needs an image_id from an upload the model cannot know",
}


# A row in the ELEMENT TYPES menu: two spaces, the name, then whitespace before its fields.
_TYPE_ROW = re.compile(r"^  ([a-z][a-z-]*)\s{2,}", re.MULTILINE)


def _types_section() -> str:
    """Just the ELEMENT TYPES block of the brief.

    Sliced deliberately: a type name appearing anywhere in the brief would be satisfied by a
    mention inside a rule or inside the withheld note, which is the opposite of the question.
    The model can only USE a type that is offered in the menu.
    """
    head = _SCHEMA_BRIEF.index("ELEMENT TYPES")
    tail = _SCHEMA_BRIEF.index("EVERY element also takes")
    return _SCHEMA_BRIEF[head:tail]


def _offered_types() -> set[str]:
    """The NAMES in the first column of the menu, parsed rather than substring-matched.

    The first version of this helper asked `name in section`, and it was inert three ways.
    Renaming a row to `polygonX` left "polygon" as a substring, so a mutation that deleted the
    type from the menu passed. `arc` is a substring of battery-ring's own description, "An arc
    around the box", so the arc check was satisfied before an arc row ever existed. And `text`
    is a substring of `battery-text`.

    Parsing the rows makes the assertion match the condition: this type is on the menu.
    """
    return set(_TYPE_ROW.findall(_types_section()))


def test_brief_offers_every_element_type_the_schema_accepts():
    """The drift that happened twice, caught by name rather than by count.

    `image` arrived with uploads and `polygon` and `arc` with the presets, and the brief kept
    describing twelve of fifteen types. Nothing failed; the cost was that describe-a-face
    could not produce the shapes the presets are made of.
    """
    offered = _offered_types()
    missing = sorted(t for t in get_args(ElementType)
                     if t not in _WITHHELD and t not in offered)
    assert missing == [], f"element types the model is never told about: {missing}"


def test_the_menu_offers_nothing_the_schema_rejects():
    """The other direction. A row for a type that does not exist is a guaranteed 422."""
    unknown = sorted(_offered_types() - set(get_args(ElementType)))
    assert unknown == [], f"offered to the model and not in the schema: {unknown}"


def test_withheld_types_are_excluded_from_the_offered_list():
    """A withheld type must not be offered, or the model will emit documents that fail."""
    offered = _offered_types()
    for name in _WITHHELD:
        assert name not in offered, f"{name} is withheld but appears in the offered list"


def test_withheld_types_are_explained_to_the_model():
    """Silence about an omission is what made the last two look like oversights.

    The brief has to name a withheld type and say it is unavailable, so a model that would
    otherwise reach for it is told why, and so a reader can tell "deliberate" from "stale".
    """
    assert "NOT AVAILABLE TO YOU" in _SCHEMA_BRIEF
    tail = _SCHEMA_BRIEF[_SCHEMA_BRIEF.index("NOT AVAILABLE TO YOU"):]
    for name in _WITHHELD:
        assert name in tail, f"{name} is withheld without telling the model"


def test_brief_records_the_polygon_bleed_exception():
    """Rule 2 became false for polygons and the brief must carry the exception.

    "Nothing may be cropped, x + w must stay inside 100%" is still right for every other
    type, and wrong for a polygon, which is allowed off the edge because that is how an
    edge-anchored wedge is built. Telling a model a constraint the validator does not enforce
    costs output for nothing.
    """
    assert "EXCEPTION" in _SCHEMA_BRIEF
    assert "misses the screen entirely" in _SCHEMA_BRIEF


def test_brief_states_the_caps_check_semantics_enforces():
    """Both caps are rejections the model could not otherwise anticipate."""
    assert "32" in _types_section(), "the polygon point cap is not stated"
    assert "24 elements" in _SCHEMA_BRIEF, "the element cap is not stated"
