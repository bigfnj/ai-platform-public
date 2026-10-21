"""Words on a status FILL reach 4.5:1, and status GRAPHICS reach 3:1, on every palette and mode.

test_status_text_scan.py keeps status colour out of `color:`. Two things it cannot see:

1. Text ON a status fill: `.cw-urg.this-week { color: #fff; background: var(--p2) }` paints a
   literal white on a rail alias of --warning, about 1.8:1. Whatever the text colour is (a
   literal, a token), when the BACKGROUND comes from a status token the pair must reach 4.5:1
   (WCAG 1.4.3). The colour may be set by a companion rule for the same element, as in
   `.bq-mode { color: #fff }` + `.bq-mode-florist { background: var(--warning) }`; a fill
   rule's companions are rules in the same file whose last compound is one of its base classes
   (`.bq-mode` for `.bq-mode-florist`, `.cw-urg` for `.cw-urg.this-week`). A translucent tint
   (color-mix with transparent) is composited over --page and over --surface-1.
2. Status GRAPHICS: a solid status fill with no text colour (a dot), a stroke, or a border or
   marker must reach 3:1 (WCAG 1.4.11) against --page and --surface-1. Tints are not graphics:
   the words on them carry the meaning, and part 1 measures those.

"Status token" means anything a var() chain reaches among --good / --warning / --critical, the
--*-text tokens, the --chip-* tokens, --info and --cli, through any alias. Every frontend is read
(web-core keeps its own node tests for its dots and is included here too). Themes: every
palette in explicit Light and Dark, resolved from web/src/styles.css the way <html> resolves it,
with the rail wrapper's own custom properties on top (light, plus its dark blocks in Dark).
Anything that cannot be resolved to a colour fails rather than passing.
"""
from __future__ import annotations

import functools
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("status_text_scan", Path(__file__).with_name("test_status_text_scan.py"))
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)

# Inline style values the resolver cannot follow (a function's result, a chart library's payload),
# judged by reading them: (frontend, the exact expression, why it is not a status colour). An
# entry that matches nothing fails; one whose frontend is absent (the public tree) is skipped.
ALLOWED = [
    ("rails/meeting-atlas", "qVar(i)", "quadrant fill var(--ma-q1..6), the rail's sequential data scale"),
    ("rails/meeting-atlas", "qVar(b)", "quadrant fill var(--ma-q1..6), the rail's sequential data scale"),
]
# A frontend may keep its own reasoned list beside its sources, as
# <rail>/frontend/contrast-allow.json = [{"expression": ..., "why": ...}]. That is how a rail the
# public tree withholds gets one: this file ships, and publishing refuses a shipped tool that
# names a withheld path, so those entries cannot live here.
for _f in sorted((REPO / "rails").glob("*/frontend/contrast-allow.json")):
    for _e in json.loads(_f.read_text(encoding="utf-8")):
        assert _e.get("why", "").strip(), f"{_f}: an allowance without a reason"
        ALLOWED.append(("/".join(_f.relative_to(REPO).parts[:2]), _e["expression"], _e["why"]))
ALLOWED_USED: set[tuple[str, str]] = set()

# web-core's status tokens. Only its four --chip-<state> names: a rail's own --chip-bg is not one.
STATUS = re.compile(r"^--(good|warning|critical|info|cli)(-text)?$|^--chip-(missing|cold|warming|loaded)$")


# --- CSS rules ------------------------------------------------------------------------------------

def parse_rules(css: str, base_line: int = 1, text_for_lines: str | None = None):
    """[{at, selectors, decls, line}] with @media descended; comments must already be blanked."""
    rules = []

    def walk(s: str, off: int, at: str | None):
        i = 0
        while True:
            o = s.find("{", i)
            if o < 0:
                return
            depth, j = 1, o + 1
            while j < len(s) and depth:
                depth += {"{": 1, "}": -1}.get(s[j], 0)
                j += 1
            head = s[i:o].strip()
            body = s[o + 1:j - 1]
            if head.startswith("@media"):
                walk(body, off + o + 1, head)
            elif not head.startswith("@") and head:
                decls = {}
                for d in body.split(";"):
                    k = d.find(":")
                    if k > 0:
                        decls[d[:k].strip()] = d[k + 1:].strip()
                line = base_line + s.count("\n", 0, o) + (off and 0)
                rules.append({"at": at, "selectors": [x.strip() for x in re.sub(r"\s+", " ", head).split(",")],
                              "decls": decls, "line": line, "off": off + o})
            i = j
    walk(css, 0, None)
    return rules


# --- the palettes, as <html> resolves them (port of web/tests/model-chips.test.js) ------------------

WEB_RULES = parse_rules(scan.strip_css_comments((REPO / "web" / "src" / "styles.css").read_text(encoding="utf-8")))
ROOT_SEL = re.compile(r"^(:root)?((?:\[[\w-]+='[^']*'\])*)(?::where\(:not\(\[([\w-]+)='([^']*)'\]\)\))?$")


def _root_match(sel, attrs):
    m = ROOT_SEL.match(sel)
    if not m or (not m.group(1) and not m.group(2)):
        return None
    spec = 1 if m.group(1) else 0
    for k, v in re.findall(r"\[([\w-]+)='([^']*)'\]", m.group(2)):
        if attrs.get(k) != v:
            return None
        spec += 1
    if m.group(3) and attrs.get(m.group(3)) == m.group(4):
        return None
    return spec


