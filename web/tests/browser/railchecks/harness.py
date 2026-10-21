"""The shared harness for railcheck.py: everything a check uses that is not one rail's.

The host page that mounts a rail's `./module` (harness_html), serving a rail's built dist and its
fixtures through `page.route` (serve, mount, rail), the XSS payload and the test that it rendered
inert (PAYLOAD, assert_inert), the stylesheet-leak probe (css_leaks), and the shell harness, which
serves the platform shell's own built bundle with the rails it mounts (Shell, serve_shell,
shell_up). Each rail's fixtures and checks are in railchecks/<rail>.py.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit


REPO = Path(__file__).resolve().parents[4]
#: Where a frontend lives when it is not rails/<id>/frontend.
FRONTEND_AT = {"shell": ("apps", "platform", "frontend"),
               "edu-suite": ("rails", "edu-suite", "apps", "dashboard", "frontend")}


def frontend_dir(name: str) -> Path:
    return REPO.joinpath(*FRONTEND_AT.get(name, ("rails", name, "frontend")))


def published_tree() -> bool:
    """web/ ships in the public snapshot, which withholds several rails and tools/publish.py
    itself: that file's absence is the tell (services/broker/tests/test_role_map.py)."""
    return not (REPO / "tools" / "publish.py").is_file()

ORIGIN = "http://rail.test"
HARNESS = "/__harness.html"
WEBCORE_CSS = "/__webcore.css"      # web/src/styles.css, which the shell always has loaded

# Computed properties a leaked rule visibly changes. Width is left out: it follows the
# container, not the element's own rules.
PROBE_PROPS = ["display", "position", "padding-top", "padding-left", "margin-top", "margin-left",
               "font-size", "font-weight", "font-family", "border-top-width", "border-top-style",
               "border-radius", "background-color", "color", "text-align", "text-transform",
               "letter-spacing", "column-count", "list-style-type", "height", "opacity", "cursor",
               "box-shadow"]

# The page itself: <html>, <body> and #root, the elements a rail's GLOBAL rule (html, body,
# :root, #root) restyles. bouquet's and ai-playground's standalone-dev sheet shipped in the
# federated bundle and inset the whole shell 20px / 18px through exactly these; the class
# probes could not see it, since a classless selector yields no probe. Height is left out: #root
# grows when the rail mounts into it. min-height and color-scheme are what those rules set.
GLOBAL_PROPS = [p for p in PROBE_PROPS if p != "height"] + ["min-height", "color-scheme",
                                                             "overflow-x", "overflow-y"]

# Bare elements outside the rail, for a rail rule like `button {}` or `h3 {}` (no class, so no
# class probe either). Each carries its tag as its probe name.
ELEMENT_PROBES = ('<h3 data-probe="&lt;h3&gt;">probe</h3><p data-probe="&lt;p&gt;">probe</p>'
                  '<a href="#" data-probe="&lt;a&gt;">probe</a>'
                  '<button type="button" data-probe="&lt;button&gt;">probe</button>'
                  '<input data-probe="&lt;input&gt;"><input type="radio" data-probe="&lt;radio&gt;">'
                  '<select data-probe="&lt;select&gt;"></select><label data-probe="&lt;label&gt;">probe</label>'
                  '<ul><li data-probe="&lt;li&gt;">probe</li></ul>'
                  '<table><tbody><tr><td data-probe="&lt;td&gt;">probe</td></tr></tbody></table>')


def shell_tokens() -> list[str]:
    """Every custom property web-core's sheet declares: a rail's `:root { --page: ... }` (or
    finance's --good / --warning) overrides the shell's value for the whole session."""
    css = (REPO / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"(--[A-Za-z0-9_-]+)\s*:", css)))


# --- the harness page -------------------------------------------------------------------------

