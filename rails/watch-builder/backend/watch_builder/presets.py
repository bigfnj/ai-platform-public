"""Starting points, read off Jonas Hietala's account of designing a personal Time 2 face.

WHY THIS POST AND NOT A GALLERY OF PRETTY FACES
-----------------------------------------------
His June 2026 write-up is the one detailed public account of designing a Pebble watchface end
to end, and it is unusually useful because nothing on the face is decoration. He built it for
three stated problems: an overview of today's calendar, getting started on tasks, and alerts
that break hyper focus. Every mark on the screen reports something, and he shows the versions
he DISCARDED and says why.

It is also machine-readable. The post carries 49 inline SVG prototypes at
`viewBox="0 0 200 228"`, which is emery's exact resolution, so the geometry below is measured
off his coordinates rather than inferred from prose or eyeballed off a screenshot.

WHAT EACH MARK MEANS
--------------------
This is the part worth reading before changing a preset, because moving an element here
changes what it says:

  * **Wedges around the rim are calendar events, and the ANGLE IS TIME UNTIL, not clock
    time.** This is his central idea and it is not Sectograph's. An event drawn at three
    o'clock is three hours away; as it approaches it drifts toward twelve; when it crosses
    twelve it is happening now; once past, it vanishes. So the rim is a twelve-hour countdown
    dial, not a clock face, and twelve o'clock means "now". Hence `_event_wedge` takes HOURS,
    not degrees. He accepts losing the ability to look back at his day in exchange for
    feeling urgency build.
  * **The thick ring just outside the dial is a completion countdown**, coloured with the
    ongoing event and draining as it runs down, like a Time Timer. It answers the question
    his first version could not: an event cut off at twelve looks the same one minute before
    it starts and forty minutes in. The same ring shows his work timer, counting UP from
    empty during a work block and DOWN from full during the deliberate five-minute warm-up.
  * **The small cream triangle at the top is the "now" marker** for that rim. Without it the
    twelve-o'clock convention is invisible.
  * **Badges are states, not gauges.** Steps are bronze at 33% of goal, silver at 66%, gold
    at 100%, diamond at 150%, and no number anywhere: he tried three numeric step designs,
    disliked all of them, then realised he does not care about the count, only about not
    sitting still all day. Battery is ok / low under 15% / critical under 5%, where critical
    escalates the calm pill into a comic blast. Bluetooth-lost and Quiet Time are badges for
    the same reason: a marker that is always present is furniture.
  * **The bottom bar is the single next task**, from Getting Things Done. He picked the wide
    bar over three prettier prototypes because it costs the least vertical space, and as a
    bonus needs no angled text.
  * **The date is `31/10` with a small week number beside it**, no background on the week
    number, because he does not look at it often.
  * **During a dismissal alarm the events disappear**, a blast takes the screen, the alarm
    word names which alarm it is, and the steps he must walk to clear it are drawn as a track
    around the border. Four alarms with distinct words and colours: PAUSE, STOP, END OF DAY,
    and a plain manual one.

HIS MOCKUP PALETTE DOES NOT SURVIVE THE WATCH, MEASURED
-------------------------------------------------------
His prototype colours are off-palette, and pushing all eighteen of them through this rail's
own `snap_channel` produces four collisions. This is not a rounding detail; it changes what
the design can say:

    #0E0C0A bg  #141210 ink  #2A251D frame  -> all #000000
    #C4452E rust ring     #C07A2E bronze    -> both #FF5500
    #FFC300 event yellow  #F5C518 batt low  -> both #FFFF00
    #7EA88A sage          #6E86A8 bt slate  -> both #55AAAA

The first line is the interesting one. His dial has a thin rim and his screen a thin frame,
drawn in two near-blacks a hair off the background; on a real Pebble all three are
`GColorBlack`, so **the rim and the frame do not exist on hardware** and neither do the drop
shadows he puts behind every label. They are artefacts of prototyping in HTML, where contrast
is free. The presets omit them rather than emit invisible elements.

The other three lines are pairs that appear on the SAME face in his own prototypes: the rust
countdown ring beside a bronze step badge, a low-battery badge beside a yellow event. So the
presets pick deliberately separated palette colours (bronze `#AA5500`, battery warning
`#FFAA55`) instead of his hexes, and the distinction he was relying on survives.

WHAT THESE PRESETS CANNOT DO, STATED RATHER THAN FAKED
------------------------------------------------------
His face is driven by a companion phone app he wrote: calendar sync, Todoist, and a work
timer toggled by round-tripping a button press from a Quick Launch app, because a watchface
cannot read the buttons. This rail has no calendar, no task source, and no conditional
elements.

So a wedge here is at a fixed angle, and the countdown ring has a fixed sweep. Each preset's
`limits` says which of its elements are live (battery, bluetooth, time and date really are)
and which are frozen depictions of a state. The distinction matters: a preset that presents a
static shape as a working gauge is worse than one that omits it.
"""
from __future__ import annotations

