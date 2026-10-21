# RC012: a rail's styles, scoped to its wrapper and on the shared palette.
#
# A rule file of tools/rail_conformance.py. That module runs it into its own namespace rather
# than importing it (its "where the rules live" says why), so rule, F, S, Manifest, Finding,
# read, rel, REPO and the rest of what is used here without being defined here are its names.
# A comment rather than a docstring: a docstring would replace that module's, its --help text.
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


WEB_CORE_CSS = REPO / "web" / "src" / "styles.css"


def _css_of(text: str, where: str) -> str:
    """The CSS in a rail's style source: all of a .css, or the `const ...CSS... = `...``
    template literals a module.tsx injects (its TypeScript is not CSS, and a `.x` in code or a
    comment is not a selector)."""
    if not where.endswith((".tsx", ".ts", ".jsx", ".js")):
        return text
    return "\n".join(re.sub(r"\$\{[^}]*\}", "", t)
                     for t in re.findall(r"const\s+\w*CSS\w*\s*=\s*`([^`]*)`", text))


def _class_prefix(cls: str) -> str:
    """What a class name claims: the part before its first '-' (`bq-btn` -> `bq`), or the whole
    name when it has none (`chip`)."""
    return cls.split("-", 1)[0]


@lru_cache(maxsize=512)
def _classes(css: str) -> frozenset[str]:
    return frozenset(re.findall(r"\.(-?[A-Za-z_][\w-]*)", re.sub(r"/\*.*?\*/", "", css, flags=re.S)))


def _prefix_owners(all_manifests: list[Manifest]) -> dict[str, set[str]]:
    """{class prefix: who uses one} over web-core's sheet and every rail's styles, wherever the
    class sits (under a wrapper or not): a rail's unscoped rule matches any element with that
    class, inside anybody's markup."""
    owners: dict[str, set[str]] = {}
    for c in _classes(read(WEB_CORE_CSS)):
        owners.setdefault(_class_prefix(c), set()).add("web-core")
    for r in all_manifests:
        t, w = r.style_text()
        for c in _classes(_css_of(t, w)):
            owners.setdefault(_class_prefix(c), set()).add(r.id)
    return owners


def unscoped_selectors(m: Manifest, all_manifests: list[Manifest],
                       sheet: tuple[str, str] | None = None) -> list[tuple[str, str]]:
    """[(selector, why)] for every selector in the rail's styles that can match outside the rail.
    `sheet` is (text, where) for another of the rail's stylesheets (shipped_stylesheets), judged
    by the same rule; by default the one style_text() names.

    The federation runtime never removes a remote's stylesheet, so after one visit a rail's CSS
    applies to the whole platform for the rest of the session. A selector is scoped when its
    first compound is the wrapper (`.recipe-book`, `.recipe-book.x`, `.recipe-book-x`), when a
    `:root` / `html` qualifier leads to the wrapper (`:root[data-theme='dark'] .ma`), or when
    the first compound carries a class whose prefix only this rail uses (`.bq-btn`, `.cw-tab`).
    Anything else (`.btn`, `.chip`, `.empty`, `h3`, `:root`, `*`, a class whose prefix web-core
    or another rail also uses) restyles somebody else's elements.
    """
    text, where = sheet if sheet is not None else m.style_text()
    wrapper = str(m.data.get("css_wrapper") or "")
    owners = _prefix_owners([r for r in all_manifests if r.id != m.id] + [m])
    out: list[tuple[str, str]] = []
    for sel in _top_level_selectors(_css_of(text, where)):
        if re.fullmatch(r"from|to|[\d.]+%", sel):
            continue
        first = re.split(r"\s*[ >+~]\s*", sel, maxsplit=1)[0]
        if _root_led_to_wrapper(first, sel, wrapper):
            continue
        classes = re.findall(r"\.(-?[A-Za-z_][\w-]*)", re.sub(r":not\([^)]*\)", "", first))
        if any(c == wrapper or (wrapper and c.startswith(wrapper + "-")) for c in classes if wrapper):
            continue
        if any("-" in c and owners.get(_class_prefix(c)) == {m.id} for c in classes):
            continue
        if not classes:
            why = "no class: matches every such element"
        else:
            parts = []
            for c in classes:
                others = sorted(owners.get(_class_prefix(c), set()) - {m.id})
                parts.append(f".{c} (prefix '{_class_prefix(c)}' also used by {', '.join(others)})"
                             if others else f".{c} (no prefix of this rail's own)")
            why = "; ".join(parts)
        out.append((sel, why))
    return out