def theme_tokens(palette, theme, prefers_dark):
    attrs = {"data-palette": palette, "data-theme": theme}
    hits = []
    for i, r in enumerate(WEB_RULES):
        spec = max((s for s in (_root_match(x, attrs) for x in r["selectors"]) if s is not None), default=None)
        if spec is None:
            continue
        if r["at"]:
            m = re.search(r"prefers-color-scheme:\s*(dark|light)", r["at"])
            if not m or (m.group(1) == "dark") != prefers_dark:
                continue
        hits.append((spec, i, r))
    tokens = {}
    for _, _, r in sorted(hits, key=lambda h: (h[0], h[1])):
        tokens.update({k: v for k, v in r["decls"].items() if k.startswith("--")})
    return tokens


PALETTES = [None] + sorted({m.group(1) for r in WEB_RULES for s in r["selectors"]
                            for m in [re.match(r"^\[data-palette='([\w-]+)'\]", s)] if m})
THEMES = [(p, mode, theme_tokens(p, mode, mode == "dark")) for p in PALETTES for mode in ("light", "dark")]


# --- frontends, their rules, their wrapper tokens ---------------------------------------------------

def frontend_units():
    """{unit: {"files": [(rel, text)], "wrapper": str|None}} for web-core, the shell, each rail."""
    units = {}
    for p in scan.frontend_files():
        rel = p.relative_to(REPO).as_posix()
        parts = rel.split("/")
        if parts[0] == "rails" and "/frontend/" in rel and "/frontend/src/" not in rel:
            # A surface outside the shell (smb's standalone phone app, frontend/mobile) owns its own
            # palette; the shell's themes do not reach it. Its rail's tests measure it.
            continue
        unit = "/".join(parts[:2]) if parts[0] == "rails" else ("web" if parts[0] == "web" else "shell")
        u = units.setdefault(unit, {"files": [], "wrapper": None})
        u["files"].append((rel, p.read_text(encoding="utf-8", errors="replace")))
        manifest = REPO / unit / "rail.json"
        if parts[0] == "rails" and manifest.exists():
            u["wrapper"] = json.loads(manifest.read_text(encoding="utf-8")).get("css_wrapper")
    return units


UNITS = frontend_units()


@functools.lru_cache(maxsize=None)
def unit_rules(unit):
    out = []
    for rel, text in UNITS[unit]["files"]:
        for off, css in scan.css_regions(REPO / rel, text):
            for r in parse_rules(css):
                r["rel"] = rel
                r["line"] = text.count("\n", 0, off + r["off"]) + 1
                out.append(r)
    return out


@functools.lru_cache(maxsize=None)
def wrapper_layers(unit):
    """(light, dark) custom properties the rail's wrapper sets on itself."""
    w = UNITS[unit]["wrapper"]
    light, dark = {}, {}
    if not w:
        return light, dark
    # The manifest's wrapper, plus any other bare class that sets custom properties: a rail that
    # backs a second tile (also_serves) scopes it under a class of its own, as edu-suite's IEP tile
    # does with .iep, and its words read that block's aliases.
    names = {w} | {r["selectors"][0][1:] for r in unit_rules(unit) if len(r["selectors"]) == 1
                   and re.fullmatch(r"\.[\w-]+", r["selectors"][0]) and any(k.startswith("--") for k in r["decls"])}
    own = re.compile(r"^(?:(?::root|html)\S*\s+)?\.(?:" + "|".join(map(re.escape, sorted(names))) + r")$")
    for r in unit_rules(unit):
        sels = [s for s in r["selectors"] if own.match(s)]
        if not sels:
            continue
        is_dark = bool(r["at"] and "prefers-color-scheme: dark" in r["at"].replace("  ", " ")) or \
            any(re.search(r"(?<!:not\()\[data-theme=[\"']dark[\"']\]", s) for s in sels)
        (dark if is_dark else light).update({k: v for k, v in r["decls"].items() if k.startswith("--")})
    return light, dark


@functools.lru_cache(maxsize=None)
def dark_twins(unit):
    """(explicit Dark tokens, System-dark tokens) the wrapper sets: the [data-theme='dark'] block
    and the prefers-color-scheme one, apart. wrapper_layers merges them, which is right for
    measuring and is exactly why a rail with only one of the two looked complete."""
    w = UNITS[unit]["wrapper"]
    explicit, system = {}, {}
    if not w:
        return explicit, system
    for r in unit_rules(unit):
        own = [s for s in r["selectors"] if re.fullmatch(rf"(?::root|html)\S*\s+\.{re.escape(w)}", s)]
        if not own:
            continue
        decls = {k: v for k, v in r["decls"].items() if k.startswith("--")}
        if r["at"] and "prefers-color-scheme: dark" in r["at"].replace("  ", " "):
            system.update(decls)
        elif any(re.search(r"(?<!:not\()\[data-theme=[\"']dark[\"']\]", s) for s in own):
            explicit.update(decls)
    return explicit, system


# --- colours ------------------------------------------------------------------------------------

