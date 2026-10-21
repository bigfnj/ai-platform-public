"""The layout audit: the check that exists because a green build is not a working layout.

Every test here is a defect class the audit must FIRE on, plus one clean design it must stay
quiet about. Both halves matter. An audit that cannot fail is decoration, and an audit that
fires on correct input trains the reader to dismiss the whole class.

The overlap case is not hypothetical: this rail's own code generator produced it. A
seven-platform build came out green, every element rendered, and the time ran straight
through the date on five of the seven panels, because positions scale with the screen and a
system font's box height does not.
"""
from __future__ import annotations

from watch_builder.design import DEFAULT_PLATFORMS, Design, Element
from watch_builder.layout import (
    audit,
    audit_platform,
    estimate_ink_width,
    resolve_box,
    resolved_boxes,
    sample_text,
    summarise,
)


def _design(elements, platforms=("aplite", "emery", "gabbro")):
    return Design.model_validate({
        "schema": 1, "name": "T", "background": "#001E41",
        "platforms": list(platforms), "elements": elements,
    })


def _codes(design):
    return {f.code for f in audit(design)}


# --- the defect classes ---------------------------------------------------------------


def test_text_overlap_fires_on_every_affected_platform():
    d = _design([
        {"type": "time", "x": 0, "y": "36%", "w": "100%"},
        {"type": "date", "x": 0, "y": "55%", "w": "100%"},
    ])
    findings = [f for f in audit(d) if f.code == "TEXT_OVERLAP"]
    assert {f.platform for f in findings} == {"aplite", "emery", "gabbro"}
    assert all(f.severity == "error" for f in findings)
    # The message has to carry the pixel overlap, or it is an opinion rather than a finding.
    assert "overlap by" in findings[0].message


def test_a_pinned_font_is_what_makes_overlap_platform_dependent():
    """The same document, clear on one screen and broken on another.

    Worth being precise about the mechanism, because the obvious guess is backwards. With
    "auto" fonts the type scales roughly with the screen, so a percentage gap that works on
    one platform tends to work on all of them. The divergence comes from a PINNED font: its
    box height is constant while the gap scales, so a pinned face overlaps on the SMALL
    screen and clears on the large one.

    Here a 42px face plus 4px padding needs 46px of gap. A 25% gap is 42px on aplite's 168
    (overlap) and 57px on emery's 228 (clear).

    That is also the argument for "auto" being the default: it is what stops this happening.
    """
    d = _design([
        {"type": "time", "x": 0, "y": "20%", "w": "100%",
         "font": "FONT_KEY_BITHAM_42_BOLD"},
        {"type": "date", "x": 0, "y": "45%", "w": "100%",
         "font": "FONT_KEY_GOTHIC_18"},
    ], platforms=("aplite", "emery"))
    plats = {f.platform for f in audit(d) if f.code == "TEXT_OVERLAP"}
    assert plats == {"aplite"}, plats


def test_out_of_bounds_fires():
    d = _design([{"type": "rect", "x": "90%", "y": "90%", "w": "40%", "h": "40%"}])
    findings = [f for f in audit(d) if f.code == "OUT_OF_BOUNDS"]
    assert findings and all(f.severity == "error" for f in findings)


def test_quick_view_warns_and_only_on_rectangular_platforms():
    """Quick View covers the bottom ~51px, and does not exist on round platforms.

    A warning rather than an error: the face still works, the element is hidden only while a
    pin is showing, and for a decorative bottom row that may well be the intent.
    """
    d = _design([{"type": "time", "x": 0, "y": "70%", "w": "100%"}],
                platforms=("emery", "gabbro"))
    findings = [f for f in audit(d) if f.code == "QUICK_VIEW"]
    assert {f.platform for f in findings} == {"emery"}
    assert findings[0].severity == "warning"


def test_content_at_the_rim_warns_on_round_platforms():
    d = _design([{"type": "text", "text": "EDGE", "x": 0, "y": 0, "w": "30%"}],
                platforms=("gabbro",))
    assert "OUTSIDE_CIRCLE" in _codes(d)


# --- the false positives that were found and fixed ------------------------------------


def test_a_ring_inscribed_in_its_box_is_not_outside_the_circle():
    """A round-shaped element is INSCRIBED in its box.

    Testing the box corners flagged a battery ring whose every drawn pixel was comfortably
    inside the screen, which was a live false positive in the first version of this module.
    """
    d = _design([{"type": "battery-ring", "x": "10%", "y": "10%",
                  "w": "80%", "h": "80%", "thickness": 5}], platforms=("gabbro",))
    assert "OUTSIDE_CIRCLE" not in _codes(d)


def test_a_ring_that_really_does_touch_the_rim_still_warns():
    """The counterpart. Fixing the false positive must not switch the check off."""
    d = _design([{"type": "battery-ring", "x": 0, "y": 0,
                  "w": "100%", "h": "100%", "thickness": 5}], platforms=("gabbro",))
    assert "OUTSIDE_CIRCLE" in _codes(d)


