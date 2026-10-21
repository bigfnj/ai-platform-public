"""The presets, and the two element types they needed.

A preset is worse than no preset if it does not build, does not audit clean, or misrepresents
what it reports. So the tests here are about those three things rather than about pixel
positions, which are a design judgement and were checked by building all eight on the real
SDK and looking at the renders.

The paired tests matter as much as the passing ones. Two checks were WEAKENED to make these
presets possible: polygons may now fall outside the screen, and `arc` was added to the set
that must declare a box. A weakened check that cannot fail is worse than the false positive
it replaced, so each relaxation has a test proving the real defect still fires.
"""
from __future__ import annotations

import pytest

from watch_builder import presets
from watch_builder.codegen import generate_c
from watch_builder.design import (
    PLATFORMS,
    Design,
    check_semantics,
    clock_elements,
    snap_channel,
)
from watch_builder.layout import audit, audit_platform, resolve_box, resolved_boxes

ALL = presets.listing()
IDS = [p["id"] for p in ALL]


def _doc(preset: dict) -> Design:
    return Design.model_validate(preset["design"])


def test_there_are_presets_and_ids_are_unique():
    assert len(ALL) >= 6
    assert len(set(IDS)) == len(IDS)
    assert presets.ids() == IDS


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_validates_and_passes_semantics(preset):
    design = _doc(preset)
    assert check_semantics(design) == []


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_has_no_layout_errors(preset):
    """Zero ERRORS on every platform the preset declares.

    Warnings are allowed and one preset has one on purpose: next-task's bar sits under
    Timeline Quick View, which is a property of the design it copies rather than a mistake,
    and its `limits` says so. Errors are not allowed, because an error means cropped,
    overlapping or off-screen.
    """
    findings = audit(_doc(preset))
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], [f.message for f in errors]


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_shows_the_time(preset):
    """Every preset is a watchFACE. One that shows no time is a wallpaper."""
    assert clock_elements(_doc(preset)) >= 1


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_colours_are_already_on_the_palette(preset):
    """Snapping a preset colour must be a no-op.

    The module claims its palette is pre-snapped, which is what lets it guarantee that two
    colours meant to be distinguishable stay distinguishable. Hietala's own mockup hexes do
    NOT have that property: four pairs collide, including a rust countdown ring against a
    bronze step badge that appear on the same face. If this test fails, some colour went back
    to a raw mockup value and a collision may have come with it.
    """
    design = _doc(preset)
    for el in design.elements:
        for value in (el.color, el.hand_color, design.background):
            if not value:
                continue
            r, g, b = (int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            snapped = f"#{snap_channel(r):02X}{snap_channel(g):02X}{snap_channel(b):02X}"
            assert snapped == value.upper(), f"{value} snaps to {snapped}"


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_live_claims_name_element_types_that_are_present(preset):
    """`live` must be honest in the one way a test can check.

    It cannot verify that a ring is wired to a battery, but it CAN verify that a preset
    claiming live battery has a battery element and that one claiming live bluetooth has a
    bluetooth element. The failure this catches is a copied `live` list surviving an edit that
    removed the element, which is exactly what happened to comic-panel: its battery bar came
    out and the claim had to come out with it.
    """
    types = {el.type for el in _doc(preset).elements}
    needs = {
        "battery": {"battery-bar", "battery-ring", "battery-text"},
        "bluetooth": {"bluetooth"},
        "date": {"date"},
        "time": {"time", "analog"},
        "steps": {"steps"},
    }
    for claim in preset["live"]:
        assert claim in needs, f"unknown live claim {claim!r}"
        assert types & needs[claim], f"claims live {claim} with no such element"


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_generates_c_for_every_declared_platform(preset):
    design = _doc(preset)
    src = generate_c(design)
    for name in design.platforms:
        assert f"PBL_PLATFORM_{name.upper()}" in src
    if any(el.type == "polygon" for el in design.elements):
        # Created once in load_paths and destroyed in unload_paths, never in the update proc:
        # an update proc runs many times a second on a 24 to 128 KB heap.
        assert "gpath_create" in src and "gpath_destroy" in src
        assert "gpath_create" not in src.split("canvas_update_proc")[-1]
    if any(el.type == "arc" for el in design.elements):
        assert "graphics_fill_radial" in src


@pytest.mark.parametrize("preset", ALL, ids=IDS)
def test_preset_documents_what_it_cannot_do(preset):
    """Every preset carries prose, and only the empty one may have no limits.

    Not a style rule. These are derived from a face driven by a calendar, a task list and a
    work timer, none of which this rail has, so most presets carry shapes that depict rather
    than report. quiet-dial is the one with nothing to disclaim: it has no wedges, no ring and
    no badge.
    """
    assert preset["why"].strip()
    assert preset["after"].strip()
    if preset["id"] != "quiet-dial":
        assert preset["limits"], "a preset with depicted elements must say so"


def test_preset_lookup():
    assert presets.get("comic-panel")["label"] == "Comic Panel"
    assert presets.get("no-such-preset") is None


def test_wedge_angle_is_hours_not_degrees():
    """The rim is a twelve-hour countdown, and the helper's units say so.

    His central idea is that an event's position is TIME UNTIL it starts, not its clock time:
    three o'clock means three hours away and twelve means now. A wedge built from degrees
    would be silently rotated by a factor of thirty, and the face would still look plausible.
    So: a wedge at hour 3 must have its inner tip on the right of centre and level with it,
    and a wedge at hour 0 must be directly above.
    """
    plat = PLATFORMS["emery"]
    cx, cy = plat.width / 2, plat.height / 2

    at_three = presets._event_wedge(3.0, 3.5, "#FF0000")
    inner = at_three["points"][-1]          # last point is the hours_from tip
    px = float(str(inner[0]).rstrip("%")) / 100 * plat.width
    py = float(str(inner[1]).rstrip("%")) / 100 * plat.height
    assert px > cx + 20, "hour 3 should sit to the RIGHT of centre"
    assert abs(py - cy) < 6, "hour 3 should be level with centre"

    at_noon = presets._event_wedge(0.0, 0.5, "#FF0000")
    inner0 = at_noon["points"][-1]
    py0 = float(str(inner0[1]).rstrip("%")) / 100 * plat.height
    assert py0 < cy - 20, "hour 0 should sit ABOVE centre, which is now"


# --------------------------------------------------------------------------------------
# The two relaxed checks, each with the defect it must still catch
# --------------------------------------------------------------------------------------


def _one(elements, platform="emery"):
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000",
        "platforms": [platform], "elements": elements,
    })
    return audit_platform(design, platform)


