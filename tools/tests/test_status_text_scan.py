"""No frontend paints TEXT with a fixed or undefined status token, or with the bare palette accent.

--good / --warning / --critical are one colour for both modes; as text they miss WCAG 4.5:1 on
about half of the palettes (web/THEMING.md rule 4). Words read --good-text / --warning-text /
--critical-text (or a rail's own *-text twin). The same holds for --accent: words read
--accent-text (or a rail's alias of it, `--ac-text: var(--accent-text, #own)`). This scan is what
stops a migrated rail drifting back, so it is built to be exact rather than generous:

* Every frontend source is read: web/src, the shell, and every rails/**/frontend/src, CSS and
  TS/TSX alike. In TS/TSX a small lexer separates code from strings, template literals and
  comments, so `color` is found as a CSS declaration (in a stylesheet, or CSS written inside a
  template literal or string), as an object key (`color: ...` in a style object), as a JSX
  attribute (`color=`), or as a shorthand property (`{ color }`, `{ fontSize: 12, color }`) of an
  object the code marks as a style (`style={{ }}`, `sx={{ }}`, a `style:` / `sx:` value, an object
  typed CSSProperties), never of a destructuring pattern such as `function Chip({ color })`.
  `background-color`, `borderColor` and `--color` are different names.
* A value is resolved, not pattern-matched: var() chains are followed through every custom
  property any stylesheet defines, so a rail alias (`--p1: var(--critical)`) is seen through.
  JS values are classified by what they can evaluate to: string literals, both arms of a ternary
  and both sides of `??` or `||` (a `?` of `??` or `?.` is not a ternary),
  `x.field` (every `field:` literal in the same file), `NAME[...]` (the const map), a const, or a
  component prop forwarded into the colour (every value passed for that prop at call sites).
* Anything it cannot classify FAILS. A use that is genuinely not text and cannot be written so
  the scan sees that goes in ALLOWED below with its reason; an entry that matches nothing fails
  too, so the list cannot rot. (The public tree lacks some rails; an entry whose file is absent
  is skipped.)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXED = {"--good", "--warning", "--critical"}
# Every frontend paints on every palette, and words in the palette colour read --accent-text: each
# palette's accent moved in lightness until text in it clears 4.5:1 on all four surfaces, bare and
# under --accent-soft, held there by web/tests/model-chips.test.js (and, for a rule with a
# backdrop of its own, by tools/tests/test_status_fill_contrast.py). --accent itself is tuned for
# fills, dots and gradients; as text it missed 4.5:1 on 1080 of 2112 pairs in web-core alone. The
# walk stops at --accent-text (a few palettes define it as var(--accent), which is the point, not
# a leak), so a rail alias of it with its own standalone fallback passes.
ACCENT, ACCENT_TEXT = "--accent", "--accent-text"
# A name that reads as a status colour. Undefined everywhere, it is painted by its fallback
# literal (or not at all): how `var(--danger, #e5534b)` hid in web-core.
STATUS_WORD = re.compile(r"(?:^|-)(danger|error|success|ok|warn|bad|fail|good|warning|critical)(?:-|$)")
# Never source: installed packages and tool state, and BUILD OUTPUT. A build output dir is not
# only `dist`: edu-suite's iep tile builds to `outDir: 'dist-iep'` (its .gitignore has
# **/dist-iep/), vite caches in .vite/, and vitest --coverage writes .js into coverage/. The walk
# used to prune `dist` alone, so a built frontend/dist-iep read as a second SOURCE dir of that
# frontend and its minified bundle was scanned line by line.
NOT_SOURCE = ("node_modules", ".venv", ".git", "__pycache__")


def is_build_output(name: str) -> bool:
    return name in ("dist", "build", "coverage", ".vite") or name.startswith("dist-")


def not_source(name: str) -> bool:
    return name in NOT_SOURCE or is_build_output(name)

# (path, a substring of the source line, why it is not text). Matched per occurrence.
ALLOWED: list[tuple[str, str, str]] = []
# A rail keeps its own reasoned list beside its sources, as <...>/frontend/text-scan-allow.json:
#   [{"file": "src/components/X.tsx", "line": "<exact text from the source line>", "why": "..."}]
# That is how a rail the public tree withholds gets an allowance: this file ships, and
# publishing refuses a shipped tool that names a withheld path, so its entries cannot live in
# ALLOWED above. The same stale check holds them (an entry that matches nothing fails), and so
# does one more: an entry whose file does not exist fails too, because the list ships with the
# rail and the rail's own file cannot be absent from the tree that has the list.
RAIL_ALLOW_FILE = "text-scan-allow.json"


class AllowFileError(ValueError):
    pass


def load_rail_allows(top: Path) -> list[tuple[str, str, str, str]]:
    """[(path from REPO, line text, why, the allow file)] from every frontend's allow file."""
    out, seen = [], set()
    for src_dir in frontend_src_dirs(top):
        f = src_dir.parent / RAIL_ALLOW_FILE
        if not f.is_file() or f in seen:  # one per frontend, however many source dirs it has
            continue
        seen.add(f)
        where = f.relative_to(REPO).as_posix() if f.is_relative_to(REPO) else str(f)
        try:
            entries = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise AllowFileError(f"{where}: not JSON: {exc}") from None
        if not isinstance(entries, list):
            raise AllowFileError(f"{where}: must be a list of entries")
        for i, e in enumerate(entries):
            if not isinstance(e, dict) or set(e) != {"file", "line", "why"}:
                raise AllowFileError(f"{where}[{i}]: an entry is exactly file, line and why")
            if not all(isinstance(e[k], str) and e[k].strip() for k in ("file", "line", "why")):
                raise AllowFileError(f"{where}[{i}]: file, line and why must all be non-empty text")
            target = (f.parent / e["file"]).resolve()
            if not target.is_relative_to(f.parent.resolve()):
                raise AllowFileError(f"{where}[{i}]: {e['file']} is outside this frontend")
            rel = target.relative_to(REPO).as_posix() if target.is_relative_to(REPO) else str(target)
            out.append((rel, e["line"], e["why"], str(f)))
    return out


