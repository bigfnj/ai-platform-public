"""The code generator: the half that makes the model's output safe.

The strongest possible test of this module is "does the generated C compile", and that needs
the Pebble SDK, which has no Windows build. So it is verified two ways:

  * here, against properties of the emitted source that the compiler would otherwise catch
    (guarded font references, per-platform blocks, no absolute screen sizes leaking in);
  * and end to end by compiling a generated project on the real SDK, which was done when
    this module was written and produced a clean seven-platform build under -Werror. That
    run is recorded in docs/DESIGN.md rather than being re-run here, because a unit test
    that needs a 2 GB toolchain is a unit test that gets skipped.
"""
from __future__ import annotations

import json

from watch_builder.codegen import (
    generate_c,
    generate_package_json,
    generate_project,
    pick_font,
)
from watch_builder.design import PLATFORMS, Design


def _design(elements, platforms=("emery",), **over):
    doc = {"schema": 1, "name": "T", "background": "#001E41",
           "platforms": list(platforms), "elements": elements}
    doc.update(over)
    return Design.model_validate(doc)


def _without_comments(source: str) -> str:
    """Generated source minus its `//` comment lines.

    Needed because this generator documents its own decisions in the C it emits, so a naive
    substring assertion matches the explanation rather than the code. `SECOND_UNIT` appears
    only in a comment saying why it is not used.
    """
    return "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("//"))


# --- font selection -------------------------------------------------------------------


def test_auto_picks_a_bigger_time_face_on_the_bigger_screens():
    """System fonts do not scale, so "auto" is the only thing that makes type proportional."""
    el = _design([{"type": "time"}]).elements[0]
    assert pick_font(el, PLATFORMS["emery"])[0] == "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM"
    assert pick_font(el, PLATFORMS["gabbro"])[0] == "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM"
    assert pick_font(el, PLATFORMS["chalk"])[0] == "FONT_KEY_LECO_42_NUMBERS"
    assert pick_font(el, PLATFORMS["basalt"])[0] == "FONT_KEY_BITHAM_42_BOLD"


def test_leco60_is_never_selected_on_a_platform_that_lacks_it():
    """Referencing LECO_60 on aplite/basalt/chalk/diorite is a hard compile error.

    'FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM' undeclared — not a graceful fallback. So the
    selector must never name it for those platforms, whatever the screen size says.
    """
    el = _design([{"type": "time"}]).elements[0]
    for name in ("aplite", "basalt", "chalk", "diorite"):
        assert "LECO_60" not in pick_font(el, PLATFORMS[name])[0]


def test_an_explicit_leco60_is_substituted_rather_than_breaking_the_build():
    """A document may name it directly; one platform's absence must not fail seven builds."""
    d = _design([{"type": "time", "font": "FONT_KEY_LECO_60_NUMBERS_AM_PM"}],
                platforms=("basalt", "emery"))
    c = generate_c(d)
    basalt_block = c.split("#if defined(PBL_PLATFORM_BASALT)")[1].split("#endif")[0]
    assert "LECO_60" not in basalt_block
    emery_block = c.split("#if defined(PBL_PLATFORM_EMERY)")[1].split("#endif")[0]
    assert "LECO_60" in emery_block


# --- emitted source properties --------------------------------------------------------


def test_one_guarded_block_per_target_platform():
    d = _design([{"type": "time"}], platforms=("aplite", "emery", "gabbro"))
    c = generate_c(d)
    for name in ("APLITE", "EMERY", "GABBRO"):
        assert f"#if defined(PBL_PLATFORM_{name})" in c
    assert "PBL_PLATFORM_BASALT)" not in c


def test_unhandled_platform_draws_something_rather_than_a_blank_screen():
    c = generate_c(_design([{"type": "time"}], platforms=("emery",)))
    assert "no layout" in c


def test_minute_unit_not_second_unit():
    """Core Devices name animated watchfaces among the top three battery consumers."""
    c = generate_c(_design([{"type": "time"}]))
    assert "tick_timer_service_subscribe(MINUTE_UNIT" in c
    # Comments stripped first: the generated source carries a comment explaining why
    # SECOND_UNIT is not used, and a naive substring check fails on the explanation.
    code = _without_comments(c)
    assert "SECOND_UNIT" not in code


def test_clears_full_bounds_but_lays_out_against_unobstructed():
    """Painting only the unobstructed area leaves stale pixels behind Quick View."""
    c = generate_c(_design([{"type": "time"}]))
    assert "layer_get_bounds(layer)" in c
    assert "layer_get_unobstructed_bounds(layer)" in c
    assert "graphics_fill_rect(ctx, full, 0, GCornerNone)" in c


def test_services_are_only_subscribed_when_something_needs_them():
    """A face with no battery element must not wake for battery events."""
    plain = generate_c(_design([{"type": "time"}]))
    assert "battery_state_service_subscribe" not in plain
    assert "connection_service_subscribe" not in plain

    rich = generate_c(_design([{"type": "time"},
                               {"type": "battery-bar", "x": 0, "y": 0, "w": "50%", "h": 4},
                               {"type": "bluetooth", "x": "50%", "y": "90%"}]))
    assert "battery_state_service_subscribe" in rich
    assert "connection_service_subscribe" in rich