def _hex(h):
    h = h.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h[:3])
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lum(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def over(top, alpha, under):
    return tuple(round(t * alpha + u * (1 - alpha)) for t, u in zip(top, under))


class Unresolvable(Exception):
    pass


def colour(value, tokens, root, seen=()):
    """(rgb, alpha) for a CSS colour value under `tokens` (wrapper on top of `root`)."""
    v = value.strip()
    v = re.sub(r"\s*!important$", "", v)
    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", v)
    if m:
        name, fb = m.group(1), m.group(2)
        if name in seen:  # `--x: var(--x, ...)` on the wrapper refers to the inherited --x
            src = root.get(name)
        else:
            src = tokens.get(name, root.get(name))
        if src is None:
            if fb is None:
                raise Unresolvable(f"{name} is defined nowhere")
            return colour(fb, tokens, root, seen)
        return colour(src, tokens if name not in seen else root, root, seen + (name,))
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
        return _hex(v), 1.0
    m = re.fullmatch(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+%?))?\s*\)", v)
    if m:
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return (int(m.group(1)), int(m.group(2)), int(m.group(3))), alpha
    named = {"white": (255, 255, 255), "black": (0, 0, 0)}
    if v in named:
        return named[v], 1.0
    if v == "transparent":
        return (0, 0, 0), 0.0
    m = re.fullmatch(r"color-mix\(\s*in srgb\s*,\s*(.+)\)", v)
    if m:
        a_part, b_part = scan._split_top(m.group(1), ",")
        am = re.fullmatch(r"(.+?)\s+([\d.]+)%", a_part.strip())
        if not am:
            raise Unresolvable(f"color-mix form not modelled: {v}")
        p = float(am.group(2)) / 100
        a_rgb, a_al = colour(am.group(1), tokens, root, seen)
        if b_part.strip() == "transparent":
            return a_rgb, a_al * p
        b_rgb, _ = colour(b_part, tokens, root, seen)
        return over(a_rgb, p, b_rgb), 1.0
    raise Unresolvable(f"not a colour this test models: {v}")


def chain_names(value, tokens, root, seen=None):
    """Every custom property name a var() chain in `value` reaches."""
    seen = set() if seen is None else seen
    for name in re.findall(r"var\(\s*(--[\w-]+)", value):
        if name in seen:
            continue
        seen.add(name)
        for src in (tokens.get(name), root.get(name)):
            if src:
                chain_names(src, tokens, root, seen)
    return seen


def is_status(value, tokens, root):
    return any(STATUS.match(n) for n in chain_names(value, tokens, root))


# --- what to measure ----------------------------------------------------------------------------------

FILL_PROPS = ("background", "background-color")
GRAPHIC_PROPS = ("fill", "stroke", "border-color", "border-left-color", "border-right-color",
                 "border-top-color", "border-bottom-color", "outline-color")
BORDER_SHORTHANDS = ("border", "border-left", "border-right", "border-top", "border-bottom", "outline")


def _border_colour(v):
    """The colour part of a border shorthand, or None when it names none."""
    parts = scan._split_top(v.strip(), " ")
    cols = [x for x in parts if x.startswith(("var(", "#", "rgb", "color-mix"))]
    return cols[-1] if cols else None


def jsx_values(expr, text, srcs):
    """scan.js_values, plus template literals whose ${...} parts it can resolve
    (`3px solid ${t.hue}` -> one string per value of t.hue)."""
    got = scan.js_values(expr, text, srcs)
    if got is not None:
        return got
    e = expr.strip()
    m = re.fullmatch(r"([A-Za-z_$][\w$]*)\[[^\]]*\]", e)
    if m:  # a const map imported from another file of the same frontend
        for _rel, src in srcs:
            d = re.search(rf"const\s+{re.escape(m.group(1))}\b[^=]*=\s*([\[{{])", src)
            if d:
                body = src[d.start(1):scan._balanced(src, d.start(1))]
                lits = re.findall(r"\"([^\"]*)\"|'([^']*)'", body)
                return [(a or b, None) for a, b in lits] or None
    if not (e.startswith("`") and e.endswith("`")):
        return None
    out = [("", None)]
    for piece in re.split(r"(\$\{[^}]*\})", e[1:-1]):
        if piece.startswith("${"):
            vals = scan.js_values(piece[2:-1], text, srcs)
            if vals is None:
                return None
            out = [(a + v, o) for a, o in out for v, _ in vals]
        else:
            out = [(a + piece, o) for a, o in out]
    return out


def _classes(compound):
    return re.findall(r"\.([\w-]+)", compound)


def companions(rule, rules):
    """Rules in the same file styling the same element by one of its base classes."""
    out = []
    for sel in rule["selectors"]:
        *anc, last = sel.split(" ")
        bases = set()
        for c in _classes(last):
            bases.add(c)
            parts = c.split("-")
            bases.update("-".join(parts[:k]) for k in range(2, len(parts)))
        for r in rules:
            if r is rule or r["rel"] != rule["rel"]:
                continue
            for s in r["selectors"]:
                *a2, l2 = s.split(" ")
                if a2 == anc and re.fullmatch(r"\.[\w-]+", l2) and l2[1:] in bases and l2 != last:
                    out.append(r)
    return out