def stale_allows(allows, used: set[tuple[str, str]], file_must_exist: bool) -> list[str]:
    """Entries that matched nothing (and, for a rail's own list, entries naming a missing file)."""
    stale = []
    for a in allows:
        present = (REPO / a[0]).exists() or Path(a[0]).exists()
        if (a[0], a[1]) in used:
            continue
        if present or file_must_exist:
            stale.append(f"{a[0]}: {a[1]!r}" + ("" if present else " (no such file)"))
    return stale


def frontend_src_dirs(top: Path) -> list[Path]:
    """Every source directory directly under a <...>/frontend/ in `top`: its src, and any other
    surface it builds (smb's standalone phone app lives in frontend/mobile), found by a walk that
    never enters node_modules, any build output or a virtualenv. A `glob("**/frontend/src")` walks every
    installed node_modules tree: once a dozen rails had been built here, that took this module
    from ~2 s to over 2 min per import, and the whole core target from ~160 s to ~360 s."""
    out = []
    for dirpath, dirnames, _ in os.walk(top):
        dirnames[:] = sorted(d for d in dirnames if not not_source(d))
        here = Path(dirpath)
        if here.parent.name == "frontend" and here.name not in ("public", "tests", "test", "e2e"):
            out.append(here)
            dirnames[:] = []
    return out


def frontend_files(rails: Path | None = None) -> list[Path]:
    roots = [REPO / "web" / "src", REPO / "apps" / "platform" / "frontend" / "src"]
    roots += frontend_src_dirs(rails or REPO / "rails")
    out = []
    for r in roots:
        for p in sorted(r.rglob("*")):
            # By path PART below the source root, never by substring of the whole path: "/dist/"
            # missed dist-iep, and would have skipped everything in a checkout under a dist dir.
            if p.is_file() and p.suffix in (".css", ".ts", ".tsx", ".js", ".jsx") \
                    and not any(not_source(s) for s in p.relative_to(r).parts[:-1]):
                out.append(p)
    return out


# --- lexing ----------------------------------------------------------------------------------

def lex(text: str) -> list[tuple[str, int, int]]:
    """[(kind, start, end)] with kind in code / string / template / comment. Template literals
    nest: `${...}` inside one is code again until its closing brace."""
    segs: list[tuple[str, int, int]] = []
    i, n, start, kind = 0, len(text), 0, "code"
    stack: list[int] = []  # brace depth at each `${` that is still open
    depth = 0

    def cut(new_kind: str, at: int):
        nonlocal start, kind
        if at > start:
            segs.append((kind, start, at))
        start, kind = at, new_kind

    while i < n:
        c = text[i]
        if kind == "code":
            if c == "/" and text.startswith("//", i):
                cut("comment", i)
                j = text.find("\n", i)
                i = n if j < 0 else j
                cut("code", i)
                continue
            if c == "/" and text.startswith("/*", i):
                cut("comment", i)
                j = text.find("*/", i + 2)
                i = n if j < 0 else j + 2
                cut("code", i)
                continue
            if c in "'\"":
                cut("string", i)
                j = i + 1
                while j < n and text[j] != c and text[j] != "\n":
                    j += 2 if text[j] == "\\" else 1
                i = j + 1
                cut("code", i)
                continue
            if c == "`":
                cut("template", i)
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if stack and depth == stack[-1]:
                    stack.pop()
                    cut("template", i)
                    i += 1
                    continue
                depth -= 1
            i += 1
        else:  # template
            if c == "\\":
                i += 2
                continue
            if c == "`":
                i += 1
                cut("code", i)
                continue
            if text.startswith("${", i):
                cut("code", i)
                stack.append(depth)
                i += 2
                continue
            i += 1
    cut("end", n)
    return segs


def strip_css_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group(0)), css, flags=re.S)


# --- definitions: every custom property any stylesheet sets ------------------------------------

def css_regions(path: Path, text: str) -> list[tuple[int, str]]:
    """(offset, css text) for the CSS in a file: all of a .css, the template literals and strings
    of a TS/TSX. Comments blanked, offsets preserved."""
    if path.suffix == ".css":
        return [(0, strip_css_comments(text))]
    # The segment keeps its delimiters (a template's opening backtick, or the `}` that closes a
    # `${...}`, and its closing backtick). Blanked, offsets kept: left in, the first rule of a
    # template read as "` .iepg" and no TSX rail's wrapper tokens were ever found.
    return [(s, strip_css_comments(_blank_delims(text[s:e]))) for k, s, e in lex(text) if k in ("template", "string")]


def _blank_delims(seg: str) -> str:
    if seg[:1] in ("`", "'", '"', "}"):
        seg = " " + seg[1:]
    if seg[-1:] in ("`", "'", '"'):
        seg = seg[:-1] + " "
    return seg


# Custom properties resolve PER SCOPE: a use in a rail sees that rail's own definitions plus
# web-core's and the shell's, which is what it sees at runtime (every rail selector sits under the
# rail's root class, RC012, so one rail's `--warn` never reaches another). One global map let a
# definition in one rail decide a verdict in another: watch-builder's `--warn: var(--warning)` made
# smb mobile's own `--warn: #f0b429` read as fixed --warning (found 2026-10-08, merging
# watch-builder with Phase 5). A file outside every scope (a classifier test's tmp file) reads the
# union, DEFS, as every file did before.
SHARED_SCOPES = ("web", "apps/platform")


def scope_of(rel: str) -> str | None:
    """Whose definitions a file's uses may resolve through: rails/<name>, web, apps/platform."""
    parts = rel.split("/")
    if parts[0] == "rails" and len(parts) > 1:
        return "/".join(parts[:2])
    if parts[0] == "web":
        return "web"
    if parts[:2] == ["apps", "platform"]:
        return "apps/platform"
    return None