#: A stylesheet a module pulls into the build: `import './x.css'`, `import s from './x.css?inline'`
#: or `import('./x.css')`. Matched after comments are stripped, so a commented-out import is not one.
_CSS_IMPORT = re.compile(r"""(?:\bimport\s*\(?|\bfrom)\s*['"]([^'"\n]+?\.css)(?:\?[^'"\n]*)?['"]""")


def _strip_js_comments(src: str) -> str:
    """Block comments and whole-line `//` comments. A trailing `//` is left alone: it may sit in
    a string ('http://...'), and a missed comment errs toward reading an import, not hiding one."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def shipped_stylesheets(m: Manifest) -> list[tuple[str, str, str]]:
    """[(text, where, via)] for every stylesheet of the rail's OWN that its sources put into the
    federated build, other than the one style_text() names (RC012 judges that one already).

    A rail does not ship only theme.css. Vite builds index.html by default (no
    rollupOptions.input), so main.tsx and everything it imports is in the bundle, and with
    cssCodeSplit:false the federation plugin has remoteEntry.js inject EVERY stylesheet the
    build emits into the shell's document, where nothing ever removes it. bouquet's
    standalone.css (html, body, #root, :root --page), imported by main.tsx for `npm run dev`,
    inset the whole platform by 20px that way, and RC012 never read it.

    Read: a .css imported by a relative path from any module under frontend/src, and a
    stylesheet <link> or inline <style> in frontend/index.html. A package's CSS (xterm.css) is
    not the rail's to scope; RC032 reads it in the built bundle with everything else.
    """
    src = m.frontend_src()
    fe = src.parent
    # The file style_text() reads, so RC012 does not judge it twice.
    own = (src / "theme.css").resolve() if (src / "theme.css").is_file() else None
    out: list[tuple[str, str, str]] = []
    seen: set[Path] = set()

    def add(path: Path, via: str, text: str | None = None) -> None:
        if text is None:
            path = path.resolve()
            if path in seen or not path.is_file() or path == own:
                return
            seen.add(path)
            text = read(path)
        out.append((text, rel(path), via))

    if src.is_dir():
        mods = sorted(p for ext in ("*.ts", "*.tsx", "*.js", "*.jsx", "*.mts")
                      for p in src.rglob(ext) if "node_modules" not in p.parts)
        for mod in mods:
            for spec in _CSS_IMPORT.findall(_strip_js_comments(read(mod))):
                if spec.startswith("."):
                    add(mod.parent / spec, f"imported by {rel(mod)}")
    html = fe / "index.html"
    if html.is_file():
        page = re.sub(r"<!--.*?-->", "", read(html), flags=re.S)
        for tag in re.findall(r"<link\b[^>]*>", page, flags=re.I):
            href = re.search(r"""\bhref\s*=\s*["']?([^"'\s>]+)""", tag, flags=re.I)
            if re.search(r"""\brel\s*=\s*["']?stylesheet""", tag, flags=re.I) and href \
                    and not re.match(r"[a-z]+:|//", href.group(1), flags=re.I):
                add(fe / href.group(1).lstrip("/"), f"linked by {rel(html)}")
        for i, body in enumerate(re.findall(r"<style\b[^>]*>(.*?)</style>", page, flags=re.S | re.I)):
            add(html, f"inline <style> #{i + 1} in {rel(html)}", text=body)
    return out


@rule("RC012", "Styles are scoped to the declared wrapper and inherit the shared palette.")
def rc012(m: Manifest, _all: list[Manifest]) -> list[Finding]:
    """web/THEMING.md rules 1, 2 and 4. Checks the failures that actually break a palette or
    the rest of the platform: a selector that can match outside the rail (unscoped_selectors),
    redefining an inherited token, and painting a status dot a literal hex or a fixed status
    token so it stops tracking the shared per-mode dot tokens (--chip-missing / --chip-cold /
    --chip-warming / --chip-loaded, one per CHIP_STATES name).
    """
    text, where = m.style_text()
    if not text:
        return [S("RC012", m, "no theme.css and no module.tsx — no styles to scope")]
    wrapper = str(m.data.get("css_wrapper") or "")
    out: list[Finding] = []
    if wrapper and not re.search(rf"[.\s]{re.escape(wrapper)}\b\s*\{{", text):
        out.append(F("RC012", m, f"no '.{wrapper} {{' block — the manifest says styles are "
                                 f"scoped there", where))
    leaks = unscoped_selectors(m, _all)
    if leaks:
        shown = "\n      ".join(f"{s}  <- {why}" for s, why in leaks[:12])
        more = f"\n      ... and {len(leaks) - 12} more" if len(leaks) > 12 else ""
        out.append(F("RC012", m, f"{len(leaks)} selector(s) can match outside the rail: the "
                                 f"federation runtime never unloads a rail's stylesheet, so after "
                                 f"one visit they restyle the shell and every other rail. Scope "
                                 f"each under '.{wrapper or '<css_wrapper>'} ' (or a class prefix "
                                 f"only this rail uses):\n      {shown}{more}", where))
    # Every other stylesheet of the rail's own that the build ships (shipped_stylesheets): the
    # same rule, because remoteEntry.js injects them into the shell just the same.
    for sheet_text, sheet_where, via in shipped_stylesheets(m):
        leaks = unscoped_selectors(m, _all, (sheet_text, sheet_where))
        if leaks:
            shown = "\n      ".join(f"{s}  <- {why}" for s, why in leaks[:12])
            more = f"\n      ... and {len(leaks) - 12} more" if len(leaks) > 12 else ""
            out.append(F("RC012", m, f"{sheet_where} ({via}) ships in the federated bundle, and "
                                     f"{len(leaks)} of its selector(s) can match outside the rail. "
                                     f"remoteEntry.js injects every stylesheet the build emits into "
                                     f"the shell, so a page sheet meant for `npm run dev` restyles "
                                     f"the whole platform. Keep a dev-only sheet out of every module "
                                     f"(link it from a serve-only Vite plugin), or scope it:"
                                     f"\n      {shown}{more}", sheet_where))
    for tok in INHERIT_ONLY_TOKENS:
        if re.search(rf"^\s*--{tok}\s*:", text, re.M):
            out.append(F("RC012", m, f"redefines --{tok}, which must inherit the chosen "
                                     f"palette (THEMING.md rule 2)", where))
    # Driven by the four STATE NAMES, not by a `.dot.` class literal: co-worker names its
    # element .cw-dot, so a `\.dot\.` pattern silently exempted it — it passed this rule while
    # hardcoding the same four hexes as everyone else. Match any selector ending in a
    # dot-ish class plus a state suffix.
    # A literal hex, or one of the FIXED status tokens (var(--good) / --warning / --critical /
    # --info, with or without a fallback): both stop tracking the per-mode dot tokens, and the
    # fixed ones miss 3:1 against the page on many palettes. The lookahead, not \b, ends the
    # name: \b would also match --good-text and --info-soft, which are different tokens.
    dot_state = re.compile(
        rf"[.\w-]*dot[\w-]*\.({'|'.join(CHIP_STATES)})\b[^{{]*\{{[^}}]*?"
        rf"background:\s*(#[0-9a-fA-F]{{3,6}}|var\(--(?:good|warning|critical|info)(?![\w-])[^)]*\))",
        re.S,
    )
    # The fix it names is the per-mode --chip-<state> token, NOT --critical / --warning / --good:
    # those are one colour for both modes and miss 3:1 against the page on many palettes, which
    # is why the shared dots stopped using them (web/THEMING.md rule 4). This message used to
    # recommend exactly them.
    for hit in dot_state.finditer(text):
        painted = hit.group(2)
        how = (f"literal {painted}" if painted.startswith("#")
               else f"{painted}, a fixed status colour that misses 3:1 on many palettes")
        out.append(F("RC012", m, f"'{hit.group(1)}' status dot painted with {how}; paint it "
                                 f"var(--chip-{hit.group(1)}), the shared per-mode dot token, so "
                                 f"it reaches 3:1 on every palette in both modes "
                                 f"(web/THEMING.md rule 4)", where))
    return out
