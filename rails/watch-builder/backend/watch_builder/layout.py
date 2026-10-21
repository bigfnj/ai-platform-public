"""Per-platform layout audit: the check none of the community builders has.

Proportional coordinates fix the porting problem and introduce a new one. Positions scale
with the screen; a system font's box height does NOT, because Pebble's system fonts render at
identical pixel sizes on every platform. So two elements placed at 36% and 55% sit 32px apart
on a 168px screen and 43px apart on a 228px one, while the text between them is 60px tall on
both. The layout that reads correctly on one screen overlaps on the other, and nothing in the
document says so.

Found while smoke-testing this rail's own code generator: a seven-platform build came out
green, every element rendered, and the time ran straight through the date on five of the seven
panels. A build that compiles is not a layout that works.

So this module resolves every element's box for a given platform and reports what is wrong
with it in pixels. Deterministic and exact. Deliberately NOT a model's opinion: cropping and
overlap are measurable, and a language model asked to judge them would be strictly worse than
arithmetic.
"""
from __future__ import annotations

import datetime as _dt
import re

from typing import Iterable, Literal

from pydantic import BaseModel

from watch_builder.codegen import pick_font
from watch_builder.design import Design, Element, PLATFORMS, Platform, resolve_length
from watch_builder.sunlight import correct_hex

Severity = Literal["error", "warning"]

# Elements that put ink in a text box, and therefore collide visibly. A rect behind text is
# usually deliberate (a plate), so shapes are excluded from overlap checks.
_TEXTUAL = {"time", "date", "text", "battery-text", "steps"}


class Box(BaseModel):
    index: int
    type: str
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


class Finding(BaseModel):
    severity: Severity
    code: str
    platform: str
    message: str
    elements: list[int] = []


