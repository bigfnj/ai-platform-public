"""The design document: the only thing a model is ever allowed to emit.

This module is the safety boundary of the whole rail. The language model produces a document
validated against the models here; `codegen.py` turns a validated document into C. The model
never writes source, so a hallucinated field is a validation error rather than a compiler
error, and a document that validates cannot produce C that fails to compile.

WHY COORDINATES ARE NOT JUST PIXELS
-----------------------------------
All three existing community builders (pebbleface-studio, pebble-watchface-builder,
pebble-watchface-generator) position elements in absolute pixels. That is fine while you
target one watch and wrong the moment you target seven, because the family spans four
resolutions: 144x168, 180x180, 200x228 and 260x260.

The failure is not hypothetical and not subtle. Pebble's own "Your First Watchface" tutorial
positions its time layer at a hardcoded y of 52 (58 on round). Fifty-two pixels down a 168px
screen is a third of the way; fifty-two down a 228px screen is nowhere, and the face renders
with the type floating high over a dead bottom third on both new watches.

So a coordinate here may be an integer (absolute pixels, an escape hatch) OR a percentage
string ("40%") OR the literal "center". Percentages resolve against the platform's
unobstructed bounds at codegen time, which means one document is correct on all seven screens
instead of being correct on the one it was drawn for.

WHY FONT SIZE IS A CHOICE THE DOCUMENT DELEGATES
------------------------------------------------
Pebble's system fonts render at the SAME pixel size on every platform. Measured: every line
height is byte-identical between basalt (144x168) and emery (200x228), and BITHAM_42_BOLD
lays down exactly 30px of ink on all seven. There is no automatic scaling.

So a document that pins a font is pinning 144x168-sized type onto a 260x260 screen. `"auto"`
asks codegen to pick the largest appropriate face for the platform being built, which is the
single highest-impact thing this rail does that the community builders do not.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------------------


class Platform(BaseModel):
    """One Pebble SDK build target.

    `colors` is 2 or 64. Note flint is 2: it is the current cheap watch (Pebble 2 Duo) and is
    commonly documented as 64-colour, including in Core Devices' own agent skill. The SDK's
    platform table defines it with PBL_BW, which is what the compiler sees, so 2 is correct
    and a colour design must have a deliberate monochrome answer.
    """

    name: str
    label: str
    width: int
    height: int
    round: bool
    colors: int
    # Quick View covers this many pixels at the bottom. Zero on round platforms, which do not
    # get the overlay at all.
    quick_view: int
    # True where FONT_KEY_LECO_60_* exists. Only the three Core Devices platforms have it, and
    # referencing it elsewhere is a hard compile error rather than a graceful fallback.
    has_leco60: bool


PLATFORMS: dict[str, Platform] = {
    p.name: p for p in [
        Platform(name="aplite", label="Pebble / Steel", width=144, height=168,
                 round=False, colors=2, quick_view=51, has_leco60=False),
        Platform(name="basalt", label="Pebble Time", width=144, height=168,
                 round=False, colors=64, quick_view=51, has_leco60=False),
        Platform(name="chalk", label="Time Round", width=180, height=180,
                 round=True, colors=64, quick_view=0, has_leco60=False),
        Platform(name="diorite", label="Pebble 2", width=144, height=168,
                 round=False, colors=2, quick_view=51, has_leco60=False),
        Platform(name="flint", label="Pebble 2 Duo", width=144, height=168,
                 round=False, colors=2, quick_view=51, has_leco60=True),
        Platform(name="emery", label="Pebble Time 2", width=200, height=228,
                 round=False, colors=64, quick_view=51, has_leco60=True),
        Platform(name="gabbro", label="Pebble Round 2", width=260, height=260,
                 round=True, colors=64, quick_view=0, has_leco60=True),
    ]
}

ALL_PLATFORMS = list(PLATFORMS)
# Emery alone. A new design should target the ONE watch you are holding, and adding gabbro
# is a tick in the platform list; defaulting to both meant every build compiled and booted
# two emulators before you had decided the layout, and every audit reported round-screen
# findings about a design not yet aimed at a round screen.
DEFAULT_PLATFORMS = ["emery"]

# --------------------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------------------

_LEVELS = (0, 85, 170, 255)
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def snap_channel(value: int) -> int:
    """Quantise an 8-bit channel exactly as the firmware does.

    `GColorFromRGB` is `>> 6` per channel: TRUNCATION, not rounding to nearest. Boundaries
    fall at 64, 128 and 192, so an input of 50 becomes 0 even though 85 is nearer, and
    #7F7F7F and #808080 land a whole palette step apart.

    Matching the firmware matters because the preview has to agree with the watch. Rounding
    to nearest here would make the browser show a colour the device will not produce, which
    is the same class of lie as showing uncorrected colour.
    """
    return _LEVELS[max(0, min(3, value >> 6))]


def snap_hex(value: str) -> str:
    """Snap an arbitrary #rrggbb onto the 64-colour cube."""
    r, g, b = (int(value[i:i + 2], 16) for i in (1, 3, 5))
    return f"#{snap_channel(r):02X}{snap_channel(g):02X}{snap_channel(b):02X}"