def harness_html(base: str, react: str, react_dom: str, probes: list[str] | None = None) -> str:
    """A host page that mounts `./module` from the remote, as the shell's RailFrame does.

    The share scope is left EMPTY, so the remote falls back to its own react/react-dom chunks;
    the page imports those same chunk URLs, so it renders with the very React instance the
    module's hooks were bound to (two copies would throw "Invalid hook call").

    `probes` are class lists for elements placed OUTSIDE the rail's root, standing in for the
    shell's and other rails' markup. Their computed style is recorded before the remote is
    imported (its stylesheet is injected by get('./module')) as window.__shellOnly, and
    window.__snap() reads it again later: a difference is a rail rule styling the platform.
    With any `probes` list (even an empty one), the bare-element probes are placed too. The
    page itself (<html>, <body>, #root) and web-core's tokens are recorded the same way, as
    window.__globalOnly, and read again by window.__snapGlobal().
    """
    probe_html = "".join(f'<div class="{c}" data-probe="{c}">probe</div>' for c in (probes or []))
    if probes is not None:
        probe_html += ELEMENT_PROBES
    return f"""<!doctype html><html data-theme="light"><head><meta charset="utf-8">
<link rel="stylesheet" href="{WEBCORE_CSS}"></head>
<body><div id="__probes">{probe_html}</div><div id="root"></div><script type="module">
window.__xss = [];
const PROPS = {json.dumps(PROBE_PROPS)};
const GPROPS = {json.dumps(GLOBAL_PROPS)};
const TOKENS = {json.dumps(shell_tokens())};
window.__snap = () => Object.fromEntries([...document.querySelectorAll('#__probes [data-probe]')].map((el) => {{
  const cs = getComputedStyle(el);
  return [el.dataset.probe, Object.fromEntries(PROPS.map((p) => [p, cs.getPropertyValue(p)]))];
}}));
// The page's own elements and the shell's tokens, read the same way: see GLOBAL_PROPS.
window.__snapGlobal = () => {{
  const out = {{}};
  for (const [k, el] of [['html', document.documentElement], ['body', document.body], ['#root', document.getElementById('root')]]) {{
    const cs = getComputedStyle(el);
    out[k] = Object.fromEntries(GPROPS.map((p) => [p, cs.getPropertyValue(p)]));
  }}
  const rs = getComputedStyle(document.documentElement);
  out[':root tokens'] = Object.fromEntries(TOKENS.map((t) => [t, rs.getPropertyValue(t).trim()]));
  return out;
}};
await new Promise((ok) => {{ const l = document.querySelector('link[href="{WEBCORE_CSS}"]'); l.sheet ? ok() : l.addEventListener('load', ok); }});
window.__shellOnly = window.__snap();
window.__globalOnly = window.__snapGlobal();
const R = (await import('/{base}/assets/{react}')).default;
const RD = (await import('/{base}/assets/{react_dom}')).default;
const remote = await import('/{base}/assets/remoteEntry.js');
remote.init({{}});
// get() hands back the component itself or, when the chunk has other exports, its namespace.
const got = (await remote.get('./module'))();
const Mod = typeof got === 'function' ? got : got.default;
let root = null;
window.__mount = () => {{ root = RD.createRoot(document.getElementById('root')); root.render(R.createElement(Mod)); }};
window.__unmount = () => {{ if (root) root.unmount(); root = null; }};
window.__ready = true;
</script></body></html>"""


# --- serving a dist + fixtures ----------------------------------------------------------------

@dataclass
class Call:
    method: str
    path: str
    query: dict[str, list[str]]
    body: Any


Api = Callable[[str, str, dict[str, list[str]], Any], Any]


@dataclass
class Rail:
    name: str
    frontend: Path
    base: str
    api: Api
    calls: list[Call] = field(default_factory=list)
    unanswered: list[str] = field(default_factory=list)
    #: The build output under `frontend`. edu-suite's dashboard builds two bundles from one
    #: source tree: `dist` (edu-suite) and `dist-iep` (IEP Present Levels, `npm run build:iep`).
    dist_name: str = "dist"

    @property
    def dist(self) -> Path:
        return self.frontend / self.dist_name

    def shared_chunk(self, pkg: str) -> str:
        # Vite's hash is 8 characters, which is what keeps "react" from also matching "react-dom".
        pat = re.compile(rf"__federation_shared_{re.escape(pkg)}-[A-Za-z0-9_-]{{8}}\.js")
        hits = sorted(f.name for f in (self.dist / "assets").iterdir() if pat.fullmatch(f.name))
        if len(hits) != 1:
            raise SystemExit(f"{self.name}: expected one __federation_shared_{pkg} chunk, found {len(hits)}")
        return hits[0]


class NoFixture(Exception):
    pass


@dataclass
class Raw:
    """A fixture answer that is not JSON (an SSE stream)."""
    content_type: str
    body: str