def resolve_box(el: Element, plat: Platform, idx: int) -> Box:
    """The pixel box an element will occupy on this platform.

    For text, height comes from the chosen font's line box when the document does not set
    one, which is exactly the number that makes overlap predictable.
    """
    if el.type in _TEXTUAL:
        _key, box_h = pick_font(el, plat)
        default_h = box_h + 4
    elif el.type == "circle":
        r = resolve_length(el.r, min(plat.width, plat.height)) if el.r is not None else 10
        cx = resolve_length(el.x, plat.width, 0)
        cy = resolve_length(el.y, plat.height, 0)
        return Box(index=idx, type=el.type, x=cx - r, y=cy - r, w=2 * r, h=2 * r)
    elif el.type == "line":
        x1 = resolve_length(el.x, plat.width, 0)
        y1 = resolve_length(el.y, plat.height, 0)
        x2 = resolve_length(el.x2, plat.width, 0) if el.x2 is not None else x1
        y2 = resolve_length(el.y2, plat.height, 0) if el.y2 is not None else y1
        return Box(index=idx, type=el.type, x=min(x1, x2), y=min(y1, y2),
                   w=abs(x2 - x1) or 1, h=abs(y2 - y1) or 1)
    elif el.type == "polygon":
        pts = el.points or []
        if not pts:
            return Box(index=idx, type=el.type, x=0, y=0, w=1, h=1)
        xs = [resolve_length(p[0], plat.width, 0) for p in pts]
        ys = [resolve_length(p[1], plat.height, 0) for p in pts]
        return Box(index=idx, type=el.type, x=min(xs), y=min(ys),
                   w=max(1, max(xs) - min(xs)), h=max(1, max(ys) - min(ys)))
    elif el.type == "bluetooth":
        r = max(3, plat.height // 45)
        cx = resolve_length(el.x, plat.width, 0)
        cy = resolve_length(el.y, plat.height, 0)
        return Box(index=idx, type=el.type, x=cx - r, y=cy - r, w=2 * r, h=2 * r)
    else:
        default_h = 20

    w = resolve_length(el.w, plat.width) if el.w is not None else plat.width
    h = resolve_length(el.h, plat.height) if el.h is not None else default_h
    x = resolve_length(el.x, plat.width, w)
    y = resolve_length(el.y, plat.height, h)
    return Box(index=idx, type=el.type, x=x, y=y, w=w, h=h)


def _overlap(a: Box, b: Box) -> tuple[int, int]:
    """Overlapping width and height in pixels. Zero on either axis means no overlap."""
    ow = min(a.right, b.right) - max(a.x, b.x)
    oh = min(a.bottom, b.bottom) - max(a.y, b.y)
    return max(0, ow), max(0, oh)


# Typical glyph count for each dynamic text type, used to estimate ink width. A box is
# usually much wider than its text (a centred time in a full-width box is the normal case),
# so testing the BOX against the circle reports a problem that does not exist.
_GLYPHS = {"time": 5, "date": 9, "battery-text": 4, "steps": 5}

# The widest moment, so a format's expansion is both deterministic and a worst case.
# "Wednesday" is the longest weekday name and "September" the longest month name, while every
# abbreviated form is three characters whatever the date. So one Wednesday in September is
# simultaneously the widest %a, %A, %b and %B, and nothing here drifts with the clock.
_REFERENCE = _dt.datetime(2026, 9, 30, 14, 38, 5)

# Shown when a date or time element does not name a format. These are the defaults codegen
# emits, so the preview matches the build rather than matching a guess.
_DEFAULT_FORMAT = {"date": "%a %d %b", "time": "%H:%M"}

_FIXED_SAMPLE = {"battery-text": "80%", "steps": "7431"}

# Padding and no-pad flags between the percent and the directive: `%-d`, `%_m`, `%0e`. glibc
# and the watch's C strftime take them; Windows Python raises. Stripped and retried.
_STRIP_FLAGS = re.compile(r"%[-_0]+")


def sample_text(el: Element) -> str:
    """What this element will actually have on screen, as a representative string.

    The single source for two things that used to disagree: the glyph count behind
    `estimate_ink_width`, and the string the editor's canvas draws. A date with
    `format: "%d/%m"` is five glyphs wide and must be five glyphs wide in both.

    Padding flags are stripped and the expansion retried before giving up. `%-d` is the case
    that matters: glibc and the watch's C strftime accept it, Windows Python raises ValueError
    on it, and a design that builds correctly must not break the editor. Stripping the flag
    keeps the estimate conservative, since `%d` is never narrower than `%-d`.

    Only a genuinely unknown directive reaches the raw-format fallback. That path
    under-estimates, which is the wrong direction for this function, so it is last resort
    rather than the first answer: returning the format verbatim for `%-d %B %Y` gave 11
    characters where the real string is 17.
    """
    if el.type == "text":
        return (el.text or "").strip()
    if el.type in _FIXED_SAMPLE:
        return _FIXED_SAMPLE[el.type]
    if el.type in _DEFAULT_FORMAT:
        fmt = el.format or _DEFAULT_FORMAT[el.type]
        for attempt in (fmt, _STRIP_FLAGS.sub("%", fmt)):
            try:
                return _REFERENCE.strftime(attempt)
            except (ValueError, TypeError):
                continue
        return fmt
    return ""

# Mean advance width as a fraction of line-box height, for the system faces this rail emits.
# LECO and Bitham digits are wide; Gothic text is narrower. Measured against the font metrics
# in the Pebble knowledge base: LECO_60 renders "00:00" at 169px on a 60px box, so 169/5/60 =
# 0.56. Gothic 24 renders "Sat 26 Sep" at roughly 0.5 per glyph.
_ADVANCE = 0.58


def estimate_ink_width(el: Element, box_h: int, box_w: int) -> int:
    """A conservative estimate of how wide the rendered text actually is.

    Conservative means it may over-estimate, never under: an over-estimate risks a warning
    nobody needed, an under-estimate misses a real clipping. Capped at the box width because
    text cannot render wider than its box.
    """
    text = sample_text(el)
    if text:
        glyphs = max(1, len(text))
    else:
        # No text and no format to expand: fall back to the per-type count. Reached by an
        # empty `text` element, which `check_semantics` rejects anyway.
        glyphs = _GLYPHS.get(el.type, 6)
    return min(box_w, int(round(glyphs * box_h * _ADVANCE)) + 4)


# Element types whose ink is the circle or ring INSCRIBED in the box, not the box itself.
# Defined once, above its first use: this list was duplicated as a literal inside _ink_box and
# as a set below it, and two copies of one fact drift silently.
_ROUND_SHAPED = {"circle", "battery-ring", "analog", "bluetooth", "arc"}


def _ink_box(box: Box, el: Element, plat: Platform) -> Box:
    """The box actually worth testing against the circle.

    Two corrections, both of which existed as false positives in the first version of this
    module and were caught by running it against a design whose render had already been
    inspected by eye:

      * A round-shaped element (circle, ring, analog dial) is INSCRIBED in its box, so its
        box corners sit outside the circle while the shape itself is comfortably inside.
        Testing the box flagged a battery ring that is visibly fine.
      * A centre-aligned text box is usually far wider than its text. Testing the box flagged
        a correctly centred time on gabbro. Narrow to the estimated ink and honour alignment.

    A warning that fires for a benign reason is worse than no warning, because it teaches
    the reader to dismiss the whole class.
    """
    if el.type in _ROUND_SHAPED:
        # Inscribed: shrink the box to the circle it contains.
        d = min(box.w, box.h)
        cx, cy = box.x + box.w // 2, box.y + box.h // 2
        return Box(index=box.index, type=box.type,
                   x=cx - d // 2, y=cy - d // 2, w=d, h=d)

    if el.type in _TEXTUAL:
        ink = estimate_ink_width(el, box.h, box.w)
        if el.align == "center":
            x = box.x + (box.w - ink) // 2
        elif el.align == "right":
            x = box.right - ink
        else:
            x = box.x
        return Box(index=box.index, type=box.type, x=x, y=box.y, w=ink, h=box.h)

    return box



def _in_circle(box: Box, plat: Platform, margin: int, *, inscribed: bool = False) -> bool:
    """Does this element's ink sit inside the usable circle?

    Checked on a round platform only. The margin exists because the bezel hides the outermost
    pixels (2px on chalk by documented guidance) and because gabbro's glass is curved, which
    distorts anything close to the rim.

    `inscribed` says the ink is the circle the box contains rather than the box. Without it
    a battery ring drawn correctly inside the screen fails, because the CORNERS of its square
    bounding box are outside the circle while every pixel it draws is inside. That was a live
    false positive in the first version of this module.
    """
    cx, cy = (plat.width - 1) / 2.0, (plat.height - 1) / 2.0
    radius = min(plat.width, plat.height) / 2.0 - margin

    if inscribed:
        bx, by = box.x + box.w / 2.0, box.y + box.h / 2.0
        own_r = min(box.w, box.h) / 2.0
        offset = ((bx - cx) ** 2 + (by - cy) ** 2) ** 0.5
        return offset + own_r <= radius

    corners = [(box.x, box.y), (box.right, box.y), (box.x, box.bottom), (box.right, box.bottom)]
    worst = max(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in corners)
    return worst <= radius


def _box_reaches_screen(box: Box, plat: Platform) -> bool:
    """Does any part of this box fall on the screen?

    Used for polygons instead of full containment. See `_bleeds` below for why.
    """
    return (box.right > 0 and box.bottom > 0
            and box.x < plat.width and box.y < plat.height)


def _box_reaches_circle(box: Box, plat: Platform, margin: int) -> bool:
    """Does any part of this box fall inside the usable circle of a round screen?

    Nearest-point test: clamp the circle centre into the box, then measure. True when the box
    overlaps the circle at all, which is the polygon question, as against `_in_circle`'s
    "is every corner inside", which is the question for everything else.
    """
    cx, cy = (plat.width - 1) / 2.0, (plat.height - 1) / 2.0
    radius = min(plat.width, plat.height) / 2.0 - margin
    nx = min(max(cx, float(box.x)), float(box.right))
    ny = min(max(cy, float(box.y)), float(box.bottom))
    return (nx - cx) ** 2 + (ny - cy) ** 2 <= radius ** 2


# Element types whose vertices are allowed outside the screen, because bleeding off the edge
# and letting the SDK clip is how the shape is meant to be built. Only polygon: GPath is the
# one primitive here positioned by explicit points rather than by a box, and an edge-bleed
# wedge is its main reason to exist. A rect that hangs off the screen is still an error.
_BLEEDS = {"polygon"}


def audit_platform(design: Design, platform: str, *, round_margin: int = 4) -> list[Finding]:
    """Everything measurably wrong with this design on this platform."""
    plat = PLATFORMS[platform]
    findings: list[Finding] = []
    boxes = [resolve_box(el, plat, i) for i, el in enumerate(design.elements)]

    # Quick View eats the bottom of a rectangular screen. An element that only collides with
    # it is a warning, not an error: the face still works, the element is just hidden while a
    # pin is showing, and that may well be the intent for a decorative row.
    safe_bottom = plat.height - plat.quick_view

    for box, el in zip(boxes, design.elements):
        if el.type in _BLEEDS:
            # A clipped polygon is the intended construction, so the only failure is a shape
            # that never reaches the screen and therefore draws nothing anywhere.
            cropped = not _box_reaches_screen(box, plat)
            detail = "so it is off-screen entirely and draws nothing"
        else:
            cropped = (box.x < 0 or box.y < 0
                       or box.right > plat.width or box.bottom > plat.height)
            detail = "so it is cropped"
        if cropped:
            findings.append(Finding(
                severity="error", code="OUT_OF_BOUNDS", platform=platform,
                elements=[box.index],
                message=(f"elements[{box.index}] ({el.type}) resolves to "
                         f"({box.x},{box.y})-({box.right},{box.bottom}) on a "
                         f"{plat.width}x{plat.height} screen, {detail}"),
            ))
        elif plat.quick_view and box.bottom > safe_bottom and el.type in _TEXTUAL:
            findings.append(Finding(
                severity="warning", code="QUICK_VIEW", platform=platform,
                elements=[box.index],
                message=(f"elements[{box.index}] ({el.type}) ends at y={box.bottom}, below "
                         f"y={safe_bottom} where Timeline Quick View covers the screen; it "
                         "will be hidden while a pin is showing"),
            ))

        outside_circle = (
            not _box_reaches_circle(box, plat, round_margin)
            if el.type in _BLEEDS
            else not _in_circle(_ink_box(box, el, plat), plat, round_margin,
                                inscribed=el.type in _ROUND_SHAPED)
        )
        if plat.round and outside_circle:
            findings.append(Finding(
                severity="warning", code="OUTSIDE_CIRCLE", platform=platform,
                elements=[box.index],
                message=(f"elements[{box.index}] ({el.type}) has a corner outside the usable "
                         f"circle on {platform}; content near the rim is hidden by the bezel "
                         "or distorted by the curved glass"),
            ))

    # Text-on-text overlap. The finding this module exists for.
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            if design.elements[i].type not in _TEXTUAL:
                continue
            if design.elements[j].type not in _TEXTUAL:
                continue
            ow, oh = _overlap(a, b)
            if ow > 0 and oh > 2:
                findings.append(Finding(
                    severity="error", code="TEXT_OVERLAP", platform=platform,
                    elements=[a.index, b.index],
                    message=(f"elements[{a.index}] ({a.type}) and elements[{b.index}] "
                             f"({b.type}) overlap by {ow}x{oh}px on {platform}; "
                             "proportional positions do not scale a font's box height, so a "
                             "gap that works on one screen closes on another"),
                ))

    return findings


def audit(design: Design) -> list[Finding]:
    """Audit every platform the design targets, worst first."""
    out: list[Finding] = []
    for p in design.platforms:
        out.extend(audit_platform(design, p))
    order = {"error": 0, "warning": 1}
    return sorted(out, key=lambda f: (order[f.severity], f.platform, f.code))


def summarise(findings: Iterable[Finding]) -> dict:
    items = list(findings)
    return {
        "errors": sum(1 for f in items if f.severity == "error"),
        "warnings": sum(1 for f in items if f.severity == "warning"),
        "findings": [f.model_dump() for f in items],
    }

def resolved_boxes(design: Design) -> dict[str, list[dict]]:
    """Every element's pixel box, per platform, for the editor's canvas to draw from.

    The point of this endpoint is that there is exactly ONE resolver of record. A client that
    reimplemented the percentage arithmetic would drift from the build, and a preview that
    disagrees with the built face is worse than no preview: it teaches the designer a layout
    that is not the one being shipped.

    The editor uses these numbers for everything it draws, and its own arithmetic only for
    the frames of an in-flight drag, which are corrected the moment the pointer is released
    and a fresh validate returns.

    Text heights come from the CHOSEN font's line box, so a text element's box here is the
    same box the overlap check uses and the same one codegen draws into.
    """
    out: dict[str, list[dict]] = {}
    for name in design.platforms:
        plat = PLATFORMS[name]
        rows = []
        for i, el in enumerate(design.elements):
            box = resolve_box(el, plat, i)
            row = {"index": i, "type": el.type, "x": box.x, "y": box.y,
                   "w": box.w, "h": box.h}
            # What the wrist actually sees. Colour platforms only: on a 2-colour screen the
            # framebuffer holds black and white and there is nothing to desaturate, so
            # correcting there would invent a shade the watch cannot display.
            if plat.colors > 2:
                row["on_watch"] = correct_hex(el.color)
                if el.hand_color:
                    row["on_watch_hand"] = correct_hex(el.hand_color)
            if el.type in _TEXTUAL:
                key, _h = pick_font(el, plat)
                row["font"] = key
                # The ink estimate, so the canvas can draw a text box the width the glyphs
                # will actually occupy rather than the width of its container.
                row["ink_w"] = estimate_ink_width(el, box.h, box.w)
                # The string itself, because the canvas used to hardcode "Sat 26 Sep" for
                # every date and so drew ten glyphs where a `%d/%m` face shows five. The
                # format declares the answer; this is the one place that expands it.
                row["sample"] = sample_text(el)
            if el.type == "polygon":
                # Resolved vertices, in screen pixels, for the same reason as the boxes: a
                # polygon's bounding box says nothing about its shape, so a canvas given only
                # the box would have to re-resolve the points itself and would drift from the
                # build. These are the exact GPoints codegen emits.
                row["points"] = [
                    [resolve_length(p[0], plat.width, 0),
                     resolve_length(p[1], plat.height, 0)]
                    for p in (el.points or [])
                ]
            if el.type == "arc":
                # Sweep, so the canvas can draw the segment rather than a full ring. A
                # battery-ring has no equivalent because its sweep is the live battery level,
                # which the editor cannot know.
                row["angles"] = [el.angle_start, el.angle_end]
                row["thickness"] = el.thickness
            out.setdefault(name, rows)
            rows.append(row)
        out[name] = rows
    return out