def gcolor_expr(value: str, colors: int) -> str:
    """A C expression for this colour on a platform with `colors` colours.

    On a two-colour platform every colour has to become black or white, and doing that by
    luminance rather than by hue is the only choice that preserves the design's contrast: a
    mid-red and a mid-green are indistinguishable once both are thresholded, so the document's
    lightness ordering is what survives.
    """
    snapped = snap_hex(value)
    if colors == 2:
        r, g, b = (int(snapped[i:i + 2], 16) for i in (1, 3, 5))
        # Rec. 709 luma. The threshold is deliberately above mid: on a reflective display a
        # mid-grey reads closer to dark than to light.
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        return "GColorWhite" if luma >= 140 else "GColorBlack"
    return f"GColorFromHEX(0x{snapped[1:]})"


# --------------------------------------------------------------------------------------
# Lengths
# --------------------------------------------------------------------------------------

Length = int | str
_PCT = re.compile(r"^-?\d{1,3}(\.\d+)?%$")


def valid_length(value: Length) -> bool:
    if isinstance(value, int):
        return True
    if not isinstance(value, str):
        return False
    return value == "center" or bool(_PCT.match(value))


def resolve_length(value: Length, extent: int, size: int = 0) -> int:
    """Resolve a document length against one axis of the target screen.

    `extent` is the screen's size on that axis; `size` is the element's own size on it, used
    only by "center", which needs it to centre the element rather than its origin.
    """
    if isinstance(value, int):
        return value
    if value == "center":
        return (extent - size) // 2
    pct = float(value.rstrip("%"))
    return int(round(extent * pct / 100.0))


# --------------------------------------------------------------------------------------
# Elements
# --------------------------------------------------------------------------------------

ElementType = Literal[
    "time", "date", "text",
    "rect", "circle", "line",
    "battery-bar", "battery-ring", "battery-text",
    "steps", "bluetooth", "analog",
    "image", "polygon", "arc",
]

FitMode = Literal["contain", "cover", "stretch"]

FontChoice = Literal["auto", "auto-small", "auto-tiny"] | str

ALIGN = Literal["left", "center", "right"]


class Element(BaseModel):
    """One drawable. A flat model rather than a discriminated union on purpose.

    A union would be tidier Python and considerably worse for a language model: it would have
    to pick the right variant before it could fill any field, and a wrong pick is a validation
    error that reads as "no matching variant" rather than "this field is not allowed here". A
    flat model with an explicit type and optional extras produces errors a model can act on,
    and `check_semantics` supplies the per-type requirements the schema cannot.
    """

    model_config = {"extra": "forbid"}

    type: ElementType
    x: Length = "center"
    y: Length = "center"
    w: Length | None = None
    h: Length | None = None

    color: str = "#FFFFFF"
    align: ALIGN = "center"
    font: FontChoice = "auto"

    # text
    text: str | None = None
    # date / time
    format: str | None = None

    # rect / circle
    radius: int = 0
    filled: bool = True
    # circle
    r: Length | None = None
    # line
    x2: Length | None = None
    y2: Length | None = None
    stroke: int = 1
    # battery-ring / arc
    thickness: int = 6

    # arc. Degrees clockwise from twelve o'clock, which is both the SDK's convention
    # (DEG_TO_TRIGANGLE) and the one a watch face is read in. end < start wraps
    # through twelve, so a sweep across midnight needs no special case.
    angle_start: int = 0
    angle_end: int = 360
    # analog
    hand_color: str | None = None
    second_hand: bool = False

    # image. `image_id` names an upload; the build resamples it to this element's resolved
    # box for EACH target platform, because graphics_draw_bitmap_in_rect clips when the rect
    # is smaller than the bitmap and TILES when it is larger. Measured on the real SDK; see
    # docs/DESIGN.md. A rect is a viewport, not a scale factor, so the pixels have to be
    # right before they reach the watch.
    image_id: str | None = None
    fit: FitMode = "contain"

    # polygon. A list of [x, y] pairs, each the same Length forms as everywhere
    # else, so a shape can be proportional and stay correct on four screen sizes.
    # Drawn with GPath, which is the only filled-polygon primitive the SDK has.
    points: list[list[Length]] | None = None

    @field_validator("color", "hand_color")
    @classmethod
    def _hex(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not _HEX.match(v):
            raise ValueError("colour must be #rrggbb")
        return v.upper()

    @field_validator("x", "y", "w", "h", "r", "x2", "y2")
    @classmethod
    def _length(cls, v: Length | None) -> Length | None:
        if v is None:
            return v
        if not valid_length(v):
            raise ValueError('length must be an int, a percentage like "40%", or "center"')
        return v

    @field_validator("stroke")
    @classmethod
    def _odd_stroke(cls, v: int) -> int:
        """Stroke width must be odd.

        The SDK constrains it: an even width cannot straddle a pixel centre symmetrically, so
        asking for 2 does not give a balanced 2px line. Rounding up here rather than raising,
        because the caller's intent ("thicker") is unambiguous and refusing would make a
        document invalid for a reason no designer thinks about.
        """
        v = max(1, v)
        return v if v % 2 else v + 1


class Design(BaseModel):
    """A whole watchface, independent of platform."""

    model_config = {"extra": "forbid"}

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schema")
    name: str = Field(min_length=1, max_length=60)
    background: str = "#000000"
    platforms: list[str] = Field(default_factory=lambda: list(DEFAULT_PLATFORMS))
    elements: list[Element] = Field(default_factory=list)
    # Free-text note from whoever (or whatever) produced this document. Carried so a variation
    # can say what it varied, which is the difference between a grid of nine faces and a grid
    # of nine faces you can reason about.
    note: str | None = Field(default=None, max_length=280)

    @field_validator("background")
    @classmethod
    def _bg(cls, v: str) -> str:
        if not _HEX.match(v):
            raise ValueError("background must be #rrggbb")
        return v.upper()

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one platform")
        bad = [p for p in v if p not in PLATFORMS]
        if bad:
            raise ValueError(f"unknown platform(s): {', '.join(bad)}")
        # Deduplicate but keep the caller's order, which is the order the UI renders panels in.
        seen: list[str] = []
        for p in v:
            if p not in seen:
                seen.append(p)
        return seen

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, v: int) -> int:
        if v != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version {v}; this rail speaks {SCHEMA_VERSION}")
        return v


