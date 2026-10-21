"""Smoke an openmaic-app IMAGE before it replaces the running one.

    <python> rails/openmaic/tools/smoke-app-image.py openmaic-app:candidate-1.1.3
    <python> rails/openmaic/tools/smoke-app-image.py openmaic-app:pre-<date>
    <python> rails/openmaic/tools/smoke-app-image.py --skip-handoff openmaic-app:candidate-1.1.3

<python> is any interpreter with Playwright and its Chromium. A container CLI is needed too:
$CONTAINER_CLI when it is set, else docker when it is on PATH, else podman. The second
form is a control: the v1.0.3 image an upgrade to v1.1.3 keeps as its rollback tag (step 5 of the
README's upgrade runbook), built before the locale edit, must FAIL html-lang, paint-lang,
zh-reader and data-dir; an image from before the request shim (tools/platform-api-prefix.ts)
must also FAIL api-prefix. The third form is the control for the theme check: without the rail's
hand-off the frame paints light under a dark shell, so it must FAIL. The other checks' controls
are images built for the purpose and then deleted: one with Pi chat left on (FAILS pi-chat), one
with no NEXT_BASE_PATH (FAILS serve), and a run with no OLLAMA_* environment (FAILS providers).

A registry-less image name is found under either spelling a build gives it, compose's first:
docker.io/library/<name> (Docker's, and compose's through podman's Docker-compatible API), then
localhost/<name> (`podman build -t <name>`'s). It is never looked up bare, because podman resolves
a short name to localhost/ first and would smoke a stale local build in place of compose's image.
The line that names the image gives the qualified name used, the name asked for, and the engine.

Docker or podman is decided by the ENGINE, from the CLI's own `version` output, so a `docker` CLI
that talks to podman is treated as podman; only when the engine does not answer is the CLI's file
name used.

WHAT IT RUNS. One throwaway container from the image, on its own container network, with its port
published on 127.0.0.1 only, and a fresh named volume at /app/data; besides it, `run --rm` ones
that prepare the volume (a docker engine only) and read the image's own /app/data. No compose project is
created, so nothing here can touch the running stack, and all of it is removed at the end. The app is given
the same environment the platform's compose gives it, except that its LLM endpoint is a host that
does not exist: nothing here asks it to generate, so no model call is made.

WHAT IT CHECKS, one PASS or FAIL line each:
  serve       the app answers under the basePath, and every /_next/ URL in its HTML is under it
  html-lang   the prerendered HTML's visible text has no CJK, and its language toggle reads EN
  paint-lang  every animation frame for 4 s from navigation in an en-US browser: no CJK, toggle EN
  zh-reader   a zh-CN browser: English in the first painted frame, its own language after hydration
  api-prefix  no same-origin request in those page loads left the basePath for the root /api/
  theme       the rail's own src/frameTheme.ts, run from a parent page the way module.tsx runs it:
              under a dark shell on a light OS the framed app paints no light frame in 6 s, the
              app's theme context is found, a shell switch moves it, and the app's own setter works
  pi-chat     /api/chat/pi answers 404 "Pi chat runtime is disabled" (the build kept legacy chat)
  providers   /api/server-providers lists ollama, so the environment compose sets is read
  data-dir    the image's own /app/data belongs to the app's user, and the app's user can write
              /app/data on the smoke's volume: under docker one that started empty and
              root-owned as the live one is; under podman a fresh one, since podman gives a
              volume its owner only at its first mount (an existing volume needs the README's
              one-off chown, whatever the image holds)

Root-asset requests (/logos/ and the rest of rail.json's root_assets) 404 here, because nothing
plays the gateway; they are counted and reported, not failed. Exit 0 only when every check passes.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIL = Path(__file__).resolve().parents[1]
FRAME_THEME = RAIL / "frontend" / "src" / "frameTheme.ts"
BASE = "/openmaic/api/app"
ROOT_ASSETS = json.loads((RAIL / "rail.json").read_text(encoding="utf-8"))["root_assets"]
LABELS = ["CN", "TW", "EN", "JA", "RU", "AR", "BR", "KO", "ES", "FR", "VI", "DE"]
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
ENV = {
    # deploy/docker-compose.yml's openmaic-app environment, with the endpoint pointed nowhere.
    "OLLAMA_BASE_URL": "http://om-smoke-no-llm.invalid:9/v1",
    "OLLAMA_API_KEY": "om-smoke",
    "OLLAMA_MODELS": "@openmaic",
    "DEFAULT_MODEL": "ollama:@openmaic",
    "TZ": "America/Los_Angeles",
}

# Runs at document start in the top frame: one sample per animation frame, for `ms`.
LANG_SAMPLER = """(() => {
  if (window.top !== window) return;
  const ms = %d, t0 = performance.now(), labels = new Set(%s);
  const cjk = /[\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff\\uac00-\\ud7af]/g;
  window.__lang = [];
  const tick = () => {
    const b = document.body;
    if (b) {
      const hits = (b.innerText || '').match(cjk) || [];
      const toggle = [...document.querySelectorAll('button')].map((x) => (x.textContent || '').trim())
        .find((s) => labels.has(s)) || null;
      window.__lang.push({ t: Math.round(performance.now() - t0), cjk: hits.length,
                           sample: hits.slice(0, 6).join(''), toggle });
    }
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
})();"""

# The parent page for the theme check: it plays the shell, on the app's own origin, as the rail
# does. module.tsx's order: the frame is inserted, then (same task) syncFrameTheme writes storage,
# and watchFrameDocuments holds the class on each new frame document.
PARENT_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script src="/__om_smoke/frame-theme.js"></script>
<script>
const ft = window.__ft;
const HOLD_ENV = {
  observe: (root, on) => { const mo = new MutationObserver((r) => on(r.length));
    mo.observe(root, { attributes: true, attributeFilter: ['class'] }); return mo; },
  setTimeout: (fn, ms) => setTimeout(fn, ms), clearTimeout: (id) => clearTimeout(id),
};
const WATCH = { raf: (fn) => requestAnimationFrame(fn), caf: (id) => cancelAnimationFrame(id),
                now: () => performance.now() };
window.__start = (src, handoff, ms) => {
  const f = document.createElement('iframe');
  f.src = src; f.style.width = '1280px'; f.style.height = '800px';
  document.body.appendChild(f);
  if (handoff) {
    ft.syncFrameTheme('dark', false, localStorage, null, null);
    let release = null;
    ft.watchFrameDocuments(() => f, (doc) => {
      if (release) release();
      if (doc.documentElement) release = ft.holdFrameClass(doc.documentElement, () => true, HOLD_ENV);
    }, WATCH);
  }
  window.__frame = f;
  window.__tl = [];
  const t0 = performance.now();
  const tick = () => {
    try {
      const d = f.contentDocument;
      if (d && d.URL !== 'about:blank' && d.body && d.defaultView) {
        const t = Math.round(performance.now() - t0);
        d.defaultView.requestAnimationFrame(() => window.__tl.push({ t,
          dark: d.documentElement.classList.contains('dark') }));
      }
    } catch (e) { /* a frame mid-navigation */ }
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
</script></body></html>"""


def sh(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check, timeout=timeout)


def container_cli() -> str:
    """The container CLI every command here goes through: $CONTAINER_CLI when it is set, else
    docker when it is on PATH, else podman.

    It was `docker`, spelled out at each call, so on a box with only podman the tool died on its
    first command. The two take the same arguments for everything this tool runs (image inspect,
    network, volume, run, exec, logs, rm), so the choice is only which binary is called."""
    chosen = os.environ.get("CONTAINER_CLI", "").strip()
    if chosen:
        return chosen
    return "docker" if shutil.which("docker") else "podman"


_IMAGE_ID = re.compile(r"(sha256:)?[0-9a-f]{12,64}")


def image_candidates(name: str) -> list[str]:
    """The names to look `name` up under, in order, so either spelling a build gives finds it.

    Compose's `openmaic-app:latest` is docker.io/library/openmaic-app:latest to Docker and, on a
    podman box, to the Docker-compatible API compose goes through, while `podman build -t
    openmaic-app:x` stores localhost/openmaic-app:x. A registry-less name is never looked up as
    given: podman resolves a short name to localhost/ FIRST, so a stale localhost/openmaic-app
    from an older bare-name build was smoked in place of compose's image, under the name asked
    for (the delta audit, 2026-10-15). It is qualified, compose's spelling first. A name already
    qualified with either is tried as given, then as the other; one with any other registry or a
    namespace, or an image id, is taken as given."""
    for prefix in ("docker.io/library/", "localhost/"):
        if name.startswith(prefix):
            bare = name[len(prefix):]
            break
    else:
        if "/" in name or _IMAGE_ID.fullmatch(name):
            return [name]
        bare = name
    qualified = [f"docker.io/library/{bare}", f"localhost/{bare}"]
    return list(dict.fromkeys([name, *qualified])) if "/" in name else qualified


def resolve_image(cli: str, name: str) -> tuple[str, dict] | None:
    """The first of image_candidates(name) the CLI holds, with its inspect record."""
    for candidate in image_candidates(name):
        p = sh(cli, "image", "inspect", candidate, check=False)
        if p.returncode == 0:
            try:
                return candidate, json.loads(p.stdout)[0]
            except (ValueError, IndexError, KeyError):
                continue
    return None


def is_podman(cli: str) -> bool:
    """Whether the ENGINE behind `cli` is podman, as its own `version` says: podman's client and
    a docker CLI on podman's Docker-compatible API both print a "Podman Engine" component, Docker
    prints none. It was the CLI's file name alone, so a `docker` that talks to podman took
    Docker's volume preparation, which spends a podman volume's one-time ownership and brings
    back the false data-dir failure (the delta audit, 2026-10-15). An engine that answers without
    naming podman is Docker; one that does not answer at all falls back to the file name."""
    try:
        p = sh(cli, "version", check=False, timeout=60)
    except (OSError, subprocess.SubprocessError):
        p = None
    said = (p.stdout or "") if p is not None else ""
    if re.search(r"\bpodman\b", said, re.I):
        return True
    if p is not None and p.returncode == 0 and said.strip():
        return False
    return Path(cli).name.lower().startswith("podman")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, method: str = "GET", body: bytes | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def frame_theme_js() -> str:
    """The rail's src/frameTheme.ts as a classic script: node strips the types, the exports become
    plain declarations, and the module's names are put on window.__ft."""
    code = sh("node", "-e", "const m=require('node:module'),fs=require('node:fs');"
              "process.stdout.write(m.stripTypeScriptTypes(fs.readFileSync(process.argv[1],'utf8')))",
              str(FRAME_THEME)).stdout
    names = re.findall(r"^export (?:function|const) (\w+)", code, re.M)
    code = re.sub(r"^export ", "", code, flags=re.M)
    return f"(() => {{\n{code}\nwindow.__ft = {{ {', '.join(names)} }};\n}})();"


class Smoke:
    def __init__(self, image: str, skip_handoff: bool, cli: str, podman: bool) -> None:
        """`podman` is whether the engine behind `cli` is podman (is_podman, asked once)."""
        self.image, self.skip_handoff, self.cli, self.podman = image, skip_handoff, cli, podman
        self.name = f"om-smoke-{os.getpid()}"
        self.port = free_port()
        self.origin = f"http://127.0.0.1:{self.port}"
        self.results: list[tuple[str, bool, str]] = []

    def record(self, check: str, ok: bool, detail: str) -> None:
        self.results.append((check, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {check:11} {detail}", flush=True)

    # --- container lifecycle ---------------------------------------------------------------
    def start(self) -> str | None:
        """Start the container and wait for the app. None when it answers 200 under the
        basePath, else what it did instead (recorded as the serve check's failure)."""
        net, vol = self.name + "-net", self.name + "-data"
        sh(self.cli, "network", "create", net)
        sh(self.cli, "volume", "create", vol)
        if not self.podman:
            # The live volume's state: made for an image with no /app/data, so empty and
            # root:root. Docker copies the image's /app/data owner onto an EMPTY volume at every
            # mount, so this is what an upgrade meets there.
            sh(self.cli, "run", "--rm", "--user", "0", "--entrypoint", "sh", "-v", f"{vol}:/mnt",
               self.image, "-c", "chown root:root /mnt && chmod 755 /mnt")
        # Not on podman: it gives a volume its owner once, at the volume's FIRST mount, and a
        # preparing mount would spend that, so the app's /app/data then stayed root:root
        # whatever the image held (the data-dir failure of 2026-10-14). There the app's own
        # mount is the first, as on a fresh install; an existing volume keeps whatever owner it
        # has, and the README gives the one-off chown.
        env = [a for k, v in ENV.items() for a in ("-e", f"{k}={v}")]
        sh(self.cli, "run", "-d", "--name", self.name, "--network", net,
           "-p", f"127.0.0.1:{self.port}:3000", "-v", f"{vol}:/app/data", *env, self.image)
        deadline = time.monotonic() + 120
        last = "no answer"
        while time.monotonic() < deadline:
            try:
                status = get(self.origin + BASE + "/")[0]
                if status == 200:
                    return None
                last = str(status)
                if status == 404 and get(self.origin + "/")[0] == 200:
                    return (f"404 under {BASE}/ while / answers 200: the image was built without "
                            "NEXT_BASE_PATH")
            except OSError:
                pass
            time.sleep(1)
        logs = sh(self.cli, "logs", self.name, check=False)
        return (f"no 200 under {BASE}/ within 120 s (last: {last}); log tail: "
                + " | ".join((logs.stdout + logs.stderr).strip().splitlines()[-3:]))

    def stop(self) -> None:
        sh(self.cli, "rm", "-f", self.name, check=False)
        sh(self.cli, "network", "rm", self.name + "-net", check=False)
        sh(self.cli, "volume", "rm", self.name + "-data", check=False)

    # --- checks ------------------------------------------------------------------------------
    def check_html(self) -> None:
        status, html = get(self.origin + BASE + "/")
        urls = re.findall(r'(?:src|href)="([^"]+)"', html)
        nexts = [u for u in urls if "/_next/" in u]
        stray = [u for u in nexts if not u.startswith(BASE + "/_next/")]
        self.record("serve", status == 200 and bool(nexts) and not stray,
                    f"{status}, {len(nexts)} /_next/ URLs, {len(stray)} outside {BASE}"
                    + (f": {stray[:3]}" if stray else ""))
        text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", " ", text)
        hits = CJK.findall(text)
        toggles = re.findall(r">\s*(" + "|".join(LABELS) + r")\s*(?:<!-- -->)?\s*</button>", html)
        self.record("html-lang", not hits and toggles[:1] == ["EN"],
                    f"{len(hits)} CJK characters in visible text ({''.join(hits[:8])!r}), "
                    f"toggle {toggles[:1] or 'not found'}")

    def sample_language(self, browser, locale: str, ms: int) -> tuple[list[dict], list[str]]:
        ctx = browser.new_context(locale=locale, color_scheme="light")
        ctx.add_init_script(LANG_SAMPLER % (ms, json.dumps(LABELS)))
        page = ctx.new_page()
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(self.origin + BASE + "/", wait_until="commit")
        page.wait_for_timeout(ms + 800)
        samples = page.evaluate("window.__lang || []")
        ctx.close()
        return samples, requests

    def check_language(self, browser) -> list[str]:
        samples, reqs = self.sample_language(browser, "en-US", 4000)
        cjk = [s for s in samples if s["cjk"]]
        toggles = sorted({s["toggle"] for s in samples if s["toggle"]})
        first = samples[0]["t"] if samples else None
        self.record("paint-lang", len(samples) >= 30 and not cjk and toggles == ["EN"],
                    f"{len(samples)} frames sampled from {first} ms, {len(cjk)} with CJK"
                    + (f" (first at {cjk[0]['t']} ms: {cjk[0]['sample']!r})" if cjk else "")
                    + f", toggles seen {toggles}")
        zh, zreqs = self.sample_language(browser, "zh-CN", 8000)
        zcjk = [s for s in zh if s["cjk"]]
        ztoggles = [s["toggle"] for s in zh if s["toggle"]]
        english_first = bool(zh) and not zh[0]["cjk"]
        own_after = bool(zcjk) and "CN" in ztoggles
        self.record("zh-reader", english_first and own_after,
                    f"{len(zh)} frames; first painted frame {'English' if english_first else 'NOT English'}"
                    + (f"; Chinese from {zcjk[0]['t']} ms, toggle CN" if own_after else
                       "; never switched to the reader's Chinese"))
        return reqs + zreqs

    def check_api_prefix(self, requests: list[str]) -> None:
        same = [u for u in requests if u.startswith(self.origin)]
        paths = [u[len(self.origin):] for u in same]
        escaped = sorted({p for p in paths if p.startswith("/api/")})
        under = [p for p in paths if p.startswith(BASE + "/api/")]
        roots = sorted({p for p in paths if any(p.startswith(a) for a in ROOT_ASSETS)})
        self.record("api-prefix", not escaped and bool(under),
                    f"{len(under)} requests to {BASE}/api/, {len(escaped)} to the root /api/"
                    + (f": {escaped[:4]}" if escaped else "")
                    + f"; {len(roots)} root-asset paths (the gateway's to serve)")

    def check_theme(self, browser) -> None:
        ctx = browser.new_context(locale="en-US", color_scheme="light")
        js = frame_theme_js()
        ctx.route(self.origin + "/__om_smoke/parent.html",
                  lambda r: r.fulfill(status=200, content_type="text/html", body=PARENT_HTML))
        ctx.route(self.origin + "/__om_smoke/frame-theme.js",
                  lambda r: r.fulfill(status=200, content_type="text/javascript", body=js))
        page = ctx.new_page()
        page.goto(self.origin + "/__om_smoke/parent.html")
        page.evaluate("([src, h, ms]) => window.__start(src, h, ms)",
                      [BASE + "/", not self.skip_handoff, 6000])
        page.wait_for_timeout(6600)
        tl = page.evaluate("window.__tl")
        light = [s["t"] for s in tl if not s["dark"]]
        found = page.evaluate("""() => { const d = window.__frame.contentDocument;
          const app = window.__ft.findAppTheme(d.body);
          return app ? { theme: app.theme, resolved: app.resolvedTheme } : null; }""")
        problems = []
        if len(tl) < 30:
            problems.append(f"only {len(tl)} frame samples")
        if light:
            problems.append(f"{len(light)} light frames under a dark shell (first at {light[0]} ms)")
        if not found:
            problems.append("findAppTheme found no theme context in the app")
        else:
            # A shell switch to Light, as module.tsx makes it: storage, class and the app's setter.
            page.evaluate("""() => { const d = window.__frame.contentDocument;
              window.__ft.syncFrameTheme('light', false, localStorage, d.documentElement,
                                         window.__ft.findAppTheme(d.body)); }""")
            page.wait_for_timeout(600)
            after = page.evaluate("""() => { const d = window.__frame.contentDocument;
              const app = window.__ft.findAppTheme(d.body);
              return { dark: d.documentElement.classList.contains('dark'),
                       stored: localStorage.getItem('theme'), theme: app && app.theme }; }""")
            if after != {"dark": False, "stored": "light", "theme": "light"}:
                problems.append(f"after a switch to Light the frame shows {after}")
            # The app's OWN setter: nothing else touches the class now, so only it can add it.
            page.evaluate("""() => window.__ft.findAppTheme(window.__frame.contentDocument.body)
              .setTheme('dark')""")
            page.wait_for_timeout(600)
            mine = page.evaluate("""() => window.__frame.contentDocument.documentElement
              .classList.contains('dark')""")
            if not mine:
                problems.append("the app's own setTheme('dark') did not put the class on")
        ctx.close()
        self.record("theme", not problems,
                    "; ".join(problems) if problems else
                    f"{len(tl)} frames, 0 light; context found ({found}); switch and setter work")

    def check_routes(self) -> None:
        status, body = get(self.origin + BASE + "/api/chat/pi", "POST", b"{}")
        self.record("pi-chat", status == 404 and "Pi chat runtime is disabled" in body,
                    f"{status} {body[:120]!r}")
        status, body = get(self.origin + BASE + "/api/server-providers")
        self.record("providers", status == 200 and '"ollama"' in body, f"{status}, ollama "
                    + ("listed" if '"ollama"' in body else "NOT listed"))

    def check_data_dir(self) -> None:
        """Two halves, both required. The IMAGE's own /app/data (read with no volume over it)
        belongs to the app's user: that is what either runtime hands a fresh volume, and the
        half a volume-only reading cannot show. Then the app's user can write the volume."""
        img = sh(self.cli, "run", "--rm", "--entrypoint", "stat", self.image, "-c", "%U",
                 "/app/data", check=False)
        p = sh(self.cli, "exec", self.name, "sh", "-c",
               "id -un; stat -c '%U:%G %a' /app/data; touch /app/data/.om-smoke && rm /app/data/.om-smoke"
               " && echo writable", check=False)
        out = " ".join(p.stdout.split())
        user = p.stdout.split()[0] if p.stdout.split() else ""
        owner = img.stdout.strip() if img.returncode == 0 else ""
        volume = ("fresh volume (podman keeps an existing one's owner)" if self.podman
                  else "empty root-owned volume, as the live one is")
        self.record("data-dir", bool(user) and owner == user
                    and p.returncode == 0 and out.endswith("writable"),
                    f"image /app/data owner {owner or 'none: ' + img.stderr.strip()[:80]}; "
                    f"{volume}: {out} {p.stderr.strip()[:120]}".strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="the openmaic-app image to smoke, e.g. openmaic-app:candidate-1.1.3")
    ap.add_argument("--skip-handoff", action="store_true",
                    help="the theme check's control: run it without the rail's frameTheme hand-off")
    args = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    cli = container_cli()
    if not shutil.which(cli):
        print(f"no container CLI: {cli!r} is not on PATH. Install docker or podman, or set "
              "CONTAINER_CLI to the one this box has.")
        return 2
    found = resolve_image(cli, args.image)
    if found is None:
        print(f"no image {args.image!r} via {cli}; tried "
              + ", ".join(image_candidates(args.image)))
        return 2
    image, inspect = found
    podman = is_podman(cli)
    print(f"image {image}  id {inspect['Id']}  {inspect['Size'] / 1e6:.0f} MB  "
          f"created {inspect['Created'][:19]}  (via {cli}, a {'podman' if podman else 'docker'} "
          "engine)" + (f"  [asked for {args.image}]" if image != args.image else ""))
    s = Smoke(image, args.skip_handoff, cli, podman)
    try:
        problem = s.start()
        print(f"container {s.name} on {s.origin}{BASE}/")
        if problem:
            # Nothing else can be checked on an app that is not serving under the basePath.
            s.record("serve", False, problem)
        else:
            s.check_html()
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                try:
                    s.check_api_prefix(s.check_language(browser))
                    s.check_theme(browser)
                finally:
                    browser.close()
            s.check_routes()
            s.check_data_dir()
        logs = sh(cli, "logs", s.name, check=False)
        noted =[ln for ln in (logs.stdout + logs.stderr).splitlines()
                 if re.search(r"warn|error", ln, re.I)][:8]
        if noted:
            print("startup log, warnings and errors:\n    " + "\n    ".join(noted))
    finally:
        s.stop()
    bad = [c for c, ok, _ in s.results if not ok]
    print(f"\n{len(s.results) - len(bad)}/{len(s.results)} checks passed"
          + (f"; FAILED: {', '.join(bad)}" if bad else ""))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
