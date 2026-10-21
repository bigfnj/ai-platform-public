"""Design document -> a buildable Pebble C project.

Deterministic. No model is involved past this point, which is the whole safety argument: a
document that validates produces C that compiles, so a language model's mistakes are caught
by `design.py` rather than by the compiler.

Everything is drawn in ONE canvas layer's update procedure rather than a tree of stock
layers. Three reasons, all of which matter more here than in a hand-written face:

  * draw order is explicit and matches the document's element order, so "bring to front" in
    the editor is a list reorder rather than a layer-tree surgery;
  * the update proc reads `layer_get_unobstructed_bounds()` every frame, so Timeline Quick
    View is handled with no per-element repositioning code at all;
  * one layer means one `layer_mark_dirty()`, so a generated face cannot accidentally redraw
    six sublayers when the minute changes.
"""
from __future__ import annotations

import json
import uuid as uuidlib

from watch_builder.design import (
    Design,
    Element,
    PLATFORMS,
    Platform,
    gcolor_expr,
    resolve_length,
)

# --------------------------------------------------------------------------------------
# Font selection
# --------------------------------------------------------------------------------------
#
# System fonts render at the SAME pixel size on every platform: measured, every line height
# byte-identical between basalt and emery. So "auto" is not cosmetic, it is the only way a
# document gets type proportional to the screen it is on. The widths in the comments are the
# measured box for "00:00", which is what decides whether a face fits.

_TIME_FONTS = [
    # (min height, requires LECO_60, key, measured width of "00:00", line box)
    (200, True, "FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM", 169, 60),
    (180, False, "FONT_KEY_LECO_42_NUMBERS", 116, 42),
    (0, False, "FONT_KEY_BITHAM_42_BOLD", 126, 42),
]

_TEXT_FONTS = {
    "auto": [(228, "FONT_KEY_GOTHIC_28_BOLD", 28),
             (180, "FONT_KEY_GOTHIC_24_BOLD", 24),
             (0, "FONT_KEY_GOTHIC_18_BOLD", 18)],
    "auto-small": [(228, "FONT_KEY_GOTHIC_24", 24),
                   (180, "FONT_KEY_GOTHIC_18", 18),
                   (0, "FONT_KEY_GOTHIC_14", 14)],
    "auto-tiny": [(228, "FONT_KEY_GOTHIC_18", 18),
                  (180, "FONT_KEY_GOTHIC_14", 14),
                  (0, "FONT_KEY_GOTHIC_09", 9)],
}


def pick_font(el: Element, plat: Platform) -> tuple[str, int]:
    """(font key, line-box height) for this element on this platform."""
    if el.font not in ("auto", "auto-small", "auto-tiny"):
        # An explicit key. Guarded below in emit(); we do not second-guess it here.
        return el.font, _explicit_height(el.font)

    if el.type in ("time",) and el.font == "auto":
        for min_h, needs60, key, _w, box in _TIME_FONTS:
            if plat.height >= min_h and (not needs60 or plat.has_leco60):
                return key, box
    table = _TEXT_FONTS[el.font if el.font in _TEXT_FONTS else "auto"]
    for min_h, key, box in table:
        if plat.height >= min_h:
            return key, box
    return table[-1][1], table[-1][2]


def _explicit_height(key: str) -> int:
    """Line box for an explicitly named font key, parsed from the key itself.

    The size is in the name for every system font, which is why this is a parse rather than a
    table that would drift. Falls back to 24, a middling value that will not crop.
    """
    digits = "".join(c for c in key if c.isdigit())
    try:
        return int(digits[:2]) if digits else 24
    except ValueError:
        return 24


# Fonts that exist only on flint/emery/gabbro. Naming one on an older platform is a hard
# compile error, not a graceful fallback, so an explicit reference has to be guarded.
_LECO60 = {"FONT_KEY_LECO_60_BOLD_NUMBERS_AM_PM", "FONT_KEY_LECO_60_NUMBERS_AM_PM"}