import math
from typing import Any

# --------------------------------------------------------------------------------------
# Palette
# --------------------------------------------------------------------------------------
# Already snapped to the 4x4x4 cube, so what the document says is what the framebuffer holds
# and `snap_channel` is a no-op. The mapping from his mockup hexes is in the module docstring,
# including the four collisions that made two of these a deliberate departure from his values.

BG = "#000000"         # his three near-blacks, which the watch cannot tell apart
CREAM = "#FFFFFF"      # #F2ECD8: dial face, labels, the now marker
RUST = "#FF5500"       # #C4452E: the completion countdown ring
EV_RED = "#FF0000"     # #FF3B30: an event, and the critical battery blast
EV_BLUE = "#00AAFF"    # #1E9BE8
EV_PURPLE = "#AA55FF"  # #8A4FFF
EV_YELLOW = "#FFFF00"  # #FFC300
EV_GREEN = "#00FF55"   # #34C759
OLIVE = "#AAAA55"      # his sage, moved off #55AAAA so bluetooth keeps that slot
BT_SLATE = "#55AAAA"   # #6E86A8: bluetooth lost
MUTED = "#AAAAAA"      # the week number, and the first draft's low-contrast marks
BATT_WARN = "#FFAA55"  # departs from his #F5C518, which collides with an event yellow
BRONZE = "#AA5500"     # departs from his #C07A2E, which collides with the rust ring
SILVER = "#AAFFFF"     # #BCC4CD
GOLD = "#FFAA00"       # #F4B82E
DIAMOND = "#55FFFF"    # #7FD8EA
ALARM_FACE = "#5555AA"  # #5566B8: the blast body
ALARM_WORD = "#AAAAFF"  # #8A90D6: the plate the alarm word sits on

# The first draft has its own palette, and no colour in common with the comic style: eight
# desaturated pastels and a muted cream, with no pure hue anywhere. Snapping those eight gives
# only six distinct colours (#E8826C salmon and #D6B56E tan both land on #FFAA55; #B292C9
# lilac and #8DA6E0 periwinkle both on #AAAAFF), so the six kept here are separated by hand.
#
# Worth noticing rather than just working around. A pastel sits BETWEEN cube points, so it has
# further to move when snapped than a saturated colour does, and what it loses is separation
# from its neighbours. The design he called uniform and low-contrast is the one the palette
# punishes hardest, which is a second reason it fell flat on hardware beyond being boring.
DRAFT_CREAM = "#FFFFAA"   # #D4CEBA, his dial marks and hands
DRAFT_SALMON = "#FFAA55"  # #E8826C
DRAFT_SAGE = "#55AAAA"    # #7EA88A
DRAFT_LILAC = "#AAAAFF"   # #B292C9
DRAFT_MINT = "#55FFAA"    # #7EC8A5
DRAFT_PINK = "#FFAAAA"    # #D68FA8, his pink, moved off the tan's slot
DRAFT_RUST = "#AA5555"    # #B56D52, the one arc stroke in his first draft

