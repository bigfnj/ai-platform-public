"""The two AI features: describe-a-face, and the variation explorer.

BOTH ARE THE SAME OPERATION. Each asks the model for a design document against this rail's
JSON schema, and neither is ever shown a C compiler. That is the safety boundary and it is
worth being blunt about why it is drawn there rather than somewhere more capable:

  * A model writing C can produce something that does not compile, and the error surfaces
    hundreds of lines into waf output with no way for the model to have known better.
  * A model writing a design document can only fail validation, which is a short list of
    field-level messages it can act on directly, and `codegen.py` then guarantees the C
    compiles because the same generator produced every face this rail has ever built.

So the model's job is taste and composition; the deterministic half owns correctness. Do not
"improve" this by letting it emit source.

WHY THE VARIATION EXPLORER EXISTS
---------------------------------
It is the one watchface design workflow with field evidence behind it. Jonas Hietala, writing
up the design of a personal Pebble Time 2 face in June 2026, was direct about both halves:
the model "seems to lack creativity and taste (but sometimes it comes up with great ideas)",
and "I've gotten good usage from telling it to generate dozens of variations of a concept and
iterating from there."

Model as search, person as filter. A variant comes back as a validated design document, not
a picture: /api/ai/variations builds and renders nothing, and the grid (VariantGrid in
module.tsx) shows each variant's axis, name, note, colour swatches and element count. To SEE
one, open it: it replaces the design in the editor, where the canvas draws it and, where the
SDK is installed, Build & render screenshots it on the emulators.

ON GPU MANNERS
--------------
The GPU may be shared with other work, and the broker evicts rather than queues. A call the
broker answers locally occupies the card for the duration of one generation and releases it,
because the broker's gate is FIFO and held per call. A nine-variant pass is therefore nine
queue entries that interleave with someone else's question, not one long lock.
A call the broker answers from a platform owner's Claude CLI subscription (the viewer is an
owner and the designer's role is switched on there) takes no GPU at all, unless the CLI fails
and the broker falls back to the local model. Callers are expected to surface that cost before
spending it; `estimate()` exists so they can, and says which of the two it will be.

HOW LONG A CALL MAY TAKE
------------------------
Both features run as background jobs (jobs.py), never inside an HTTP request, so no request
budget applies. Every call waits the whole broker.CHAT_TIMEOUT and carries the person who
asked; what bounds a job is a check before each call (``gate``), which refuses to START a call
once the job is cancelled, past its ceiling, or watched by nobody past its lease. A call already
sent is never cut short.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from pydantic import ValidationError

from watch_builder import broker, modelstate
from watch_builder.config import settings
from watch_builder.design import (
    ALL_PLATFORMS,
    DEFAULT_PLATFORMS,
    Design,
    SCHEMA_VERSION,
    check_semantics,
)
from watch_builder.layout import audit

# --------------------------------------------------------------------------------------
# The schema, as the model sees it
# --------------------------------------------------------------------------------------
#
# Hand-written rather than generated from the pydantic models. A generated JSON Schema is
# accurate and unreadable, and a model given 400 lines of $defs spends its budget parsing
# them. This is the same contract expressed as instructions, and the pydantic models remain
# the enforcement — so a drift between the two shows up as a validation error the repair pass
# then fixes, rather than as a wrong face.

_SCHEMA_BRIEF = f"""\
You emit ONE JSON object describing a Pebble watchface. No prose, no markdown fence, no
explanation. JSON only.

{{
  "schema": {SCHEMA_VERSION},
  "name": "short name, max 60 chars",
  "background": "#rrggbb",
  "platforms": ["emery"],
  "note": "one line on what this design is going for (max 280 chars)",
  "elements": [ ... ]
}}

COORDINATES. Every x/y/w/h/r/x2/y2 is one of:
  - a percentage STRING like "40%"   <- STRONGLY PREFERRED, scales across screen sizes
  - the string "center"              <- centres the element on that axis
  - an integer (absolute pixels)     <- escape hatch, avoid
Percentages resolve against the target screen, so one document is correct on every Pebble.
Absolute pixels are correct only on the screen you had in mind, which is the single most
common way a watchface breaks when it reaches a newer watch.

