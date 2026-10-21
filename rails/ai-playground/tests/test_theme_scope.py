"""Every rule in this rail's stylesheet is scoped under its wrapper, `.ai-playground`.

The federation runtime never removes a remote's style.css, so a top-level rule here is a rule on
the WHOLE platform from the first visit to this rail until a reload. The audit measured
it in Chromium in both visit orders: this file's `.chip`, `.cite`, `.caret` and `.src` restyled
terminal-fun's and recipe-book's chips and the shell's own. rails/recipe-book had the same
defect; tools/rail_conformance.py's RC012 checked only that a wrapper block existed.

Keyframe names are global in exactly the same way, so they carry the rail's `ap-` prefix.
Read from the source file: no bundler, no browser.
"""
from __future__ import annotations

import re
from pathlib import Path

THEME = Path(__file__).resolve().parents[1] / "frontend" / "src" / "theme.css"
WRAPPER = ".ai-playground"


def _rules(css: str) -> tuple[list[str], list[str]]:
    """(every rule selector outside @keyframes, every @keyframes name)."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors: list[str] = []
    keyframes: list[str] = []
    stack: list[str] = []
    buf = ""
    for ch in css:
        if ch == "{":
            head = buf.strip()
            buf = ""
            if head.startswith("@keyframes"):
                keyframes.append(head.split()[1])
                stack.append("keyframes")
            elif head.startswith("@"):
                stack.append("group")
            elif stack and stack[-1] == "keyframes":
                stack.append("step")
            else:
                selectors.append(head)
                stack.append("rule")
        elif ch == "}":
            buf = ""
            if stack:
                stack.pop()
        elif ch == ";" and stack and stack[-1] == "rule":
            buf = ""
        else:
            buf += ch
    return selectors, keyframes


def _unscoped(selectors: list[str]) -> list[str]:
    bad = []
    for sel in selectors:
        for part in sel.split(","):
            p = part.strip()
            if not re.match(re.escape(WRAPPER) + r"(?![\w-])", p):
                bad.append(p)
    return bad


def test_every_rule_is_under_the_wrapper():
    selectors, _ = _rules(THEME.read_text(encoding="utf-8"))
    assert len(selectors) > 150, "the parser found almost nothing, so this proves nothing"
    assert _unscoped(selectors) == []


def test_keyframe_names_carry_the_rail_prefix():
    _, keyframes = _rules(THEME.read_text(encoding="utf-8"))
    assert keyframes, "no @keyframes found, so this proves nothing"
    assert [k for k in keyframes if not k.startswith("ap-")] == []


def test_the_check_can_fail():
    """The same parser on the defect's own shape: a top-level `.chip`, one inside @media, and a
    list where only the first selector is scoped. All three must be reported."""
    css = (".ai-playground .ok { a: 1; }\n.chip { a: 1; }\n"
           "@media (max-width: 9px) { .src { a: 1; } }\n"
           ".ai-playground .x, .cite { a: 1; }\n"
           ".ai-playgroundish { a: 1; }\n"
           "@keyframes blink { 50% { opacity: 0; } }\n")
    selectors, keyframes = _rules(css)
    assert _unscoped(selectors) == [".chip", ".src", ".cite", ".ai-playgroundish"]
    assert keyframes == ["blink"]