def serve(page, rail: Rail, probes: list[str] | None = None) -> None:
    react = rail.shared_chunk("react")
    react_dom = rail.shared_chunk("react-dom")
    html = harness_html(rail.base, react, react_dom, probes)

    def handler(route):
        req = route.request
        u = urlsplit(req.url)
        if u.path == HARNESS:
            return route.fulfill(status=200, content_type="text/html", body=html)
        if u.path == WEBCORE_CSS:
            return route.fulfill(status=200, content_type="text/css",
                                 body=(REPO / "web" / "src" / "styles.css").read_bytes())
        prefix = f"/{rail.base}/assets/"
        if u.path.startswith(prefix):
            f = rail.dist / "assets" / u.path[len(prefix):]
            if f.is_file():
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if f.suffix == ".js":
                    ctype = "text/javascript"
                return route.fulfill(status=200, content_type=ctype, body=f.read_bytes())
            return route.fulfill(status=404, body="no such asset")
        api_prefix = f"/{rail.base}"
        path = u.path[len(api_prefix):] if u.path.startswith(api_prefix + "/api/") else u.path
        if path.startswith("/api/"):
            body: Any = None
            raw = req.post_data_buffer      # bytes: a multipart upload is not UTF-8
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body = raw
            q = parse_qs(u.query)
            rail.calls.append(Call(req.method, path, q, body))
            try:
                out = rail.api(req.method, path, q, body)
            except NoFixture:
                rail.unanswered.append(f"{req.method} {path}")
                return route.fulfill(status=404, content_type="application/json", body='{"detail":"no fixture"}')
            status = 200
            if isinstance(out, Raw):
                return route.fulfill(status=200, content_type=out.content_type, body=out.body)
            if isinstance(out, tuple):
                status, out = out
            return route.fulfill(status=status, content_type="application/json", body=json.dumps(out))
        return route.fulfill(status=404, body="outside the harness")

    page.route("**/*", handler)


def mount(page, rail: Rail) -> None:
    page.goto(ORIGIN + HARNESS)
    page.wait_for_function("window.__ready === true", timeout=15000)
    page.evaluate("window.__mount()")


# --- the payload ------------------------------------------------------------------------------

# Every vector here executes when marked's output goes into innerHTML unsanitised (the first run
# of railcheck.py, against the pre-fix bundles, recorded which). The benign half is asserted too:
# a sanitiser that strips EVERYTHING would pass the hostile half, so the bold text and the table
# must still render as elements.
PAYLOAD = """## Assessment

**Benign bold** and a table:

| merchant | amount |
|---|---|
| ACME | 12.00 |

<img src=x onerror="window.__xss.push('img-onerror')">

<svg onload="window.__xss.push('svg-onload')"><circle r="4"/></svg>

<details open ontoggle="window.__xss.push('details-ontoggle')"><summary>s</summary>d</details>

<iframe srcdoc="<script>parent.__xss.push('iframe-srcdoc')</script>"></iframe>

<a id="rawjs" href="javascript:window.__xss.push('raw-js-href')">raw link</a>

[md link](javascript:window.__xss.push('md-js-href'))

<style>body { visibility: hidden }</style>

<form action="https://example.invalid/steal"><input name="password"><button>Sign in</button></form>

<p style="position:fixed;inset:0" class="rb-modal-bg">overlay</p>
"""


def assert_inert(page, container: str, label: str) -> list[str]:
    """Problems found in `container` (a Playwright selector) after the payload rendered into it;
    [] means inert."""
    problems: list[str] = []
    root = page.locator(container).first
    root.locator("strong").first.wait_for(state="attached", timeout=8000)
    page.wait_for_timeout(400)          # let an onerror/onload/ontoggle fire if it is going to
    # el.click(), not a mouse click: a payload that got through may have hidden the page, and a
    # hidden element takes no pointer events. Only anchors: a form button would navigate away.
    root.evaluate("r => { for (const a of r.querySelectorAll('a')) a.click(); }")
    page.wait_for_timeout(200)
    fired = page.evaluate("window.__xss")
    if fired:
        problems.append(f"{label}: script ran: {fired}")
    found = root.evaluate(
        """(root) => {
          const out = [];
          for (const el of root.querySelectorAll('*')) {
            for (const at of el.attributes) {
              if (/^on/i.test(at.name)) out.push(`${el.tagName.toLowerCase()}[${at.name}]`);
              if (at.name === 'style' || at.name === 'class' || at.name === 'id') out.push(`${el.tagName.toLowerCase()}[${at.name}]`);
              if (/^\\s*javascript:/i.test(at.value)) out.push(`${el.tagName.toLowerCase()}[${at.name}=javascript:]`);
            }
            if (/^(img|svg|iframe|style|form|input|button|object|embed|script|details)$/i.test(el.tagName))
              out.push(`<${el.tagName.toLowerCase()}>`);
          }
          if (!root.querySelector('table td')) out.push('MISSING the benign table');
          if (getComputedStyle(document.body).visibility === 'hidden') out.push('a <style> restyled the page');
          return out;
        }"""
    )
    problems += [f"{label}: {f}" for f in found]
    return problems


def rail(name: str, api: Api) -> Rail:
    return Rail(name=name, frontend=frontend_dir(name), base=name, api=api)


