"""The standalone phone surface (frontend/mobile) reads at WCAG 4.5:1 in its own palette.

It is NOT rendered inside the platform shell, so the shell's palettes never reach it and the
repo-wide tests (tools/tests/test_status_fill_contrast.py) skip it: mobile.css owns its tokens on
:root, dark only. Measured here instead, against the three surfaces it defines (--bg, --s1, --s2):

* the words colours: --ink, --muted, and --accent-text for words in the accent. The accent itself,
  #2f81f7, managed 4.42:1 on --s2;
* white words on a fill: every rule that sets `color: #fff` on its own background (the avatar
  initials, your own chat bubble, the mic). White on #2f81f7 was 3.75:1, so they sit on
  --accent-fill.

And no `color` in the mobile sources reaches the bare --accent. tools/tests/test_status_text_scan.py
enforces the same rule repo-wide; this keeps the measurement and the rule beside each other.
"""
from __future__ import annotations

import re
from pathlib import Path

MOBILE = Path(__file__).resolve().parents[1] / "frontend" / "mobile"
CSS = re.sub(r"/\*.*?\*/", "", (MOBILE / "mobile.css").read_text(encoding="utf-8"), flags=re.S)


def _rules(css: str) -> list[tuple[str, dict[str, str]]]:
    out = []
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        decls = dict((k.strip(), v.strip()) for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", m.group(2)))
        out.append((re.sub(r"\s+", " ", m.group(1)).strip(), decls))
    return out


RULES = _rules(CSS)
ROOT = next(d for sel, d in RULES if sel == ":root")


def colour(value: str, seen: tuple[str, ...] = ()) -> str:
    v = value.strip()
    m = re.fullmatch(r"var\((--[\w-]+)(?:,\s*(.+))?\)", v)
    if m:
        assert m.group(1) not in seen, f"var() cycle at {m.group(1)}"
        src = ROOT.get(m.group(1), m.group(2))
        assert src is not None, f"{m.group(1)} is not defined in mobile.css"
        return colour(src, seen + (m.group(1),))
    if v in ("#fff", "white"):
        return "#ffffff"
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", v), f"not a colour this test models: {value!r}"
    return v.lower()


def _lum(h: str) -> float:
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


SURFACES = sorted(k for k in ROOT if re.fullmatch(r"--(bg|s\d+)", k))
WORDS = ("--ink", "--muted", "--accent-text")


def test_the_words_colours_read_at_4_5_to_1_on_every_mobile_surface():
    assert SURFACES == ["--bg", "--s1", "--s2"], SURFACES
    fails = []
    for tok in WORDS:
        for s in SURFACES:
            r = contrast(colour(f"var({tok})"), colour(f"var({s})"))
            if r < 4.5:
                fails.append(f"{tok} {colour(f'var({tok})')} on {s} {colour(f'var({s})')} = {r:.2f}:1")
    assert not fails, "\n".join(fails)


def test_white_words_on_a_fill_read_at_4_5_to_1():
    sites = [(sel, d["background"]) for sel, d in RULES
             if d.get("color", "").lower() in ("#fff", "#ffffff", "white") and "background" in d]
    assert len(sites) >= 3, sites  # the avatar, your chat bubble, the mic
    fails = []
    for sel, bg in sites:
        r = contrast("#ffffff", colour(bg))
        if r < 4.5:
            fails.append(f"{sel}: white on {bg} {colour(bg)} = {r:.2f}:1")
    assert not fails, "\n".join(fails)


def test_no_mobile_colour_reads_the_bare_accent():
    bad = [sel for sel, d in RULES if re.search(r"var\(--accent\s*[,)]", d.get("color", ""))]
    for p in sorted(MOBILE.glob("*.tsx")):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""(?<![\w-])color\s*:\s*["'`]?var\(--accent\s*[,)]""", line):
                bad.append(f"{p.name}:{n}")
    assert not bad, bad
    # Witness: the accent words this rule moved are still there, reading --accent-text.
    assert sum("var(--accent-text)" in d.get("color", "") for _s, d in RULES) >= 4