def checks():
    """[(kind, rel, line, selector, fg_value or None, value)] for every pair and every graphic."""
    found = []
    for unit in sorted(UNITS):
        rules = unit_rules(unit)
        light, dark = wrapper_layers(unit)
        layer = {**light}
        root0 = THEMES[0][2]
        for r in rules:
            d = r["decls"]
            sel = r["selectors"][0]
            fill = next((d[p] for p in FILL_PROPS if p in d), None)
            if fill and is_status(fill, layer, root0):
                fg = d.get("color") or next((c["decls"]["color"] for c in companions(r, rules)
                                             if "color" in c["decls"]), None)
                if fg:
                    found.append(("text-on-fill", unit, r["rel"], r["line"], sel, fg, fill))
                elif "transparent" not in fill:
                    found.append(("graphic", unit, r["rel"], r["line"], sel, None, fill))
            for p in GRAPHIC_PROPS:
                if p in d and is_status(d[p], layer, root0) and "transparent" not in d[p]:
                    found.append(("graphic", unit, r["rel"], r["line"], sel, None, d[p]))
            for p in BORDER_SHORTHANDS:
                if p in d:
                    c = _border_colour(d[p])
                    if c and is_status(c, layer, root0) and "transparent" not in c:
                        found.append(("graphic", unit, r["rel"], r["line"], sel, None, c))
        # inline style objects in TSX: style={{ ... }}
        for rel, text in UNITS[unit]["files"]:
            if not rel.endswith((".tsx", ".jsx")):
                continue
            srcs = [(r2, t2) for r2, t2 in UNITS[unit]["files"]]
            for m in re.finditer(r"style=\{\{", text):
                inner = m.start() + 7  # the object literal's own `{`
                body = text[inner + 1:scan._balanced(text, inner) - 1]
                keys = {}
                for part in scan._split_top(body, ","):
                    km = re.match(r"\s*([A-Za-z]+)\s*:\s*(.+)", part, re.S)
                    if km:
                        keys[km.group(1)] = km.group(2).strip()
                line = text.count("\n", 0, m.start()) + 1
                for key in ("background", "backgroundColor", "fill", "stroke", "borderColor", "border",
                            "borderLeft", "borderRight", "borderTop", "borderBottom", "outline"):
                    if key not in keys:
                        continue
                    vals = jsx_values(keys[key], text, srcs)
                    if vals is not None and key.startswith(("border", "outline")) and key != "borderColor":
                        vals = [(_border_colour(v) or "transparent", o) for v, o in vals]
                    if vals is None:
                        if any(u == unit and x == keys[key].strip() for u, x, _ in ALLOWED):
                            ALLOWED_USED.add((unit, keys[key].strip()))
                        else:
                            found.append(("unresolvable", unit, rel, line, "style", None, keys[key]))
                        continue
                    for v, _o in vals:
                        if not is_status(v, layer, root0):
                            continue
                        if key in ("background", "backgroundColor") and "color" in keys:
                            fgs = jsx_values(keys["color"], text, srcs)
                            for fv, _ in fgs or [(None, None)]:
                                found.append(("text-on-fill", unit, rel, line, "style", fv, v))
                        elif "transparent" not in v:
                            found.append(("graphic", unit, rel, line, "style", None, v))
    return found


CHECKS = checks()


def measure(check):
    """Failures [(theme, ratio, detail)] for one check across every theme."""
    kind, unit, rel, line, sel, fg, value = check
    light, dark = wrapper_layers(unit)
    out = []
    for palette, mode, root in THEMES:
        tokens = {**light, **(dark if mode == "dark" else {})}
        page = colour("var(--page)", tokens, root)[0]
        s1 = colour("var(--surface-1)", tokens, root)[0]
        rgb, alpha = colour(value, tokens, root)
        for name, under in (("--page", page), ("--surface-1", s1)):
            shown = over(rgb, alpha, under) if alpha < 1 else rgb
            if kind == "text-on-fill":
                frgb, falpha = colour(fg, tokens, root)
                text = over(frgb, falpha, shown) if falpha < 1 else frgb
                r, need = contrast(text, shown), 4.5
            else:
                r, need = contrast(shown, under), 3.0
            if r < need:
                out.append((f"{palette or '(no palette)'} {mode} on {name}", r))
    return out


def _ids(c):
    return f"{c[2]}:{c[3]} {c[4][:40]}"


# --- words: in the palette accent, on it, and in a rail's own colours ------------------------------
# Three kinds of words the shell's own tests cannot vouch for.
#
# 1. Words in the accent read --accent-text, directly or through a rail alias of it. The token is
#    held to 4.5:1 on all four palette surfaces, bare and under --accent-soft, by
#    web/tests/model-chips; what that cannot see is a rule that puts the words on a backdrop of its
#    OWN (a rail's tint, a fixed wash, a hover fill).
# 2. Words in a rail's OWN colour: a token the rail's wrapper defines without reaching any shell
#    token (finance owns its palette, iep its status hues, servicenow its brand). Nothing else
#    measures those against the palettes they sit on.
# 3. Words ON the accent: a primary button, a selected chip, a chosen tab, whose fill reaches
#    --accent or --grad-accent. They read --on-accent (or fall back to white standalone); white
#    itself missed 4.5:1 on 104 of 132 palette/mode stops.
#
# The backdrop of a rule is the first fill found on the rule itself, another rule for the same
# element, a companion, or an ancestor its selector names (`.x .y` sits on `.x`'s fill); a
# translucent one is composited over each surface, and a gradient is measured on EVERY colour
# stop, because the words cross all of them. With none, the words are measured on the page and on --surface-1
# and --surface-2. The page is the wrapper's own background when it paints one, otherwise the
# SHELL's --page: a rail that sets --page under its wrapper and never paints it (finance) still
# sits on the palette's page.