def top_level_compounds(css: str, wrapper: str) -> list[str]:
    """The first compound selector of every top-level rule in `css` that does not start at the
    wrapper, as a class list a probe element can carry (`.chip.pick` -> "chip pick").
    @media blocks are entered; @keyframes and other at-rules are skipped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    depth, buf, skip_depth = 0, "", None
    for ch in css:
        if ch == "{":
            sel = buf.strip()
            buf = ""
            if skip_depth is None and sel.startswith("@") and not sel.startswith("@media"):
                skip_depth = depth
            elif skip_depth is None and not sel.startswith("@"):
                for part in sel.split(","):
                    first = re.split(r"[\s>+~]", part.strip(), maxsplit=1)[0]
                    classes = re.findall(r"\.([\w-]+)", first)
                    if classes and classes[0] != wrapper:
                        out.append(" ".join(classes))
            depth += 1
        elif ch == "}":
            depth -= 1
            if skip_depth is not None and depth == skip_depth:
                skip_depth = None
            buf = ""
        elif ch == ";":
            buf = ""
        else:
            buf += ch
    return sorted(set(out))


def css_leaks(page, rail: Rail, own_prefix: str = "") -> list[str]:
    """Mount the rail with a probe for each class list its own stylesheet styles outside the
    wrapper, and report every probe whose computed style changed once the stylesheet loaded.
    Every such class is probed, the rail's own prefix included: a prefix is not a rail's alone
    (meeting-atlas renders `.ma .rb-block`, so an unscoped `.rb-*` rule reaches it).

    GLOBAL rules too: <html>, <body>, #root and web-core's custom properties are read before
    and after (window.__snapGlobal), and so are bare elements outside the rail (ELEMENT_PROBES).
    A classless selector gives no class probe, which is how bouquet's `html, body, #root` and
    `:root { --page }` shipped past this check: it only ever looked at classes.

    `own_prefix` names a class prefix that rail_conformance's RC012 records as this rail's
    alone (bouquet's `bq-`): its rules are the rail's own by that contract, so those probes
    are skipped rather than reported. The generic names and the page are still probed."""
    css = "".join(f.read_text(encoding="utf-8") for f in (rail.dist / "assets").glob("style-*.css"))
    probes = top_level_compounds(css, rail.name)
    # The generic names the audit measured restyling the platform, probed whether or not the
    # rail still has a rule for them, so the check cannot pass by having nothing to look at.
    probes = sorted(set(probes) | {"btn", "chip", "empty", "row", "spinner", "rule", "stars"})
    if own_prefix:
        probes = [p for p in probes if not p.split()[0].startswith(own_prefix + "-")]
    serve(page, rail, probes)
    mount(page, rail)
    page.wait_for_function(
        f"[...document.styleSheets].some((s) => (s.href || '').includes('/{rail.base}/assets/style-'))",
        timeout=8000)
    page.wait_for_timeout(300)
    before = page.evaluate("window.__shellOnly")
    after = page.evaluate("window.__snap()")
    out = []
    for probe, props in sorted(before.items()):
        diff = {k: f"{v} -> {after[probe][k]}" for k, v in props.items() if after[probe][k] != v}
        if diff:
            name = probe if probe.startswith("<") else "." + probe.replace(" ", ".")
            out.append(f"{name} outside the rail changed: {diff}")
    if not before:
        out.append("no probes, so nothing was checked")
    g_before = page.evaluate("window.__globalOnly")
    g_after = page.evaluate("window.__snapGlobal()")
    for target, props in g_before.items():
        diff = {k: f"{v or '(unset)'} -> {g_after[target][k] or '(unset)'}"
                for k, v in props.items() if g_after[target][k] != v}
        if diff:
            out.append(f"the page's {target} changed (a global rule): {diff}")
    if not g_before.get(":root tokens"):
        out.append("no web-core tokens were read, so a :root override could not be seen")
    return out


def mount_in_shell_frame(page, r: Rail) -> None:
    """mount(), inside the frame the shell gives a rail at 1440x900: a 76px app rail on the left,
    and #root standing in for main.pcontent, the one element that scrolls (web/src/styles.css
    locks html and body). A scrollIntoView then has to move the scroller the user has."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(ORIGIN + HARNESS)
    page.wait_for_function("window.__ready === true", timeout=15000)
    page.evaluate("""() => { const r = document.getElementById('root');
        r.className = 'pcontent'; r.style.height = '100vh'; r.style.marginLeft = '76px'; }""")
    page.evaluate("window.__mount()")


# --- contrast, in every palette ----------------------------------------------------------------

