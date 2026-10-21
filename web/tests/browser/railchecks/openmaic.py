"""openmaic's checks: the framed app follows the shell's light/dark mode, not the OS, from the
frame's first painted frame through the app's boot, and in the app's own theme state on a live
switch.
"""
from __future__ import annotations

from .harness import HARNESS, ORIGIN, NoFixture, Raw, rail, serve


# A stand-in for the upstream OpenMAIC page, served as the rail's iframe. Its script does what
# OpenMAIC's ThemeProvider does (lib/hooks/use-theme.tsx in the upstream checkout): read
# localStorage 'theme' ONCE, fall back to the OS media query, follow that query's changes, and
# touch the 'dark' class on <html> only when its own resolved theme changes. The body colours
# are the measurement: light rgb(255, 255, 255), dark rgb(10, 10, 10).
OM_FRAME_STUB = """<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; background: rgb(255, 255, 255); } html.dark body { background: rgb(10, 10, 10); }
</style></head><body>OpenMAIC stand-in<script>
let theme = 'system';
const stored = localStorage.getItem('theme');
if (stored && ['light', 'dark', 'system'].includes(stored)) theme = stored;
const mq = matchMedia('(prefers-color-scheme: dark)');
let system = mq.matches ? 'dark' : 'light';
let applied = null;
const apply = () => {
  const r = theme === 'system' ? system : theme;
  if (r !== applied) { applied = r; document.documentElement.classList.toggle('dark', r === 'dark'); }
};
apply();
mq.addEventListener('change', () => { system = mq.matches ? 'dark' : 'light'; apply(); });
window.__stubReady = true;
</script></body></html>"""


def check_om_frame_theme(page) -> list[str]:
    """The framed app follows the SHELL's light/dark mode, not the OS: Dark on a
    light OS, Light on a dark OS, a live switch with a class already open, and System."""
    def api(method, path, q, body):
        if path == "/api/capabilities":
            return {"broker": "ok", "models": [], "app": {"reachable": True, "url": "/openmaic/api/app/"}}
        if path in ("/api/app", "/api/app/"):
            return Raw("text/html", OM_FRAME_STUB)
        raise NoFixture(path)

    r = rail("openmaic", api)
    serve(page, r)
    problems: list[str] = []
    measure = """() => {
      const f = document.querySelector('.openmaic iframe.appframe');
      const d = f && f.contentDocument;
      if (!d || !d.body || !f.contentWindow.__stubReady) return null;
      return { dark: d.documentElement.classList.contains('dark'),
               bg: d.defaultView.getComputedStyle(d.body).backgroundColor };
    }"""
    want_bg = {True: "rgb(10, 10, 10)", False: "rgb(255, 255, 255)"}

    def expect(label: str, dark: bool) -> None:
        try:
            page.wait_for_function(f"(() => {{ const m = ({measure})(); return m && m.dark === {str(dark).lower()}; }})()",
                                   timeout=4000)
        except Exception:
            pass
        m = page.evaluate(measure)
        if not m:
            problems.append(f"{label}: the frame never loaded")
        elif m["dark"] != dark or m["bg"] != want_bg[dark]:
            problems.append(f"{label}: frame is {'dark' if m['dark'] else 'light'} "
                            f"(body {m['bg']}), the shell is {'dark' if dark else 'light'}")

    def shell(mode: str | None) -> None:
        page.evaluate("(m) => m ? document.documentElement.setAttribute('data-theme', m)"
                      " : document.documentElement.removeAttribute('data-theme')", mode)

    page.emulate_media(color_scheme="light")
    page.goto(ORIGIN + HARNESS)
    page.wait_for_function("window.__ready === true", timeout=15000)
    shell("dark")
    page.evaluate("window.__mount()")
    expect("OS light, shell Dark, on mount", True)
    shell("light")
    expect("then shell switched to Light with the class open", False)
    shell(None)
    expect("then shell on System (OS light)", False)
    page.emulate_media(color_scheme="dark")
    expect("then the OS goes dark under System", True)

    page.evaluate("window.__unmount()")
    page.evaluate("localStorage.clear()")
    shell("light")
    page.evaluate("window.__mount()")
    expect("OS dark, shell Light, on a fresh mount", False)
    return problems + [f"unanswered: {u}" for u in r.unanswered]