def test_centred_text_in_a_full_width_box_is_not_outside_the_circle():
    """A centre-aligned box is usually far wider than its text.

    Testing the box flagged a correctly centred time on gabbro. The audit narrows to the
    estimated ink and honours alignment instead.
    """
    d = _design([{"type": "time", "x": 0, "y": "center", "w": "100%", "align": "center"}],
                platforms=("gabbro",))
    assert "OUTSIDE_CIRCLE" not in _codes(d)


def test_clean_design_produces_no_errors():
    d = _design([
        {"type": "battery-ring", "x": "8%", "y": "8%", "w": "84%", "h": "84%",
         "thickness": 5, "color": "#FFAA00"},
        {"type": "time", "x": 0, "y": "26%", "w": "100%", "color": "#FFFFFF"},
        {"type": "date", "x": 0, "y": "62%", "w": "100%", "color": "#AAAAFF",
         "font": "auto-small"},
    ])
    report = summarise(audit(d))
    assert report["errors"] == 0, report["findings"]


# --- box resolution -------------------------------------------------------------------


def test_text_height_comes_from_the_chosen_font_when_absent():
    """The number that makes overlap predictable.

    A text element with no explicit h must get the selected font's line box, or the audit is
    measuring a box the renderer will not use.
    """
    from watch_builder.design import PLATFORMS
    el = Design.model_validate({
        "schema": 1, "name": "T", "platforms": ["emery"],
        "elements": [{"type": "time", "x": 0, "y": 0, "w": "100%"}],
    }).elements[0]
    box = resolve_box(el, PLATFORMS["emery"], 0)
    # LECO_60 on emery: a 60px line box, plus the 4px of padding codegen adds.
    assert box.h == 64


def test_ink_width_estimate_is_capped_by_the_box():
    from watch_builder.design import Element
    el = Element(type="text", text="a very long string indeed", w="100%")
    assert estimate_ink_width(el, 24, 60) == 60


def test_audit_orders_errors_before_warnings():
    d = _design([
        {"type": "time", "x": 0, "y": "36%", "w": "100%"},
        {"type": "date", "x": 0, "y": "55%", "w": "100%"},
        {"type": "text", "text": "EDGE", "x": 0, "y": 0, "w": "30%"},
    ])
    severities = [f.severity for f in audit(d)]
    assert severities == sorted(severities, key=lambda s: 0 if s == "error" else 1)

# --------------------------------------------------------------------------------------
# A date element's width and preview come from its FORMAT
# --------------------------------------------------------------------------------------
#
# The owner caught this by loading the Next Task preset and looking at the canvas. That
# preset's date is `%d/%m`, five glyphs, which the real emulator build places neatly on its
# plate. The editor drew "Sat 26 Sep", ten glyphs, which overflowed the plate, clipped
# mid-word and collided with the week number beside it. Two places were guessing at what a
# date says: `_GLYPHS` hardcoded 9 for every date, and the canvas hardcoded the string.
#
# The format DECLARES the answer, so `sample_text` is the one place that expands it and both
# the width estimate and the canvas read from it.


def test_sample_text_expands_the_format():
    assert sample_text(Element(type="date", format="%d/%m")) == "30/09"
    assert sample_text(Element(type="date", format="%a %d %b")) == "Wed 30 Sep"
    assert sample_text(Element(type="time", format="%H:%M")) == "14:38"


def test_sample_text_uses_the_format_codegen_defaults_to():
    """No format means codegen's own default, not a different guess.

    A preview that disagrees with the build is the failure this whole module exists to stop,
    and it is just as wrong when the disagreement comes from a default as from a literal.
    """
    assert sample_text(Element(type="date")) == "Wed 30 Sep"
    assert sample_text(Element(type="time")) == "14:38"


def test_the_reference_moment_is_the_widest_one():
    """Wednesday in September, so the estimate is conservative and deterministic.

    "Wednesday" is the longest weekday name and "September" the longest month name, and every
    abbreviated form is three characters whatever the date. So one moment is simultaneously
    the worst case for %a, %A, %b and %B. A reference of, say, a Monday in May would let a
    `%A` date under-estimate by six glyphs, and this function promises never to under-estimate.
    """
    assert sample_text(Element(type="date", format="%A")) == "Wednesday"
    assert sample_text(Element(type="date", format="%B")) == "September"
    for fmt, width in (("%a", 3), ("%b", 3)):
        assert len(sample_text(Element(type="date", format=fmt))) == width


def test_padding_flags_are_stripped_rather_than_giving_up():
    """`%-d` builds on the watch and raises in Windows Python.

    glibc and the watch's C strftime accept the no-pad flag; CPython on Windows does not. A
    design that compiles correctly must not break the editor, and falling back to the raw
    format string would UNDER-estimate: "%-d %B %Y" is 9 characters and its expansion is 17.
    """
    got = sample_text(Element(type="date", format="%-d %B %Y"))
    assert got == "30 September 2026"
    assert len(got) > len("%-d %B %Y")