# Emery only, which is the Pebble Time 2 his post is about and this rail's default target.
#
# Every preset except next-task also renders correctly on gabbro, verified by building and
# screenshotting all eight on both, and on the round screen the wedges clip into clean arc
# segments that arguably look better. It is still not the default: choosing a round layout is
# a design decision rather than a resolution setting, which is his own conclusion, and
# defaulting to two platforms compiled and booted two emulators on every build before the
# layout was settled. Tick gabbro to see it.
#
# The older 144x168 platforms are left out for a stronger reason: they need a different
# composition, not this one scaled.
TARGETS = ["emery"]

# Dial geometry, measured off his prototypes on emery (200x228, centre 100,114) and converted
# to percentages so one document holds on both screens.
#   cream face      r = 43px  -> 21.5% of the 200px min dimension
#   countdown ring  outer r = 52.5px, thickness 8  -> a 105px box
#   tick ring       r = 36px, which is what analog derives from a 43% box
#   inner wedge tip r = 70px  -> 33 in the percentage units below
FACE_R = "21.5%"
RING_W, RING_H = "52%", "46%"
ANALOG_W, ANALOG_H = "43%", "38%"
WEDGE_INNER = 33.0
# Far enough out that the straight edge between two outer points still clears the screen
# corner (at 70.7 in these units) after subdivision, so a wedge is bounded by the screen edge
# and not by its own chord. See `_event_wedge`.
WEDGE_OUTER = 85.0


def _pct(cx: float, cy: float, rx: float, ry: float, deg: float) -> list[str]:
    """A point at `deg` clockwise from twelve, as a percentage pair.

    Percentages resolve per axis, so an rx == ry circle is slightly taller than wide on emery
    (228 > 200) and truly circular on square gabbro. That is the right trade: the pattern
    fills whatever screen it is on, and the angular error on emery peaks near the diagonals
    at about 7 degrees, a quarter hour on a twelve-hour rim. Absolute pixels would be
    angularly exact on emery and wrong everywhere else.
    """
    a = math.radians(deg)
    return [f"{cx + rx * math.sin(a):.2f}%", f"{cy - ry * math.cos(a):.2f}%"]