ELEMENT TYPES and the fields each needs:
  time          x, y, w        digital clock. Respects the wearer's 12/24h setting.
  date          x, y, w        plus optional "format" (strftime, default "%a %d %b")
  text          x, y, w, text  static label
  battery-text  x, y, w        the percentage as text
  steps         x, y, w        today's step count ("--" where Health is unavailable)
  rect          x, y, w, h     plus "radius", "filled", "stroke"
  circle        x, y, r        x/y are the CENTRE. plus "filled", "stroke"
  line          x, y, x2, y2   plus "stroke" (odd numbers only)
  battery-bar   x, y, w, h     fills left-to-right with charge
  battery-ring  x, y, w, h     plus "thickness". An arc around the box; good on round screens
  bluetooth     x, y           a dot shown ONLY while disconnected
  analog        x, y, w, h     dial, hour and minute hands. plus "hand_color"
  polygon       points         a filled shape. "points" is a list of [x, y] pairs, 3 to 32 of
                               them, each pair using the same forms as any other coordinate.
                               plus "filled", "stroke". There is no x/y/w/h: the points ARE
                               the position. Use it for wedges, arrows, stars and badges.
  arc           x, y, w, h     plus "thickness", "angle_start", "angle_end". A thick ring
                               SEGMENT. Angles are degrees clockwise from twelve o'clock, so
                               0 to 90 draws the top-right quarter and 0 to 360 a full ring.
                               end < start wraps through twelve. Unlike battery-ring the
                               sweep is fixed by you, not by the battery level.

EVERY element also takes:
  "color": "#rrggbb"
  "align": "left" | "center" | "right"        (text only)
  "font":  "auto" | "auto-small" | "auto-tiny"  <- USE THESE, not a font name
           "auto" picks the largest appropriate face for the target screen. Pebble's system
           fonts do NOT scale with the screen, so a pinned font is 144x168-sized type on a
           260x260 watch.

NOT AVAILABLE TO YOU: there is an "image" element type in this schema and you cannot use it.
It needs the id of a file the wearer uploaded, which you have no way to know. Draw with
polygons and the shape types instead.

HARD RULES, all machine-checked after you answer:
  1. Nothing may overlap. A font's box height does NOT scale with the screen, so two text
     elements 20% apart can be clear on a tall screen and overlapping on a short one. Leave
     at least 8% of vertical space between text elements, more for a "time" element, which is
     the tallest thing on the face.
  2. Nothing may be cropped. x + w must stay inside 100%, same for y + h.
     ONE EXCEPTION: a polygon MAY have points outside 0-100%. The watch clips them, and
     running a shape off the edge is how you anchor a wedge to the screen border. A polygon
     is only rejected if it misses the screen entirely. Every other type is still checked.
  3. Include exactly one element that shows the time (time or analog) unless told otherwise.
  4. Colours must be from Pebble's 64-colour palette: each channel is one of 00, 55, AA, FF.
     "#00AAFF" is valid, "#3080C0" is not.
  5. On round screens keep content away from the rim; the bezel hides it.
  6. Two to four colours total. A watchface is read in under a second and a rainbow reads as
     noise.
  7. Contrast comes from lightness, not hue. The display is reflective and desaturates hard:
     pure #00FF00 reaches the eye as a pale mint, #FF0000 as a dusty rose. Light-on-dark
     works; red-on-green does not.
  8. At most 24 elements, and fewer is better. A glance lasts under a second.
  9. An arc needs w, h AND a thickness of at least 1, and angle_start must differ from
     angle_end. An arc with equal angles draws nothing.