NO_FILL = {"none", "transparent", "inherit", "initial", "unset", "0"}
SHELL_TOKENS = set().union(*(set(root) for _p, _m, root in THEMES))


def _fill_of(decls):
    v = next((decls[p] for p in FILL_PROPS if p in decls), None)
    return None if v is None or v.strip() in NO_FILL else v


def _resolve_text(value, tokens, root, seen=()):
    """A value with its var() chain followed to the literal it names, as colour() follows it."""
    v = re.sub(r"\s*!important$", "", value.strip())
    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", v)
    if not m:
        return v
    name, fb = m.group(1), m.group(2)
    src = root.get(name) if name in seen else tokens.get(name, root.get(name))
    if src is None:
        if fb is None:
            raise Unresolvable(f"{name} is defined nowhere")
        return _resolve_text(fb, tokens, root, seen)
    return _resolve_text(src, tokens if name not in seen else root, root, seen + (name,))


GRADIENT = re.compile(r"(?:repeating-)?(?:linear|radial|conic)-gradient\((.*)\)", re.S)
DIRECTION = re.compile(r"^\s*(?:to\s|[-\d.]+(?:deg|turn|rad|grad)\b|circle|ellipse|closest|farthest|at\s|from\s)")


def fill_stops(value, tokens, root):
    """[(rgb, alpha)] a fill can show behind words: its one colour, or EVERY colour stop of a
    gradient (the words cross all of them). Positions after a stop are ignored."""
    v = _resolve_text(value, tokens, root)
    m = GRADIENT.fullmatch(v)
    if not m:
        return [colour(v, tokens, root)]
    parts = [x.strip() for x in scan._split_top(m.group(1), ",")]
    if parts and DIRECTION.match(parts[0]):
        parts = parts[1:]
    stops = [colour(scan._split_top(x, " ")[0], tokens, root) for x in parts if x]
    if len(stops) < 2:
        raise Unresolvable(f"a gradient with fewer than two colour stops: {v}")
    return stops


def _compound(c):
    """(tag, classes, pseudos, attributes) of one compound selector. A class inside :not() is a
    pseudo's argument, not a class the element has (`.tab:hover:not(.active)` is no `.tab.active`)."""
    tag = re.match(r"^[a-z][\w-]*", c)
    own = re.sub(r":not\([^)]*\)", "", c)
    return (tag.group(0) if tag else None, frozenset(re.findall(r"\.([\w-]+)", own)),
            frozenset(re.findall(r"(?<!:):([\w-]+(?:\([^)]*\))?)", c)), frozenset(re.findall(r"\[[^\]]*\]", own)))


def _same_element(general, specific):
    """Does every element `specific` matches also match `general` (one compound each)?"""
    g, s = _compound(general), _compound(specific)
    return (g[0] in (None, s[0])) and g[1] <= s[1] and g[2] <= s[2] and g[3] <= s[3] and g != (None, frozenset(), frozenset(), frozenset())


def backdrop_fill(rule, rules):
    """The fill behind a rule's words: its own, another rule for the same element (same ancestors,
    a last compound that every element of this one matches: `.sn-btn.primary` for
    `a.sn-btn.primary`, `.w .t` for `.w .t.on`), a companion's, or the nearest named ancestor's."""
    own = _fill_of(rule["decls"])
    if own:
        return own
    *anc, last = rule["selectors"][0].split(" ")
    # A rule that sets its own background to nothing (a ghost button's `transparent`) has said what
    # the element shows: not the fill of the general rule it overrides, so only ancestors remain.
    cleared = any(p in rule["decls"] for p in FILL_PROPS)
    for r in ([] if cleared else rules):
        if r is rule or r["rel"] != rule["rel"] or r["at"]:
            continue
        for s in r["selectors"]:
            *a2, l2 = s.split(" ")
            if a2 == anc and _same_element(l2, last):
                f = _fill_of(r["decls"])
                if f:
                    return f
    for c in ([] if cleared else companions(rule, rules)):
        f = _fill_of(c["decls"])
        if f:
            return f
    for k in range(len(anc), 0, -1):
        prefix = " ".join(anc[:k])
        for r in rules:
            if r["rel"] == rule["rel"] and any(s == prefix for s in r["selectors"]) and not r["at"]:
                f = _fill_of(r["decls"])
                if f:
                    return f
    return None


def reads_accent_text(value, tokens, root):
    return "--accent-text" in chain_names(value, tokens, root)


@functools.lru_cache(maxsize=None)
def own_colour_tokens(unit):
    """The wrapper's custom properties that are colours and reach no shell token."""
    light, dark = wrapper_layers(unit)
    root0 = THEMES[0][2]
    out = set()
    for layer in (light, dark):
        for k, v in layer.items():
            if chain_names(v, {}, {}) & SHELL_TOKENS:
                continue
            try:
                colour(v, layer, root0)
            except Unresolvable:
                continue
            out.add(k)
    return frozenset(out)