def palettes() -> list[str]:
    """Every palette web-core's sheet defines, which is every swatch the theme menu offers."""
    css = (REPO / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\[data-palette='([a-z0-9-]+)'\]", css)))


def set_theme(page, palette: str, mode: str) -> None:
    page.evaluate("([p, m]) => { const h = document.documentElement; h.dataset.palette = p; h.dataset.theme = m; }",
                  [palette, mode])


# Contrast of each `sel` element's words against what is behind them: every ancestor's
# background-color composited bottom up on a 1px canvas, then the element's `color` on top.
# It reads COMPUTED colours, so it is only true of words on a flat fill: a gradient
# (background-image), an opacity or an emoji's own colours never reach it. A check that uses it
# must assert the fill is flat first, or measure pixels (GLYPH_PIXELS). [{sel, ratio}] per sel.
WORD_CONTRAST = """(sels) => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const ctx = cv.getContext('2d', { willReadFrequently: true });
  const paint = (layers) => {
    ctx.globalCompositeOperation = 'copy'; ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, 1, 1);
    ctx.globalCompositeOperation = 'source-over';
    for (const c of layers) { ctx.fillStyle = c; ctx.fillRect(0, 0, 1, 1); }
    return [...ctx.getImageData(0, 0, 1, 1).data.slice(0, 3)];
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  return sels.map((sel) => {
    const el = document.querySelector(sel);
    if (!el) return { sel, missing: true };
    const layers = [];
    for (let e = el; e; e = e.parentElement) layers.unshift(getComputedStyle(e).backgroundColor);
    const back = lum(paint(layers)), text = lum(paint([...layers, getComputedStyle(el).color]));
    return { sel, ratio: (Math.max(back, text) + 0.05) / (Math.min(back, text) + 0.05) };
  });
}"""

# What is actually painted in a screenshot of a small glyph or icon: the most common colour is
# taken as its background, every pixel that differs from it (contrast over 1.15) is ink, and the
# answer is how far the ink's contrast reaches at its 50th, 75th and 90th percentiles. Pixels,
# not computed colours, because a colour emoji ignores `color`: smb's 🎙 and 🔊 were set in the
# button's ink and painted their own grey. Which percentile a check holds to 3:1 is the check's
# call, made against what the owner saw, and its docstring says why: the strongest tenth (p90)
# is the core of a thin outline glyph, whose other pixels are mostly antialiased edge; the
# strongest quarter (p75) is the body of a filled icon, where one dark detail must not carry it.
GLYPH_PIXELS = """async (b64) => {
  const img = new Image(); img.src = 'data:image/png;base64,' + b64; await img.decode();
  const cv = document.createElement('canvas'); cv.width = img.width; cv.height = img.height;
  const ctx = cv.getContext('2d', { willReadFrequently: true }); ctx.drawImage(img, 0, 0);
  const d = ctx.getImageData(0, 0, cv.width, cv.height).data;
  const f = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (i) => 0.2126 * f(d[i]) + 0.7152 * f(d[i + 1]) + 0.0722 * f(d[i + 2]);
  const counts = new Map();
  for (let i = 0; i < d.length; i += 4) { const k = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2]; counts.set(k, (counts.get(k) || 0) + 1); }
  let bg = 0, most = -1;
  for (const [k, n] of counts) if (n > most) { most = n; bg = k; }
  const bl = 0.2126 * f(bg >> 16) + 0.7152 * f((bg >> 8) & 255) + 0.0722 * f(bg & 255);
  const ink = [];
  for (let i = 0; i < d.length; i += 4) {
    const l = lum(i), r = (Math.max(l, bl) + 0.05) / (Math.min(l, bl) + 0.05);
    if (r > 1.15) ink.push(r);
  }
  ink.sort((a, b) => a - b);
  const q = (at) => ink.length ? ink[Math.floor(at * (ink.length - 1))] : 1;
  return { ink: ink.length, p50: q(0.5), p75: q(0.75), p90: q(0.9) };
}"""


def glyph_contrast(page, locator, whole: bool = False) -> dict:
    """GLYPH_PIXELS over `locator`'s content box, its border and padding left out so a button's
    own outline is not counted as its icon; `whole` measures the border box instead, for a
    control whose border IS what shows (an empty checkbox). {ink, p50, p75, p90}."""
    import base64
    box = locator.evaluate("""(e, whole) => { const r = e.getBoundingClientRect(), c = getComputedStyle(e);
        const px = (p) => whole ? 0 : (parseFloat(c[p]) || 0);
        const l = px('borderLeftWidth') + px('paddingLeft'), t = px('borderTopWidth') + px('paddingTop');
        return { x: r.left + l, y: r.top + t,
                 width: r.width - l - px('borderRightWidth') - px('paddingRight'),
                 height: r.height - t - px('borderBottomWidth') - px('paddingBottom') }; }""", whole)
    png = page.screenshot(clip=box)
    return page.evaluate(GLYPH_PIXELS, base64.b64encode(png).decode())


def each_theme(page, measure: Callable[[str, str], list[str]]) -> list[str]:
    """Call `measure(palette, mode)` under every palette in both explicit modes; it returns
    problems as 'what: why'. Problems are grouped by `what`, so 42 themes print one line each."""
    bad: dict[str, list[str]] = {}
    themes = [(p, m) for p in palettes() for m in ("light", "dark")]
    for pal, mode in themes:
        set_theme(page, pal, mode)
        for prob in measure(pal, mode):
            what, _, why = prob.partition(": ")
            bad.setdefault(what, []).append(f"{pal} {mode} {why}")
    return [f"{what}: {len(v)} of {len(themes)} themes fail, e.g. {'; '.join(v[:3])}"
            for what, v in bad.items()]


# --- the shell ---------------------------------------------------------------------------------
#
# The platform shell's own built bundle (apps/platform/frontend/dist), served at the origin root
# the way the gateway serves it, with the rails it mounts served from their own dists. Fixtures
# answer /api/platform/*; the test flips them mid-run (an expired session, a failing unload).

SHELL_DIST = REPO / "apps" / "platform" / "frontend" / "dist"

STATUS_OK = {"broker_reachable": True, "ollama_reachable": True, "heavy_loaded": [], "gpu": None,
             "queue": {"active": 0, "waiting": 0}, "jobs": [],
             "loaded": [{"name": "qwen3:8b", "class": "heavy", "size_vram": 5 * 2**30}]}
THEME = {"palette": "indigo", "mode": "light", "palettes": ["indigo"], "override": None,
         "default": {"palette": "indigo", "mode": "light"}}


@dataclass
class Shell:
    admin: bool = False
    status: Any = field(default_factory=lambda: dict(STATUS_OK))   # a dict, or an int HTTP status
    unload: Any = 200
    calls: list[str] = field(default_factory=list)
    # "METHOD name" -> (status, detail): the gateway refusing that call in its own words.
    refuse: dict[str, tuple[int, str]] = field(default_factory=dict)
    # The Users list the admin pages read; None = owner-one alone, as super-admin.
    users: Any = None
    # That route's catalog and the caller's grantable; None = recipe-book alone, grantable.
    catalog: Any = None
    grantable: Any = None
    workspaces: Any = None
    # The Rails tab's payload; None = a fresh copy of ADMIN_RAILS. A Claude switch (PUT
    # admin/rails/<role>/cli) flips cli_on on every slot of that role here, as the gateway does.
    rails: Any = None
    # The JSON bodies of every Claude switch the page sent, in order: (role, body).
    cli_puts: list[tuple[str, Any]] = field(default_factory=list)
    # The gateway's answer when the broker took a switch but the Rails tab could not be re-read:
    # the switch's own `cli` block and this sentence, no `rails` (main.py admin_set_rail_cli).
    # None answers with the whole tab, as usual.
    cli_reload_error: str | None = None


def serve_shell(page, shell: Shell, rails: dict[str, Rail]) -> None:
    def handler(route):
        req = route.request
        u = urlsplit(req.url)
        path = u.path
        if path in ("/", "/index.html"):
            return route.fulfill(status=200, content_type="text/html", body=(SHELL_DIST / "index.html").read_bytes())
        if path.startswith("/assets/"):
            f = SHELL_DIST / "assets" / path[len("/assets/"):]
            if f.is_file():
                ctype = "text/javascript" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                return route.fulfill(status=200, content_type=ctype, body=f.read_bytes())
            return route.fulfill(status=404, body="no such asset")
        if path.startswith("/api/platform/"):
            shell.calls.append(f"{req.method} {path}")
            name = path[len("/api/platform/"):]
            no = shell.refuse.get(f"{req.method} {name}")
            if no:
                return route.fulfill(status=no[0], content_type="application/json",
                                     body=json.dumps({"detail": no[1]}))
            if name == "admin/workspaces" and req.method == "GET" and shell.workspaces is not None:
                return route.fulfill(status=200, content_type="application/json",
                                     body=json.dumps(shell.workspaces))
            if name == "admin/users" and req.method == "GET":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "users": shell.users or [{"id": 1, "username": "owner-one", "is_admin": True,
                                               "is_superadmin": True, "apps": []}],
                    "catalog": (shell.catalog if shell.catalog is not None
                                else [{"id": "recipe-book", "label": "Recipe Book"}]),
                    "grantable": shell.grantable if shell.grantable is not None else ["recipe-book"]}))
            if name == "admin/broker/tokens" and req.method == "GET":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"tokens": [], "scopes": ["inference", "full"], "shared_token_in_use": False}))
            if name == "apps":
                apps = [{"id": r, "label": r, "icon": "x", "status": "ready"} for r in rails]
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"apps": apps, "user": {"username": "owner-one", "is_admin": shell.admin, "theme": THEME}}))
            if name == "status":
                if isinstance(shell.status, int):
                    return route.fulfill(status=shell.status, content_type="application/json", body='{"detail":"authentication required"}')
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(shell.status))
            if shell.rails is None:
                shell.rails = copy.deepcopy(ADMIN_RAILS)
            if name == "admin/rails":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(shell.rails))
            if name.startswith("admin/rails/") and name.endswith("/cli") and req.method == "PUT":
                role = name[len("admin/rails/"):-len("/cli")]
                body = json.loads(req.post_data or "null")
                shell.cli_puts.append((role, body))
                change = body if isinstance(body, dict) else {}
                # Like the gateway: a body changes the switch only if it sends `enabled`, and the
                # role's Claude model only if it sends `model` (null for the default).
                if "enabled" in change:
                    on = change.get("enabled") is True
                    for r in shell.rails["rails"]:
                        for s in r["slots"]:
                            if s["role"] == role:
                                s["cli_on"] = on
                    live = set(shell.rails["cli"]["enabled_roles"])
                    (live.add if on else live.discard)(role)
                    shell.rails["cli"]["enabled_roles"] = sorted(live)
                if "model" in change:
                    chosen = dict(shell.rails["cli"].get("role_models") or {})
                    if change["model"] is None:
                        chosen.pop(role, None)
                    else:
                        chosen[role] = change["model"]
                    shell.rails["cli"]["role_models"] = chosen
                    for r in shell.rails["rails"]:
                        for s in r["slots"]:
                            if s["role"] == role:
                                s["cli_model"] = change["model"]
                if shell.cli_reload_error is not None:
                    return route.fulfill(status=200, content_type="application/json", body=json.dumps({
                        "cli": shell.rails["cli"], "cli_models": shell.rails["cli_models"],
                        "cli_editable": shell.rails["cli_editable"],
                        "reload_error": shell.cli_reload_error}))
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(shell.rails))
            if name == "admin/schedules":
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(SCHEDULES))
            if name.startswith("admin/schedules/") and name.endswith("/run"):
                return route.fulfill(status=200, content_type="application/json",
                                     body='{"rail":"recipe-book","task_id":"t1","status":"ok"}')
            if name in ("unload", "cancel"):
                if shell.unload != 200:
                    return route.fulfill(status=shell.unload, content_type="application/json", body='{"detail":"the broker refused the unload"}')
                return route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return route.fulfill(status=200, content_type="application/json", body="{}")
        for name, r in rails.items():
            pre = f"/{name}/assets/"
            if path.startswith(pre):
                f = r.dist / "assets" / path[len(pre):]
                if f.is_file():
                    ctype = "text/javascript" if f.suffix == ".js" else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                    return route.fulfill(status=200, content_type=ctype, body=f.read_bytes())
                return route.fulfill(status=404, body="no such asset")
            if path.startswith(f"/{name}/api/"):
                try:
                    out = r.api(req.method, path[len(name) + 1:], parse_qs(u.query), None)
                except NoFixture:
                    return route.fulfill(status=404, content_type="application/json", body="{}")
                return route.fulfill(status=200, content_type="application/json", body=json.dumps(out))
        return route.fulfill(status=404, body="outside the harness")

    page.route("**/*", handler)