def _event_wedge(hours_from: float, hours_to: float, color: str,
                 *, jitter: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    """One calendar event, placed by HOURS UNTIL it starts.

    `hours_from=0` is happening now and sits at twelve o'clock; `hours_to=3` is three hours
    out and sits at three o'clock. Twelve hours wraps the rim.

    The outer edge runs beyond the screen and is clipped, which is how his wedges get a
    straight border-following edge out of an arc definition. It is subdivided to at most 45
    degrees per step so the chord between two outer points cannot cut inside a screen corner.

    `jitter` offsets the two inner radii. This is the non-uniform variant: he randomises event
    geometry because his brain filters out anything static, and he means it literally ("I
    completely stopped noticing them", about phone widgets). Baked rather than generated,
    since a design document has no runtime.
    """
    a0, a1 = hours_from * 30.0, hours_to * 30.0
    steps = max(2, math.ceil(abs(a1 - a0) / 45.0) + 1)
    outer = [_pct(50, 50, WEDGE_OUTER, WEDGE_OUTER, a0 + (a1 - a0) * i / (steps - 1))
             for i in range(steps)]
    inner = [_pct(50, 50, WEDGE_INNER + jitter[1], WEDGE_INNER + jitter[1], a1),
             _pct(50, 50, WEDGE_INNER + jitter[0], WEDGE_INNER + jitter[0], a0)]
    return {"type": "polygon", "color": color, "points": outer + inner}


def _blast(cx: float, cy: float, rx: float, ry: float, color: str,
           *, spikes: int = 11, inner: float = 0.62,
           wobble: tuple[float, ...] = (1.0, 0.93, 1.07)) -> dict[str, Any]:
    """A comic starburst: his badge surround and his alarm centrepiece.

    Alternating outer and inner radii, with `wobble` scaling successive spikes so the star is
    lopsided. That asymmetry is the point of the style he settled on ("thicker lines, brighter
    colors, and more irregularities"); a perfectly regular star reads as clip art.

    Point count is 2 * spikes, kept under the schema's 32-point cap, because GPath allocates
    every point up front on a heap of 24 to 128 KB.
    """
    pts: list[list[str]] = []
    for i in range(spikes * 2):
        deg = 360.0 * i / (spikes * 2)
        scale = (1.0 if i % 2 == 0 else inner) * wobble[(i // 2) % len(wobble)]
        pts.append(_pct(cx, cy, rx * scale, ry * scale, deg))
    return {"type": "polygon", "color": color, "points": pts}


def _plate(x: str, y: str, w: str, h: str, fill: str, ink: str,
           *, text: str | None = None, date_format: str | None = None,
           font: str = "auto-small") -> list[dict[str, Any]]:
    """A rounded plate with text on it: his date label and his alarm word.

    Two elements, not three. His prototypes put a dark plate behind each label as a comic drop
    shadow; that colour quantises to the same black as the background, so on hardware the
    shadow is invisible and emitting it would cost a draw call for nothing.
    """
    out: list[dict[str, Any]] = [
        {"type": "rect", "x": x, "y": y, "w": w, "h": h,
         "color": fill, "filled": True, "radius": 4},
    ]
    if date_format:
        out.append({"type": "date", "x": x, "y": y, "w": w, "h": h, "color": ink,
                    "align": "center", "font": font, "format": date_format})
    else:
        out.append({"type": "text", "x": x, "y": y, "w": w, "h": h, "text": text or "",
                    "color": ink, "align": "center", "font": font})
    return out


def _now_marker() -> dict[str, Any]:
    """The cream triangle at twelve o'clock.

    Small and easy to dismiss, and the rim is meaningless without it: it is what says the
    wedges are measured from here, now, rather than from a clock position.
    """
    return {"type": "polygon", "color": CREAM,
            "points": [["47%", 0], ["53%", 0], ["50%", "5.7%"]]}


def _dial(*, ink: str = BG, face: str = CREAM) -> list[dict[str, Any]]:
    """The cream disc with ticks and hands.

    Two elements: a filled circle, then `analog` on top for the twelve ticks and the hands.
    `analog` draws no outer circle of its own, which is why the disc is separate, and why the
    disc can be a different colour from the marks on it.
    """
    return [
        {"type": "circle", "x": "center", "y": "center", "r": FACE_R,
         "color": face, "filled": True},
        {"type": "analog", "x": "center", "y": "center", "w": ANALOG_W, "h": ANALOG_H,
         "color": ink, "hand_color": ink},
    ]


def _countdown_ring(color: str = RUST, *, start: int = 0, end: int = 250) -> dict[str, Any]:
    """The completion countdown just outside the dial.

    A full sweep is a fresh event or a finished work block; a short one is nearly done. The
    sweep is fixed in a preset because the rail has no event or timer source, so this is a
    depiction. Included anyway, because it is the element his design leans on hardest and a
    preset without it teaches the layout wrong.
    """
    return {"type": "arc", "x": "center", "y": "center", "w": RING_W, "h": RING_H,
            "thickness": 8, "color": color, "angle_start": start, "angle_end": end}


def _date_and_week() -> list[dict[str, Any]]:
    """`31/10` on a cream plate, upper left, with the week number bare beside it.

    The week number has no plate in his final version because he does not look at it often,
    and he says he is not sold on it at all: the yellow-plated prototypes "were a lot more
    fun". Kept as he shipped it rather than as he preferred it.
    """
    return _plate("18%", "18%", "27%", "9%", CREAM, BG, date_format="%d/%m") + [
        {"type": "text", "x": "47%", "y": "18%", "w": "16%", "text": "v.44",
         "color": MUTED, "align": "center", "font": "auto-tiny"},
    ]


# Both labels sit further in, and the week number further down and right, than his prototypes
# put them. Two audit findings forced it, and both are things his HTML mockups cannot show:
#
#   * TEXT_OVERLAP. His week-number plate overlaps the corner of his date plate by about 5px.
#     It works there because both are ROTATED, so they interleave. Pebble cannot rotate text
#     (graphics_draw_text has no angle, and he says himself that picking the flat task bar
#     meant "I don't have to mess around with rendering angled text"), so axis-aligned the two
#     simply collide.
#   * OUTSIDE_CIRCLE on gabbro. At his 14% inset the date plate's upper-left corner lands
#     132px from centre on a 260px round screen, past the 126px the bezel leaves usable.
#
# Same information, same reading order, positioned for the screens this rail actually builds.
#
# A third correction came from LOOKING at the render, and no check would have produced it.
# The week number first went BELOW the date plate, which cleared it in y and then sat on the
# white dial: grey text on white, illegible, in seven of eight presets. TEXT_OVERLAP compares
# text against text, so text over a filled circle passes, and every audit was clean. It is
# beside the date now, on the same row, which is also closer to where he puts it.


def _step_badge(color: str) -> list[dict[str, Any]]:
    """A step tier badge, lower left: a rosette with a cream centre.

    Two elements rather than his five; the ribbon tails and the diamond tier's sparkles are
    below the size that survives a 200px screen. The tier is set by the colour, which is
    exactly what differs between his four.
    """
    return [
        _blast(18, 69, 9.0, 8.0, color, spikes=12, inner=0.78, wobble=(1.0,)),
        {"type": "circle", "x": "18%", "y": "69%", "r": "4%", "color": CREAM, "filled": True},
        # The dark pip in the cream centre. His is a five-pointed star; at 16px across a star
        # is mush, and without anything in the centre the rosette renders as a flower rather
        # than a medal. Found by looking at the build, not by any check.
        {"type": "circle", "x": "18%", "y": "69%", "r": "1.6%", "color": BG, "filled": True},
    ]


# --------------------------------------------------------------------------------------
# The presets
# --------------------------------------------------------------------------------------
# Ordered as his post moves: the version he rejected, the version he shipped, its busy and
# tidy variants, then the individual features he worked out one at a time.

PRESETS: list[dict[str, Any]] = [
    {
        "id": "muted-first-draft",
        "label": "Muted First Draft",
        "after": "figure 05, 'The original watchface'",
        "why": "The clean, uniform, low-contrast design he finished before the watch even "
               "arrived and was certain was perfect. Then he built it: \"it sucked. It was "
               "so boring\". It is here because knowing why a design fails on a wrist is "
               "worth more than the design that replaced it, and nothing about this one "
               "looks wrong on a screen.",
        "live": ["time", "date", "battery"],
        "limits": ["Deliberately underwhelming. Start here to feel the problem, not to ship.",
                   "No now marker, and that is correct rather than an omission. Drifting "
                   "events toward twelve came later, with the comic-style rewrite; in this "
                   "version events sat at their actual clock times, so the rim is a clock "
                   "here and a countdown in every other preset.",
                   "Wedge angles are fixed, as in every preset here.",
                   "His eight pastels do not all survive the 64-colour cube: the salmon and "
                   "the tan both land on #FFAA55, and the lilac and the periwinkle both on "
                   "#AAAAFF. This uses six separated colours instead, so the uniformity is "
                   "his and the collisions are not."],
        "elements": lambda: (
            [_event_wedge(1.0, 2.2, DRAFT_SALMON),
             _event_wedge(3.2, 4.0, DRAFT_MINT),
             _event_wedge(4.5, 5.4, DRAFT_SAGE),
             _event_wedge(8.0, 9.0, DRAFT_LILAC),
             _event_wedge(6.2, 6.8, DRAFT_PINK)]
            # Thin and dusty, from the single arc stroke in his first draft (#B56D52). The
            # first render of this preset used a thick pink ring, which dominated the face
            # and made the one design he describes as uniform and low-contrast the loudest
            # thing on the sheet.
            + [{**_countdown_ring(DRAFT_RUST, end=300), "thickness": 4}]
            + _dial(ink=DRAFT_CREAM, face=BG)
            + _date_and_week()
            + [{"type": "battery-bar", "x": "40%", "y": "88%", "w": "20%", "h": "2%",
                "color": DRAFT_CREAM}]
        ),
    },
    {
        "id": "comic-panel",
        "label": "Comic Panel",
        "after": "figure 08, 'The new comic book style'",
        "why": "The design that replaced the first draft, and the one he wears. Thicker "
               "lines, brighter colours, deliberate irregularity, panel idea borrowed from "
               "the Comic Drop watchface. Four events out to ten hours, a draining countdown "
               "on the ongoing one, plus date, week, battery and connection.",
        "live": ["time", "date", "bluetooth"],
        "limits": ["Wedge angles are fixed. On his face they are calendar events drifting "
                   "toward twelve as they approach.",
                   "The countdown ring sweep is fixed; his drains with the ongoing event.",
                   "No battery indicator, on purpose: his appears only below 15% and "
                   "escalates to a blast below 5%, and a conditional badge is not something "
                   "the schema can express. An always-on bar was in the first build of this "
                   "preset and read as debris at the bottom of the face, which is the same "
                   "argument he makes for badges over gauges. Muted First Draft has a live "
                   "battery bar if you want one to look at."],
        "elements": lambda: (
            [_event_wedge(0.0, 1.1, EV_RED, jitter=(0, 4)),
             _event_wedge(1.9, 3.6, EV_BLUE, jitter=(3, -2)),
             _event_wedge(4.6, 5.1, EV_YELLOW),
             _event_wedge(7.7, 9.9, EV_PURPLE, jitter=(-3, 5))]
            + [_countdown_ring(RUST, end=250)]
            + _dial()
            + [_now_marker()]
            + _date_and_week()
            # The one genuinely conditional element the schema has: codegen wraps it in
            # `if (!s_connected)`, so it is absent on a connected watch and appears on a
            # disconnection. That is exactly his badge argument, and it is why the verify
            # pass finds 7 of 8 design colours in this render rather than 8.
            + [{"type": "bluetooth", "x": "20%", "y": "47%", "color": BT_SLATE}]
        ),
    },
    {
        "id": "loaded-day",
        "label": "Loaded Day",
        "after": "figures 11 and 51, 'Fully loaded day' and 'When it's (very) busy'",
        "why": "The same face with the day packed: wedges most of the way round the rim, "
               "close to stained glass. Worth a preset because it is the stress test. A "
               "layout that survives this survives a real Tuesday, and it shows what the "
               "twelve-hour rim looks like when it is actually full.",
        "live": ["time", "date"],
        "limits": ["Wedge angles are fixed.",
                   "The gold badge depicts the 100%-of-goal tier. Tiers need a conditional "
                   "element, so it will not follow your step count."],
        "elements": lambda: (
            [_event_wedge(0.0, 0.9, EV_RED, jitter=(0, 3)),
             _event_wedge(1.2, 2.0, EV_BLUE),
             _event_wedge(2.4, 3.9, EV_GREEN, jitter=(4, -1)),
             _event_wedge(4.3, 5.0, EV_YELLOW),
             _event_wedge(5.5, 6.4, EV_PURPLE, jitter=(-2, 3)),
             _event_wedge(7.0, 8.1, EV_BLUE, jitter=(2, 5)),
             _event_wedge(8.6, 9.4, EV_RED),
             _event_wedge(10.2, 11.5, EV_PURPLE, jitter=(3, 0))]
            + [_countdown_ring(RUST, end=95)]
            + _dial()
            + [_now_marker()]
            + _date_and_week()
            + _step_badge(GOLD)
        ),
    },
    {
        "id": "uniform-events",
        "label": "Uniform Events",
        "after": "figures 12 and 14, 'Uniform events'",
        "why": "The tidy variant, every wedge the same shape on a constant inner radius. He "
               "rejected it for himself and was straight about the trade: it would look "
               "better on the Round 2, and \"there's no denying that a round layout on a "
               "round watch fits very well\". Build it on gabbro and see.",
        "live": ["time", "date"],
        "limits": ["Wedge angles are fixed.",
                   "This is the A side of his A/B. Compare against Comic Panel, whose wedges "
                   "carry the same information with jittered geometry."],
        "elements": lambda: (
            [_event_wedge(0.4, 1.4, EV_RED),
             _event_wedge(2.4, 3.4, EV_BLUE),
             _event_wedge(5.0, 6.0, EV_YELLOW),
             _event_wedge(8.2, 9.2, EV_PURPLE)]
            + [_countdown_ring(RUST, end=250)]
            + _dial()
            + [_now_marker()]
            + _date_and_week()
        ),
    },
    {
        "id": "quiet-dial",
        "label": "Quiet Dial",
        "after": "figures 31 and 50, 'When it's clear'",
        "why": "Nothing on the calendar: dial, date, week number, now marker. His baseline, "
               "and the honest place to start a design of your own, because it is the state "
               "the face is in most of the time.",
        "live": ["time", "date"],
        "limits": [],
        "elements": lambda: (
            _dial() + [_now_marker()] + _date_and_week()
        ),
    },
    {
        "id": "next-task",
        "label": "Next Task",
        "after": "figure 47, 'The next task displayed at the bottom'",
        "why": "One actionable thing, from Getting Things Done. He chose this wide bar over "
               "three prettier prototypes because it costs the least vertical space, and as "
               "a bonus needs no angled text. The coloured cap on the left is the priority "
               "marker.",
        "live": ["time", "date"],
        "targets": ["emery"],
        "limits": ["The task text is static. His arrives from Todoist through a companion "
                   "app, and he completes or reschedules it from a Quick Launch app, because "
                   "a watchface cannot read the physical buttons.",
                   "Rectangular screens only. A bar 88% wide at 86% down has its corners "
                   "165px from the centre of a 260px round screen, and the bezel leaves 126. "
                   "A bar narrow enough to fit the circle is too short for a task, which is "
                   "the same conclusion he reaches about round needing its own layout.",
                   "It sits under Timeline Quick View. While a pin is showing, the bottom of "
                   "a Pebble screen is covered and the bar is hidden; the audit says so on "
                   "every build. His design has the same property."],
        "elements": lambda: (
            _dial() + [_now_marker()] + _date_and_week()
            + [{"type": "rect", "x": "6%", "y": "86%", "w": "88%", "h": "9.6%",
                "color": CREAM, "filled": True, "radius": 3},
               {"type": "rect", "x": "6%", "y": "86%", "w": "7.5%", "h": "9.6%",
                "color": EV_RED, "filled": True, "radius": 3},
               {"type": "text", "x": "13.5%", "y": "86%", "w": "80.5%", "h": "9.6%",
                "text": "Write about Pebble", "color": BG, "align": "center",
                "font": "auto-tiny"}]
        ),
    },
    {
        "id": "step-badge",
        "label": "Step Badge",
        "after": "figures 35 to 38, 'Bronze', 'Silver', 'Gold', 'Diamond'",
        "why": "His answer to the step counter, after three numeric designs he disliked: "
               "\"I don't care about the exact step count\". Bronze at 33% of goal, silver "
               "at 66%, gold at 100%, diamond at 150% for over-achieving. An easy tier for "
               "lazy days and a hard one for a kick. Change the badge colour to change the "
               "tier: that is all that differs between his four.",
        "live": ["time", "date"],
        "limits": ["The tier is fixed at silver. Switching on step count needs a conditional "
                   "element, which the schema does not have.",
                   "His rosette has ribbon tails, and the diamond tier has sparkles. Left "
                   "out: below the size that survives a 200px screen."],
        "elements": lambda: (
            _dial() + [_now_marker()] + _date_and_week() + _step_badge(SILVER)
        ),
    },
    {
        "id": "break-alarm",
        "label": "Break Alarm",
        "after": "figure 24, 'Time for a break'",
        "why": "The state that gives the face its reason to exist. An alarm fires when he "
               "should stop working and, because he has always simply ignored alarms, this "
               "one is deliberately annoying to clear: walk twenty steps first, then tap the "
               "watch repeatedly, with vibration and a tune every thirty seconds until it "
               "goes. Events vanish, a blast takes the screen, the word names the alarm, and "
               "the border track is the walking he owes. His own verdict: it helps and still "
               "fails often, mostly because he forgets to start the timer, or waves his arm "
               "\"like a crazy person\" to fake the steps.",
        "live": ["time"],
        "limits": ["A depiction of the alarm state, not a working alarm. Vibration, sound, "
                   "tap detection and the dismissal flow are watchface code, not layout.",
                   "The border track is a fixed sweep; his fills as the steps are walked.",
                   "The other three alarms are this preset with a different word and blast "
                   "colour: STOP, END OF DAY, and a plain manual one."],
        "elements": lambda: (
            [_blast(50, 50, 40, 36, ALARM_FACE, spikes=13, inner=0.55,
                    wobble=(1.0, 0.9, 1.08, 0.95))]
            # The steps he still owes before the alarm can be dismissed. His track follows the
            # rounded screen border; a circle is the closest the SDK gets, since there is no
            # rounded-rect stroke primitive. First build had this at thickness 7 sweeping 140
            # degrees, which read as a white swoosh decorating the right-hand side rather
            # than as a partly-filled track. Thinner and most of the way round reads as
            # progress.
            + [{"type": "arc", "x": "center", "y": "center", "w": "94%", "h": "94%",
                "thickness": 4, "color": CREAM, "angle_start": 0, "angle_end": 290}]
            + _dial()
            + [_now_marker()]
            + _plate("28%", "16%", "44%", "11%", ALARM_WORD, BG, text="PAUSE")
        ),
    },
]


def _build(spec: dict[str, Any]) -> dict[str, Any]:
    """Turn a preset spec into its metadata plus a full design document.

    The document is built here rather than stored as a literal so the geometry stays declared
    as intent: `_event_wedge(1.9, 3.6, EV_BLUE)` says "an event starting in under two hours
    and running about ninety minutes", which a reader can check against the post. The same
    thing written as twelve percentage pairs is unreviewable, and would get maintained by
    nudging numbers until it looked right rather than by changing what it means.
    """
    return {
        "id": spec["id"],
        "label": spec["label"],
        "after": spec["after"],
        "why": spec["why"],
        "live": spec["live"],
        "limits": spec["limits"],
        "design": {
            "schema": 1,
            "name": spec["label"],
            "background": BG,
            # A preset may PIN its own targets. Only next-task does, and it currently names
            # the same single platform as the default, which is the point: a full-width
            # bottom bar has no round-screen equivalent, so it must stay emery-only even if
            # TARGETS later grows. The finding behind it is measured and lives in that
            # preset's `limits`.
            "platforms": list(spec.get("targets") or TARGETS),
            "note": f"After Hietala's {spec['after']}.",
            "elements": spec["elements"](),
        },
    }


def listing() -> list[dict[str, Any]]:
    """Every preset, metadata and document together.

    Both, so the picker can show what a preset is FOR and then open it with no second round
    trip. The `why` / `live` / `limits` fields are the point: a preset whose elements look
    like gauges but report nothing is a trap, so each one states which of its elements are
    wired to real data and which are depictions.
    """
    return [_build(s) for s in PRESETS]


def get(preset_id: str) -> dict[str, Any] | None:
    return next((_build(s) for s in PRESETS if s["id"] == preset_id), None)


def ids() -> list[str]:
    return [s["id"] for s in PRESETS]