def reads_own_colour(value, unit, tokens, root):
    return bool(chain_names(value, tokens, root) & own_colour_tokens(unit))


def reads_shell_accent(value, tokens, root):
    """Does a fill reach the shell's accent (--accent or --grad-accent), through any alias?"""
    return bool(value) and bool(chain_names(value, tokens, root) & {"--accent", "--grad-accent"})


def word_checks(kind):
    """[(kind, unit, rel, line, selector, text value, fill value or None)]"""
    found = []
    root0 = THEMES[0][2]
    for unit in sorted(UNITS):
        if kind == "own" and not unit.startswith("rails/"):
            continue
        light, _dark = wrapper_layers(unit)

        def wanted(v, fill):
            if kind == "accent":
                return reads_accent_text(v, light, root0)
            if kind == "fill":
                # words ON the accent: a primary button, a selected chip, a chosen tab
                return not reads_accent_text(v, light, root0) and reads_shell_accent(fill, light, root0)
            # a rail's own colour in the words, or under them (iep's status words read the shell's
            # per-mode tokens but sit on tints of its own)
            return not reads_accent_text(v, light, root0) and not reads_shell_accent(fill, light, root0) and (
                reads_own_colour(v, unit, light, root0) or bool(fill and reads_own_colour(fill, unit, light, root0)))
        rules = unit_rules(unit)
        for r in rules:
            c = r["decls"].get("color")
            if not c:
                continue
            fill = backdrop_fill(r, rules)
            if wanted(c, fill):
                found.append((kind, unit, r["rel"], r["line"], r["selectors"][0], c, fill))
        for rel, text in UNITS[unit]["files"]:
            if not rel.endswith((".tsx", ".jsx")):
                continue
            srcs = UNITS[unit]["files"]
            for m in re.finditer(r"style=\{\{", text):
                inner = m.start() + 7
                body = text[inner + 1:scan._balanced(text, inner) - 1]
                keys = {}
                for part in scan._split_top(body, ","):
                    km = re.match(r"\s*([A-Za-z]+)\s*:\s*(.+)", part, re.S)
                    if km:
                        keys[km.group(1)] = km.group(2).strip()
                if "color" not in keys:
                    continue
                fgs = jsx_values(keys["color"], text, srcs) or []
                fills = jsx_values(keys.get("background") or keys.get("backgroundColor") or '"none"', text, srcs)
                line = text.count("\n", 0, m.start()) + 1
                for fv, _o in fgs:
                    for bv, _ in fills or [("unresolvable", None)]:
                        fill = None if bv.strip() in NO_FILL else bv
                        if wanted(fv, fill):
                            found.append((kind, unit, rel, line, "style", fv, fill))
    return found


ACCENT_WORDS = word_checks("accent")
OWN_WORDS = word_checks("own")
ON_ACCENT_WORDS = word_checks("fill")


@functools.lru_cache(maxsize=None)
def page_of(unit):
    """The wrapper rule's own background, if it paints one."""
    w = UNITS[unit]["wrapper"]
    if not w:
        return None
    for r in unit_rules(unit):
        if r["selectors"] == [f".{w}"] and not r["at"]:
            f = _fill_of(r["decls"])
            if f and "gradient" not in f:
                return f
    return None


def measure_words(check):
    _kind, unit, rel, line, sel, fg, fill = check
    light, dark = wrapper_layers(unit)
    out = []
    for palette, mode, root in THEMES:
        tokens = {**light, **(dark if mode == "dark" else {})}
        frgb, falpha = colour(fg, tokens, root)
        wpage = page_of(unit)
        page = colour(wpage, tokens, root)[0] if wpage else colour("var(--page)", {}, root)[0]
        unders = [("the page", page), ("--surface-1", colour("var(--surface-1)", tokens, root)[0]),
                  ("--surface-2", colour("var(--surface-2)", tokens, root)[0])]
        stops = fill_stops(fill, tokens, root) if fill is not None else [None]
        for name, under in unders:
            for i, st in enumerate(stops):
                shown = under if st is None else (over(st[0], st[1], under) if st[1] < 1 else st[0])
                text = over(frgb, falpha, shown) if falpha < 1 else frgb
                r = contrast(text, shown)
                if r < 4.5:
                    at = "" if len(stops) == 1 else f" stop {i}"
                    where = f"{fill}{at} over {name}" if fill else name
                    out.append((f"{palette or '(no palette)'} {mode} on {where}", r))
    return out