# Visibility, settable from the test: document.visibilityState is a getter on the prototype, so an
# own property on the document shadows it, and a dispatched event reaches the listeners.
VISIBILITY = """
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => window.__vis || 'visible' });
Object.defineProperty(document, 'hidden', { configurable: true, get: () => (window.__vis || 'visible') === 'hidden' });
window.__setVis = (v) => { window.__vis = v; document.dispatchEvent(new Event('visibilitychange')); };
"""


def shell_up(page, shell: Shell, rails: dict[str, Rail]) -> None:
    page.add_init_script(VISIBILITY)
    serve_shell(page, shell, rails)
    page.goto(ORIGIN + "/#/" + next(iter(rails)))


def _slot(role: str, label: str, shared_with: list[str] | None = None, *, kind: str = "chat",
          cli_on: bool = False, cli_wired: bool = False, cli_model: str | None = None) -> dict:
    # cli_switchable as the gateway's build_rails_view says it (CLI_SLOT_KINDS: text or vision);
    # cli_unavailable None, as on a broker that sends images and streams to Claude (the cli
    # block below).
    return {"slot": label, "label": label, "role": role, "kind": kind, "env": "X", "default": "m",
            "description": "", "model": "m", "pattern": "m", "installed": True, "upstream": "local",
            "shared_with": shared_with or [], "cli_on": cli_on, "cli_wired": cli_wired,
            "cli_ready": True, "cli_model": cli_model,
            "cli_switchable": kind in ("chat", "vision"), "cli_unavailable": None}