def test_polygon_bleeding_off_the_edge_is_allowed():
    """The relaxation. A wedge whose outer points are off-screen is the intended shape."""
    findings = _one([
        {"type": "polygon", "color": "#FF0000",
         "points": [["50%", "50%"], ["180%", "-40%"], ["180%", "40%"]]},
    ])
    assert [f.code for f in findings if f.code == "OUT_OF_BOUNDS"] == []


def test_polygon_entirely_off_screen_still_errors():
    """The paired half. Without this the relaxation would accept a shape that draws nothing.

    Mutation check: reverting `_BLEEDS` to an empty set makes the previous test fail, and
    removing the `_box_reaches_screen` call makes this one fail. Neither passes both ways, so
    the relaxation is narrower than "stop checking polygons".
    """
    findings = _one([
        {"type": "polygon", "color": "#FF0000",
         "points": [["150%", "150%"], ["190%", "150%"], ["170%", "190%"]]},
    ])
    codes = [f.code for f in findings]
    assert "OUT_OF_BOUNDS" in codes
    assert any("draws nothing" in f.message for f in findings if f.code == "OUT_OF_BOUNDS")


def test_rect_off_the_edge_is_still_an_error():
    """The relaxation must not have leaked to other types. Cropping a rect IS the defect."""
    findings = _one([{"type": "rect", "x": "80%", "y": "10%", "w": "50%", "h": "10%",
                      "color": "#FFFFFF"}])
    assert "OUT_OF_BOUNDS" in [f.code for f in findings]


def test_arc_without_a_box_is_rejected():
    """`arc` joined _NEEDS_SIZE because the fallthrough default draws a sliver.

    An element with no w/h falls through to full screen width and 20px high. For a ring that
    is not a wrong size, it is a shape nobody asked for, drawn without complaint.
    """
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [{"type": "arc", "color": "#FF5500"}],
    })
    problems = check_semantics(design)
    assert any("needs both 'w' and 'h'" in p for p in problems)


def test_arc_with_a_zero_sweep_is_rejected():
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [{"type": "arc", "w": "60%", "h": "60%", "color": "#FF5500",
                      "angle_start": 90, "angle_end": 90}],
    })
    assert any("draws nothing" in p for p in check_semantics(design))


def test_arc_with_zero_thickness_is_rejected():
    """The thickness floor used to be keyed on the literal "battery-ring"."""
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [{"type": "arc", "w": "60%", "h": "60%", "color": "#FF5500",
                      "thickness": 0}],
    })
    assert any("thickness" in p for p in check_semantics(design))


def test_arc_is_treated_as_round_shaped_on_a_round_screen():
    """An arc inside the usable circle must not warn.

    Its square bounding box has corners outside the circle while every pixel it draws is
    inside, which is the false positive already fixed once here for battery-ring.
    """
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["gabbro"],
        "elements": [{"type": "arc", "x": "center", "y": "center", "w": "80%", "h": "80%",
                      "thickness": 6, "color": "#FF5500"}],
    })
    assert [f.code for f in audit(design) if f.code == "OUTSIDE_CIRCLE"] == []