def collect_defs(files, root: Path) -> dict:
    """{scope: {--name: {definitions}}} over `files`, each file's scope taken relative to `root`."""
    out: dict = {}
    for p in files:
        try:
            scope = scope_of(p.relative_to(root).as_posix())
        except ValueError:
            scope = None
        text = p.read_text(encoding="utf-8", errors="replace")
        for _off, css in css_regions(p, text):
            for m in re.finditer(r"(?<![\w-])(--[\w-]+)\s*:\s*([^;{}]+)", css):
                out.setdefault(scope, {}).setdefault(m.group(1), set()).add(m.group(2).strip())
    return out


def visible_defs(by_scope: dict, scope: str) -> dict[str, set[str]]:
    """The definitions a use in `scope` can reach: its own, then the shared scopes'."""
    seen: dict[str, set[str]] = {}
    for s in (scope, *SHARED_SCOPES):
        for name, vals in by_scope.get(s, {}).items():
            seen.setdefault(name, set()).update(vals)
    return seen


def union_defs(by_scope: dict) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {}
    for defs in by_scope.values():
        for name, vals in defs.items():
            seen.setdefault(name, set()).update(vals)
    return seen


DEFS_BY_SCOPE = collect_defs(frontend_files(), REPO)
DEFS: dict[str, set[str]] = union_defs(DEFS_BY_SCOPE)
_VISIBLE: dict[str, dict[str, set[str]]] = {}


def css_verdict(value: str, scope: str | None = None, by_scope: dict | None = None) -> str | None:
    """None if the CSS value carries no status token and not the bare palette accent; else what
    it carries. var() resolves through what `scope` can see (visible_defs); with no scope,
    through every definition in `by_scope` (default: the repo's, i.e. DEFS)."""
    if by_scope is None:
        if scope is None:
            defs = DEFS
        else:
            defs = _VISIBLE.get(scope)
            if defs is None:
                defs = _VISIBLE[scope] = visible_defs(DEFS_BY_SCOPE, scope)
    else:
        defs = union_defs(by_scope) if scope is None else visible_defs(by_scope, scope)
    seen: set[str] = set()
    frontier = [(n, n) for n in re.findall(r"var\(\s*(--[\w-]+)", value)]
    while frontier:
        name, via = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in FIXED:
            return f"fixed {name}" + (f" (via {via})" if via != name else "")
        if name == ACCENT:
            return f"palette accent {ACCENT} as text, read {ACCENT_TEXT}" + (f" (via {via})" if via != name else "")
        if name == ACCENT_TEXT:
            continue
        if name not in defs:
            if STATUS_WORD.search(name[2:]):
                return f"undefined status token {name}"
            continue
        for d in defs[name]:
            frontier += [(m, via) for m in re.findall(r"var\(\s*(--[\w-]+)", d)]
    return None


# --- JS values ---------------------------------------------------------------------------------

