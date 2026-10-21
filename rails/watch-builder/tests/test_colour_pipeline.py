"""The screenshot is ALREADY sunlight-corrected. Pinned, because it came back once.

Measured on the real SDK: a face filled with GColorRed screenshots as #E35462, and the other
three primaries match their corrected values exactly. `pebble screenshot --no-correction` is
what returns the raw framebuffer.

That was NOT how this rail was first written. It captured only the default screenshot and
then ran a correction table over it, returned the result as "sunlight", and defaulted the UI
to it. Every colour judged on that view had the correction applied twice, and the label said
it was ground truth.

These tests exist so the mistake cannot be made again by someone reading the correction table
and reasonably concluding it should be applied to a picture.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from watch_builder import sdk, sunlight
from watch_builder.api import app as api


def _code_without_docstring(func) -> str:
    """`func`'s source with its docstring cut out by the PARSER, every other line verbatim.

    The text way, `src.replace(func.__doc__, "")`, stopped working in Python 3.13, which dedents
    a docstring at compile time: __doc__ no longer equals the indented text in the source, so the
    replace removed nothing and the assertions read the prose again. It still worked on 3.11 (the
    container's Python), so the test failed only on a newer host. Sliced by the docstring node's
    line numbers rather than ast.unparse'd, because unparse re-quotes every string and the
    assertions below are about the source as written.
    """
    src = textwrap.dedent(inspect.getsource(func))
    fn = ast.parse(src).body[0]
    first = fn.body[0]
    if not (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        return src
    assert first.lineno > fn.lineno, "a docstring on the def line is not cut by lines"
    lines = src.splitlines(keepends=True)
    return "".join(lines[:first.lineno - 1] + lines[first.end_lineno:])


def _flag_only_in_its_docstring():
    """Captures with `pebble screenshot --no-correction`, says the docstring."""
    return ["screenshot", "--no-open", "--emulator"]


def test_the_docstring_stripper_cuts_the_prose_and_keeps_the_code():
    """The plant the render test exists to catch: the flag in the prose, absent from the call.
    Run on whatever Python this is, so the stripping is proven where the suite runs."""
    body = _code_without_docstring(_flag_only_in_its_docstring)
    assert "--no-correction" not in body, body
    assert 'return ["screenshot", "--no-open", "--emulator"]' in body, body
    assert "--no-correction" in inspect.getsource(_flag_only_in_its_docstring)   # it was there


def test_the_correction_table_is_intact():
    """The table itself was never the problem; only where it was applied."""
    assert len(sunlight.SUNLIGHT) == 64
    assert sunlight.correct_hex("#FF0000") == "#E35462"
    assert sunlight.correct_hex("#00FF00") == "#8EE391"
    assert sunlight.correct_hex("#0000FF") == "#0068CA"
    assert sunlight.correct_hex("#FFFF00") == "#FFEEAB"


def test_there_is_no_image_correcting_helper_to_reach_for():
    """Deleted rather than kept with a warning.

    A helper that is wrong for the one input anybody has is a trap, and the next reader
    would reach for it for exactly that input.
    """
    assert not hasattr(sunlight, "correct_image")


def test_the_module_says_where_the_table_does_not_apply():
    """A docstring is the only thing standing between the next reader and the same bug."""
    doc = sunlight.__doc__ or ""
    assert "does not apply" in doc.lower() or "DOES AND DOES NOT" in doc
    assert "no-correction" in doc


def test_render_asks_for_the_uncorrected_capture_explicitly():
    """The raw view has to come from the SDK's own flag, not from post-processing."""
    # The docstring explains --no-correction at length, so searching the whole source finds
    # the prose rather than the call. Strip it: an earlier version of this test split on the
    # first occurrence and was asserting against the docstring, and a later one stripped it
    # by text, which Python 3.13 quietly broke (see _code_without_docstring).
    body = _code_without_docstring(sdk.render)

    assert "--no-correction" in body, "the raw capture must use the SDK's own flag"
    # And the default capture must NOT pass the flag, or "on watch" would be raw.
    default_call = body.split("--no-correction")[0]
    assert '"screenshot", "--no-open", "--emulator"' in default_call


def test_render_result_keeps_the_two_captures_apart():
    r = sdk.RenderResult(ok=True)
    assert hasattr(r, "shots") and hasattr(r, "raw_shots")
    assert r.shots == {} and r.raw_shots == {}


def test_build_returns_on_watch_and_never_re_corrects():
    """The response shape is the contract the frontend labels itself from.

    `on_watch` must be the SDK's bytes verbatim. A `sunlight` key would mean the old
    double-correction is back.
    """
    src = inspect.getsource(api.build)
    assert '"on_watch"' in src
    assert "sunlight.correct" not in src
    assert '"sunlight":' not in src


def test_specified_colours_are_still_reported_through_the_table():
    """The one place the correction genuinely belongs: colours a design ASKED for.

    Deleting the image helper must not take this with it. /api/validate telling a designer
    that their #FFAA00 arrives as #F1AA86 is correct and useful.
    """
    src = inspect.getsource(api.validate)
    assert "worst_shifts" in src
    rows = sunlight.worst_shifts(["#FFAA00", "#0000FF"])
    assert rows[0]["specified"] == "#FFAA00"
    assert rows[0]["on_watch"] == "#F1AA86"