def image_resource_name(index: int, platform: str) -> str:
    """The RESOURCE_ID_* suffix for one image element on one platform.

    Per platform, not per image, because the same element resolves to a different pixel box
    on each screen and graphics_draw_bitmap_in_rect will not scale. Each entry carries its
    own `targetPlatforms`, so a platform's resource pack only contains its own variant and
    nobody pays for the others.
    """
    return f"IMG_{index}_{platform.upper()}"


# --------------------------------------------------------------------------------------
# Element emission
# --------------------------------------------------------------------------------------


def _box(el: Element, plat: Platform, default_h: int) -> tuple[int, int, int, int]:
    """Resolve an element's box against this platform's unobstructed bounds."""
    w = resolve_length(el.w, plat.width) if el.w is not None else plat.width
    h = resolve_length(el.h, plat.height) if el.h is not None else default_h
    x = resolve_length(el.x, plat.width, w)
    y = resolve_length(el.y, plat.height, h)
    return x, y, w, h


def _align(a: str) -> str:
    return {"left": "GTextAlignmentLeft",
            "center": "GTextAlignmentCenter",
            "right": "GTextAlignmentRight"}[a]


def _emit_element(el: Element, plat: Platform, idx: int) -> list[str]:
    """C statements drawing one element. Runs inside the canvas update proc.

    `bounds` (unobstructed) and `ctx` are in scope. Coordinates are already resolved to
    integers for this platform, so the generated C contains no layout arithmetic.
    """
    col = gcolor_expr(el.color, plat.colors)
    out: list[str] = [f"  // [{idx}] {el.type}"]

    if el.type in ("time", "date", "text", "battery-text", "steps"):
        font, box_h = pick_font(el, plat)
        if font in _LECO60 and not plat.has_leco60:
            # An explicit LECO_60 on a platform without it. Substituting keeps the build
            # green; refusing would make one platform's absence fail a seven-platform build.
            font = "FONT_KEY_LECO_42_NUMBERS"
            box_h = 42
        x, y, w, h = _box(el, plat, box_h + 4)

        src = {
            "time": "s_time_buf",
            "date": "s_date_buf",
            "battery-text": "s_batt_buf",
            "steps": "s_steps_buf",
        }.get(el.type)
        if el.type == "text":
            literal = json.dumps(el.text or "")
            src = None
            out.append(f"  graphics_context_set_text_color(ctx, {col});")
            out.append(
                f"  graphics_draw_text(ctx, {literal}, fonts_get_system_font({font}),"
                f" GRect({x}, {y}, {w}, {h}), GTextOverflowModeTrailingEllipsis,"
                f" {_align(el.align)}, NULL);")
        else:
            out.append(f"  graphics_context_set_text_color(ctx, {col});")
            out.append(
                f"  graphics_draw_text(ctx, {src}, fonts_get_system_font({font}),"
                f" GRect({x}, {y}, {w}, {h}), GTextOverflowModeTrailingEllipsis,"
                f" {_align(el.align)}, NULL);")
        return out

    if el.type == "rect":
        x, y, w, h = _box(el, plat, 20)
        if el.filled:
            out.append(f"  graphics_context_set_fill_color(ctx, {col});")
            corners = "GCornersAll" if el.radius else "GCornerNone"
            out.append(f"  graphics_fill_rect(ctx, GRect({x}, {y}, {w}, {h}),"
                       f" {el.radius}, {corners});")
        else:
            out.append(f"  graphics_context_set_stroke_color(ctx, {col});")
            out.append(f"  graphics_context_set_stroke_width(ctx, {el.stroke});")
            if el.radius:
                out.append(f"  graphics_draw_round_rect(ctx, GRect({x}, {y}, {w}, {h}),"
                           f" {el.radius});")
            else:
                out.append(f"  graphics_draw_rect(ctx, GRect({x}, {y}, {w}, {h}));")
        return out

    if el.type == "circle":
        r = resolve_length(el.r, min(plat.width, plat.height)) if el.r is not None else 10
        cx = resolve_length(el.x, plat.width, 0)
        cy = resolve_length(el.y, plat.height, 0)
        if el.filled:
            out.append(f"  graphics_context_set_fill_color(ctx, {col});")
            out.append(f"  graphics_fill_circle(ctx, GPoint({cx}, {cy}), {r});")
        else:
            out.append(f"  graphics_context_set_stroke_color(ctx, {col});")
            out.append(f"  graphics_context_set_stroke_width(ctx, {el.stroke});")
            out.append(f"  graphics_draw_circle(ctx, GPoint({cx}, {cy}), {r});")
        return out

    if el.type == "line":
        x1 = resolve_length(el.x, plat.width, 0)
        y1 = resolve_length(el.y, plat.height, 0)
        x2 = resolve_length(el.x2, plat.width, 0)
        y2 = resolve_length(el.y2, plat.height, 0)
        out.append(f"  graphics_context_set_stroke_color(ctx, {col});")
        out.append(f"  graphics_context_set_stroke_width(ctx, {el.stroke});")
        out.append(f"  graphics_draw_line(ctx, GPoint({x1}, {y1}), GPoint({x2}, {y2}));")
        return out

    if el.type == "battery-bar":
        x, y, w, h = _box(el, plat, 6)
        out.append(f"  graphics_context_set_fill_color(ctx, {col});")
        out.append(f"  graphics_fill_rect(ctx, GRect({x}, {y},"
                   f" ({w} * s_battery) / 100, {h}), 0, GCornerNone);")
        return out

    if el.type == "battery-ring":
        x, y, w, h = _box(el, plat, 40)
        out.append(f"  graphics_context_set_fill_color(ctx, {col});")
        # fill_radial is the purpose-built primitive here: there is no stroked-arc call, and
        # punching a hole with a background circle would break over a non-flat background.
        out.append(f"  graphics_fill_radial(ctx, GRect({x}, {y}, {w}, {h}),"
                   f" GOvalScaleModeFitCircle, {el.thickness},"
                   " DEG_TO_TRIGANGLE(0), DEG_TO_TRIGANGLE((360 * s_battery) / 100));")
        return out

    if el.type == "arc":
        x, y, w, h = _box(el, plat, 40)
        out.append(f"  graphics_context_set_fill_color(ctx, {col});")
        # Same primitive as battery-ring, with the sweep taken from the document
        # instead of from battery level. GOvalScaleModeFitCircle keeps it circular in
        # a non-square box, which matters because a box given in percentages of a
        # 200x228 screen is not square.
        out.append(f"  graphics_fill_radial(ctx, GRect({x}, {y}, {w}, {h}),"
                   f" GOvalScaleModeFitCircle, {el.thickness},"
                   f" DEG_TO_TRIGANGLE({el.angle_start}),"
                   f" DEG_TO_TRIGANGLE({el.angle_end}));")
        return out

    if el.type == "bluetooth":
        r = max(3, plat.height // 45)
        cx = resolve_length(el.x, plat.width, 0)
        cy = resolve_length(el.y, plat.height, 0)
        # Drawn only while DISCONNECTED. A marker that is always present is furniture; one
        # that appears on a state change is information.
        out.append("  if (!s_connected) {")
        out.append(f"    graphics_context_set_fill_color(ctx, {col});")
        out.append(f"    graphics_fill_circle(ctx, GPoint({cx}, {cy}), {r});")
        out.append("  }")
        return out

    if el.type == "analog":
        x, y, w, h = _box(el, plat, 100)
        cx, cy = x + w // 2, y + h // 2
        radius = min(w, h) // 2
        hand = gcolor_expr(el.hand_color or el.color, plat.colors)
        out.append(f"  graphics_context_set_stroke_color(ctx, {col});")
        out.append("  graphics_context_set_stroke_width(ctx, 1);")
        out.append(f"  for (int i = 0; i < 12; i++) {{")
        out.append("    int32_t a = (i * TRIG_MAX_ANGLE) / 12;")
        out.append(f"    int ri = {radius} - {max(4, radius // 8)}, ro = {radius} - 2;")
        out.append(f"    GPoint p1 = {{ {cx} + (sin_lookup(a) * ri) / TRIG_MAX_RATIO,"
                   f" {cy} - (cos_lookup(a) * ri) / TRIG_MAX_RATIO }};")
        out.append(f"    GPoint p2 = {{ {cx} + (sin_lookup(a) * ro) / TRIG_MAX_RATIO,"
                   f" {cy} - (cos_lookup(a) * ro) / TRIG_MAX_RATIO }};")
        out.append("    graphics_draw_line(ctx, p1, p2);")
        out.append("  }")
        out.append(f"  graphics_context_set_stroke_color(ctx, {hand});")
        # The hour hand creeps with the minutes. An hour hand that jumps on the hour is the
        # clearest tell that a face was generated rather than designed.
        out.append("  int32_t ha = (((s_tm.tm_hour % 12) * TRIG_MAX_ANGLE) / 12)"
                   " + ((s_tm.tm_min * TRIG_MAX_ANGLE) / (12 * 60));")
        out.append("  int32_t ma = (s_tm.tm_min * TRIG_MAX_ANGLE) / 60;")
        # Hour hand heavier than minute hand, at 14% and 10% of the radius. Those are the
        # ratios Hietala's prototypes use (7px and 5px on a 50px dial) and they match
        # conventional watch practice: weight is how the eye tells the hands apart at a
        # glance, since length alone is ambiguous when they are near each other.
        #
        # The previous expressions were `max(3, radius // 12)` and `max(1, radius // 20)`,
        # which collapse to 3 and 3 at radius 43 — emery's dial at the size these presets
        # use. Every face built for a Time 2 had two identical hands, and the floor is what
        # hid it: the hour hand was clamped up to 3 while the minute hand computed to 3.
        # Found by looking at a render, where the dial appears to have one hand.
        out.append(f"  graphics_context_set_stroke_width(ctx, "
                   f"{max(3, (radius * 14) // 100) | 1});")
        out.append(f"  draw_hand(ctx, GPoint({cx}, {cy}), {int(radius * 0.55)}, ha);")
        out.append(f"  graphics_context_set_stroke_width(ctx, "
                   f"{max(1, (radius * 10) // 100) | 1});")
        out.append(f"  draw_hand(ctx, GPoint({cx}, {cy}), {int(radius * 0.85)}, ma);")
        out.append(f"  graphics_context_set_fill_color(ctx, {hand});")
        out.append(f"  graphics_fill_circle(ctx, GPoint({cx}, {cy}),"
                   f" {max(2, radius // 18)});")
        return out

    if el.type == "polygon":
        if el.filled:
            out.append(f"  graphics_context_set_fill_color(ctx, {col});")
            out.append(f"  if (s_poly_{idx}) gpath_draw_filled(ctx, s_poly_{idx});")
        else:
            out.append(f"  graphics_context_set_stroke_color(ctx, {col});")
            out.append(f"  graphics_context_set_stroke_width(ctx, {el.stroke});")
            out.append(f"  if (s_poly_{idx}) gpath_draw_outline(ctx, s_poly_{idx});")
        return out

    if el.type == "image":
        x, y, w, h = _box(el, plat, 40)
        res = image_resource_name(idx, plat.name)
        # GCompOpSet honours the palette's transparency index. Without it a "contain" fit
        # paints its padding black and the image sits in a box.
        out.append("  graphics_context_set_compositing_mode(ctx, GCompOpSet);")
        out.append(f"  if (s_img_{idx}) {{")
        out.append(f"    graphics_draw_bitmap_in_rect(ctx, s_img_{idx},"
                   f" GRect({x}, {y}, {w}, {h}));")
        out.append("  }")
        out.append(f"  // resource {res} is resampled to exactly {w}x{h} at build time")
        return out

    out.append(f"  // unhandled element type {el.type!r} — nothing drawn")
    return out


# --------------------------------------------------------------------------------------
# main.c
# --------------------------------------------------------------------------------------


def generate_c(design: Design) -> str:
    """One main.c covering every platform in the design, branching at compile time.

    Per-platform blocks rather than runtime branching, because the resolved coordinates
    differ per platform and baking them in keeps the generated C free of layout arithmetic.
    """
    needs_steps = any(el.type == "steps" for el in design.elements)
    needs_batt = any(el.type.startswith("battery") for el in design.elements)
    needs_bt = any(el.type == "bluetooth" for el in design.elements)
    needs_time_buf = any(el.type == "time" for el in design.elements)
    needs_date_buf = any(el.type == "date" for el in design.elements)
    needs_analog = any(el.type == "analog" for el in design.elements)
    image_idx = [i for i, el in enumerate(design.elements) if el.type == "image"]
    poly_idx = [i for i, el in enumerate(design.elements) if el.type == "polygon"]
    date_fmt = next((el.format for el in design.elements
                     if el.type == "date" and el.format), "%a %d %b")

    L: list[str] = []
    L.append("// Generated by the Watch Builder rail. Do not hand-edit:")
    L.append("// the design document is the source, this file is the artefact.")
    L.append(f"// design: {design.name}")
    L.append("")
    L.append("#include <pebble.h>")
    L.append("")
    L.append("static Window *s_window;")
    L.append("static Layer  *s_canvas;")
    L.append("static struct tm s_tm;")
    if needs_batt:
        L.append("static int s_battery = 100;")
    if needs_bt:
        L.append("static bool s_connected = true;")
    if needs_time_buf:
        L.append("static char s_time_buf[12];")
    if needs_date_buf:
        L.append("static char s_date_buf[24];")
    if needs_batt:
        L.append("static char s_batt_buf[8];")
    if needs_steps:
        L.append("static char s_steps_buf[12];")
    for i in image_idx:
        L.append(f"static GBitmap *s_img_{i};")
    for i in poly_idx:
        n = len(design.elements[i].points or [])
        L.append(f"static GPoint s_poly_{i}_pts[{n}];")
        L.append(f"static GPathInfo s_poly_{i}_info = "
                 f"{{ .num_points = {n}, .points = s_poly_{i}_pts }};")
        L.append(f"static GPath *s_poly_{i};")
    L.append("")

    if image_idx:
        L.append("// Each image is shipped per platform, resampled to the exact box it is")
        L.append("// drawn into, because graphics_draw_bitmap_in_rect clips a rect smaller")
        L.append("// than the bitmap and TILES a larger one. So the resource NAME is")
        L.append("// platform-specific and the load is guarded.")
        L.append("static void load_images(void) {")
        for n, name in enumerate(design.platforms):
            kw = "#if" if n == 0 else "#elif"
            L.append(f"{kw} defined(PBL_PLATFORM_{name.upper()})")
            for i in image_idx:
                res = image_resource_name(i, name)
                L.append(f"  s_img_{i} = gbitmap_create_with_resource(RESOURCE_ID_{res});")
        L.append("#endif")
        L.append("}")
        L.append("")
        L.append("static void unload_images(void) {")
        for i in image_idx:
            L.append(f"  if (s_img_{i}) {{ gbitmap_destroy(s_img_{i}); s_img_{i} = NULL; }}")
        L.append("}")
        L.append("")

    if poly_idx:
        L.append("// GPath points are RELATIVE to the anchor set by gpath_move_to, and the")
        L.append("// anchor defaults to (0,0), so these are screen-absolute. Filled in per")
        L.append("// platform because a proportional point resolves differently on each.")
        L.append("//")
        L.append("// Created ONCE here rather than in the update proc: an update proc can run")
        L.append("// many times a second and allocating a path each time fragments a heap of")
        L.append("// 24 to 128 KB.")
        L.append("static void load_paths(void) {")
        for n_i, name in enumerate(design.platforms):
            plat = PLATFORMS[name]
            kw = "#if" if n_i == 0 else "#elif"
            L.append(f"{kw} defined(PBL_PLATFORM_{name.upper()})")
            for i in poly_idx:
                for k, pt in enumerate(design.elements[i].points or []):
                    px = resolve_length(pt[0], plat.width, 0)
                    py = resolve_length(pt[1], plat.height, 0)
                    L.append(f"  s_poly_{i}_pts[{k}] = GPoint({px}, {py});")
        L.append("#endif")
        for i in poly_idx:
            L.append(f"  s_poly_{i} = gpath_create(&s_poly_{i}_info);")
        L.append("}")
        L.append("")
        L.append("static void unload_paths(void) {")
        for i in poly_idx:
            L.append(f"  if (s_poly_{i}) {{ gpath_destroy(s_poly_{i}); "
                     f"s_poly_{i} = NULL; }}")
        L.append("}")
        L.append("")

    if needs_analog:
        L.append("static void draw_hand(GContext *ctx, GPoint c, int len, int32_t angle) {")
        L.append("  // Minus on y: screen y grows downward, so subtracting cos puts angle 0")
        L.append("  // at twelve o'clock. Multiply before dividing or the result is zero.")
        L.append("  GPoint e = { c.x + (sin_lookup(angle) * len) / TRIG_MAX_RATIO,")
        L.append("               c.y - (cos_lookup(angle) * len) / TRIG_MAX_RATIO };")
        L.append("  graphics_draw_line(ctx, c, e);")
        L.append("}")
        L.append("")

    # ---- refresh -------------------------------------------------------------------
    L.append("static void refresh_state(void) {")
    L.append("  time_t now = time(NULL);")
    L.append("  s_tm = *localtime(&now);")
    if needs_time_buf:
        L.append("  strftime(s_time_buf, sizeof(s_time_buf),")
        L.append("           clock_is_24h_style() ? \"%H:%M\" : \"%I:%M\", &s_tm);")
    if needs_date_buf:
        L.append(f"  strftime(s_date_buf, sizeof(s_date_buf), {json.dumps(date_fmt)}, &s_tm);")
    if needs_batt:
        L.append("  BatteryChargeState bs = battery_state_service_peek();")
        L.append("  s_battery = bs.charge_percent;")
        L.append("  snprintf(s_batt_buf, sizeof(s_batt_buf), \"%d%%\", s_battery);")
    if needs_bt:
        L.append("  s_connected = connection_service_peek_pebble_app_connection();")
    if needs_steps:
        # Health is not on every platform, and where it is the user may have it off. An
        # unchecked read renders "no data" as a confident zero, which is a different and
        # worse statement than showing nothing.
        L.append("#if defined(PBL_HEALTH)")
        L.append("  HealthServiceAccessibilityMask acc = health_service_metric_accessible(")
        L.append("      HealthMetricStepCount, time_start_of_today(), time(NULL));")
        L.append("  if (acc & HealthServiceAccessibilityMaskAvailable) {")
        L.append("    int steps = (int)health_service_sum_today(HealthMetricStepCount);")
        L.append("    snprintf(s_steps_buf, sizeof(s_steps_buf), \"%d\", steps);")
        L.append("  } else {")
        L.append("    snprintf(s_steps_buf, sizeof(s_steps_buf), \"--\");")
        L.append("  }")
        L.append("#else")
        L.append("  snprintf(s_steps_buf, sizeof(s_steps_buf), \"--\");")
        L.append("#endif")
    L.append("}")
    L.append("")

    # ---- update proc ---------------------------------------------------------------
    L.append("static void canvas_update_proc(Layer *layer, GContext *ctx) {")
    L.append("  // Clear the FULL bounds but lay out against the UNOBSTRUCTED ones. Painting")
    L.append("  // only the unobstructed area leaves stale pixels behind the Quick View")
    L.append("  // overlay while it animates.")
    L.append("  GRect full = layer_get_bounds(layer);")
    L.append("  GRect bounds = layer_get_unobstructed_bounds(layer);")
    L.append("  (void)bounds;")
    L.append("")

    for name in design.platforms:
        plat = PLATFORMS[name]
        L.append(f"#if defined(PBL_PLATFORM_{name.upper()})")
        L.append(f"  graphics_context_set_fill_color(ctx,"
                 f" {gcolor_expr(design.background, plat.colors)});")
        L.append("  graphics_fill_rect(ctx, full, 0, GCornerNone);")
        for i, el in enumerate(design.elements):
            L.extend(_emit_element(el, plat, i))
        L.append("#endif")
        L.append("")

    # A build for a platform the design does not list still has to draw something, or the
    # face is a blank screen with no clue why. Bezel Mode is the system's answer to an
    # unlisted platform, but a LISTED build for an unhandled one is our bug, so say so.
    listed = " || ".join(f"defined(PBL_PLATFORM_{p.upper()})" for p in design.platforms)
    L.append(f"#if !({listed})")
    L.append("  graphics_context_set_fill_color(ctx, GColorBlack);")
    L.append("  graphics_fill_rect(ctx, full, 0, GCornerNone);")
    L.append("  graphics_context_set_text_color(ctx, GColorWhite);")
    L.append("  graphics_draw_text(ctx, \"no layout\","
             " fonts_get_system_font(FONT_KEY_GOTHIC_18),")
    L.append("                     full, GTextOverflowModeWordWrap,"
             " GTextAlignmentCenter, NULL);")
    L.append("#endif")
    L.append("}")
    L.append("")

    # ---- services ------------------------------------------------------------------
    L.append("static void tick_handler(struct tm *t, TimeUnits units) {")
    L.append("  refresh_state();")
    L.append("  layer_mark_dirty(s_canvas);")
    L.append("}")
    L.append("")
    if needs_batt:
        L.append("static void battery_handler(BatteryChargeState state) {")
        L.append("  refresh_state();")
        L.append("  layer_mark_dirty(s_canvas);")
        L.append("}")
        L.append("")
    if needs_bt:
        L.append("static void connection_handler(bool connected) {")
        L.append("  refresh_state();")
        L.append("  layer_mark_dirty(s_canvas);")
        L.append("}")
        L.append("")
    L.append("static void unobstructed_change(AnimationProgress p, void *ctx) {")
    L.append("  layer_mark_dirty(s_canvas);  // layout is recomputed in the update proc")
    L.append("}")
    L.append("")
    L.append("static void window_load(Window *window) {")
    if poly_idx:
        L.append("  load_paths();")
    if image_idx:
        L.append("  load_images();")
    L.append("  Layer *root = window_get_root_layer(window);")
    L.append("  s_canvas = layer_create(layer_get_bounds(root));")
    L.append("  layer_set_update_proc(s_canvas, canvas_update_proc);")
    L.append("  layer_add_child(root, s_canvas);")
    L.append("  unobstructed_area_service_subscribe(")
    L.append("      (UnobstructedAreaHandlers) { .change = unobstructed_change }, NULL);")
    L.append("}")
    L.append("")
    L.append("static void window_unload(Window *window) {")
    L.append("  layer_destroy(s_canvas);")
    if image_idx:
        L.append("  unload_images();")
    if poly_idx:
        L.append("  unload_paths();")
    L.append("}")
    L.append("")
    L.append("static void init(void) {")
    L.append("  refresh_state();")
    L.append("  s_window = window_create();")
    L.append("  window_set_background_color(s_window, GColorBlack);")
    L.append("  window_set_window_handlers(s_window, (WindowHandlers) {")
    L.append("    .load = window_load, .unload = window_unload });")
    L.append("  window_stack_push(s_window, true);")
    L.append("  // MINUTE_UNIT, never SECOND_UNIT. Core Devices name animated watchfaces as")
    L.append("  // one of the three largest battery consumers on the Time 2.")
    L.append("  tick_timer_service_subscribe(MINUTE_UNIT, tick_handler);")
    if needs_batt:
        L.append("  battery_state_service_subscribe(battery_handler);")
    if needs_bt:
        L.append("  connection_service_subscribe((ConnectionHandlers) {")
        L.append("    .pebble_app_connection_handler = connection_handler });")
    L.append("}")
    L.append("")
    L.append("static void deinit(void) {")
    L.append("  unobstructed_area_service_unsubscribe();")
    if needs_bt:
        L.append("  connection_service_unsubscribe();")
    if needs_batt:
        L.append("  battery_state_service_unsubscribe();")
    L.append("  tick_timer_service_unsubscribe();")
    L.append("  window_destroy(s_window);")
    L.append("}")
    L.append("")
    L.append("int main(void) {")
    L.append("  init();")
    L.append("  app_event_loop();")
    L.append("  deinit();")
    L.append("}")
    L.append("")
    return "\n".join(L)


# --------------------------------------------------------------------------------------
# package.json / wscript
# --------------------------------------------------------------------------------------


def generate_package_json(design: Design, *, app_uuid: str | None = None,
                          author: str = "Watch Builder") -> str:
    needs_health = any(el.type == "steps" for el in design.elements)
    capabilities: list[str] = []
    if needs_health:
        capabilities.append("health")

    # One media entry per (image element, platform). Each carries its own targetPlatforms so
    # a platform's resource pack contains only its own variant: seven variants of a
    # background would otherwise blow the 256 KB published resource budget on their own.
    media: list[dict] = []
    for i, el in enumerate(design.elements):
        if el.type != "image":
            continue
        for name in design.platforms:
            res = image_resource_name(i, name)
            media.append({
                "type": "bitmap",
                "name": res,
                "file": f"images/{res.lower()}.png",
                "targetPlatforms": [name],
                # Smallest is the default everywhere and is what a palettised PNG wants.
                "memoryFormat": "Smallest",
            })

    pebble: dict = {
        "displayName": design.name[:30],
        "uuid": app_uuid or str(uuidlib.uuid4()),
        "sdkVersion": "3",
        "enableMultiJS": True,
        "targetPlatforms": list(design.platforms),
        "watchapp": {"watchface": True},
        "messageKeys": [],
        "resources": {"media": media},
    }
    if capabilities:
        pebble["capabilities"] = capabilities

    doc = {
        "name": slug(design.name),
        "author": author,
        "version": "1.0.0",
        "keywords": ["pebble-app"],
        "private": True,
        "dependencies": {},
        "pebble": pebble,
    }
    return json.dumps(doc, indent=2) + "\n"


def slug(name: str) -> str:
    """A filesystem- and SDK-safe name.

    Public because /api/build names the downloaded .pbw from the DESIGN rather
    than from the build directory, which is a mkdtemp path.
    """
    out = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out or "watchface"


# The stock Pebble wscript. Byte-for-byte what `pebble new-project` emits, kept as a literal
# because it is an SDK artefact rather than something this rail has an opinion about.
WSCRIPT = '''import os.path

top = '.'
out = 'build'


def options(ctx):
    ctx.load('pebble_sdk')


def configure(ctx):
    ctx.load('pebble_sdk')


def build(ctx):
    ctx.load('pebble_sdk')
    build_worker = os.path.exists('worker_src')
    binaries = []
    cached_env = ctx.env
    for platform in ctx.env.TARGET_PLATFORMS:
        ctx.env = ctx.all_envs[platform]
        ctx.set_group(ctx.env.PLATFORM_NAME)
        app_elf = '{}/pebble-app.elf'.format(ctx.env.BUILD_DIR)
        ctx.pbl_build(source=ctx.path.ant_glob('src/c/**/*.c'),
                      target=app_elf, bin_type='app')
        if build_worker:
            worker_elf = '{}/pebble-worker.elf'.format(ctx.env.BUILD_DIR)
            binaries.append({'platform': platform, 'app_elf': app_elf,
                             'worker_elf': worker_elf})
            ctx.pbl_build(source=ctx.path.ant_glob('worker_src/c/**/*.c'),
                          target=worker_elf, bin_type='worker')
        else:
            binaries.append({'platform': platform, 'app_elf': app_elf})
    ctx.env = cached_env
    ctx.set_group('bundle')
    ctx.pbl_bundle(binaries=binaries,
                   js=ctx.path.ant_glob(['src/pkjs/**/*.js',
                                         'src/pkjs/**/*.json']),
                   js_entry_file='src/pkjs/index.js')
'''


def generate_project(design: Design, *, app_uuid: str | None = None,
                     author: str = "Watch Builder") -> dict[str, str]:
    """Every file the SDK needs, as {relative path: contents}."""
    return {
        "src/c/main.c": generate_c(design),
        "package.json": generate_package_json(design, app_uuid=app_uuid, author=author),
        "wscript": WSCRIPT,
    }