def test_every_subscribe_has_a_matching_unsubscribe():
    c = generate_c(_design([{"type": "time"},
                            {"type": "battery-bar", "x": 0, "y": 0, "w": "50%", "h": 4},
                            {"type": "bluetooth", "x": "50%", "y": "90%"}]))
    for service in ("tick_timer_service", "battery_state_service", "connection_service",
                    "unobstructed_area_service"):
        assert f"{service}_subscribe" in c
        assert f"{service}_unsubscribe" in c


def test_layer_create_is_matched_by_layer_destroy():
    c = generate_c(_design([{"type": "time"}]))
    assert c.count("layer_create(") == 1
    assert "layer_destroy(s_canvas)" in c


def test_health_read_is_guarded_and_checks_accessibility():
    """An unchecked Health read renders "no data" as a confident zero.

    Two separate requirements: the PBL_HEALTH guard (the API does not exist everywhere) and
    the accessibility mask (the user may have Health switched off where it does).
    """
    c = generate_c(_design([{"type": "steps", "x": 0, "y": "70%", "w": "100%"}]))
    assert "#if defined(PBL_HEALTH)" in c
    assert "health_service_metric_accessible" in c
    assert "HealthServiceAccessibilityMaskAvailable" in c
    assert '"--"' in c


def test_time_respects_the_users_12_or_24_hour_setting():
    c = generate_c(_design([{"type": "time"}]))
    assert "clock_is_24h_style()" in c


def test_text_buffers_are_static():
    """text drawn from a stack buffer is the classic Pebble crash.

    graphics_draw_text reads through the pointer during the call so a stack buffer would
    survive here, but the buffers are filled in refresh_state() and read in the update proc,
    which are different calls. static is required, not stylistic.
    """
    c = generate_c(_design([{"type": "time"}, {"type": "date"}]))
    assert "static char s_time_buf" in c
    assert "static char s_date_buf" in c


def test_analog_hour_hand_creeps_with_the_minutes():
    """An hour hand that jumps on the hour is the clearest tell of a generated face."""
    c = generate_c(_design([{"type": "analog", "x": 0, "y": 0, "w": "80%", "h": "80%"}]))
    assert "s_tm.tm_min * TRIG_MAX_ANGLE) / (12 * 60)" in c


def test_fixed_point_trig_multiplies_before_dividing():
    """Dividing first truncates to zero, which is the commonest fixed-point bug here."""
    c = generate_c(_design([{"type": "analog", "x": 0, "y": 0, "w": "80%", "h": "80%"}]))
    assert "/ TRIG_MAX_RATIO" in c
    assert "sin_lookup(a) * ri) / TRIG_MAX_RATIO" in c


def test_text_literals_are_escaped_into_the_source():
    """A quote or backslash in a label must not break out of the C string literal."""
    c = generate_c(_design([{"type": "text", "text": 'He said "hi"\\done', "x": 0, "y": 0,
                             "w": "100%"}]))
    assert r'\"hi\"' in c
    assert "He said" in c


def test_no_float_arithmetic_in_the_emitted_source():
    """No FPU on these MCUs; floats are emulated and large.

    Checked as a property of the source rather than trusted, because the generator does
    plenty of float maths internally and only the OUTPUT has to be integer.
    """
    import re
    c = generate_c(_design([
        {"type": "time"},
        {"type": "analog", "x": 0, "y": 0, "w": "80%", "h": "80%"},
        {"type": "battery-ring", "x": "5%", "y": "5%", "w": "90%", "h": "90%"},
    ]))
    code = _without_comments(c)
    assert not re.search(r"\b\d+\.\d+\b", code), "float literal in generated C"
    assert "float " not in code
    assert "double " not in code


# --- package.json ---------------------------------------------------------------------


def test_package_json_declares_a_watchface_and_the_target_platforms():
    d = _design([{"type": "time"}], platforms=("emery", "gabbro"))
    doc = json.loads(generate_package_json(d, app_uuid="x"))
    assert doc["pebble"]["watchapp"] == {"watchface": True}
    assert doc["pebble"]["targetPlatforms"] == ["emery", "gabbro"]
    assert doc["pebble"]["uuid"] == "x"


def test_health_capability_is_declared_only_when_steps_are_used():
    plain = json.loads(generate_package_json(_design([{"type": "time"}])))
    assert "capabilities" not in plain["pebble"]
    steps = json.loads(generate_package_json(
        _design([{"type": "steps", "x": 0, "y": 0, "w": "100%"}])))
    assert steps["pebble"]["capabilities"] == ["health"]


def test_each_build_gets_a_fresh_uuid_by_default():
    d = _design([{"type": "time"}])
    a = json.loads(generate_package_json(d))["pebble"]["uuid"]
    b = json.loads(generate_package_json(d))["pebble"]["uuid"]
    assert a != b


def test_project_has_exactly_the_three_files_the_sdk_needs():
    files = generate_project(_design([{"type": "time"}]))
    assert set(files) == {"src/c/main.c", "package.json", "wscript"}
    assert files["wscript"].startswith("import os.path")


def test_slug_is_filesystem_safe_and_never_empty():
    """The downloaded .pbw is named from the design, not the build directory.

    waf names the bundle after the project dir, which is a mkdtemp path, so the
    honest-looking result.pbw.name is something like "wb-ma06nyic.pbw". Observed in the
    container smoke test before this was fixed.
    """
    from watch_builder.codegen import slug
    assert slug("My Nice Face") == "my-nice-face"
    assert slug("  weird///name  ") == "weird-name"
    assert slug("!!!") == "watchface"
    assert slug("") == "watchface"