def test_an_unknown_directive_falls_back_instead_of_raising():
    assert sample_text(Element(type="date", format="%QQ")) == "%QQ"


def test_ink_width_follows_the_format():
    """The estimate behind the overlap check and the ink box, not just the preview.

    Same box, same font, two formats: the five-glyph one must estimate narrower than the
    ten-glyph one. Before the fix both returned the same number, because the glyph count was
    a per-type constant.
    """
    short = Element(type="date", format="%d/%m")
    long = Element(type="date", format="%A %d %B %Y")
    assert estimate_ink_width(short, 21, 200) < estimate_ink_width(long, 21, 200)


def test_resolved_box_carries_the_sample_for_the_canvas():
    design = _design([
        {"type": "date", "x": "18%", "y": "18%", "w": "27%", "h": "9%",
         "color": "#FFFFFF", "font": "auto-small", "format": "%d/%m"},
    ], platforms=("emery",))
    row = resolved_boxes(design)["emery"][0]
    assert row["sample"] == "30/09"
    # And the ink fits the plate, which is the defect the owner saw: a 54px box cannot hold
    # ten glyphs of Gothic 24, and that is exactly what the canvas was drawing into it.
    assert row["ink_w"] <= row["w"]


def test_a_narrow_date_format_no_longer_overlaps_its_neighbour():
    """The Next Task layout, reduced to the two elements that collided.

    With a `%d/%m` date the plate and the week number beside it do not overlap. With the old
    nine-glyph assumption the estimate spilled across the gap. This is the geometry of a
    shipped preset, so a regression here breaks something a user can load.
    """
    design = _design([
        {"type": "date", "x": "18%", "y": "18%", "w": "27%", "h": "9%",
         "color": "#000000", "font": "auto-small", "format": "%d/%m"},
        {"type": "text", "x": "47%", "y": "18%", "w": "16%", "text": "v.44",
         "color": "#AAAAAA", "font": "auto-tiny"},
    ], platforms=("emery",))
    assert [f.code for f in audit(design) if f.code == "TEXT_OVERLAP"] == []


# --------------------------------------------------------------------------------------
# The default target
# --------------------------------------------------------------------------------------


def test_a_new_design_targets_emery_alone():
    """One watch by default.

    Defaulting to two compiled and booted two emulators on every build before the layout was
    settled, and reported round-screen findings about a design not yet aimed at a round
    screen. Choosing round is a design decision, not a resolution setting.
    """
    assert DEFAULT_PLATFORMS == ["emery"]
    blank = Design.model_validate({"schema": 1, "name": "T"})
    assert blank.platforms == ["emery"]


# --------------------------------------------------------------------------------------
# The on-watch colour the canvas draws with
# --------------------------------------------------------------------------------------


def test_resolved_box_carries_the_on_watch_colour():
    """The canvas shows what a wrist sees, and the correction happens HERE.

    Pebble's screens are reflective e-paper and desaturate hard, so a preview in specified
    colours looks markedly unlike the render even when the geometry is pixel-correct. The
    Preview tab already defaults to the on-wrist appearance; the canvas used to disagree with
    it. Computed server-side because the 64-entry table lives here, and a second copy in
    TypeScript would drift, which is the mistake this rail already made with the date sample.
    """
    design = _design([
        {"type": "rect", "x": 0, "y": 0, "w": "50%", "h": "10%", "color": "#FF0000"},
        {"type": "analog", "x": "center", "y": "center", "w": "50%", "h": "44%",
         "color": "#FFFF00", "hand_color": "#00FF00"},
    ], platforms=("emery",))
    rows = resolved_boxes(design)["emery"]
    assert rows[0]["on_watch"] == "#E35462"
    assert rows[1]["on_watch"] == "#FFEEAB"
    assert rows[1]["on_watch_hand"] == "#8EE391"


def test_two_colour_platforms_get_no_on_watch_colour():
    """Nothing to desaturate on a black-and-white screen.

    Emitting a correction there would invent a shade the framebuffer cannot hold, and the
    canvas would draw a grey that the watch renders as pure black or pure white. Absent is the
    honest answer, and the canvas falls back to the specified colour.
    """
    design = _design([
        {"type": "rect", "x": 0, "y": 0, "w": "50%", "h": "10%", "color": "#FF0000"},
    ], platforms=("aplite",))
    row = resolved_boxes(design)["aplite"][0]
    assert "on_watch" not in row
    assert "on_watch_hand" not in row


def test_on_watch_colour_is_not_applied_twice():
    """The correction maps a SPECIFIED colour, and its output must not be re-correctable.

    This rail once ran the table over an emulator screenshot that the SDK had already
    corrected, and shipped the result labelled as ground truth. The guard here is cheap:
    correcting an already-corrected value must move it again, which proves the function is not
    idempotent and therefore must never be applied to its own output.
    """
    from watch_builder.sunlight import correct_hex
    once = correct_hex("#FF0000")
    twice = correct_hex(once)
    assert once == "#E35462"
    assert twice != once, "correct_hex looks idempotent; the double-correction guard is void"