def _split_top(expr: str, sep: str) -> list[str]:
    parts, depth, start, i = [], 0, 0, 0
    quote = None
    while i < len(expr):
        c = expr[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith(sep, i):
            parts.append(expr[start:i])
            start = i + len(sep)
            i += len(sep)
            continue
        i += 1
    parts.append(expr[start:])
    return parts


def _ternary(expr: str) -> str | None:
    """What follows the top-level `?` of a conditional (`cond ? a : b` -> `a : b`), or None if
    there is none. A `?` of `??` (nullish) or `?.` (optional chaining) is not one: splitting on
    every `?` read `x.on ?? 'var(--warning)'` as a conditional whose condition was `x.on`, so the
    left side was never classified, and `x?.on` became two arms that classified as nothing."""
    depth, quote, i = 0, None, 0
    while i < len(expr):
        c = expr[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "?" and depth == 0:
            nxt = expr[i + 1:i + 2]
            if nxt == "?":
                i += 2
                continue
            if nxt == "." and not expr[i + 2:i + 3].isdigit():
                i += 1
                continue
            return expr[i + 1:]
        i += 1
    return None


def _balanced(s: str, i: int) -> int:
    """Index just past the bracket that closes s[i] (one of ( [ {), skipping strings."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack, quote, j = [pairs[s[i]]], None, i + 1
    while j < len(s) and stack:
        c = s[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            stack.pop()
        j += 1
    return j


def js_values(expr: str, source: str, rail_sources: list[tuple[str, str]],
              depth: int = 0) -> list[tuple[str, str | None]] | None:
    """[(css string, origin)] that `expr` can evaluate to, or None if that cannot be determined.
    origin is the call site (path:line) when the value arrived through a forwarded prop."""
    e = expr.strip()
    while e.startswith("(") and e.endswith(")") and len(_split_top(e[1:-1], ",")) == 1:
        e = e[1:-1].strip()
    if depth > 6 or not e:
        return None
    q = _ternary(e)
    if q is not None:  # cond ? a : b (the arms, not the condition)
        arms = _split_top(q, ":")
        vals = [js_values(a, source, rail_sources, depth + 1) for a in arms]
        return None if any(v is None for v in vals) else [x for v in vals for x in v]
    for op in ("??", "||"):
        parts = _split_top(e, op)
        if len(parts) > 1:
            vals = [js_values(a, source, rail_sources, depth + 1) for a in parts]
            return None if any(v is None for v in vals) else [x for v in vals for x in v]
    m = re.fullmatch(r"\"([^\"\\]*)\"|'([^'\\]*)'|`([^`$\\]*)`", e)
    if m:
        return [(next(g for g in m.groups() if g is not None), None)]
    if e in ("undefined", "null", "true", "false") or re.fullmatch(r"-?\d+(\.\d+)?", e):
        return [("inherit", None)]
    m = re.fullmatch(r"[A-Za-z_$][\w$]*(?:\?)?\.([A-Za-z_$][\w$]*)", e)
    if m:  # x.field / x?.field: every literal of that field in the file
        lits = re.findall(rf"(?<![\w$.]){re.escape(m.group(1))}\s*:\s*(\"[^\"]*\"|'[^']*')", source)
        return [(x[1:-1], None) for x in lits] if lits else None
    m = re.fullmatch(r"([A-Za-z_$][\w$]*)\[[^\]]*\]", e)
    if m:  # NAME[...] into a const map or array literal
        d = re.search(rf"const\s+{re.escape(m.group(1))}\b[^=]*=\s*([\[{{])", source)
        if not d:
            return None
        body = source[d.start(1):_balanced(source, d.start(1))]
        lits = re.findall(r"\"([^\"]*)\"|'([^']*)'", body)
        return [(a or b, None) for a, b in lits] or None
    m = re.fullmatch(r"[A-Za-z_$][\w$]*", e)
    if m:
        d = re.search(rf"const\s+{re.escape(e)}\s*(?::[^=]+)?=\s*", source)
        if d:
            rhs = _split_top(source[d.end():], ";")[0].split("\n")[0]
            return js_values(rhs, source, rail_sources, depth + 1)
        comp = re.search(rf"function\s+(\w+)\s*\(\s*\{{[^}}]*\b{re.escape(e)}\b[^}}]*\}}", source) or \
            re.search(rf"const\s+(\w+)\s*=\s*\(\s*\{{[^}}]*\b{re.escape(e)}\b[^}}]*\}}", source)
        if comp:  # a prop forwarded into the colour: every value its callers pass
            vals: list[tuple[str, str | None]] = []
            for src_rel, src in rail_sources:
                for site in re.finditer(rf"<{comp.group(1)}\b", src):
                    tag = src[site.end():_value_end(src, site.end(), ">")]
                    a = re.search(rf"(?<![\w-]){re.escape(e)}=", tag)
                    if not a:
                        continue
                    rest = tag[a.end():]
                    if rest.startswith("{"):
                        got = js_values(rest[1:_balanced(rest, 0) - 1], src, rail_sources, depth + 1)
                    else:
                        got = js_values(re.match(r"\"[^\"]*\"|'[^']*'|\S+", rest).group(0), src,
                                        rail_sources, depth + 1)
                    if got is None:
                        return None
                    at = f"{src_rel}:{src.count(chr(10), 0, site.start()) + 1}"
                    vals += [(v, o or at) for v, o in got]
            return vals
    return None


def _value_end(text: str, i: int, stops: str) -> int:
    depth, quote, j = 0, None, i
    while j < len(text):
        c = text[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return j
            depth -= 1
        elif depth == 0 and c in stops:
            return j
        j += 1
    return j


def color_uses(path: Path, text: str):
    """(line, kind, value) for every `color` the file sets: kind css (value is CSS) or js."""
    out = []
    line_of = lambda k: text.count("\n", 0, k) + 1  # noqa: E731
    for off, css in css_regions(path, text):
        for m in re.finditer(r"(?<![\w-])color\s*:\s*", css):
            v = css[m.end():_value_end(css, m.end(), ";}\n")].strip().rstrip("'\"`")
            if v:
                out.append((line_of(off + m.start()), "css", v))
    if path.suffix != ".css":
        for kind, s, e in lex(text):
            if kind != "code":
                continue
            code = text[s:e]
            for m in re.finditer(r"(?<![\w$.\-])color\s*(:|=)(?!=)", code):
                k = s + m.start()
                prev = text[:k].rstrip()[-1:] or "{"
                start = s + m.end()
                if m.group(1) == "=":
                    if not _in_jsx_tag(text, k) or text[start:start + 1] not in ('"', "'", "{"):
                        continue  # a default parameter or an assignment, not an attribute
                    v = text[start:]
                    v = v[1:_balanced(v, 0) - 1] if v.startswith("{") else re.match(r"\"[^\"]*\"|'[^']*'", v).group(0)
                else:
                    if prev not in "{,(\n":
                        continue  # `cond ? color : other`, a label
                    v = text[start:_value_end(text, start, ",;}\n")]
                    if re.fullmatch(r"\s*(string|number|boolean|any|unknown|never)(\[\])?\s*", v):
                        continue  # a TypeScript annotation: `{ color: string }`
                out.append((line_of(k), "js", v.strip()))
        out += [(line_of(k), "js", "color") for k in shorthand_colors(text)]
    return out


# A shorthand `color` property, `{ color }` or `{ fontSize: 12, color }`, sets the colour to the
# variable of that name, but only in a style object: the same text is a destructuring pattern in
# `function Chip({ color })` or `const { color } = props`, and an export list in `export { color }`.
# So it is a use ONLY inside an object literal the code marks as a style: a JSX `style={{ }}` or
# `sx={{ }}`, the value of a `style:` / `sx:` property of an object literal (not of a pattern), or
# an object typed CSSProperties. Its value is the identifier `color`, resolved by js_values.
_SHORTHAND_COLOR = re.compile(r"(?<![\w$.\-])color(?=\s*[,}])")
_STYLE_ATTR = re.compile(r"(?<![\w$.\-])(?:style|sx)\s*=\s*\{\s*$")
_STYLE_KEY = re.compile(r"(?<![\w$.\-])(?:style|sx)\s*:\s*$")
_CSS_TYPED = re.compile(r":\s*(?:React\.)?CSSProperties\s*(?:=|=>\s*\()\s*$")
_CSS_CAST = re.compile(r"\s*(?:as|satisfies)\s+(?:React\.)?CSSProperties\b")
_DECLARES = re.compile(r"(?<![\w$])(?:const|let|var)\s*$")
_PARAMS_END = re.compile(r"\s*(?::\s*[\w$][\w$.<>\[\]|, ]*?\s*)?(?:=>|\{)")


def code_only(text: str) -> str:
    """`text` with everything but code blanked to spaces (strings, template literals and comments;
    newlines kept, so offsets and line numbers hold), and the `${` that opens each template
    substitution blanked too, so the brackets left behind balance."""
    chars = list(text)
    for kind, s, e in lex(text):
        if kind == "code":
            if text.startswith("${", s):
                chars[s] = chars[s + 1] = " "
            continue
        for i in range(s, e):
            if chars[i] != "\n":
                chars[i] = " "
    return "".join(chars)


def _prev_char(code: str, k: int) -> str:
    j = k - 1
    while j >= 0 and code[j].isspace():
        j -= 1
    return code[j] if j >= 0 else ""


def _opener(code: str, k: int) -> int | None:
    """Index of the bracket enclosing position k, or None at the top level."""
    depth = 0
    for j in range(k - 1, -1, -1):
        c = code[j]
        if c in ")]}":
            depth += 1
        elif c in "([{":
            if depth == 0:
                return j
            depth -= 1
    return None


def shorthand_colors(text: str) -> list[int]:
    """Offsets of each shorthand `color` property of a style object literal in a TS/TSX source."""
    if not _SHORTHAND_COLOR.search(text):
        return []
    code = code_only(text)
    out = []
    for m in _SHORTHAND_COLOR.finditer(code):
        k = m.start()
        if _prev_char(code, k) not in ("{", ","):
            continue  # not a property position: `ok ? color : other`, `return color }`
        brace = _opener(code, k)
        if brace is not None and code[brace] == "{" and _is_style_object(code, brace):
            out.append(k)
    return out


def _is_style_object(code: str, brace: int) -> bool:
    """Does the code around the object literal opening at `brace` mark it as a style?"""
    lo = max(0, brace - 240)
    if _STYLE_ATTR.search(code, lo, brace):
        return True  # style={{ ... }}: a JSX attribute's value, an expression and never a pattern
    if _CSS_TYPED.search(code, lo, brace):
        return True  # const s: CSSProperties = { ... }, or (): CSSProperties => ({ ... })
    if _CSS_CAST.match(code, _balanced(code, brace)):
        return True  # { ... } as CSSProperties
    if _STYLE_KEY.search(code, lo, brace):
        return not _in_pattern(code, brace)  # style: { ... }
    return False


def _in_pattern(code: str, brace: int) -> bool:
    """Is the object at `brace` inside a destructuring pattern (`const { style: { color } } = p`,
    `function F({ style: { color } })`)? Walk out through the objects and arrays it is a value of
    to the outermost one, then read what surrounds that: a declaration keyword before it, a single
    `=` after it, or a parameter list it sits in (one followed by `=>` or a function body)."""
    top = brace
    while True:
        up = _opener(code, top)
        prev = _prev_char(code, top)
        if up is None or not ((code[up] == "{" and prev == ":")
                              or (code[up] == "[" and prev in ("[", ","))):
            break
        top = up
    if _DECLARES.search(code, max(0, top - 16), top):
        return True
    end = _balanced(code, top)
    while end < len(code) and code[end].isspace():
        end += 1
    if code.startswith("=", end) and not code.startswith(("==", "=>"), end):
        return True
    up = _opener(code, top)
    if up is not None and code[up] == "(":
        return bool(_PARAMS_END.match(code, _balanced(code, up)))
    return False


def _in_jsx_tag(text: str, k: int) -> bool:
    """Is position k inside a JSX opening tag (`<Name ... HERE`)? Walk back over attributes;
    a `(`, `;`, `=>` or a closed tag first means plain code."""
    j, depth = k - 1, 0
    while j >= 0:
        c = text[j]
        if c in "}])":
            depth += 1
        elif c in "{[(":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0:
            if c == "<":
                return bool(re.match(r"<[A-Za-z]", text[j:j + 2]))
            if c in ";>" and not text[j - 1:j + 1] == "=>":
                return False
        j -= 1
    return False


def verdicts_for(path: Path, text: str, rail_sources: list[tuple[str, str]], root: Path = REPO,
                 by_scope: dict | None = None):
    """[(line, value, [problems])] for every `color` in one file. The ONE place a use is judged:
    the repo scan and the classifier test both call it, so neither can drift from the other.
    Each use is judged in the file's scope (scope_of, relative to `root`)."""
    try:
        scope = scope_of(path.relative_to(root).as_posix())
    except ValueError:
        scope = None
    out = []
    for line, kind, v in color_uses(path, text):
        if kind == "css":
            verdicts = [css_verdict(v, scope, by_scope)]
        else:
            vals = js_values(v, text, rail_sources)
            verdicts = ["unclassifiable"] if vals is None else [
                (f"{css_verdict(x, scope, by_scope)} (passed at {o})"
                 if o and css_verdict(x, scope, by_scope) else css_verdict(x, scope, by_scope))
                for x, o in vals]
        out.append((line, v, [x for x in verdicts if x]))
    return out


def scan(allows=None) -> tuple[list[str], int, set[str]]:
    """(problems, color uses scanned, the allowances used). `allows` defaults to ALLOWED plus
    every rail's own list."""
    if allows is None:
        allows = [a[:3] for a in ALLOWED] + [a[:3] for a in load_rail_allows(REPO / "rails")]
    files = frontend_files()
    by_rail: dict[str, list[tuple[str, str]]] = {}
    texts = {}
    for p in files:
        rel = p.relative_to(REPO).as_posix()
        texts[p] = p.read_text(encoding="utf-8", errors="replace")
        by_rail.setdefault("/".join(rel.split("/")[:2]), []).append((rel, texts[p]))
    problems, count, used = [], 0, set()
    for p in files:
        rel = p.relative_to(REPO).as_posix()
        text = texts[p]
        lines = text.split("\n")
        for line, v, bad in verdicts_for(p, text, by_rail["/".join(rel.split("/")[:2])]):
            count += 1
            if not bad:
                continue
            src = lines[line - 1]
            allow = [a for a in allows if a[0] == rel and a[1] in src]
            if allow:
                used.add((allow[0][0], allow[0][1]))
                continue
            problems.append(f"{rel}:{line}  color {v!r} -> {', '.join(sorted(set(bad)))}")
    return problems, count, used


def test_no_frontend_paints_text_with_a_fixed_or_undefined_status_token():
    problems, count, used = scan()
    assert count > 500, f"only {count} color uses scanned; the walker or the lexer lost files"
    assert not problems, (f"{len(problems)} color use(s) on a fixed/undefined status token, on the "
                          f"bare palette accent, or not classifiable (use --*-text or "
                          f"--accent-text, or add a reasoned ALLOWED entry or an entry in the "
                          f"rail's frontend/{RAIL_ALLOW_FILE}):\n  "
                          + "\n  ".join(problems))
    # ALLOWED may name a rail the public tree lacks (skipped then); a rail's own list may not.
    stale = stale_allows(ALLOWED, used, file_must_exist=False)
    stale += stale_allows(load_rail_allows(REPO / "rails"), used, file_must_exist=True)
    assert not stale, f"allowances that match nothing: {stale}"


def _scoped_tree(tmp_path):
    """Two rails that define `--warn` differently, a rail alias, and a web-core definition."""
    files = []
    for rel, css in (
        ("rails/alpha/frontend/src/a.css", ".alpha { --warn: var(--warning); }\n"),
        ("rails/beta/frontend/src/b.css",
         ".beta { --warn: #f0b429; --b-note: var(--critical); }\n.beta .x { color: var(--warn); }\n"),
        ("web/src/w.css", ":root { --shared-flag: var(--warning); }\n"),
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(css, encoding="utf-8")
        files.append(p)
    return files, collect_defs(files, tmp_path)


def test_a_custom_property_resolves_only_through_what_its_rail_can_see(tmp_path):
    """One rail's definition must not decide another rail's verdict, and a rail still sees its own
    aliases and web-core's. With one global map, watch-builder's `--warn: var(--warning)` made smb
    mobile's own `--warn: #f0b429` read as fixed --warning."""
    _files, by_scope = _scoped_tree(tmp_path)
    assert css_verdict("var(--warn)", "rails/beta", by_scope) is None, "alpha's --warn decided beta's verdict"
    assert css_verdict("var(--warn)", "rails/alpha", by_scope) == "fixed --warning (via --warn)"
    assert css_verdict("var(--b-note)", "rails/beta", by_scope) == "fixed --critical (via --b-note)", \
        "a rail's own alias was not seen through"
    assert css_verdict("var(--shared-flag)", "rails/beta", by_scope) == "fixed --warning (via --shared-flag)", \
        "web-core's definitions did not reach a rail"
    assert css_verdict("var(--b-note)", "rails/alpha", by_scope) is None, "beta's alias reached alpha"


def test_the_scan_judges_each_use_in_its_own_rails_scope(tmp_path):
    """verdicts_for, the one place a use is judged, hands the file's scope on: beta's
    `color: var(--warn)` reads beta's own #f0b429, not alpha's var(--warning)."""
    files, by_scope = _scoped_tree(tmp_path)
    beta = files[1]
    got = verdicts_for(beta, beta.read_text(encoding="utf-8"), [], root=tmp_path, by_scope=by_scope)
    assert got, "no color use found in the beta stylesheet; the test checks nothing"
    assert all(not bad for _line, _v, bad in got), got


def _rail_tree(tmp_path, name, src, allow):
    """A throwaway rail under tmp_path/rails/<name>/frontend with one source and an allow file."""
    fe = tmp_path / "rails" / name / "frontend"
    (fe / "src").mkdir(parents=True)
    (fe / "src" / "x.tsx").write_text(src, encoding="utf-8")
    if allow is not None:
        (fe / RAIL_ALLOW_FILE).write_text(json.dumps(allow) if not isinstance(allow, str) else allow,
                                          encoding="utf-8")
    return fe


@pytest.mark.parametrize("allow,error", [
    ([{"file": "src/x.tsx", "line": "x", "why": "  "}], "must all be non-empty"),
    ([{"file": "src/x.tsx", "line": "x"}], "exactly file, line and why"),
    ([{"file": "../../../web/src/x.tsx", "line": "x", "why": "w"}], "outside this frontend"),
    ({"file": "src/x.tsx"}, "must be a list"),
    ("[{oops", "not JSON"),
])
def test_a_rail_allow_file_that_says_less_than_why_is_refused(tmp_path, allow, error):
    _rail_tree(tmp_path, "zr", "const a = 1\n", allow)
    with pytest.raises(AllowFileError, match=error):
        load_rail_allows(tmp_path / "rails")


def test_a_rail_allow_file_suppresses_its_line_and_its_stale_entries_fail(tmp_path):
    # Its entry is keyed to the line's text, an entry naming nothing (or a missing file) is
    # stale, and ALLOWED's absent-rail leniency does not extend to a rail's own list.
    src = '<i style={{ color: "var(--warning)" }} />\n'
    _rail_tree(tmp_path, "zr", src, [
        {"file": "src/x.tsx", "line": 'color: "var(--warning)"', "why": "a hue swatch, not words"},
        {"file": "src/x.tsx", "line": "nothing like this", "why": "stale"},
        {"file": "src/gone.tsx", "line": "x", "why": "the file went"},
    ])
    allows = load_rail_allows(tmp_path / "rails")
    assert [a[2] for a in allows] == ["a hue swatch, not words", "stale", "the file went"]
    p = tmp_path / "rails" / "zr" / "frontend" / "src" / "x.tsx"
    rel = allows[0][0]
    lines = src.split("\n")
    used = set()
    for line, _v, bad in verdicts_for(p, src, [(rel, src)]):
        assert bad == ["fixed --warning"]
        hit = [a for a in allows if a[0] == rel and a[1] in lines[line - 1]]
        assert hit and hit[0][2] == "a hue swatch, not words"
        used.add((hit[0][0], hit[0][1]))
    stale = stale_allows(allows, used, file_must_exist=True)
    assert len(stale) == 2 and "nothing like this" in stale[0] and "(no such file)" in stale[1]
    assert stale_allows(allows, used, file_must_exist=False) == stale[:1]


def test_the_real_rails_allow_files_load():
    # Witness that the loader runs over the real tree (and that every real entry is well formed).
    allows = load_rail_allows(REPO / "rails")
    assert allows, "no rail allow entry loaded, so there was nothing to check"
    assert all(a[2].strip() for a in allows)


def test_the_scan_reaches_every_frontend():
    files = frontend_files()
    rails = {p.relative_to(REPO).parts[1] for p in files if p.relative_to(REPO).parts[0] == "rails"}
    # Enumerated independently of the walker: a rail's own frontend/src, or one under apps/*/.
    present = {p.name for p in (REPO / "rails").iterdir() if p.is_dir() and (
        (p / "frontend" / "src").is_dir() or any(a.is_dir() for a in p.glob("apps/*/frontend/src")))}
    assert rails == present, f"rails with a frontend the walker did not read: {present - rails}"
    assert any(p.suffix == ".tsx" for p in files) and any(p.suffix == ".css" for p in files)
    # A second surface beside src is read too (enumerated here by name, not by the walker).
    mobile = REPO / "rails" / "smb-partner-enablement" / "frontend" / "mobile"
    if mobile.is_dir():
        assert any(mobile in p.parents for p in files), "smb's frontend/mobile was not read"


@pytest.mark.parametrize("out_dir", ["dist-iep", "dist", "coverage", ".vite", "build"])
def test_a_build_output_dir_is_never_read_as_source(tmp_path, out_dir):
    """edu-suite builds its iep tile to frontend/dist-iep. The walk pruned `dist` alone, so that
    directory was a direct child of a frontend that was not excluded, and read as a SECOND source
    dir: the built bundle was scanned as source. Planted here beside real sources, at the
    frontend level and nested inside src, with a colour the scan does flag (checked below, so
    the plant is not a file the scan would have passed anyway)."""
    bad = ".a { color: var(--critical); }\n"
    front = tmp_path / "rails" / "zz" / "apps" / "dashboard" / "frontend"
    for d in ("src", "mobile", out_dir, f"src/{out_dir}", f"{out_dir}/assets"):
        (front / d).mkdir(parents=True, exist_ok=True)
    (front / "src" / "a.css").write_text(bad, encoding="utf-8")
    (front / "mobile" / "m.css").write_text(bad, encoding="utf-8")
    planted = [front / out_dir / "assets" / "index-abc.css", front / "src" / out_dir / "b.css",
               front / out_dir / "c.js"]
    for p in planted:
        p.write_text(bad, encoding="utf-8")
    assert verdicts_for(planted[0], bad, []), "witness: the planted text is not one the scan flags"
    assert sorted(d.name for d in frontend_src_dirs(tmp_path / "rails")) == ["mobile", "src"]
    files = set(frontend_files(tmp_path / "rails"))
    assert {front / "src" / "a.css", front / "mobile" / "m.css"} <= files, "the real sources were not read"
    assert not files & set(planted), sorted(str(p.relative_to(tmp_path)) for p in files & set(planted))


@pytest.mark.parametrize("source,want", [
    # CSS declarations
    (".a { color: var(--critical); }", "fixed --critical"),
    (".a { color: var(--warning, #b7761f); }", "fixed --warning"),
    (".a { color: var(--good-text); }", None),
    (".a { background-color: var(--good); border-color: var(--good); }", None),
    (".a { --x: var(--good); } .b { color: var(--x); }", "fixed --good (via --x)"),
    (".a { color: var(--danger-zz, #e5534b); }", "undefined status token"),
    # TSX: style objects, JSX attributes, CSS in a template literal
    ('const s = { color: "var(--warning)" };', "fixed --warning"),
    ('const s = { borderColor: "var(--warning)" };', None),
    ('<i style={{ color: ok ? "var(--good-text)" : "var(--critical)" }} />', "fixed --critical"),
    ('const M = { a: "var(--good)" };\n<i style={{ color: M[k] }} />', "fixed --good"),
    ('const T = { a: { text: "var(--warning)" } };\n<i style={{ color: t.text }} />', "fixed --warning"),
    ('<i style={{ color: pick(k) }} />', "unclassifiable"),
    # `??` and `?.` are not conditionals: BOTH sides of `??` are values (the left one was dropped
    # when every `?` was read as a ternary), and `x?.on` is a field read like `x.on`.
    ('const T = { a: { on: "var(--critical)" } };\n<i style={{ color: x.on ?? "var(--good-text)" }} />',
     "fixed --critical"),
    ('const T = { a: { on: "var(--critical)" } };\n<i style={{ color: "var(--good-text)" ?? x.on }} />',
     "fixed --critical"),
    ('const T = { a: { on: "var(--critical)" } };\n<i style={{ color: x?.on }} />', "fixed --critical"),
    ('<i style={{ color: x?.on ? "var(--good-text)" : "var(--critical-text)" }} />', None),
    ('<i style={{ color: x.on ?? "var(--good-text)" }} />', "unclassifiable"),
    ('<Foo color="var(--critical)" />', "fixed --critical"),
    ("const css = `.x { color: var(--good); }`;", "fixed --good"),
    ('function Tile({ accent }) { return <b style={{ color: accent }} /> }\n<Tile accent="var(--warning)" />',
     "fixed --warning"),
    ('function Tile({ accent }) { return <b style={{ color: accent }} /> }\n<Tile accent="var(--good-text)" />', None),
    ("// color: var(--good)\nconst n = 1;", None),
    # A shorthand `color` in a style object is a use, its value resolved like any identifier...
    ('const color = "var(--warning)";\n<i style={{ color }} />', "fixed --warning"),
    ('const color = "var(--critical)";\n<i style={{ fontSize: 12, color }} />', "fixed --critical"),
    ('const color = "var(--critical)";\n<i style={{\n  fontSize: 12,\n  color, // the label\n}} />',
     "fixed --critical"),
    ('const color = "var(--critical)";\n<i style={{ width: `${w}px`, color }} />', "fixed --critical"),
    ('const color = "var(--critical)";\n<Box sx={{ color }} />', "fixed --critical"),
    ('const color = "var(--good)";\nconst s = { style: { color } };', "fixed --good"),
    ('const color = "var(--good)";\nReact.createElement("i", { style: { color } });', "fixed --good"),
    ('const color = "var(--warning)";\nconst s: React.CSSProperties = { color };', "fixed --warning"),
    ('const color = "var(--warning)";\nconst s = { color, padding: 4 } as CSSProperties;', "fixed --warning"),
    ('const color = "var(--critical)";\nconst f = (): CSSProperties => ({ color });', "fixed --critical"),
    ('<i style={{ color }} />', "unclassifiable"),
    ('const color = ok ? "var(--good-text)" : "var(--critical-text)";\n<i style={{ color }} />', None),
    # ...and the same text is not a use anywhere else: patterns, exports, JSX children.
    ('const color = "var(--critical)";\nfunction Chip({ color }) { return <b>{color}</b> }', None),
    ("const { color } = props;", None),
    ("items.map(({ color }) => color.length);", None),
    ('const color = "var(--critical)";\nfunction F({ color }: CSSProperties) { return null }', None),
    ("const { style: { color } } = props;", None),
    ("function paint(el, { style: { color } }) { el.title = color; }", None),
    ('const color = "var(--critical)";\nconst Chip = ({ style: { color } }: P) => <b />;', None),
    ('const color = "var(--critical)";\nfor (const { style: { color } } of xs) {}', None),
    ("({ style: { color } } = props);", None),
    ('const color = "var(--critical)";\nexport { color };', None),
    ('const color = "var(--critical)";\n<p>{color}</p>', None),
])
def test_the_classifier_on_each_form(tmp_path, source, want):
    # The scan's own witness: each form it claims to read is read, and reaches the right verdict.
    p = tmp_path / ("x.css" if source.startswith(".") else "x.tsx")
    p.write_text(source, encoding="utf-8")
    saved = {k: set(v) for k, v in DEFS.items()}
    try:
        for _off, css in css_regions(p, source):
            for m in re.finditer(r"(?<![\w-])(--[\w-]+)\s*:\s*([^;{}]+)", css):
                DEFS.setdefault(m.group(1), set()).add(m.group(2).strip())
        got = verdicts_for(p, source, [("x.tsx", source)])
    finally:  # the synthetic definitions must not leak into the repo scan
        DEFS.clear()
        DEFS.update(saved)
    bad = sorted({b for _line, _v, bs in got for b in bs})
    if want is None:
        assert bad == [], bad
    else:
        assert bad and all(b.startswith(want) or want in b for b in bad), (want, bad, got)


@pytest.mark.parametrize("source,want", [
    (".a { color: var(--accent); }", "palette accent --accent"),
    (".a { color: var(--accent-text); }", None),
    (".a { background: var(--accent-soft); border-color: var(--accent); }", None),
    (".a { color: var(--accent-soft); }", None),  # another name, not the accent
    (".a { --x: var(--accent); } .b { color: var(--x); }", "palette accent --accent as text, read --accent-text (via --x)"),
    # a rail alias of the text token, with the rail's own standalone fallback, passes
    (".a { --zz-text: var(--accent-text, #4f7cff); } .b { color: var(--zz-text); }", None),
    # and one whose fallback is the bare accent does not: a fallback can paint
    (".a { --zz-text: var(--accent-text, var(--accent)); } .b { color: var(--zz-text); }", "palette accent --accent"),
    (".a { color: color-mix(in oklab, var(--accent) 85%, var(--text-primary)); }", "palette accent --accent"),
    ('<i style={{ color: "var(--accent)" }} />', "palette accent --accent"),
    ('<i style={{ color: on ? "var(--accent-text)" : "var(--text-secondary)" }} />', None),
])
def test_the_accent_rule_on_each_form(tmp_path, source, want):
    # Same witness for the accent rule. The real --accent-text definitions (a few are
    # var(--accent)) are in DEFS here, so the case that reads --accent-text also proves the walk
    # stops there instead of following them back to the accent.
    assert any(d == "var(--accent)" for d in DEFS.get(ACCENT_TEXT, ())), "witness: no --accent-text: var(--accent) seen"
    p = tmp_path / ("x.css" if source.startswith(".") else "x.tsx")
    p.write_text(source, encoding="utf-8")
    saved = {k: set(v) for k, v in DEFS.items()}
    try:
        for _off, css in css_regions(p, source):
            for m in re.finditer(r"(?<![\w-])(--[\w-]+)\s*:\s*([^;{}]+)", css):
                DEFS.setdefault(m.group(1), set()).add(m.group(2).strip())
        got = verdicts_for(p, source, [("x.tsx", source)])
    finally:
        DEFS.clear()
        DEFS.update(saved)
    bad = sorted({b for _line, _v, bs in got for b in bs})
    if want is None:
        assert bad == [], bad
    else:
        assert bad and all(b.startswith(want) for b in bad), (want, bad, got)


def test_the_accent_rule_reaches_the_rails():
    # The scope witness. web-core's sheet paints words with --accent-text, and so does at least
    # one rail through an alias of its own; and the walk resolves that alias from the rail's real
    # definitions (its bare --ac twin still reaches the accent, its --ac-text does not).
    files = frontend_files()
    sheet = REPO / "web" / "src" / "styles.css"
    uses = [v for _l, k, v in color_uses(sheet, sheet.read_text(encoding="utf-8")) if k == "css"]
    assert any("var(--accent-text)" in v for v in uses), uses
    rail_uses = [v for p in files if p.relative_to(REPO).parts[0] == "rails"
                 for _l, k, v in color_uses(p, p.read_text(encoding="utf-8", errors="replace")) if k == "css"]
    aliases = {n for n, ds in DEFS.items() if n.endswith("-text") and any(f"var({ACCENT_TEXT}" in d for d in ds)
               and n != ACCENT_TEXT}
    assert aliases, "no rail alias of --accent-text is defined anywhere"
    assert any(f"var({a})" in v for a in aliases for v in rail_uses), (aliases, "no rail colour reads one")
    for a in aliases:
        assert css_verdict(f"var({a})") is None, a
        bare = a[:-len("-text")]
        if bare in DEFS:
            assert css_verdict(f"var({bare})"), f"{bare} no longer reaches the accent: the witness is degenerate"