# @edu and @iep are wired and switched on, and @iep has a Claude model of its own; finance-chat
# is a text slot whose rail passes no requester, and finance-vision a vision slot whose rail
# passes none either, so both switches are disabled for that reason. Photo Lab is synthetic: a
# WIRED vision slot, switched off, which gets a switch and a model select like a text slot, and
# an image slot, which gets neither. Study Desk is synthetic too: a WIRED text slot, switched
# off, on a streaming role (STREAM_ROLES), which a broker that streams to Claude treats like
# any other.
ADMIN_RAILS = {
    "rails": [
        {"id": "edu-suite", "label": "EDU-Suite", "icon": "x",
         "slots": [_slot("edu", "Content", ["IEP Goals", "IEP Present Levels"], cli_on=True,
                         cli_wired=True)]},
        {"id": "iep-goals", "label": "IEP Goals", "icon": "x",
         "slots": [_slot("iep", "Draft", cli_on=True, cli_wired=True, cli_model="claude-opus-5-5"),
                   _slot("edu", "Translate", ["EDU-Suite"], cli_on=True, cli_wired=True)]},
        {"id": "finance", "label": "Finance", "icon": "x",
         "slots": [_slot("finance-chat", "Chat"), _slot("finance-vision", "Receipts", kind="vision")]},
        {"id": "photo-lab", "label": "Photo Lab", "icon": "x",
         "slots": [_slot("photo-vision", "Photo reader", kind="vision", cli_wired=True),
                   _slot("photo-icon", "Icons", kind="image")]},
        {"id": "study-desk", "label": "Study Desk", "icon": "x",
         "slots": [_slot("desk-answers", "Answers", cli_wired=True)]},
    ],
    "models": {"local": [{"name": "m", "parameter_size": "8B", "vision": True}]},
    "media": [], "upstreams": [{"name": "local", "url": "", "healthy": True}],
    # `vision` and `streams` as a current broker reports them, normalised by the gateway's
    # _cli_block (only a JSON true counts); broker_streams() serves one that does not stream.
    "cli": {"ready": True, "blockers": [], "switch_blockers": [], "enabled_roles": ["edu", "iep"],
            "roles_source": "file", "roles_file_set": True, "roles_problem": "",
            "role_models": {"iep": "claude-opus-5-5"}, "default_model": "", "vision": True,
            "streams": True},
    "cli_models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
    "cli_editable": True,
}