"""

_SYSTEM = (
    "You design Pebble smartwatch watchfaces. You are precise, you follow the schema "
    "exactly, and you answer with a single JSON object and nothing else.\n\n" + _SCHEMA_BRIEF
)


class AIError(RuntimeError):
    """The model could not be used, or would not produce a valid document."""


class Stopped(AIError):
    """The job this call belongs to starts no more calls: it was cancelled, it reached its
    ceiling, or nobody was watching it (its lease lapsed). Raised by a ``gate`` before a call is
    sent, never while one is in flight."""


#: Called before EVERY broker call a feature makes (the first, the reformat, the repair, each
#: variant's): raises Stopped to refuse the call. jobs.py builds one per job (Run.gate); None
#: starts every call, which is what a caller with nothing to stop (a test) wants.
Gate = Callable[[], None]


# --------------------------------------------------------------------------------------
# Cost, so a caller can ask before spending
# --------------------------------------------------------------------------------------


def on_claude(requester: str | None) -> bool:
    """Would the broker answer this person's designer calls from the Claude CLI?

    The predicate the Designer chip uses (``modelstate.cli_serves`` over ``/v1/status["cli"]``)
    on the role every designer call is sent with, for the same identity those calls carry. A
    status read that fails answers False: the local figure is the one that is never a false
    claim that the GPU stays free.
    """
    if not requester:
        return False
    try:
        st = broker.status()
    except (broker.BrokerError, ValueError):
        return False
    cli = st.get("cli") if isinstance(st, dict) else None
    return modelstate.cli_serves(cli, requester, settings.designer_model)


def estimate(kind: str, count: int = 1, requester: str | None = None) -> dict:
    """What a call is about to cost, for the caller to show BEFORE it runs.

    The GPU may be shared with other work, so an AI action here is a thing
    you ask about rather than a thing that happens. The numbers are deliberately rough: a
    plausible upper bound the person can act on beats a precise figure they have to wait for.

    ``requester`` is the viewer, the identity the AI routes pass on every call of the job. When
    the broker would answer that viewer from the Claude CLI, the estimate says so and claims no
    GPU: the card is used only if a call falls back to the local model.
    """
    calls = max(1, count)
    if on_claude(requester):
        # Rough, like the local figure: a CLI answer is a round trip to Anthropic rather than a
        # generation on this card. jobs.JOB_CEILING_S is sized against this figure.
        return {
            "model_ref": settings.designer_model,
            "backend": modelstate.CLAUDE,
            "calls": count,
            "approx_seconds": 40 * calls,
            "holds_gpu": False,
            "note": (
                "Answered by Claude on your own subscription, so this uses no GPU unless a "
                "call falls back to the local model. It runs as a background job you can "
                f"cancel, {calls} call(s) one after another."
            ),
        }
    return {
        "model_ref": settings.designer_model,
        "backend": modelstate.OLLAMA,
        "calls": count,
        "approx_seconds": 12 * calls,
        "holds_gpu": True,
        "note": (
            "Each call holds the GPU for one generation and releases it, so this queues as "
            f"{calls} separate broker call(s) and interleaves with other work rather than "
            "locking the card for the whole pass. It runs as a background job you can cancel."
        ),
    }


# --------------------------------------------------------------------------------------
# Getting JSON out of a language model
# --------------------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict:
    """The first JSON object in a model's reply.

    Three layers because all three happen in practice, and a feature that fails on a
    markdown fence is a feature that fails most of the time:

      1. the whole reply is JSON (what the prompt asks for);
      2. it is wrapped in a ```json fence;
      3. there is prose around it and the object has to be found by brace matching.

    Brace matching rather than a regex for the third case: a regex cannot balance braces, and
    nested objects are the normal shape here, so a greedy pattern swallows trailing prose and
    a lazy one stops at the first inner closing brace.
    """
    text = (text or "").strip()
    if not text:
        raise AIError("empty response")

    try:
        got = json.loads(text)
        if isinstance(got, dict):
            return got
    except json.JSONDecodeError:
        pass

    m = _FENCE.search(text)
    if m:
        try:
            got = json.loads(m.group(1).strip())
            if isinstance(got, dict):
                return got
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    if start >= 0:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        got = json.loads(text[start:i + 1])
                        if isinstance(got, dict):
                            return got
                    except json.JSONDecodeError:
                        break
    raise AIError("no JSON object in the model's reply")


def _problems(doc: dict) -> tuple[Design | None, list[str]]:
    """Validate a candidate document. Returns (design, problems); one of them is empty."""
    try:
        design = Design.model_validate(doc)
    except ValidationError as exc:
        msgs = []
        for err in exc.errors()[:12]:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            msgs.append(f"{loc}: {err.get('msg')}")
        return None, msgs

    problems = check_semantics(design)
    problems.extend(f.message for f in audit(design) if f.severity == "error")
    return (design, []) if not problems else (None, problems)


def _ask(messages: list[dict], temperature: float, *, requester: str | None = None,
         gate: Gate | None = None) -> str:
    """One call, which waits the whole broker.CHAT_TIMEOUT and carries ``requester``.

    No timeout is passed, so broker.chat waits its full ceiling: the time a hung CLI call takes
    plus the local answer the broker falls back to. That is what makes it safe for every call
    to carry the name, the reformat and the repair included. ``gate`` runs first and may refuse
    the call (Stopped); once it has let the call through, nothing here cuts it short.
    """
    if gate is not None:
        gate()
    try:
        return broker.chat(messages, model=settings.designer_model,
                           max_tokens=settings.designer_max_tokens,
                           temperature=temperature, requester=requester)
    except broker.BrokerError as exc:
        raise AIError(f"the model is unavailable: {exc}") from exc


def _generate(user_prompt: str, platforms: list[str], temperature: float, *,
              requester: str | None = None, gate: Gate | None = None) -> Design:
    """One design, with a single repair round trip on failure.

    ONE repair, not a loop. A model that produced an invalid document twice given explicit
    field-level errors is not going to converge on the third attempt, and an unbounded loop
    on a shared GPU is the rudest thing this rail could do. Failing with the errors visible
    lets the person fix it in the editor, which is faster anyway.
    """
    want = ", ".join(platforms)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",
         "content": f'Target platforms: [{want}].\n\n{user_prompt}'},
    ]
    reply = _ask(messages, temperature, requester=requester, gate=gate)

    try:
        doc = extract_json(reply)
    except AIError:
        messages.append({"role": "assistant", "content": reply[:2000]})
        messages.append({"role": "user",
                         "content": "That was not a JSON object. Reply with the JSON "
                                    "document only: no prose, no markdown fence."})
        doc = extract_json(_ask(messages, temperature, requester=requester, gate=gate))

    doc.setdefault("platforms", platforms)
    design, problems = _problems(doc)
    if design is not None:
        return design

    messages.append({"role": "assistant", "content": json.dumps(doc)[:3000]})
    messages.append({
        "role": "user",
        "content": ("That document was rejected. Fix every one of these and reply with the "
                    "corrected JSON document only:\n- " + "\n- ".join(problems[:12])),
    })
    doc2 = extract_json(_ask(messages, temperature, requester=requester, gate=gate))
    doc2.setdefault("platforms", platforms)
    design, problems2 = _problems(doc2)
    if design is None:
        raise AIError("the model could not produce a valid design. Last problems: "
                      + "; ".join(problems2[:6]))
    return design


# --------------------------------------------------------------------------------------
# Feature 1: describe a face, get a face
# --------------------------------------------------------------------------------------


def design_from_prompt(prompt: str, platforms: list[str] | None = None, *,
                       requester: str | None = None, gate: Gate | None = None) -> Design:
    """``requester`` is the person who asked (the job's owner, captured from the route's
    ``ident.user``); see ``broker.chat``. ``gate`` runs before each of the up to three calls."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise AIError("say what you want the face to look like")
    if len(prompt) > 2000:
        raise AIError("that description is too long; a sentence or two works better")
    plats = _clean_platforms(platforms)
    return _generate(f"Design this watchface:\n\n{prompt}", plats, temperature=0.6,
                     requester=requester, gate=gate)


# --------------------------------------------------------------------------------------
# Feature 2: the variation explorer
# --------------------------------------------------------------------------------------

_AXES = [
    "Change only the colour scheme. Keep the layout identical.",
    "Change only the layout. Keep the colours identical.",
    "Make the primary element substantially larger and give it more room by cutting "
    "something else.",
    "Make it markedly more minimal: remove every element that is not load-bearing.",
    "Add one secondary datum that earns its place, and rebalance around it.",
    "Invert the value structure: if it was light on dark, make it dark on light.",
    "Change the type scale: widen the size gap between the primary and secondary elements.",
    "Reinterpret it as an analog face if it is digital, or digital if it is analog.",
    "Keep everything and shift the composition off-centre in a deliberate way.",
]


def variation_count(count: int | None) -> int:
    """The number of variants a pass will make, or AIError for a count no pass may have.

    Separate from variations() so the route can refuse a bad count with a 422 before it
    creates a job, and size the job's progress from the same number the pass then uses.
    """
    # `count if count is not None` rather than `count or`: 0 is falsy, so `or` would silently
    # turn an explicit request for zero variations into the default nine, which is a request
    # for nine generations nobody made.
    n = count if count is not None else settings.variation_count
    if n < 1:
        raise AIError("ask for at least one variation")
    if n > settings.variation_max:
        raise AIError(f"at most {settings.variation_max} variations per pass; "
                      f"each one is a separate model generation")
    return n


def variations(base: Design, count: int | None = None, *,
               requester: str | None = None, gate: Gate | None = None,
               on_variant: Callable[[dict], None] | None = None) -> list[dict]:
    """N variants of a design, each with the axis it varied recorded.

    Each variant is a SEPARATE call along a NAMED axis rather than one call asking for N
    designs. Two reasons. A single call asking for nine tends to produce nine near-identical
    documents, because the model is completing a list rather than exploring. And a named axis
    makes the grid legible: nine faces you can reason about beats nine faces you have to
    reverse-engineer.

    Returns dicts rather than Designs so a failed variant can be reported alongside the ones
    that worked, instead of failing the whole pass. A grid with seven good cells is useful;
    an exception is not.

    ``on_variant`` is handed each variant the moment it is finished, which is how a job shows
    partial results. When ``gate`` refuses a call (Stopped: the job was cancelled, reached its
    ceiling, or nobody was watching it), the pass stops there: that variant and every later one
    are returned as not finished, with the reason, and none of them is handed to
    ``on_variant``. The refused call may be that variant's reformat or repair (``_generate``),
    so an answer its first call already brought back is not used: a call already sent finishes,
    and what it brings is kept only when it completes the variant.
    """
    n = variation_count(count)
    base_json = base.model_dump_json(by_alias=True, exclude_none=True)
    out: list[dict] = []
    for i in range(n):
        axis = _AXES[i % len(_AXES)]
        prompt = (
            "Here is an existing design document:\n\n"
            f"{base_json}\n\n"
            f"Produce a VARIATION of it. {axis}\n"
            "Keep the same target platforms. Set 'note' to one line naming what you changed "
            "and why. It must still obey every hard rule."
        )
        try:
            # Temperature climbs across the pass. The early variants stay close to the base
            # so the grid has recognisable neighbours; the later ones range further, which is
            # where the surprises come from. A flat temperature gives either nine timid
            # variants or nine unrelated ones. The broker's CLI path drops temperature, so on
            # Claude the named axis alone is what separates the variants.
            temperature = 0.5 + 0.06 * i
            design = _generate(prompt, list(base.platforms), temperature=min(1.1, temperature),
                               requester=requester, gate=gate)
            entry = {"ok": True, "axis": axis,
                     "design": json.loads(design.model_dump_json(by_alias=True,
                                                                 exclude_none=True))}
        except Stopped as exc:
            out.extend({"ok": False, "axis": _AXES[j % len(_AXES)], "error": str(exc)}
                       for j in range(i, n))
            break
        except AIError as exc:
            entry = {"ok": False, "axis": axis, "error": str(exc)}
        out.append(entry)
        if on_variant is not None:
            on_variant(entry)
    return out


def _clean_platforms(platforms: list[str] | None) -> list[str]:
    if not platforms:
        return list(DEFAULT_PLATFORMS)
    keep = [p for p in platforms if p in ALL_PLATFORMS]
    return keep or list(DEFAULT_PLATFORMS)


def describe_capabilities() -> dict[str, Any]:
    """What the AI half can do right now, for /api/capabilities."""
    return {
        "designer_model": settings.designer_model,
        "features": ["design_from_prompt", "variations"],
        "variation_default": settings.variation_count,
        "variation_max": settings.variation_max,
    }