# --------------------------------------------------------------------------------------
# What the canvas is given
# --------------------------------------------------------------------------------------


def test_resolved_boxes_carry_polygon_points_and_arc_angles():
    """The canvas must not re-resolve geometry; there is one resolver of record.

    A polygon's bounding box says nothing about its shape, so without points the editor would
    have to redo the percentage arithmetic in TypeScript and would drift from the build.
    """
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [
            {"type": "polygon", "color": "#FF0000",
             "points": [["10%", "10%"], ["90%", "10%"], ["50%", "60%"]]},
            {"type": "arc", "x": "center", "y": "center", "w": "60%", "h": "50%",
             "thickness": 7, "color": "#FF5500", "angle_start": 30, "angle_end": 200},
        ],
    })
    rows = resolved_boxes(design)["emery"]
    poly, arc = rows[0], rows[1]
    # 10% of 228 is 22.8 and resolve_length ROUNDS, so 23 rather than 22. Spelled out
    # because getting it wrong here is how a test ends up asserting the resolver's bug.
    assert poly["points"] == [[20, 23], [180, 23], [100, 137]]
    assert arc["angles"] == [30, 200]
    assert arc["thickness"] == 7
    # And the box still bounds the shape, so selection and hit-testing work.
    assert poly["x"] == 20 and poly["y"] == 23
    assert poly["w"] == 160 and poly["h"] == 114


def test_polygon_box_bounds_a_bleeding_shape():
    """A bleeding polygon's box may start at a negative coordinate, and must still bound it."""
    el = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [{"type": "polygon", "color": "#FF0000",
                      "points": [["-20%", "50%"], ["40%", "20%"], ["40%", "80%"]]}],
    }).elements[0]
    box = resolve_box(el, PLATFORMS["emery"], 0)
    assert box.x == -40
    assert box.right == 80


# --------------------------------------------------------------------------------------
# The analog hands
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["basalt", "chalk", "emery", "gabbro"])
def test_hour_hand_is_heavier_than_the_minute_hand(platform):
    """Weight is how the eye separates the hands when they are near each other.

    The previous expressions, `max(3, radius // 12)` and `max(1, radius // 20)`, both give 3
    at radius 43, which is emery's dial at the size these presets use. Every Time 2 face this
    rail built had two identical hands, and the floor hid it: the hour hand was clamped UP to
    3 while the minute hand happened to compute to 3. Found by looking at a render, where the
    dial appears to have a single hand.
    """
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": [platform],
        "elements": [{"type": "analog", "x": "center", "y": "center",
                      "w": "43%", "h": "38%", "color": "#FFFFFF"}],
    })
    src = generate_c(design)
    widths = [int(line.split(",")[1].strip(" );"))
              for line in src.splitlines()
              if "graphics_context_set_stroke_width" in line]
    # Three stroke widths in an analog block, in order: the ticks, the hour hand, the minute
    # hand. Indexing rather than searching, because a search for "the biggest" would pass on a
    # generator that emitted them in any order.
    assert len(widths) == 3, widths
    _ticks, hour, minute = widths
    assert hour > minute, f"hour {hour} is not heavier than minute {minute} on {platform}"
    assert hour % 2 == 1 and minute % 2 == 1, "stroke widths must be odd"


def test_analog_draws_no_outer_circle():
    """The contract the editor's canvas mirrors.

    `analog` emits twelve ticks, two hands and a centre dot, and NO ring around the outside.
    That absence is load-bearing: it is what lets a preset put a filled disc underneath in one
    colour and the marks on top in another, which is how every dial here is built.

    The canvas used to draw a circle border for analog, so the editor showed a ring no build
    produces, on the most prominent element of every preset. The canvas is fixed; this pins
    the codegen side, because if codegen ever gained an outer circle the canvas would be wrong
    again in the other direction and nothing else would notice.
    """
    design = Design.model_validate({
        "schema": 1, "name": "T", "background": "#000000", "platforms": ["emery"],
        "elements": [{"type": "analog", "x": "center", "y": "center",
                      "w": "43%", "h": "38%", "color": "#FFFFFF"}],
    })
    src = generate_c(design)
    assert "graphics_draw_circle" not in src, "analog must not outline itself"
    # Exactly one filled circle: the centre dot. More than one means a disc crept in.
    assert src.count("graphics_fill_circle") == 1
    # Twelve ticks, drawn in a loop rather than unrolled.
    assert "for (int i = 0; i < 12; i++)" in src
    assert src.count("draw_hand(ctx") == 2