#: The streaming roles among ADMIN_RAILS' slots: what rails_models.py's STREAM_SLOT_ROLES is to
#: the gateway. The payload carries no per-slot flag; the gateway marks every slot of such a role.
STREAM_ROLES = frozenset({"desk-answers"})

#: main.py's CLI_NO_STREAM, restated so a check asserts what the admin reads.
CLI_NO_STREAM = ("the broker does not say it streams to Claude, so every streamed answer on this "
                 "role stays local")


def broker_streams(payload: dict, streams: bool) -> dict:
    """The Rails-tab payload as the gateway draws it for a broker that does (or does not) say it
    streams to Claude, changed in place and returned: `streams` in the cli block, and
    CLI_NO_STREAM as `cli_unavailable` on every slot of a streaming role (main.py
    _rails_payload). A broker from before streamed CLI calls sends no `streams` key, which the
    gateway reads as false, so `streams=False` is how such a broker looks here. A slot that
    already carries another reason keeps it, as the gateway puts the image reason first."""
    payload["cli"]["streams"] = streams
    for r in payload["rails"]:
        for s in r["slots"]:
            if s["role"] in STREAM_ROLES and s["cli_unavailable"] in (None, CLI_NO_STREAM):
                s["cli_unavailable"] = None if streams else CLI_NO_STREAM
    return payload
SCHEDULES = {"rails": [{"rail": "recipe-book", "icon": "x", "tasks": [{
    "task_id": "t1", "label": "Nightly", "description": "d", "enabled": True,
    "recurrence": {"freq": "daily", "interval": 1, "at": "03:00", "tz": "UTC"},
    "next_run": None, "last_run": None, "last_status": None}]}]}
