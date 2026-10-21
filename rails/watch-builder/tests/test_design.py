"""The design document: the boundary a language model cannot cross.

Everything here is a property the model's output is checked against, so a gap in this file is
a gap in the only thing standing between a hallucinated field and a generated watchface.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from watch_builder.design import (
    Design,
    Element,
    check_semantics,
    clock_elements,
    gcolor_expr,
    resolve_length,
    snap_channel,
    snap_hex,
    valid_length,
)


def _doc(**over):
    base = {
        "schema": 1,
        "name": "Test",
        "background": "#000000",
        "platforms": ["emery"],
        "elements": [{"type": "time", "x": 0, "y": "40%", "w": "100%"}],
    }
    base.update(over)
    return base


# --- colour ---------------------------------------------------------------------------


def test_snap_channel_truncates_it_does_not_round():
    """GColorFromRGB is `>> 6`, so boundaries are 64/128/192 and 50 becomes 0.

    Pinned as its own test because "quantises" is almost always read as "to the nearest", and
    reading it that way makes the editor preview disagree with the watch. 50 is the
    discriminating input: nearest would give 85.
    """
    assert snap_channel(0) == 0
    assert snap_channel(50) == 0        # nearest would say 85
    assert snap_channel(63) == 0
    assert snap_channel(64) == 85
    assert snap_channel(127) == 85
    assert snap_channel(128) == 170
    assert snap_channel(191) == 170
    assert snap_channel(192) == 255
    assert snap_channel(255) == 255


def test_one_unit_of_input_can_move_the_output_a_whole_step():
    assert snap_hex("#7F7F7F") == "#555555"
    assert snap_hex("#808080") == "#AAAAAA"


def test_bad_hex_is_rejected():
    for bad in ("red", "#FFF", "#GGGGGG", "FF0000", ""):
        with pytest.raises(ValidationError):
            Element(type="text", text="x", color=bad)


def test_two_colour_platform_thresholds_on_lightness_not_hue():
    """A mid-red and a mid-green must not both survive as the same value by accident.

    Thresholding by luminance is what preserves the design's contrast on aplite/diorite/flint.
    Yellow is light, blue is dark, and that ordering is the thing a monochrome face keeps.
    """
    assert gcolor_expr("#FFFF00", 2) == "GColorWhite"
    assert gcolor_expr("#0000FF", 2) == "GColorBlack"
    assert gcolor_expr("#FFFFFF", 2) == "GColorWhite"
    assert gcolor_expr("#000000", 2) == "GColorBlack"


def test_colour_platform_emits_a_snapped_hex():
    assert gcolor_expr("#00FF00", 64) == "GColorFromHEX(0x00FF00)"
    # Off-palette input is snapped, and snapped by truncation.
    assert gcolor_expr("#C86432", 64) == "GColorFromHEX(0xFF5500)"


# --- lengths --------------------------------------------------------------------------


def test_length_forms_accepted_and_rejected():
    for good in (0, 42, -3, "0%", "40%", "100%", "12.5%", "-10%", "center"):
        assert valid_length(good), good
    for bad in ("40", "40 %", "%", "middle", "40px", None, 1.5, "1000%"):
        assert not valid_length(bad), bad


def test_percentages_resolve_against_the_screen():
    assert resolve_length("50%", 200) == 100
    assert resolve_length("50%", 260) == 130
    assert resolve_length("100%", 228) == 228
    assert resolve_length(17, 200) == 17


def test_center_accounts_for_the_element_size():
    """"center" must centre the ELEMENT, not its origin.

    Centring the origin puts a 100px-wide element half off the screen, which is the bug this
    parameter exists to prevent.
    """
    assert resolve_length("center", 200, 100) == 50
    assert resolve_length("center", 200, 0) == 100


# --- schema ---------------------------------------------------------------------------


def test_unknown_field_is_rejected():
    """extra="forbid" is load-bearing.

    A model that invents `"shadow": true` must fail loudly rather than have it silently
    dropped, because a silently dropped field is a design that does not look like what was
    asked for and gives no clue why.
    """
    with pytest.raises(ValidationError):
        Design.model_validate(_doc(elements=[{"type": "time", "shadow": True}]))


def test_unknown_element_type_is_rejected():
    with pytest.raises(ValidationError):
        Design.model_validate(_doc(elements=[{"type": "hologram"}]))


def test_unknown_platform_is_rejected_and_named():
    with pytest.raises(ValidationError) as exc:
        Design.model_validate(_doc(platforms=["emery", "granite"]))
    assert "granite" in str(exc.value)


def test_empty_platform_list_is_rejected():
    with pytest.raises(ValidationError):
        Design.model_validate(_doc(platforms=[]))


def test_platforms_are_deduplicated_in_caller_order():
    d = Design.model_validate(_doc(platforms=["gabbro", "emery", "gabbro"]))
    assert d.platforms == ["gabbro", "emery"]


def test_future_schema_version_is_refused():
    """A document from a newer rail must not be silently misread as a v1 document."""
    with pytest.raises(ValidationError):
        Design.model_validate(_doc(schema=2))


def test_stroke_width_is_forced_odd():
    """The SDK constrains stroke width to odd values; 2 does not give a balanced 2px line.

    Rounded up rather than rejected, because "thicker" is an unambiguous intent and refusing
    would invalidate a document for a reason no designer thinks about.
    """
    assert Element(type="line", x=0, y=0, x2=10, y2=10, stroke=2).stroke == 3
    assert Element(type="line", x=0, y=0, x2=10, y2=10, stroke=4).stroke == 5
    assert Element(type="line", x=0, y=0, x2=10, y2=10, stroke=3).stroke == 3
    assert Element(type="line", x=0, y=0, x2=10, y2=10, stroke=0).stroke == 1


# --- semantics ------------------------------------------------------------------------


def test_semantics_catch_missing_per_type_fields():
    d = Design.model_validate(_doc(elements=[
        {"type": "text"},                     # no text
        {"type": "circle", "x": 10, "y": 10},  # no r
        {"type": "line", "x": 0, "y": 0},      # no x2/y2
        {"type": "rect", "x": 0, "y": 0},      # no w/h
    ]))
    problems = check_semantics(d)
    assert len(problems) == 4
    assert all("elements[" in p for p in problems)


def test_semantics_are_quiet_on_a_valid_document():
    assert check_semantics(Design.model_validate(_doc())) == []


def test_clock_elements_counts_time_and_analog():
    assert clock_elements(Design.model_validate(_doc())) == 1
    assert clock_elements(Design.model_validate(_doc(elements=[
        {"type": "analog", "x": 0, "y": 0, "w": "80%", "h": "80%"}]))) == 1
    assert clock_elements(Design.model_validate(_doc(elements=[
        {"type": "text", "text": "hi"}]))) == 0