def test_the_word_checks_were_found():
    # Witness: rails were reached through their own aliases, both shapes exist (bare words, and
    # words on a fill), and rails' own colours were found as well as the accent.
    units = {c[1] for c in ACCENT_WORDS}
    assert "web" in units and len([u for u in units if u.startswith("rails/")]) >= 5, units
    assert any(c[6] is not None for c in ACCENT_WORDS) and any(c[6] is None for c in ACCENT_WORDS)
    # CSS written inside a TSX template: its first rule used to carry the template's backtick into
    # its selector, so the wrapper block was never recognised and no alias in it resolved.
    assert any(c[2].endswith(".tsx") and c[4] != "style" for c in ACCENT_WORDS), "no accent words in TSX-template CSS"
    if "rails/terminal-fun" in UNITS:
        assert "--ac-text" in wrapper_layers("rails/terminal-fun")[0], "terminal-fun's .ft wrapper tokens not read"
    # The ancestor form: words that sit on the fill of an ancestor their selector names.
    assert any(c[6] == "var(--accent-soft)" and c[4] == ".tm-sw.on .tm-sw-l" for c in ACCENT_WORDS)
    # The own-FILL form: words in a shell token on a fill of the rail's own.
    if "rails/meeting-atlas" in UNITS:
        assert any(c[4] == ".ma .seg mark" and c[6] == "var(--ma-mark)" for c in OWN_WORDS), "own-fill words not found"


def _canon(v):
    """A value as a comparable thing: the colour it is, or its text without spacing (#fff equals
    #ffffff, and rgba(255,255,255,.10) equals rgba(255, 255, 255, 0.10))."""
    if v is None:
        return None
    try:
        rgb, alpha = colour(v, {}, {})
        return rgb, round(alpha, 3)
    except Unresolvable:
        return re.sub(r"\s+", "", v).lower()


def test_a_rails_dark_block_has_its_system_dark_twin():
    # The menu's Dark sets [data-theme='dark']; System on a dark OS sets nothing and the
    # prefers-color-scheme query does the work. A rail that wrote only the first kept its LIGHT
    # tokens under System on a dark OS (meeting-atlas's ramp did, on dark surfaces). Every rail
    # dark block needs the other one, with the same values.
    bad, pairs = [], 0
    for unit in sorted(UNITS):
        explicit, system = dark_twins(unit)
        if not explicit and not system:
            continue
        pairs += 1
        for k in sorted(set(explicit) | set(system)):
            if _canon(explicit.get(k)) != _canon(system.get(k)):
                bad.append(f"{unit}: {k} Dark {explicit.get(k, '(unset)')} vs System-dark {system.get(k, '(unset)')}")
    assert pairs >= 1, "witness: no rail with a dark block was found"
    assert not bad, "\n".join(bad)


def test_backdrops_are_found_on_the_rule_a_companion_or_an_ancestor():
    css = (".w .bar { background: #111111; } .w .bar .x { color: var(--y); }"
           " .w .t.sel { background: #333333; }"
           " .w .t { color: var(--y); } .w .t.on { color: var(--y); }"
           " .w .t { background: #222222; } .w .solo { color: var(--y); }"
           " .w .t.ghost { background: transparent; color: var(--y); }"
           " .w .t:hover:not(.sel) { color: var(--y); }")
    rules = parse_rules(css)
    for r in rules:
        r["rel"] = "x.css"
    got = {r["selectors"][0]: backdrop_fill(r, rules) for r in rules if "color" in r["decls"]}
    # `.t:hover:not(.sel)` is never a `.t.sel`: a class inside :not() is not one the element has
    assert got == {".w .bar .x": "#111111", ".w .t": "#222222", ".w .t.on": "#222222", ".w .solo": None,
                   ".w .t.ghost": None, ".w .t:hover:not(.sel)": "#222222"}, got


@pytest.mark.parametrize("check", ACCENT_WORDS, ids=lambda c: f"{c[2]}:{c[3]} {c[4][:40]}")
def test_words_in_the_accent_reach_4_5_to_1(check):
    fails = measure_words(check)
    assert not fails, (f"{check[2]}:{check[3]} {check[4]}: {check[5]} is below 4.5:1 on {len(fails)} "
                       f"theme/backdrop pairs, worst {min(r for _, r in fails):.2f} "
                       f"({min(fails, key=lambda f: f[1])[0]})")


@pytest.mark.parametrize("check", OWN_WORDS, ids=lambda c: f"{c[2]}:{c[3]} {c[4][:40]}")
def test_words_in_a_rails_own_colour_reach_4_5_to_1(check):
    fails = measure_words(check)
    assert not fails, (f"{check[2]}:{check[3]} {check[4]}: {check[5]} is below 4.5:1 on {len(fails)} "
                       f"theme/backdrop pairs, worst {min(r for _, r in fails):.2f} "
                       f"({min(fails, key=lambda f: f[1])[0]})")


def test_the_checks_were_found():
    # Witness: the walker reached the rails and found both kinds, including the cross-rule pair.
    kinds = {c[0] for c in CHECKS}
    assert {"text-on-fill", "graphic"} <= kinds, kinds
    assert len(THEMES) == len(PALETTES) * 2 and len(PALETTES) >= 22
    units = {c[1] for c in CHECKS}
    assert len(units) >= 5, units


# One real instance of every form the detector claims to read. If detection breaks, the form
# vanishes from CHECKS and every measurement below passes on nothing; this is what notices.
# (kind, file, selector, the measured value or text colour contains) -- skipped if the file is
# absent. Published files only: this test ships. (The companion form has its own unit cases.)
KNOWN = [
    ("text-on-fill", "rails/co-worker/frontend/src/theme.css", ".cw-urg.this-week", "var(--p2)"),       # alias fill
    ("text-on-fill", "rails/ai-playground/frontend/src/theme.css", ".ai-playground .rag-error", "transparent"),  # tint
    ("graphic", "rails/co-worker/frontend/src/theme.css", ".cw-card.p2", "--p2-mark"),                  # alias marker
    ("graphic", "rails/meeting-atlas/frontend/src/theme.css", ".ma .now-line", "--chip-missing"),       # stroke
    ("graphic", "rails/openmaic/frontend/src/theme.css", ".openmaic .om-down", "--chip-missing"),       # shorthand
    ("graphic", "apps/platform/frontend/src/AdminPage.tsx", "style", "--chip-warming"),               # JSX
]