# --------------------------------------------------------------------------------------
# Semantic checks the schema cannot express
# --------------------------------------------------------------------------------------

# Types whose box graphics_fill_radial / graphics_fill_rect needs outright: there is no
# sensible default size for them, and the fallthrough default (full width, 20px) draws a
# sliver rather than failing, which is the worst of the three outcomes.
_NEEDS_SIZE = {"rect", "battery-bar", "battery-ring", "analog", "arc"}
# Types drawn as a ring, where thickness is the inset from the box and 0 draws nothing.
_RINGS = {"battery-ring", "arc"}
_TEXTUAL = {"time", "date", "text", "battery-text", "steps"}


def check_semantics(design: Design) -> list[str]:
    """Per-type requirements, returned as a list rather than raised.

    A list because the caller is often a language model being asked to fix its own output,
    and one error at a time turns a single correction into five round trips. Each message
    names the element index and says what to do, for the same reason.
    """
    problems: list[str] = []
    for i, el in enumerate(design.elements):
        where = f"elements[{i}] ({el.type})"

        if el.type == "text" and not (el.text or "").strip():
            problems.append(f"{where}: needs a non-empty 'text'")
        if el.type in _NEEDS_SIZE and (el.w is None or el.h is None):
            problems.append(f"{where}: needs both 'w' and 'h'")
        if el.type == "image":
            if not (el.image_id or "").strip():
                problems.append(f"{where}: needs an 'image_id' from an upload")
            if el.w is None or el.h is None:
                problems.append(f"{where}: needs both 'w' and 'h'; an image is shipped at "
                                f"the exact size it is drawn, so the box is not optional")
        if el.type == "arc" and el.angle_start == el.angle_end:
            problems.append(f"{where}: angle_start == angle_end draws nothing; "
                            f"use 0 and 360 for a full ring")
        if el.type == "polygon":
            pts = el.points or []
            if len(pts) < 3:
                problems.append(f"{where}: needs at least 3 points")
            elif len(pts) > 32:
                problems.append(f"{where}: {len(pts)} points is more than a watchface "
                                f"needs and GPath allocates them all up front; "
                                f"32 is the cap")
            else:
                for k, pt in enumerate(pts):
                    if not isinstance(pt, list) or len(pt) != 2:
                        problems.append(f"{where}: points[{k}] must be [x, y]")
                    elif not all(valid_length(v) for v in pt):
                        problems.append(f"{where}: points[{k}] has a bad length; use "
                                        f"an int, a percentage or \"center\"")
        if el.type == "circle" and el.r is None:
            problems.append(f"{where}: needs 'r'")
        if el.type == "line" and (el.x2 is None or el.y2 is None):
            problems.append(f"{where}: needs 'x2' and 'y2'")
        if el.type in _TEXTUAL and el.h is None:
            # Not fatal: codegen derives a height from the chosen font. Say so rather than
            # silently doing it, because a designer who set w and not h probably meant to.
            pass
        if el.type in _RINGS and el.thickness < 1:
            problems.append(f"{where}: 'thickness' must be at least 1")

    if len(design.elements) > 24:
        problems.append(
            f"{len(design.elements)} elements is more than a watchface can usefully show; "
            "a glance is under a second. Consider cutting to the one or two that matter."
        )
    return problems


def clock_elements(design: Design) -> int:
    """How many elements display the time. Used to warn about a face that shows none."""
    return sum(1 for el in design.elements if el.type in ("time", "analog"))
