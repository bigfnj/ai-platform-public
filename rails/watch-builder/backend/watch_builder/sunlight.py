"""Sunlight correction: what the wearer actually sees.

Pebble's colour screens are reflective e-paper, not backlit LCD. Saturated colours come out
markedly paler than their RGB value suggests: pure #00ff00 reaches the eye as about #8ee391,
#ff0000 as #e35462, #ffff00 as #ffeeab. Blues survive noticeably better than warm colours.

SUNLIGHT is the SDK's own correction table, the same one behind the Sunlight toggle on the
developer site's Color Picker. All 64 keys are palette colours, because the framebuffer can
only hold palette colours.

WHERE THIS TABLE DOES AND DOES NOT APPLY
----------------------------------------
It applies to a colour a DESIGN SPECIFIED: "you asked for #FFAA00, the wrist sees #F1AA86".

It does NOT apply to an emulator screenshot. `pebble screenshot` already applies this
correction itself, measured: a face filled with GColorRed screenshots as #E35462, and the
other three primaries match their corrected values exactly. `--no-correction` is the flag
that gives the raw framebuffer.

There used to be a `correct_image()` here that walked a screenshot's pixels through the
table, and /api/build returned its output labelled "sunlight-corrected" with the UI
defaulting to it. That was the correction applied TWICE. It is deleted rather than kept with
a warning, because a helper that is wrong for the one input anybody has is a trap, and the
next reader would reach for it for exactly that.

Two design consequences worth stating where the table lives:

  * Contrast survives where saturation does not. Reds and greens both converge toward
    mid-tones, so a red-on-green design loses most of its separation on the real display
    while light-on-dark keeps working.
  * Judge colour on the corrected image. The uncorrected one is what the emulator shows, not
    what a watch shows.
"""
from __future__ import annotations

from typing import Iterable

# Palette colour -> its appearance on the reflective display. Keys and values are lowercase
# rrggbb without the leading '#'.
SUNLIGHT: dict[str, str] = {
    "000000": "000000", "000055": "001e41", "0000aa": "004387",
    "0000ff": "0068ca", "005500": "2b4a2c", "005555": "27514f",
    "0055aa": "16638d", "0055ff": "007dce", "00aa00": "5e9860",
    "00aa55": "5c9b72", "00aaaa": "57a5a2", "00aaff": "4cb4db",
    "00ff00": "8ee391", "00ff55": "8ee69e", "00ffaa": "8aebc0",
    "00ffff": "84f5f1", "550000": "4a161b", "550055": "482748",
    "5500aa": "40488a", "5500ff": "2f6bcc", "555500": "564e36",
    "555555": "545454", "5555aa": "4f6790", "5555ff": "4180d0",
    "55aa00": "759a64", "55aa55": "759d76", "55aaaa": "71a6a4",
    "55aaff": "69b5dd", "55ff00": "9ee594", "55ff55": "9de7a0",
    "55ffaa": "9becc2", "55ffff": "95f6f2", "aa0000": "99353f",
    "aa0055": "983e5a", "aa00aa": "955694", "aa00ff": "8f74d2",
    "aa5500": "9d5b4d", "aa5555": "9d6064", "aa55aa": "9a7099",
    "aa55ff": "9587d5", "aaaa00": "afa072", "aaaa55": "aea382",
    "aaaaaa": "ababab", "aaaaff": "a7bae2", "aaff00": "c9e89d",
    "aaff55": "c9eaa7", "aaffaa": "c7f0c8", "aaffff": "c3f9f7",
    "ff0000": "e35462", "ff0055": "e25874", "ff00aa": "e16aa3",
    "ff00ff": "de83dc", "ff5500": "e66e6b", "ff5555": "e6727c",
    "ff55aa": "e37fa7", "ff55ff": "e194df", "ffaa00": "f1aa86",
    "ffaa55": "f1ad93", "ffaaaa": "efb5b8", "ffaaff": "ecc3eb",
    "ffff00": "ffeeab", "ffff55": "fff1b5", "ffffaa": "fff6d3",
    "ffffff": "ffffff",
}

_LEVELS = (0, 85, 170, 255)


def _snap(c: int) -> int:
    """Nearest palette level.

    Deliberately NEAREST, which is NOT what the firmware's GColorFromRGB does (that
    truncates, `>> 6`). Different question: truncation is right when converting a colour a
    developer SPECIFIED, nearest is right when recovering the palette colour an
    already-rendered pixel WAS. This function only ever sees rendered pixels.
    """
    return min(_LEVELS, key=lambda lv: abs(lv - c))


def correct_rgb(r: int, g: int, b: int) -> tuple[int, int, int]:
    """One pixel, corrected. Off-palette input is snapped first."""
    key = f"{r:02x}{g:02x}{b:02x}"
    hit = SUNLIGHT.get(key)
    if hit is None:
        hit = SUNLIGHT[f"{_snap(r):02x}{_snap(g):02x}{_snap(b):02x}"]
    return int(hit[0:2], 16), int(hit[2:4], 16), int(hit[4:6], 16)


def correct_hex(value: str) -> str:
    """'#rrggbb' -> '#RRGGBB' as the display renders it."""
    r, g, b = (int(value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    nr, ng, nb = correct_rgb(r, g, b)
    return f"#{nr:02X}{ng:02X}{nb:02X}"


def worst_shifts(colors: Iterable[str], limit: int = 3) -> list[dict]:
    """The colours in a design whose appearance changes most, worst first.

    Distance in plain RGB space rather than a perceptual metric, on purpose: the point is to
    name which colour to look at, and a rank order is enough for that. A perceptual metric
    would add a dependency and change no decision.
    """
    rows = []
    for c in dict.fromkeys(colors):
        shown = correct_hex(c)
        a = [int(c.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        b = [int(shown.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
        dist = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
        rows.append({"specified": c.upper(), "on_watch": shown, "shift": round(dist, 1)})
    return sorted(rows, key=lambda r: -r["shift"])[:limit]