@pytest.mark.parametrize("kind,rel,sel,needle", KNOWN, ids=[k[2] for k in KNOWN])
def test_each_form_is_still_detected(kind, rel, sel, needle):
    if not (REPO / rel).exists():
        pytest.skip(f"{rel} is not in this tree")
    hits = [c for c in CHECKS if c[0] == kind and c[2] == rel and c[4] == sel and needle in (c[6] + str(c[5]))]
    assert hits, f"{kind} {sel} in {rel} is no longer detected; the detector lost a form"


@pytest.mark.parametrize("css,fill_sel,want", [
    # the stem form: .bq-mode-florist is used with .bq-mode, which sets the ink
    (".bq-mode { color: #fff; } .bq-mode-florist { background: var(--warning); }", ".bq-mode-florist", ".bq-mode"),
    # the compound form: .cw-urg.this-week is used with .cw-urg
    (".cw-urg { color: #fff; } .cw-urg.this-week { background: var(--p2); }", ".cw-urg.this-week", ".cw-urg"),
    # the same ancestor chain is required
    (".a .x { color: #fff; } .b .x-y { background: var(--good); }", ".b .x-y", None),
])
def test_companion_rules_are_found(css, fill_sel, want):
    # Today every fill that carries words also sets its own ink, so no real check depends on the
    # companion search; this is what keeps it honest for the next one that does not.
    rules = parse_rules(css)
    for r in rules:
        r["rel"] = "x.css"
    fill = next(r for r in rules if r["selectors"][0] == fill_sel)
    got = [c["selectors"][0] for c in companions(fill, rules)]
    assert got == ([want] if want else []), got


def test_nothing_is_unresolvable():
    bad = [_ids(c) + f" {c[6]!r}" for c in CHECKS if c[0] == "unresolvable"]
    assert not bad, "\n".join(bad)
    stale = [(u, x) for u, x, _ in ALLOWED if u in UNITS and (u, x) not in ALLOWED_USED]
    assert not stale, f"ALLOWED entries that match nothing: {stale}"


@pytest.mark.parametrize("check", [c for c in CHECKS if c[0] == "text-on-fill"], ids=_ids)
def test_words_on_a_status_fill_reach_4_5_to_1(check):
    fails = measure(check)
    assert not fails, (f"{check[2]}:{check[3]} {check[4]}: text {check[5]} on fill {check[6]} is below 4.5:1 "
                       f"on {len(fails)} theme/surface pairs, worst {min(r for _, r in fails):.2f} "
                       f"({min(fails, key=lambda f: f[1])[0]})")


@pytest.mark.parametrize("check", [c for c in CHECKS if c[0] == "graphic"], ids=_ids)
def test_a_status_graphic_reaches_3_to_1(check):
    fails = measure(check)
    assert not fails, (f"{check[2]}:{check[3]} {check[4]}: {check[6]} is below 3:1 on {len(fails)} "
                       f"theme/surface pairs, worst {min(r for _, r in fails):.2f} "
                       f"({min(fails, key=lambda f: f[1])[0]})")


@pytest.mark.parametrize("check", ON_ACCENT_WORDS, ids=lambda c: f"{c[2]}:{c[3]} {c[4][:40]}")
def test_words_on_an_accent_fill_reach_4_5_to_1_on_every_stop(check):
    # White on the accent gradient missed 4.5:1 on 104 of 132 palette/mode stops; the words on a
    # filled control read --on-accent (or a rail alias of it) and are measured on every stop.
    fails = measure_words(check)
    assert not fails, (f"{check[2]}:{check[3]} {check[4]}: {check[5]} is below 4.5:1 on {len(fails)} "
                       f"theme/backdrop pairs, worst {min(r for _, r in fails):.2f} "
                       f"({min(fails, key=lambda f: f[1])[0]})")


def test_gradient_fills_are_measured_stop_by_stop():
    # The stop parser on each form it claims, and the witness that real gradient fills were
    # found among the word checks rather than skipped.
    root = {"--g": "linear-gradient(135deg, #111111, #eeeeee 60%)", "--c": "#222222"}
    assert fill_stops("var(--g)", {}, root) == [((17, 17, 17), 1.0), ((238, 238, 238), 1.0)]
    assert fill_stops("linear-gradient(to right, var(--c) 0%, rgba(0, 0, 0, 0.5) 100%)", {}, root) == \
        [((34, 34, 34), 1.0), ((0, 0, 0), 0.5)]
    assert fill_stops("var(--c)", {}, root) == [((34, 34, 34), 1.0)]
    assert ON_ACCENT_WORDS, "no words on an accent fill were found"
    assert any(c[1] == "web" for c in ON_ACCENT_WORDS) and len({c[1] for c in ON_ACCENT_WORDS}) >= 5