# A stand-in for the upstream page that a real React app takes over, for om-frame-boot. The HTML is
# what the server renders: a light page whose theme button shows 'monitor', as upstream's renders
# before hydration (its ThemeProvider starts from 'system'). boot.js is the app's script; the
# handler holds it back BOOT_DELAY_S, as the walk saw the real one take 2-3 s through two proxies,
# so the check sees the page painted before any of the app's code has run.
OM_BOOT_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body { margin: 0; background: rgb(255, 255, 255); } html.dark body { background: rgb(10, 10, 10); }
</style></head><body><div id="app"><button id="theme-btn" data-icon="monitor">monitor</button></div>
<script type="module" src="/openmaic/api/app/boot.js"></script></body></html>"""
BOOT_DELAY_S = 0.8

# upstream lib/hooks/use-theme.tsx in createElement form, rendered with the rail's own React copy:
# the same initial state ('system', the OS taken as light), the same three effects in the same
# order, add()/remove() on <html>, and the context value { theme, setTheme, resolvedTheme } the
# rail looks for. The button picks its icon from the theme STATE, as upstream's does. ONE change:
# the stored theme is applied 150 ms after the first render rather than in the re-render straight
# after it. Upstream's first render takes the class away for one render (measured against the
# real hook without the rail's hold: two light frames at hydration in one run of six), and the
# wider gap makes a missing hold fail every run instead of by timing luck.
OM_BOOT_JS = """const R = (await import('/openmaic/assets/__REACT__')).default;
const RD = (await import('/openmaic/assets/__REACT_DOM__')).default;
const h = R.createElement;
const Ctx = R.createContext(undefined);
function ThemeProvider({ children }) {
  const [theme, setThemeState] = R.useState('system');
  const [systemTheme, setSystemTheme] = R.useState('light');
  const resolvedTheme = theme === 'system' ? systemTheme : theme;
  R.useEffect(() => {
    const t = setTimeout(() => {
      const stored = localStorage.getItem('theme');
      if (stored && ['light', 'dark', 'system'].includes(stored)) setThemeState(stored);
      setSystemTheme(matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    }, 150);
    return () => clearTimeout(t);
  }, []);
  R.useEffect(() => {
    const root = document.documentElement;
    if (resolvedTheme === 'dark') root.classList.add('dark'); else root.classList.remove('dark');
  }, [resolvedTheme]);
  R.useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const on = () => setSystemTheme(mq.matches ? 'dark' : 'light');
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  const setTheme = (t) => { setThemeState(t); localStorage.setItem('theme', t); };
  window.__appSetTheme = setTheme;
  return h(Ctx.Provider, { value: { theme, setTheme, resolvedTheme } }, children);
}
function Button() {
  const { theme } = R.useContext(Ctx);
  const icon = theme === 'light' ? 'sun' : theme === 'dark' ? 'moon' : 'monitor';
  return h('button', { id: 'theme-btn', 'data-icon': icon }, icon);
}
RD.createRoot(document.getElementById('app')).render(h(ThemeProvider, null, h(Button)));
window.__booted = true;
"""

# Sampled from the FRAME document's own animation-frame callback, which runs after every callback
# of the host page (the rail's included) and before the frame paints: what it reads is what is
# painted, whatever order the host page's callbacks run in.
OM_BOOT_SAMPLER = """(ms) => {
  window.__tl = [];
  const t0 = performance.now();
  const tick = () => {
    const f = document.querySelector('.openmaic iframe.appframe');
    try {
      const d = f && f.contentDocument;
      if (d && d.URL !== 'about:blank' && d.body && d.defaultView) {
        const t = Math.round(performance.now() - t0);
        d.defaultView.requestAnimationFrame(() => window.__tl.push({ t,
          dark: d.documentElement.classList.contains('dark'), booted: !!d.defaultView.__booted }));
      }
    } catch (e) { /* a frame mid-navigation */ }
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""


def check_om_frame_boot(page) -> list[str]:
    """The frame is painted in the shell's mode from its first frame, through the app's boot, and
    the app's OWN theme state follows a live shell switch: its theme
    button's icon moves with the colours, and the user's own pick in the app is left alone."""
    import time

    r = rail("openmaic", lambda *a: None)
    boot_js = (OM_BOOT_JS.replace("__REACT__", r.shared_chunk("react"))
               .replace("__REACT_DOM__", r.shared_chunk("react-dom")))

    def api(method, path, q, body):
        if path == "/api/capabilities":
            return {"broker": "ok", "models": [], "app": {"reachable": True, "url": "/openmaic/api/app/"}}
        if path in ("/api/app", "/api/app/"):
            return Raw("text/html", OM_BOOT_HTML)
        if path == "/api/app/boot.js":
            time.sleep(BOOT_DELAY_S)
            return Raw("text/javascript", boot_js)
        raise NoFixture(path)

    r.api = api
    serve(page, r)
    problems: list[str] = []
    state = """() => { const d = document.querySelector('.openmaic iframe.appframe').contentDocument;
      return { dark: d.documentElement.classList.contains('dark'),
               icon: d.getElementById('theme-btn').dataset.icon }; }"""

    def settle(label: str, dark: bool, icon: str) -> None:
        want = {"dark": dark, "icon": icon}
        try:
            page.wait_for_function(f"(() => {{ try {{ const s = ({state})(); return s.dark === {str(dark).lower()}"
                                   f" && s.icon === '{icon}'; }} catch (e) {{ return false; }} }})()", timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(300)        # a late effect of the app's would undo it here
        got = page.evaluate(state)
        if got != want:
            problems.append(f"{label}: the frame shows {got}, the shell wants {want}")

    def shell(mode: str | None) -> None:
        page.evaluate("(m) => m ? document.documentElement.setAttribute('data-theme', m)"
                      " : document.documentElement.removeAttribute('data-theme')", mode)

    page.emulate_media(color_scheme="light")
    page.goto(ORIGIN + HARNESS)
    page.wait_for_function("window.__ready === true", timeout=15000)
    shell("dark")
    page.evaluate("window.__mount()")
    page.evaluate(OM_BOOT_SAMPLER, 2600)
    page.wait_for_timeout(2900)
    tl = page.evaluate("window.__tl")
    before = [s for s in tl if not s["booted"]]
    after = [s for s in tl if s["booted"]]
    if len(before) < 10 or len(after) < 10:
        problems.append(f"witness: {len(before)} frames sampled before the app's script ran and "
                        f"{len(after)} after; the check needs both windows to mean anything")
    light_before = [s["t"] for s in before if not s["dark"]]
    light_after = [s["t"] for s in after if not s["dark"]]
    if light_before:
        problems.append(f"OS light, shell Dark: the frame painted light in {len(light_before)} frames "
                        f"before the app's script ran (first at {light_before[0]} ms)")
    if light_after:
        problems.append(f"OS light, shell Dark: the frame painted light in {len(light_after)} frames "
                        f"while the app booted (first at {light_after[0]} ms)")
    settle("after the app booted", True, "moon")
    shell("light")
    settle("then shell switched to Light with the app open", False, "sun")
    shell(None)
    settle("then shell on System (OS light)", False, "monitor")
    page.emulate_media(color_scheme="dark")
    settle("then the OS goes dark under System", True, "monitor")
    shell("dark")
    settle("then shell switched to Dark", True, "moon")
    fr = next(f for f in page.frames if "/openmaic/api/app" in f.url)
    fr.evaluate("window.__appSetTheme('light')")
    page.wait_for_timeout(600)
    got = page.evaluate(state)
    if got != {"dark": False, "icon": "sun"}:
        problems.append(f"the user's own pick (Light) in the app was not left alone: the frame shows {got}")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "om-frame-theme": check_om_frame_theme,
    "om-frame-boot": check_om_frame_boot,
}
