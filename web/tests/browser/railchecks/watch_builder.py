"""watch-builder's checks: the canvas inside its pane, the canvas footer captions' contrast, the
live tag in every palette, and the preset button on one line. And the AI jobs (2026-10-09):
design and variations run as background jobs the page polls, so the page must show progress and
partial variants, stop polling on a final state or a 404, cancel, pick a job up again after a
reload, and say whose resources the estimate spends.
And a 409 (2026-10-09 review): a job another tab started is shown, but its design is only
offered, never put over this tab's editor; a refusal that names only a cancelled job is said.
And the lease (2026-10-09): the rail stops a job nobody reads for jobs.LEASE_S, so a hidden tab
keeps reading its job on a keep-alive timer, and a job the rail stopped says why. And the lease
review (2026-10-09): the keep-alive outlives the component (another rail in the same tab), asks
on through an outage longer than the poll's give-up, gives a hung read a deadline, and ends with
its job (final, or refused by the platform). And the second lease review (2026-10-09): the
keep-alive holds only its job's id, so after a sign-out the next viewer is shown nothing of the
last one's job; its read leaves a finished pass on offer to a new tab; a browser without
AbortSignal.timeout reads its job; and with no page mounted, a 401, a 403 and a 404 end it
while an outage does not. And the third lease review (2026-10-09): a page says a job is gone
only if it has shown that job, so the next viewer is not told about the last one's even when
their own read of it fails or is slow; a page that comes back while the rail is down says it
has lost contact, naming no job; and only a read no page shows leaves a finished pass on offer,
so the poll's read, the read as a page mounts and a hidden tab's keep-alive read deliver it.
"""
from __future__ import annotations

import ast
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .harness import REPO, VISIBILITY, Api, NoFixture, Rail, frontend_dir, mount, rail, serve


@dataclass
class WbJobs:
    """The rail's AI job routes, answered from a script.

    Each GET of the job answers the next entry of `views` and the last one repeats; GONE is a
    404, the answer a restarted rail gives. POST of either AI route answers 202 with `views[0]`,
    or, with `refuse` set to "busy" or "finishing", the 409 app._submit answers for that kind,
    naming job-1. DELETE answers `cancelled`. `listing` is GET /api/ai/jobs, what a reloaded
    page reads first. `claude` decides which estimate the page is shown. While `fail` is set, a
    GET of the job answers that status instead (502: the gateway is down; 401: signed out), and
    `served` records it as the number. `marked` counts the GETs that carried the keep-alive's
    ?keepalive=1, and `delivered` turns true once a GET without it has served a final view,
    which is when jobs.get marks a job delivered."""
    views: list[Any]
    cancelled: dict | None = None
    listing: list[dict] = field(default_factory=list)
    claude: bool = False
    refuse: str | None = None
    fail: int | None = None
    gets: int = 0
    posts: int = 0
    deletes: int = 0
    served: list[str] = field(default_factory=list)
    marked: int = 0
    delivered: bool = False

    def answer(self, method: str, path: str, q: dict | None = None):
        if path == "/api/ai/jobs" and method == "GET":
            return {"jobs": self.listing}
        if path in ("/api/ai/design", "/api/ai/variations") and method == "POST":
            self.posts += 1
            if self.refuse:
                return 409, {"detail": {"error": self.refuse, "job_id": "job-1",
                                        "message": WB_REFUSALS[self.refuse]}}
            return 202, self.views[0]
        if path == "/api/ai/estimate":
            return wb_estimate(self.claude)
        if path.startswith("/api/ai/jobs/") and method == "DELETE":
            self.deletes += 1
            return self.cancelled
        if path.startswith("/api/ai/jobs/") and method == "GET":
            keepalive = bool((q or {}).get("keepalive"))
            self.marked += keepalive
            if self.fail is not None:
                self.served.append(str(self.fail))
                return self.fail, {"detail": f"the fixture answers {self.fail}"}
            view = self.views[min(self.gets, len(self.views) - 1)]
            self.gets += 1
            if view is GONE:
                self.served.append("gone")
                return 404, {"detail": "no such job. A restart of this rail loses every job in "
                                       "flight; nothing is running for this id"}
            self.served.append(view["state"])
            if not keepalive and view["state"] in ("done", "failed", "cancelled"):
                self.delivered = True
            return view
        return None


GONE = object()

# app._submit's two 409 messages, in its own words.
WB_REFUSALS = {
    "busy": "You already have an AI job running. It is shown here; cancel it to start another.",
    "finishing": "Your cancelled AI job is still waiting on a model call it sent before the "
                 "cancel; that call finishes and its answer is dropped. You can start another "
                 "once it has returned.",
}


def wb_estimate(claude: bool) -> dict:
    """ai.estimate's two answers, in its own words."""
    if claude:
        return {"model_ref": "@watch-builder-designer", "backend": "claude", "calls": 9,
                "approx_seconds": 360, "holds_gpu": False,
                "note": "Answered by Claude on your own subscription, so this uses no GPU unless "
                        "a call falls back to the local model."}
    return {"model_ref": "@watch-builder-designer", "backend": "ollama", "calls": 9,
            "approx_seconds": 108, "holds_gpu": True,
            "note": "Each call holds the GPU for one generation and releases it."}


def wb_variant(i: int) -> dict:
    """A finished variant: a real preset document under a name the check can find."""
    sys.path.insert(0, str(REPO / "rails" / "watch-builder" / "backend"))
    from watch_builder import presets
    doc = copy.deepcopy(presets.listing()[0]["design"])
    doc["name"] = f"Variant {i + 1}"
    return {"ok": True, "axis": f"Axis {i + 1}", "design": doc}


def wb_jobs_number(name: str) -> float:
    """A number jobs.py defines (LEASE_S, JOB_CEILING_S), read from its source as a literal, so
    a fixture carries the rail's own figure rather than a copy. Not imported: jobs.py reaches
    pydantic through ai.py, and the interpreter that runs these checks has none."""
    path = REPO / "rails" / "watch-builder" / "backend" / "watch_builder" / "jobs.py"
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name
                                                for t in node.targets):
            return float(ast.literal_eval(node.value))
    raise SystemExit(f"watch-builder: jobs.py defines no {name}")


def wb_module_number(name: str) -> float:
    """A number module.tsx defines as `const NAME = <digits>` (KEEPALIVE_MS), read from its
    source, so a check steps the page's clock by the page's own interval."""
    src = (frontend_dir("watch-builder") / "src" / "module.tsx").read_text(encoding="utf-8")
    m = re.search(rf"^const {name} = (\d+)\s*$", src, re.M)
    if not m:
        raise SystemExit(f"watch-builder: module.tsx defines no {name}")
    return float(m.group(1))


def wb_job(state: str, done: int, *, total: int = 3, kind: str = "variations",
           result: Any = None, calls: int | None = None, stopped: str | None = None,
           error: str | None = None) -> dict:
    """One job view, shaped as jobs.py `_view` answers it."""
    unit = "variant" if kind == "variations" else "design"
    return {"job_id": "job-1", "kind": kind, "state": state,
            "progress": {"done": done, "total": total, "unit": unit},
            "calls": calls if calls is not None else done + (state == "running"),
            "partial": [wb_variant(i) for i in range(done)] if kind == "variations" else [],
            "result": result, "error": error, "created": 0, "started": 0, "finished": None,
            "ceiling_s": wb_jobs_number("JOB_CEILING_S"), "stopped": stopped,
            "lease_s": wb_jobs_number("LEASE_S")}


def watch_builder_api(ai_jobs: WbJobs | None = None) -> Api:
    """The rail's own preset listing, and real captured boxes for /api/validate.

    presets.py is plain Python, so `listing()` is the very list GET /api/presets serves. The
    boxes and screen are what the rail's canvas-shot tool captured from a running rail's
    /api/validate (frontend/tools/canvas-shot/fixtures.json), keyed by design name; a design
    with no capture gets the real screen and no boxes. Every preset and capture targets emery.
    `ai_jobs` answers the AI routes; without it the viewer has no jobs."""
    sys.path.insert(0, str(REPO / "rails" / "watch-builder" / "backend"))
    from watch_builder import presets
    listing = presets.listing()
    shots = json.loads((frontend_dir("watch-builder") / "tools" / "canvas-shot" / "fixtures.json")
                       .read_text(encoding="utf-8"))
    boxes = {f["design"]["name"]: f["boxes"] for f in shots.values()}
    screens = {f["platform"]: f["screen"] for f in shots.values()}

    def api(method, path, q, body):
        if ai_jobs is not None:
            got = ai_jobs.answer(method, path, q)
            if got is not None:
                return got
        elif path == "/api/ai/jobs":
            return {"jobs": []}
        if path == "/api/capabilities":
            return {"broker": "ok", "models": [], "build_available": False, "sdk_version": None,
                    "platforms": [{"name": n, "label": n, "width": s["w"], "height": s["h"],
                                   "round": s["round"], "colors": s["colors"],
                                   "quick_view": s["quick_view"], "has_leco60": False}
                                  for n, s in screens.items()],
                    "ai": {"designer_model": "none", "features": [], "variation_default": 9,
                           "variation_max": 24}}
        if path == "/api/presets":
            return {"presets": listing}
        if path == "/api/designs":
            return {"designs": []}
        if path == "/api/images":
            return {"images": []}
        if path == "/api/validate" and method == "POST":
            if any(p not in screens for p in body["platforms"]):
                raise NoFixture(f"{path} for {body['platforms']}")
            return {"errors": 0, "warnings": 0, "findings": [], "semantics": [], "colour_shift": [],
                    "boxes": {p: boxes.get(body["name"], []) for p in body["platforms"]},
                    "screens": {p: screens[p] for p in body["platforms"]}}
        raise NoFixture(path)

    return api


def wb_up(page, width: int, preset: str | None = None, ai_jobs: WbJobs | None = None) -> Rail:
    page.set_viewport_size({"width": width, "height": 1000})
    r = rail("watch-builder", watch_builder_api(ai_jobs))
    serve(page, r)
    mount(page, r)
    page.locator(".wb-canvas").wait_for(timeout=10000)
    if preset:
        page.get_by_role("button", name="Presets", exact=True).click()
        page.locator(".wb-preset", has_text=preset).get_by_role("button", name="Use this").click()
        page.locator(".wb-canvas .wb-el").first.wait_for(timeout=8000)
    return r


def wb_fit_viewport(page, selector: str, width: int) -> None:
    """Grow the viewport until `selector` is wholly inside it. The harness page has no scrollbar
    (web-core sets overflow: hidden on html and body), and elementFromPoint answers nothing
    outside the viewport."""
    bottom = page.evaluate(f"document.querySelector('{selector}').getBoundingClientRect().bottom")
    page.set_viewport_size({"width": width, "height": max(1000, int(bottom) + 40)})


# Problems with the canvas's placement at one zoom, as sentences ([] means it sits in its pane).
WB_CANVAS_FIT = """({z, w}) => {
  const out = [];
  const R = (el) => el.getBoundingClientRect();
  const pane = document.querySelector('.wb-canvas-pane');
  const wrap = document.querySelector('.wb-canvas-wrap');
  const cv = document.querySelector('.wb-canvas');
  const insp = document.querySelector('.wb-cols > .wb-pane:nth-child(2)');
  const root = document.querySelector('.watch-builder');
  wrap.scrollLeft = 0;
  const p = R(pane), c = R(cv), i = R(insp);
  // The witness: the zoom took. A canvas that never grew would pass everything below.
  if (Math.abs(c.width - w * z) > 0.5) out.push(`the canvas is ${c.width}px wide at ${z}x, not ${w * z}`);
  const frameLeft = R(wrap).left + wrap.clientLeft;
  const frameRight = frameLeft + wrap.clientWidth;
  if (c.left < frameLeft - 0.5) out.push(`the canvas starts ${Math.round(frameLeft - c.left)}px left of its frame`);
  // The panes, not the grid box: tracks wider than their grid overflow it and leave its edges put.
  const railRight = R(root).right - parseFloat(getComputedStyle(root).paddingRight);
  const paneRight = Math.max(...[...document.querySelectorAll('.wb-cols > .wb-pane')].map((e) => R(e).right));
  if (paneRight > railRight + 0.5) out.push(`the panes run ${Math.round(paneRight - railRight)}px past the rail`);
  const hits = (x, sel) => {
    let n = 0, of = 0;
    for (let y = c.top + 4; y < c.bottom - 4; y += 12) {
      if (y < 0 || y >= innerHeight || x < 0 || x >= innerWidth) continue;
      of++;
      const e = document.elementFromPoint(x, y);
      if (e && e.closest(sel)) n++;
    }
    return [n, of];
  };
  if (i.right <= p.left) {
    for (const dx of [1, 8, 16]) {
      const [n, of] = hits(i.right - dx, '.wb-canvas-pane');
      if (n) out.push(`${n} of ${of} points ${dx}px inside the Inspector's right edge hit the canvas pane`);
    }
  }
  for (const [x, where] of [[p.left - 2, 'left of'], [p.left + 2, 'over the left edge of'],
                            [p.right - 2, 'over the right edge of'], [p.right + 2, 'right of']]) {
    const [n, of] = hits(x, '.wb-canvas');
    if (n) out.push(`the canvas paints ${where} its pane (${n} of ${of} points)`);
  }
  if (c.width > frameRight - frameLeft) {
    wrap.scrollLeft = wrap.scrollWidth;
    const end = R(cv).right;
    wrap.scrollLeft = 0;
    if (end > frameRight + 0.5) out.push(`the canvas's right ${Math.round(end - frameRight)}px cannot be scrolled into view`);
  }
  return out;
}"""


def check_wb_canvas_fits(page) -> list[str]:
    """At 3x the 600px emery canvas is wider than its pane at the shell's 1440 viewport. It
    was centred with plain `align-items: center`, so it overflowed BOTH sides of the pane, over
    the Inspector's inputs on the left and past the pane border on the right, and nothing
    clipped or scrolled it. Every zoom must leave the canvas inside its pane, wholly reachable.

    1300 here is the width the shell gives a rail at a 1440 viewport. Mounted in the built shell
    at 1440x900, the panes land on the walk's own rects (Inspector right edge 846, canvas pane
    860 to 1393): a 1284px pane grid, and the rail pads 8px each side. 600 is under the 1100px
    breakpoint, where the panes stack in one `1fr` column. They never did: a later `.wb-cols`
    rule overrode the media query on source order, so three columns' minimums (838px with the
    gaps) ran past a 584px rail. And a `1fr` track has an automatic minimum, so once the panes
    stack, a 3x canvas widens the column past the rail unless its pane may shrink."""
    problems: list[str] = []
    r = wb_up(page, 1300, "Quiet Dial")
    for width in (1300, 600):
        page.set_viewport_size({"width": width, "height": 1000})
        for z in (1, 2, 3):
            page.locator(".wb-canvas-bar .wb-tab", has_text=f"{z}x").click()
            page.wait_for_timeout(80)
            wb_fit_viewport(page, ".wb-canvas", width)
            problems += [f"{width}px, {z}x: {m}" for m in page.evaluate(WB_CANVAS_FIT, {"z": z, "w": 200})]
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def wb_palettes() -> list[str]:
    css = (REPO / "web" / "src" / "styles.css").read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\[data-palette='([a-z0-9-]+)'\]", css)))


# Contrast of each `sel` element's text against what is actually painted behind it: every
# ancestor's background-color composited bottom up on a 1px canvas, then the text colour on top.
# Returns [{sel, ratio, text, back}] with luminances, for the caller to judge.
WB_CONTRAST = """(sels) => {
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
  const out = [];
  for (const sel of sels) {
    const el = document.querySelector(sel);
    if (!el) { out.push({ sel, missing: true }); continue; }
    const layers = [];
    for (let e = el; e; e = e.parentElement) layers.unshift(getComputedStyle(e).backgroundColor);
    const back = lum(paint(layers));
    const text = lum(paint([...layers, getComputedStyle(el).color]));
    const ratio = (Math.max(back, text) + 0.05) / (Math.min(back, text) + 0.05);
    out.push({ sel, ratio, back, text });
  }
  return out;
}"""


def wb_each_theme(page, sels: list[str], judge: Callable[[str, dict], str | None]) -> list[str]:
    """Run WB_CONTRAST under every palette in both explicit modes; `judge(mode, row)` returns a
    problem or None. Failures are grouped per element so 42 themes do not print 42 lines."""
    bad: dict[str, list[str]] = {}
    for pal in wb_palettes():
        for mode in ("light", "dark"):
            page.evaluate("([p, m]) => { const h = document.documentElement; h.dataset.palette = p; h.dataset.theme = m; }",
                          [pal, mode])
            for row in page.evaluate(WB_CONTRAST, sels):
                if row.get("missing"):
                    bad.setdefault(row["sel"], []).append(f"{pal} {mode}: not rendered")
                    continue
                why = judge(mode, row)
                if why:
                    bad.setdefault(row["sel"], []).append(f"{pal} {mode} {why}")
    return [f"{sel}: {len(v)} of {2 * len(wb_palettes())} themes fail, e.g. {'; '.join(v[:3])}"
            for sel, v in bad.items()]


def check_wb_caption_contrast(page) -> list[str]:
    """The canvas footer's '· layout preview, not a render' and 'on-watch colour' read the
    palette's muted ink, which is tuned for the page, on the canvas frame, which is #2a2a33 in
    every theme. Both must reach 4.5:1 on that frame in every palette and mode."""
    r = wb_up(page, 1300)
    out = wb_each_theme(page, [".wb-canvas-meta .wb-sub", ".wb-canvas-toggle"],
                        lambda mode, row: None if row["ratio"] >= 4.5 else f"{row['ratio']:.2f}:1")
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_live_tag_palette(page) -> list[str]:
    """A preset's 'live' tag kept hard-coded dark colours, so in a light palette it was a
    dark green pill on a light card. In every palette and mode its fill must follow the mode
    (lighter than its text in light, darker in dark) and its text must reach 4.5:1 on it."""
    r = wb_up(page, 1300)
    page.get_by_role("button", name="Presets", exact=True).click()
    page.locator(".wb-tag.live").first.wait_for(timeout=8000)

    def judge(mode: str, row: dict) -> str | None:
        flipped = row["back"] < row["text"] if mode == "light" else row["back"] > row["text"]
        if flipped:
            return f"{'dark' if mode == 'light' else 'light'} fill under {'light' if mode == 'light' else 'dark'} text"
        return None if row["ratio"] >= 4.5 else f"{row['ratio']:.2f}:1"

    out = wb_each_theme(page, [".wb-tag.live"], judge)
    return out + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_use_this_one_line(page) -> list[str]:
    """On a preset whose footer caption is long, the 'Use this' button shrank to its
    longest word and wrapped onto two lines. Every card's button must stay on one line."""
    problems: list[str] = []
    r = wb_up(page, 1300)
    page.get_by_role("button", name="Presets", exact=True).click()
    page.locator(".wb-preset").first.wait_for(timeout=8000)
    for width in (1300, 1000, 700):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(80)
        rows = page.evaluate("""() => [...document.querySelectorAll('.wb-preset')].map((card) => {
          const btn = card.querySelector('.wb-preset-foot button');
          const range = document.createRange(); range.selectNodeContents(btn);
          const lines = new Set([...range.getClientRects()].map((q) => Math.round(q.top))).size;
          return [card.querySelector('.wb-preset-name').textContent, lines];
        })""")
        if len(rows) < 8:
            problems.append(f"{width}px: only {len(rows)} preset cards rendered")
        problems += [f"{width}px: '{name}' has 'Use this' on {n} lines" for name, n in rows if n != 1]
    return problems + [f"unanswered: {u}" for u in r.unanswered]


# --- the AI jobs -----------------------------------------------------------------------------
#
# The page polls GET /api/ai/jobs/{id} every 1.5 s (module.tsx JOB_POLL_MS), so each check waits
# on what the page shows and counts what it asked, and a "stopped polling" claim is measured over
# 3.5 s, more than two poll intervals.

QUIET_S = 3.5


def wb_start(page, kind: str) -> None:
    """The two clicks every AI action takes: the action, then Run it under its cost."""
    page.get_by_role("button", name="AI", exact=True).click()
    if kind == "design":
        page.locator(".wb-prompt").fill("a quiet dark face")
        page.get_by_role("button", name="Design it").click()
    else:
        page.get_by_role("button", name=re.compile(r"^Explore \d+ variations$")).click()
    page.get_by_role("button", name="Run it").click()


def wb_polls_stopped(page, jobs: WbJobs, what: str) -> list[str]:
    before = jobs.gets
    page.wait_for_timeout(QUIET_S * 1000)
    extra = jobs.gets - before
    return [f"the page polled {what} {extra} more time(s) in {QUIET_S:g} s"] if extra else []


def check_wb_job_progress(page) -> list[str]:
    """Explore 3 variations: progress in variants while the job runs, each variant in the grid
    as soon as the job reports it (well before the job is done), the whole grid at the end, and
    no poll and no Cancel once it is done."""
    problems: list[str] = []
    jobs = WbJobs([wb_job("running", 0), wb_job("running", 1), wb_job("running", 1),
                   wb_job("running", 2), wb_job("running", 2),
                   wb_job("done", 3, result={"variants": [wb_variant(i) for i in range(3)],
                                             "requested": 3})])
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    page.locator(".wb-variant-name", has_text="Variant 1").wait_for(timeout=8000)
    if "done" in jobs.served:
        problems.append("the first variant showed only once the job was done, not as it finished")
    page.locator(".wb-variant-name", has_text="Variant 3").wait_for(timeout=15000)
    page.wait_for_timeout(300)
    if (n := page.locator(".wb-variant").count()) != 3:
        problems.append(f"{n} variant cells after the job finished, not 3")
    if page.locator(".wb-job").count():
        problems.append("the job's progress and Cancel are still shown after it finished")
    problems += wb_polls_stopped(page, jobs, "a finished job")
    if jobs.posts != 1:
        problems.append(f"{jobs.posts} jobs were started, not 1")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_cancel(page) -> list[str]:
    """Cancel job sends the DELETE, the page stops polling at once, says so, and keeps the
    variant that finished before it."""
    problems: list[str] = []
    jobs = WbJobs([wb_job("running", 1)], cancelled=wb_job("cancelled", 1))
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    page.get_by_role("button", name="Cancel job").click()
    note = page.locator(".wb-note", has_text="Cancelled")
    note.wait_for(timeout=8000)
    # The cancel never reaches the broker (jobs.cancel), so the note may promise only that no
    # NEW call starts, and must say what happens to the one already sent.
    said = note.inner_text()
    if "No new call starts" not in said or "a call already sent finishes" not in said:
        problems.append(f"the cancelled note does not say what a cancel stops: {said!r}")
    if jobs.deletes != 1:
        problems.append(f"Cancel job sent {jobs.deletes} DELETE(s), not 1")
    problems += wb_polls_stopped(page, jobs, "a cancelled job")
    if page.locator(".wb-job").count():
        problems.append("Cancel job is still offered for a cancelled job")
    if not page.locator(".wb-variant-name", has_text="Variant 1").count():
        problems.append("the variant finished before the cancel was dropped from the grid")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_lost(page) -> list[str]:
    """A restart loses the job: its id answers 404. The page says the job is gone and nothing is
    running, stops polling, and offers the AI actions again."""
    problems: list[str] = []
    jobs = WbJobs([wb_job("running", 0), GONE])
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    page.locator(".wb-err", has_text="This AI job is gone").wait_for(timeout=10000)
    problems += wb_polls_stopped(page, jobs, "a job the rail no longer has")
    if page.locator(".wb-job").count():
        problems.append("the lost job's progress and Cancel are still shown")
    explore = page.get_by_role("button", name=re.compile(r"^Explore \d+ variations$"))
    if not explore.is_enabled():
        problems.append("Explore stays disabled after the job was lost")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_reload(page) -> list[str]:
    """A reload mid-job: the rail lists the viewer's running design job, so the page shows its
    progress without a click, polls it, and puts the finished design in the editor, without
    starting a second job."""
    problems: list[str] = []
    doc = wb_variant(0)["design"]
    doc["name"] = "Reattached"
    jobs = WbJobs([wb_job("running", 0, total=1, kind="design", calls=1),
                   wb_job("running", 0, total=1, kind="design", calls=2),
                   wb_job("done", 1, total=1, kind="design", calls=2,
                          result={"design": doc, "layout": {
                              "errors": 0, "warnings": 0, "findings": [], "semantics": []}})],
                  listing=[{"job_id": "job-1", "kind": "design", "state": "running",
                            "progress": {"done": 0, "total": 1, "unit": "design"},
                            "created": 0, "finished": None, "delivered": False}])
    r = wb_up(page, 1300, ai_jobs=jobs)
    page.locator(".wb-job", has_text="Designing").wait_for(timeout=8000)
    page.wait_for_function("document.querySelector('.wb-name')?.value === 'Reattached'",
                           timeout=15000)
    if jobs.posts:
        problems.append(f"the page started {jobs.posts} new job(s) instead of the running one")
    problems += wb_polls_stopped(page, jobs, "a finished job")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_busy_attach(page) -> list[str]:
    """Run it answered 409 busy: the viewer's design job from another tab is running. This tab
    shows that job's progress and the 409's line while it runs. Once it is done the line goes,
    and the design is OFFERED, not put in this tab's editor, which holds an unsaved edit; Open
    this design is what opens it."""
    problems: list[str] = []
    doc = wb_variant(0)["design"]
    doc["name"] = "From The Other Tab"
    jobs = WbJobs([wb_job("running", 0, total=1, kind="design", calls=1),
                   wb_job("running", 0, total=1, kind="design", calls=2),
                   wb_job("done", 1, total=1, kind="design", calls=2,
                          result={"design": doc, "layout": {
                              "errors": 0, "warnings": 0, "findings": [], "semantics": []}})],
                  refuse="busy")
    r = wb_up(page, 1300, ai_jobs=jobs)
    page.locator(".wb-name").fill("My unsaved edit")
    wb_start(page, "design")
    line = page.locator(".wb-err", has_text="You already have an AI job running")
    page.locator(".wb-job", has_text="Designing").wait_for(timeout=8000)
    if not line.count():
        problems.append("the 409's line is not shown while the job it names runs")
    page.locator(".wb-job").wait_for(state="detached", timeout=15000)
    page.wait_for_timeout(300)
    if "done" not in jobs.served:
        problems.append("the page stopped showing the job before it was done")
    if line.count():
        problems.append("the 409's line is still shown after the job it names finished")
    offer = page.get_by_role("button", name="Open this design")
    if not offer.count():
        problems.append("the other tab's finished design is not offered")
    page.get_by_role("button", name="Design", exact=True).click()
    name = page.locator(".wb-name").input_value()
    if name != "My unsaved edit":
        problems.append(f"the other tab's design replaced this tab's editor (its name is {name!r})")
    if offer.count():
        offer.click()
        try:
            page.wait_for_function(
                "document.querySelector('.wb-name')?.value === 'From The Other Tab'", timeout=8000)
        except Exception:  # noqa: BLE001 - a timeout here is the finding, not a crash
            problems.append("Open this design did not put the offered design in the editor")
        if page.get_by_role("button", name="Open this design").count():
            problems.append("the offer is still shown after it was opened")
    problems += wb_polls_stopped(page, jobs, "a finished job")
    if jobs.posts != 1:
        problems.append(f"{jobs.posts} jobs were started, not 1")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_busy_finishing(page) -> list[str]:
    """Run it answered 409 finishing: the viewer's cancelled jobs are still waiting on a call
    sent before the cancel, so nothing of theirs is running. The page says so in the 409's own
    words and attaches to nothing: it does not fetch the cancelled job, show progress or offer
    Cancel, and the line stays (it is the only reason the click did nothing)."""
    problems: list[str] = []
    jobs = WbJobs([wb_job("cancelled", 0)], refuse="finishing")
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    line = page.locator(".wb-err", has_text="still waiting on a model call")
    try:
        line.wait_for(timeout=8000)
    except Exception:  # noqa: BLE001 - a timeout here is the finding, not a crash
        problems.append("the 409's message is not shown")
    page.wait_for_timeout(QUIET_S * 1000)
    if jobs.gets:
        problems.append(f"the page fetched the cancelled job {jobs.gets} time(s)")
    if not line.count():
        problems.append("the 409's message went away while nothing had changed")
    if page.locator(".wb-job").count():
        problems.append("the page shows a job running, but none is")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


# --- the lease ------------------------------------------------------------------------------
#
# The rail stops a job nobody reads for jobs.LEASE_S (300 s), and usePoll skips its ticks while
# the tab is hidden. The page's clock is Playwright's fake one, so ten hidden minutes take a few
# real seconds, and every read of the job is stamped with that clock by WB_JOB_READS: when it was
# sent (__jobReads), and, once answered, [sent, HTTP status] (__jobAnswers). Setting __hang to N
# leaves the next N reads unanswered, the way a connection that stalled leaves a fetch: they
# settle only if the page aborts them (__hung records when each was sent). Setting __hold to N
# sends the next N reads at once, so the fixture answers each as things stand then, but holds
# each answer back from the page until __release() hands over the oldest (__holding).

WB_JOB_READS = """(() => {
  window.__jobReads = [];
  window.__jobAnswers = [];
  window.__hang = 0;
  window.__hung = [];
  window.__hold = 0;
  window.__holding = [];
  window.__release = () => { const go = window.__holding.shift(); if (go) go(); return window.__holding.length; };
  const f = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.href);
    const method = (init && init.method) || 'GET';
    if (method !== 'GET' || !/\\/api\\/ai\\/jobs\\/[^/]+$/.test(url.pathname)) return f(input, init);
    const sent = Date.now();
    window.__jobReads.push(sent);
    if (window.__hang > 0) {
      window.__hang -= 1;
      window.__hung.push(sent);
      const signal = init && init.signal;
      return new Promise((_, reject) => {
        if (signal) signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      });
    }
    const answered = f(input, init).then((r) => { window.__jobAnswers.push([sent, r.status]); return r; });
    if (window.__hold > 0) {
      window.__hold -= 1;
      const signal = init && init.signal;
      return new Promise((ok, no) => {
        if (signal) signal.addEventListener('abort', () => no(new DOMException('aborted', 'AbortError')));
        window.__holding.push(() => answered.then(ok, no));
      });
    }
    return answered;
  };
})()"""


def wb_job_reads(page) -> list[float]:
    return page.evaluate("window.__jobReads.slice()")


def wb_answered(page) -> list[float]:
    """When each read the rail answered 200 was sent: the reads that renewed the lease."""
    return [sent for sent, status in page.evaluate("window.__jobAnswers.slice()") if status == 200]


def wb_step(page, total_ms: float, step_ms: float, settle_ms: int = 30) -> None:
    """Move the paused page clock `total_ms` in steps of `step_ms`, letting each step's reads
    settle in real time between them (page.route answers them from this process)."""
    for _ in range(max(1, int(total_ms // step_ms))):
        page.clock.run_for(int(step_ms))
        page.wait_for_timeout(settle_ms)


def wb_widest(times: list[float]) -> float:
    return max((b - a for a, b in zip(times, times[1:])), default=0.0)


def wb_pause(page) -> None:
    """Stop the page's clock a second from now; only run_for and fast_forward move it after."""
    page.clock.pause_at((page.evaluate("Date.now()") + 1000) / 1000)   # seconds, not ms


def wb_remount(page) -> None:
    """Mount the module again, as the shell does on the way back from another rail, and let the
    paused clock run the timers its first render waits on (the 250 ms validate debounce)."""
    page.evaluate("window.__mount()")
    page.locator(".watch-builder").wait_for(timeout=10000)
    wb_step(page, 1500, 100)
    page.locator(".wb-canvas").wait_for(timeout=10000)


def check_wb_job_hidden_keepalive(page) -> list[str]:
    """A hidden tab keeps its job. Hidden for twice the lease, first on timers that fire on time
    (run_for) and then on timers a browser clamps to once a minute, the way Chrome throttles a
    hidden tab (fast_forward fires each due timer at most once), the page must read the job at
    least once a minute but not at the visible poll's 1.5 s, so no gap between two reads reaches
    the lease. Shown again, it polls at once and the job is still running. While it runs, the
    page says that closing the tab stops it."""
    problems: list[str] = []
    lease_ms = wb_jobs_number("LEASE_S") * 1000
    jobs = WbJobs([wb_job("running", 1)])
    page.add_init_script(VISIBILITY)
    page.add_init_script(WB_JOB_READS)
    page.clock.install()
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    line = page.locator(".wb-job", has_text="1 of 3 done")
    line.wait_for(timeout=8000)
    said = line.inner_text()
    if f"with none open, it stops within about {round(lease_ms / 60_000)} minutes" not in said:
        problems.append(f"the running job does not say a closed tab stops it: {said!r}")
    # The promise names its exception: a browser that freezes a background tab stops its timers.
    if "unless the browser puts that tab to sleep" not in said or "on another rail" not in said:
        problems.append(f"the running job's promise does not name another rail and its exception: {said!r}")
    page.wait_for_timeout(2000)

    page.evaluate("window.__setVis('hidden')")
    hidden_at = page.evaluate("Date.now()") + 1000
    page.clock.pause_at(hidden_at / 1000)          # a number here is seconds, not ms
    before = len(wb_job_reads(page))
    # Two minutes on time, one keep-alive interval (30 s) per step, each read let finish.
    on_time: list[int] = []
    for _ in range(4):
        n = len(wb_job_reads(page))
        page.clock.run_for(30_000)
        page.wait_for_timeout(500)
        on_time.append(len(wb_job_reads(page)) - n)
    if sum(on_time) < 3:
        problems.append(f"{sum(on_time)} reads of the job in 2 hidden minutes on time, {on_time} "
                        "a step: the hidden tab sends no keep-alive")
    if sum(on_time) > 8:
        problems.append(f"{sum(on_time)} reads of the job in 2 hidden minutes: the hidden tab "
                        "polls at the visible rate")
    # Eight minutes clamped to one timer a minute.
    clamped: list[int] = []
    for _ in range(8):
        n = len(wb_job_reads(page))
        page.clock.fast_forward(60_000)
        page.wait_for_timeout(500)
        clamped.append(len(wb_job_reads(page)) - n)
    if any(c == 0 for c in clamped):
        problems.append(f"no read of the job in {clamped.count(0)} of 8 clamped hidden minutes "
                        f"(reads a minute: {clamped})")

    hidden_end = len(wb_job_reads(page))
    shown_at = page.evaluate("Date.now()")
    page.evaluate("window.__setVis('visible')")
    page.clock.resume()
    page.wait_for_timeout(2500)
    reads = wb_job_reads(page)
    hidden, after = reads[before:hidden_end], reads[hidden_end:]
    # The gaps the rail's lease sees: from the last read before hiding to the first after.
    around = reads[max(0, before - 1):before] + hidden + after[:1]
    gaps = [b - a for a, b in zip(around, around[1:])]
    if not gaps or max(gaps) >= lease_ms:
        widest = max(gaps) / 1000 if gaps else (shown_at - hidden_at) / 1000
        problems.append(f"the rail would have stopped the job: {widest:.0f} s without a read "
                        f"against a {lease_ms / 1000:.0f} s lease")
    if len(after) < 2:
        problems.append(f"{len(after)} read(s) of the job in 2.5 s after the tab was shown again: "
                        "the poll did not resume at its own rate")
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("the job is no longer shown running after the tab was shown again")
    if page.locator(".wb-stopped").count() or page.locator(".wb-err").count():
        problems.append("the page shows the job stopped or lost after being hidden")
    if not hidden:
        problems.append("no read of the job at all while the tab was hidden")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def wb_unwatched() -> str:
    """Run.gate's reason for a lapsed lease, in its own words."""
    return (f"stopped: nobody was watching it for {wb_jobs_number('LEASE_S') / 60:g} minutes, "
            "and no call starts after that")


def check_wb_job_stopped(page) -> list[str]:
    """The viewer comes back after the rail stopped their job for want of anyone watching it: a
    reload within the keep window picks up the job nobody collected. The page says it stopped
    because nobody was watching, says how many variants it kept, shows no error, starts no job
    and polls no more. Of the three variants that finished before the stop, one failed (the
    model's document was rejected twice): progress.done counts it, since jobs.Run.variant_done is
    handed every finished entry, but it is not a face anyone can open, so the note says 2 of 4
    are kept, not 3. A design the rail stopped says so the same way, instead of showing the
    gate's reason as a model failure."""
    problems: list[str] = []
    failed = {"ok": False, "axis": "Axis 2",
              "error": "the model could not produce a valid design. Last problems: overlap"}
    variants = [wb_variant(0), failed, wb_variant(2),
                {"ok": False, "axis": "Axis 4", "error": wb_unwatched()}]
    view = wb_job("done", 3, total=4, result={"variants": variants, "requested": 4},
                  stopped="unwatched")
    view["partial"] = variants[:3]
    jobs = WbJobs([view],
                  listing=[{"job_id": "job-1", "kind": "variations", "state": "done",
                            "progress": {"done": 3, "total": 4, "unit": "variant"},
                            "created": 0, "finished": 1, "delivered": False}])
    r = wb_up(page, 1300, ai_jobs=jobs)
    note = page.locator(".wb-stopped")
    try:
        note.wait_for(timeout=8000)
    except Exception:  # noqa: BLE001 - a timeout here is the finding, not a crash
        problems.append("a variation pass the rail stopped shows no reason")
    else:
        said = note.inner_text()
        if "nobody was watching" not in said:
            problems.append(f"the stopped note does not say nobody was watching: {said!r}")
        if "2 of 4 variations finished first and are kept" not in said:
            problems.append(f"the stopped note does not say how many variants it kept: {said!r}")
    page.get_by_role("button", name="AI", exact=True).click()
    cells = page.locator(".wb-variant")
    if cells.count() != 4 or page.locator(".wb-variant-name").count() != 2:
        problems.append(f"{cells.count()} variant cells and {page.locator('.wb-variant-name').count()} "
                        "finished, not 4 and 2")
    if page.locator(".wb-err").count():
        problems.append(f"a stopped pass shows an error: {page.locator('.wb-err').first.inner_text()!r}")
    problems += wb_polls_stopped(page, jobs, "a stopped job")
    if jobs.posts:
        problems.append(f"the page started {jobs.posts} job(s) on its own")

    design = WbJobs([wb_job("failed", 0, total=1, kind="design", calls=1, stopped="unwatched",
                            error=wb_unwatched())],
                    listing=[{"job_id": "job-1", "kind": "design", "state": "failed",
                              "progress": {"done": 0, "total": 1, "unit": "design"},
                              "created": 0, "finished": 1, "delivered": False}])
    r2 = wb_up(page, 1300, ai_jobs=design)
    try:
        page.locator(".wb-stopped", has_text="nobody was watching").wait_for(timeout=8000)
    except Exception:  # noqa: BLE001 - a timeout here is the finding, not a crash
        problems.append("a design the rail stopped shows no reason")
    else:
        if "The design was not finished" not in page.locator(".wb-stopped").inner_text():
            problems.append("the stopped design's note does not say it was not finished")
    if page.locator(".wb-err").count():
        problems.append("a stopped design is shown as a model failure: "
                        f"{page.locator('.wb-err').first.inner_text()!r}")
    return problems + [f"unanswered: {u}" for u in r.unanswered + r2.unanswered]


def wb_running_job(page) -> tuple[WbJobs, Rail]:
    """Explore 3 variations on the page's fake clock, every read of the job recorded, and the
    clock paused once the running job shows."""
    jobs = WbJobs([wb_job("running", 1)])
    page.add_init_script(VISIBILITY)
    page.add_init_script(WB_JOB_READS)
    page.clock.install()
    r = wb_up(page, 1300, ai_jobs=jobs)
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    wb_pause(page)
    return jobs, r


def check_wb_job_outage(page) -> list[str]:
    """The gateway is down for longer than the poll's give-up (MAX_FAILED_POLLS failed reads,
    about 90 s) while the tab stays visible: a platform rebuild. The page says it has lost touch
    and that it keeps asking, and it does ask, at the keep-alive's pace rather than the poll's.
    Once the rail answers again it carries on: the note goes, the poll resumes and the job still
    runs. From the last read the rail answered before the outage to the first one after, less
    than the lease passes, so the rail would not have stopped the job. Before the fix the page
    stopped asking at the give-up, and a job whose tab stayed open stopped a lease later."""
    problems: list[str] = []
    lease_ms = wb_jobs_number("LEASE_S") * 1000
    keep_ms = wb_module_number("KEEPALIVE_MS")
    jobs, r = wb_running_job(page)
    wb_step(page, 4500, 1500)
    note = page.locator(".wb-out-of-touch")
    jobs.fail = 502
    down_at = page.evaluate("Date.now()")
    for _ in range(400):                     # the poll's rate, until it gives up
        page.clock.run_for(1500)
        page.wait_for_timeout(30)
        if note.count():
            break
    if not note.count():
        problems.append("the page never said it lost touch with the rail")
        return problems + [f"unanswered: {u}" for u in r.unanswered]
    said = note.inner_text()
    if f"asks again every {keep_ms / 1000:g} seconds" not in said:
        problems.append(f"the lost-touch note does not say the page keeps asking: {said!r}")
    # Two more minutes down, after the give-up.
    n = len(wb_job_reads(page))
    wb_step(page, 120_000, 1500)
    down = len(wb_job_reads(page)) - n
    want = int(120_000 // keep_ms)
    if down < want - 1:
        problems.append(f"{down} reads of the job in the 2 minutes after the page lost touch, not "
                        f"about {want}: it stopped asking, so the rail would stop the job")
    if down > want + 2:
        problems.append(f"{down} reads of the job in 2 minutes out of touch: it asks at the "
                        "poll's rate, not the keep-alive's")
    jobs.fail = None
    up_at = page.evaluate("Date.now()")
    for _ in range(int(keep_ms // 1500) + 4):
        page.clock.run_for(1500)
        page.wait_for_timeout(30)
        if not note.count():
            break
    good = wb_answered(page)
    before = [t for t in good if t < down_at]
    after = [t for t in good if t >= up_at]
    if not after:
        problems.append(f"no read was answered in the {keep_ms / 1000 + 6:g} s after the rail came back")
    elif before and after[0] - before[-1] >= lease_ms:
        problems.append(f"{(after[0] - before[-1]) / 1000:.0f} s between two answered reads across "
                        f"the outage, against a {lease_ms / 1000:.0f} s lease: the rail would have "
                        "stopped the job")
    if note.count():
        problems.append("the lost-touch note is still shown after the rail answered")
    n = len(wb_job_reads(page))
    wb_step(page, 4500, 1500)
    if len(wb_job_reads(page)) - n < 2:
        problems.append("the poll did not resume at its own rate after the rail came back")
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("the job is not shown running after the rail came back")
    if page.locator(".wb-err").count():
        problems.append(f"an error is shown after the rail came back: {page.locator('.wb-err').first.inner_text()!r}")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_other_rail(page) -> list[str]:
    """The viewer moves to another rail in the same platform tab, which unmounts the module (the
    shell renders only the active rail), and stays there for twice the lease. The keep-alive is
    not the component's: it goes on reading the job every KEEPALIVE_MS while nothing of Watch
    Builder is mounted, so no gap between two reads reaches the lease. Back on Watch Builder the
    page shows the job still running without being asked, and polls it again; no job was
    started."""
    problems: list[str] = []
    lease_ms = wb_jobs_number("LEASE_S") * 1000
    keep_ms = wb_module_number("KEEPALIVE_MS")
    jobs, r = wb_running_job(page)
    wb_step(page, 4500, 1500)
    before = len(wb_job_reads(page))
    page.evaluate("window.__unmount()")
    if page.locator(".watch-builder").count():
        problems.append("the module is still mounted, so nothing was tested")
    wb_step(page, 2 * lease_ms, keep_ms, settle_ms=300)
    reads = wb_job_reads(page)
    away = reads[before:]
    want = int(2 * lease_ms // keep_ms)
    if len(away) < want - 2:
        problems.append(f"{len(away)} reads of the job in {2 * lease_ms / 60_000:g} minutes on "
                        f"another rail, not about {want}")
    # The gaps the rail's lease sees: from the last read before leaving to the moment of return.
    widest = wb_widest(reads[max(0, before - 1):] + [page.evaluate("Date.now()")])
    if widest >= lease_ms:
        problems.append(f"the rail would have stopped the job: {widest / 1000:.0f} s without a read "
                        f"while the viewer was on another rail, against a {lease_ms / 1000:.0f} s lease")
    wb_remount(page)
    n = len(wb_job_reads(page))
    wb_step(page, 4500, 1500)
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("back on Watch Builder, the job is not shown running")
    if len(wb_job_reads(page)) - n < 2:
        problems.append("back on Watch Builder, the page does not poll the job")
    if jobs.posts != 1:
        problems.append(f"{jobs.posts} jobs were started, not 1")
    if page.locator(".wb-err").count() or page.locator(".wb-stopped").count():
        problems.append("back on Watch Builder, the job is shown stopped or lost")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_hung_read(page) -> list[str]:
    """A keep-alive read that is never answered (a connection that stalled when the laptop slept
    or changed network) must not hold up the reads after it. The tab is hidden, so only the
    keep-alive reads, and its first read is left hanging. Within two keep-alive intervals the
    page has given that read up (its deadline, KEEPALIVE_MS) and sent the next, the reads go on,
    and no gap between two reads sent reaches the lease. Shown again, the job still runs."""
    problems: list[str] = []
    lease_ms = wb_jobs_number("LEASE_S") * 1000
    keep_ms = wb_module_number("KEEPALIVE_MS")
    jobs, r = wb_running_job(page)
    page.evaluate("window.__setVis('hidden')")
    before = len(wb_job_reads(page))
    page.evaluate("window.__hang = 1")
    wb_step(page, 8 * keep_ms, keep_ms, settle_ms=400)
    hung = page.evaluate("window.__hung.slice()")
    reads = wb_job_reads(page)
    if not hung:
        problems.append("no keep-alive read was sent while hidden, so none could hang")
    else:
        later = [t for t in reads if t > hung[0]]
        if not later:
            problems.append("no read after the one that hung: it held up every later keep-alive")
        elif later[0] - hung[0] > 2 * keep_ms + 1000:
            problems.append(f"the next read went {(later[0] - hung[0]) / 1000:.0f} s after the hung "
                            f"one, not within two keep-alive intervals ({2 * keep_ms / 1000:g} s)")
        if len(later) < 4:
            problems.append(f"{len(later)} reads in the {8 * keep_ms / 60_000:g} hidden minutes after "
                            "the hung one")
    widest = wb_widest(reads[max(0, before - 1):] + [page.evaluate("Date.now()")])
    if widest >= lease_ms:
        problems.append(f"the rail would have stopped the job: {widest / 1000:.0f} s without a read "
                        f"against a {lease_ms / 1000:.0f} s lease")
    page.evaluate("window.__setVis('visible')")
    page.clock.resume()
    page.wait_for_timeout(2500)
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("the job is no longer shown running after the hung read")
    if page.locator(".wb-err").count():
        problems.append(f"an error is shown after the hung read: {page.locator('.wb-err').first.inner_text()!r}")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_keepalive_ends(page) -> list[str]:
    """The keep-alive ends with its job. A pass that finishes while the viewer is on another rail
    is read by the keep-alive, whose read leaves it undelivered on the rail (?keepalive=1) and
    keeps nothing of it: nothing reads the job after it was final, and back on Watch Builder the
    page picks it up from the listing and shows the finished variants. Back before the
    keep-alive's next read, the page's own read as it mounts finds the pass done instead, shows
    it and, a plain read, delivers it. A pass that finishes in view is delivered by the poll's
    last read, which carries no marker, and leaves no keep-alive behind either. And a read the
    platform refuses (401: signed out) ends it too: the page says why, and sends no read after
    it, hidden or shown. Before the third review nothing asserted that a plain read delivers:
    a poll or a mount read that carried the marker passed every check."""
    problems: list[str] = []
    keep_ms = wb_module_number("KEEPALIVE_MS")
    finished = wb_job("done", 3, result={"variants": [wb_variant(i) for i in range(3)],
                                         "requested": 3})

    def reads_after(state: str) -> int:
        return len(jobs.served) - 1 - jobs.served.index(state) if state in jobs.served else -1

    # Finished while on another rail.
    jobs, r = wb_running_job(page)
    wb_step(page, 3000, 1500)
    page.evaluate("window.__unmount()")
    jobs.views, jobs.gets = [finished], 0
    wb_step(page, 6 * keep_ms, keep_ms, settle_ms=300)
    if "done" not in jobs.served:
        problems.append("nothing read the job while the viewer was on another rail")
    elif (extra := reads_after("done")) > 0:
        problems.append(f"{extra} read(s) of the job after it was final, on another rail")
    jobs.listing = wb_listed(finished, jobs.delivered)
    wb_remount(page)
    jobs.listing = []
    page.get_by_role("button", name="AI", exact=True).click()
    page.wait_for_timeout(300)
    if (named := page.locator(".wb-variant-name").count()) != 3:
        problems.append(f"back on Watch Builder, {named} of the 3 variants that finished meanwhile "
                        "are shown")
    served = len(jobs.served)
    wb_step(page, 3 * keep_ms, keep_ms, settle_ms=300)
    if len(jobs.served) > served:
        problems.append(f"{len(jobs.served) - served} read(s) of the finished job after returning")

    # Finished on another rail, and back before the keep-alive's next read: the page's own read
    # of the kept id as it mounts is what finds the pass done. A page shows that answer, so it is
    # a plain read and delivers the pass: no reload, new tab or later return offers it again.
    jobs.views, jobs.gets = [wb_job("running", 1)], 0
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    wb_step(page, 3000, 1500)
    page.evaluate("window.__unmount()")
    n = len(wb_job_reads(page))
    for _ in range(int(keep_ms // 1000) + 2):          # to the keep-alive's read on another rail
        wb_step(page, 1000, 1000)
        if len(wb_job_reads(page)) > n:
            break
    page.wait_for_timeout(300)
    if len(wb_job_reads(page)) == n:
        problems.append("no keep-alive read on another rail, so the return was not timed")
    jobs.views, jobs.gets, jobs.delivered, jobs.listing = [finished], 0, False, []
    marked = jobs.marked
    wb_remount(page)
    page.get_by_role("button", name="AI", exact=True).click()
    page.wait_for_timeout(300)
    if (named := page.locator(".wb-variant-name").count()) != 3:
        problems.append(f"back within one keep-alive interval, {named} of the 3 variants that "
                        "finished meanwhile are shown")
    if jobs.marked > marked:
        problems.append(f"{jobs.marked - marked} read(s) on the way back carried the keep-alive's "
                        "marker, although the page showed the answer")
    if not jobs.delivered:
        problems.append("back within one keep-alive interval, the page showed the finished pass, "
                        "but no read of its delivered it, so every reload offers it again")

    # Finished in view: the poll's last read finds it done, and delivers it.
    jobs.views, jobs.gets, jobs.served = [wb_job("running", 1), finished], 0, []
    jobs.delivered, marked = False, jobs.marked
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    wb_step(page, 4500, 1500)
    if "done" not in jobs.served or page.locator(".wb-job").count():
        problems.append(f"the pass did not finish in view (reads answered {jobs.served})")
    if jobs.marked > marked:
        problems.append(f"{jobs.marked - marked} read(s) of a pass watched in view carried the "
                        "keep-alive's marker")
    if not jobs.delivered:
        problems.append("a pass watched to the end in view was not delivered by the poll's last "
                        "read, so every reload, new tab or return offers it again")
    served = len(jobs.served)
    wb_step(page, 4 * keep_ms, keep_ms, settle_ms=300)
    if len(jobs.served) > served:
        problems.append(f"{len(jobs.served) - served} read(s) of a job finished in view: its "
                        "keep-alive was left running")

    # Refused: signed out, hidden.
    jobs.views, jobs.gets, jobs.served = [wb_job("running", 1)], 0, []
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    page.evaluate("window.__setVis('hidden')")
    jobs.fail = 401
    wb_step(page, 2 * keep_ms, keep_ms, settle_ms=400)
    if "401" not in jobs.served:
        problems.append("no keep-alive read reached the refusal")
    note = page.locator(".wb-err", has_text="stopped answering this page as you")
    if not note.count():
        problems.append("a read the platform refused (401) is not said")
    served = len(jobs.served)
    wb_step(page, 6 * keep_ms, keep_ms, settle_ms=300)
    page.evaluate("window.__setVis('visible')")
    wb_step(page, 6000, 1500)
    if len(jobs.served) > served:
        problems.append(f"{len(jobs.served) - served} read(s) after the platform refused one: a "
                        "signed-out page would ask for ever")
    if page.locator(".wb-job").count():
        problems.append("the refused job is still shown running")
    if jobs.posts != 4:
        problems.append(f"{jobs.posts} jobs were started, not 4")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


# --- the second lease review (2026-10-09) ------------------------------------------------------

def wb_listed(view: dict, delivered: bool) -> list[dict]:
    """GET /api/ai/jobs as jobs.listing answers it for the one job `view` shows: what a page that
    mounts with no job kept picks a job up from."""
    return [{"job_id": view["job_id"], "kind": view["kind"], "state": view["state"],
             "progress": view["progress"], "created": view["created"],
             "finished": view["finished"], "delivered": delivered}]


def wb_shown(page) -> list[str]:
    """Everything the page shows of an AI job, on its AI tab: none of it, for a viewer with none."""
    page.get_by_role("button", name="AI", exact=True).click()
    page.wait_for_timeout(300)
    out: list[str] = []
    if page.locator(".wb-variant").count():
        out.append(f"variants {page.locator('.wb-variant-name').all_inner_texts()}")
    for sel, what in ((".wb-job", "a running job"), (".wb-stopped", "a stopped note"),
                      (".wb-offer", "an offer"), (".wb-note", "a note"), (".wb-err", "an error")):
        if page.locator(sel).count():
            out.append(f"{what}: {page.locator(sel).first.inner_text()[:70]!r}")
    return out


def check_wb_job_signout(page) -> list[str]:
    """One viewer signs out and another signs in on the same tab. The shell does that without a
    reload (App.tsx renders the login form, which unmounts every rail, then loads the apps again),
    so this module and its keep-alive stay loaded. The next viewer is shown nothing of the first
    one's job, and every read of it sent as them is the 404 the rail gives anyone but its owner,
    so none is answered with its data. Three ways in: a pass that finished while the first viewer
    was on another rail (the keep-alive read it there, as them); a pass still running when they
    signed out; and a keep-alive read sent as the first viewer whose answer lands after the next
    viewer's page has mounted (only the page mounted when a read is sent may show it). After the
    next viewer's 404 the keep-alive has forgotten the job: nothing reads it again. Before the fix
    the module held the first viewer's last view of the job for whichever page mounted next.
    Two more ways in, from the third review: the next viewer's own read of the kept id fails, or
    is only slow, and the keep-alive's next read, sent while their page is mounted and so as
    them, is the 404. Their page never showed that job, so it says nothing of how it ended:
    before that fix it said "This AI job is gone" about the first viewer's job."""
    problems: list[str] = []
    keep_ms = wb_module_number("KEEPALIVE_MS")
    finished = wb_job("done", 3, result={"variants": [wb_variant(i) for i in range(3)],
                                         "requested": 3})

    def someone_else() -> int:
        """The first viewer signs out and another signs in: job-1 is not theirs (the 404
        jobs._theirs gives), and they have no jobs of their own."""
        jobs.views, jobs.gets, jobs.listing, jobs.fail = [GONE], 0, [], None
        wb_step(page, 5000, 1000)                       # typing a name and a password
        return len(jobs.served)

    def first_viewer() -> None:
        """Back to a page of the first viewer's, with a pass of theirs running and shown."""
        page.evaluate("window.__unmount()")
        jobs.views, jobs.gets, jobs.listing = [wb_job("running", 1)], 0, []
        wb_remount(page)
        if page.locator(".wb-job").count():
            # The phase before left its job kept (its 404 did not end the keep-alive), and this
            # page's read of it is answered running now. Say so, and cancel it, so this phase
            # starts its own pass rather than stopping at a disabled Run it, which would
            # report only that crash and lose every finding before it.
            problems.append("a job of the phase before was still kept, and read again as the "
                            "next page mounted: the keep-alive did not forget it")
            jobs.cancelled = wb_job("cancelled", 1)
            page.get_by_role("button", name="Cancel job").click()
            page.locator(".wb-job").wait_for(state="detached", timeout=8000)
        wb_start(page, "variations")
        page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
        wb_step(page, 3000, 1500)

    def judge(how: str, mark: int) -> None:
        if seen := wb_shown(page):
            problems.append(f"{how}: the next viewer is shown the first viewer's job: {seen}")
        if answered := [s for s in jobs.served[mark:] if s != "gone"]:
            problems.append(f"{how}: reads of the first viewer's job answered with data: {answered}")
        n = len(jobs.served)
        wb_step(page, 3 * keep_ms, keep_ms, settle_ms=200)
        if len(jobs.served) > n:
            problems.append(f"{how}: {len(jobs.served) - n} read(s) of that job after the next "
                            "viewer's 404: the keep-alive did not forget it")
        if seen := wb_shown(page):
            problems.append(f"{how}: later, the next viewer is shown {seen}")

    # A pass that finished on another rail, read there by the keep-alive, then a sign-out.
    jobs, r = wb_running_job(page)
    wb_step(page, 3000, 1500)
    page.evaluate("window.__unmount()")
    jobs.views, jobs.gets = [finished], 0
    wb_step(page, 2 * keep_ms, keep_ms, settle_ms=300)
    if "done" not in jobs.served:
        problems.append("nothing read the pass that finished on another rail, so nothing was tested")
    mark = someone_else()
    wb_remount(page)
    judge("finished on another rail", mark)

    # A pass still running at the sign-out; the next viewer signs in within one keep-alive
    # interval, so their page's read is the first one sent after it.
    first_viewer()
    if not page.locator(".wb-variant-name", has_text="Variant 1").count():
        problems.append("the first viewer's running pass shows no variant, so nothing was tested")
    page.evaluate("window.__unmount()")
    mark = someone_else()
    wb_remount(page)
    if jobs.served[mark:] != ["gone"]:
        problems.append(f"reads of the kept job since the sign-out: {jobs.served[mark:]}, not one 404")
    judge("running at the sign-out", mark)

    # A keep-alive read sent as the first viewer, answered once the next viewer's page has
    # mounted, and before that page's own read is answered. The clock moves a second at a time
    # until the keep-alive's read is out, so it is well inside its deadline when it is released.
    first_viewer()
    page.evaluate("window.__unmount()")
    page.evaluate("window.__hold = 2")
    for _ in range(int(keep_ms // 1000) + 2):          # the first viewer's read: held
        wb_step(page, 1000, 1000)
        if page.evaluate("window.__holding.length"):
            break
    sent_as_first = jobs.served[-1:]
    mark = someone_else()
    wb_remount(page)                                   # the next viewer's read: held
    held = page.evaluate("window.__holding.length")
    if held != 2 or sent_as_first != ["running"]:
        problems.append(f"{held} read(s) held, the first answered {sent_as_first}: not the first "
                        "viewer's running job and the next viewer's 404, so nothing was tested")
    page.evaluate("window.__release()")                # the first viewer's answer lands
    page.wait_for_timeout(300)
    page.evaluate("window.__release()")                # then the next viewer's 404
    page.wait_for_timeout(300)
    judge("a read answered after the next viewer's page mounted", len(jobs.served))

    # The next viewer's own read of the kept id fails (a gateway blip as they open Watch
    # Builder), and the keep-alive's next read, sent while their page is mounted, is the 404.
    first_viewer()
    page.evaluate("window.__unmount()")
    someone_else()
    jobs.fail = 502
    wb_remount(page)                                   # the next viewer's read: a 502
    jobs.fail = None
    mark = len(jobs.served)
    if jobs.served[-1:] != ["502"]:
        problems.append(f"the next viewer's read of the kept id answered {jobs.served[-1:]}, not "
                        "a 502, so nothing was tested")
    n = len(wb_job_reads(page))
    for _ in range(int(keep_ms // 1000) + 2):          # to the keep-alive's next read, as them
        wb_step(page, 1000, 1000)
        if len(wb_job_reads(page)) > n:
            break
    page.wait_for_timeout(300)
    if jobs.served[mark:] != ["gone"]:
        problems.append(f"the keep-alive's read after the next viewer's failed one answered "
                        f"{jobs.served[mark:]}, not one 404, so nothing was tested")
    judge("the next viewer's own read failed", mark)

    # Their own read is only slow, and the keep-alive's tick falls inside it: its read, sent as
    # them while their page is mounted, is answered (a 404) first, and then their own.
    first_viewer()
    page.evaluate("window.__unmount()")
    mark = someone_else()
    page.evaluate("window.__hold = 1")
    n = len(wb_job_reads(page))
    wb_remount(page)                                   # the next viewer's read: held
    for _ in range(int(keep_ms // 1000) + 2):          # the keep-alive's read, answered at once
        if len(wb_job_reads(page)) >= n + 2:
            break
        wb_step(page, 1000, 1000)
    page.wait_for_timeout(300)
    sent, held = len(wb_job_reads(page)) - n, page.evaluate("window.__holding.length")
    if sent != 2 or held != 1:
        problems.append(f"{sent} read(s) since the next viewer's page mounted, {held} held: not "
                        "their own held and the keep-alive's answered, so nothing was tested")
    if seen := wb_shown(page):
        problems.append(f"with the keep-alive's 404 in before their own read's, the next viewer "
                        f"is shown {seen}")
    page.evaluate("window.__release()")                # then their own read's 404
    page.wait_for_timeout(300)
    judge("the keep-alive's read answered before the next viewer's own", mark)
    if jobs.posts != 5:
        problems.append(f"{jobs.posts} jobs were started, not 5")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_offered_later(page) -> list[str]:
    """A pass that finishes while the viewer is on another rail stays on offer. The keep-alive
    reads it there with ?keepalive=1, which leaves it undelivered on the rail (jobs.get), so the
    listing a new tab of the same viewer reads (a reload's is the same: a fresh copy of the module)
    still offers it, and that tab shows the finished variants and delivers it with its own read.
    Before the fix the keep-alive's read marked it delivered, the result lived only in the first
    tab's module, and a reload or a new tab within the keep window showed nothing.
    A pass that finishes while its tab is hidden is the other way round: the keep-alive reads it
    for the page mounted there, which shows it, so that read carries no marker and delivers the
    pass. Before the third review it carried the marker, and a return or a reload brought that
    pass back over a newer one the viewer had watched to the end since."""
    problems: list[str] = []
    keep_ms = wb_module_number("KEEPALIVE_MS")
    finished = wb_job("done", 3, result={"variants": [wb_variant(i) for i in range(3)],
                                         "requested": 3})
    # Finished while its tab is hidden: the page mounted there is shown the keep-alive's read.
    jobs, r = wb_running_job(page)
    wb_step(page, 3000, 1500)
    page.evaluate("window.__setVis('hidden')")
    wb_step(page, 3000, 1500)              # the poll's next tick finds the tab hidden, and waits
    jobs.views, jobs.gets, jobs.delivered = [finished], 0, False
    marked = jobs.marked
    wb_step(page, 2 * keep_ms, keep_ms, settle_ms=300)
    if "done" not in jobs.served:
        problems.append("nothing read the pass that finished while its tab was hidden")
    if jobs.marked > marked:
        problems.append(f"{jobs.marked - marked} keep-alive read(s) for a mounted, hidden page "
                        "carried the marker: the pass it shows stays on offer, and a return "
                        "brings it back over a newer one")
    if not jobs.delivered:
        problems.append("the hidden page was shown the finished pass, but no read delivered it")
    page.evaluate("window.__setVis('visible')")
    wb_step(page, 3000, 1500)
    page.get_by_role("button", name="AI", exact=True).click()
    page.wait_for_timeout(300)
    if (named := page.locator(".wb-variant-name").count()) != 3:
        problems.append(f"shown again, the tab shows {named} of the 3 variants that finished "
                        "while it was hidden")

    # Finished on another rail: nobody is shown that read, so it leaves the pass on offer.
    jobs.views, jobs.gets = [wb_job("running", 1)], 0
    wb_start(page, "variations")
    page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
    wb_step(page, 3000, 1500)
    page.evaluate("window.__unmount()")
    jobs.views, jobs.gets, jobs.served, jobs.delivered = [finished], 0, [], False
    marked = jobs.marked
    wb_step(page, 3 * keep_ms, keep_ms, settle_ms=300)
    if "done" not in jobs.served:
        problems.append("nothing read the job while the viewer was on another rail")
    if jobs.marked == marked:
        problems.append("no read of the job carried the keep-alive marker (?keepalive=1)")
    if jobs.delivered:
        problems.append("a read the viewer never saw marked the finished pass delivered, so the "
                        "listing will not offer it again")
    jobs.listing = wb_listed(finished, jobs.delivered)
    # The fake clock is the context's, so the new tab shares it: let it run in real time.
    page.clock.resume()
    tab = page.context.new_page()
    r2 = wb_up(tab, 1300, ai_jobs=jobs)
    tab.get_by_role("button", name="AI", exact=True).click()
    try:
        tab.locator(".wb-variant-name", has_text="Variant 3").wait_for(timeout=8000)
    except Exception:  # noqa: BLE001 - a timeout here is the finding, not a crash
        problems.append(f"a new tab is not offered the pass that finished (listing {jobs.listing})")
    if (named := tab.locator(".wb-variant-name").count()) != 3:
        problems.append(f"the new tab shows {named} of the 3 finished variants")
    if not jobs.delivered:
        problems.append("the new tab showed the pass, but no read of its delivered it")
    if jobs.posts != 2:
        problems.append(f"{jobs.posts} jobs were started, not 2")
    tab.close()
    return problems + [f"unanswered: {u}" for u in r.unanswered + r2.unanswered]


# A document.visibilityState that throws while window.__visThrow is above 0, once per read. One
# script with VISIBILITY, since the order separate init scripts run in is not defined.
WB_VIS_THROWS = VISIBILITY + """
window.__visThrow = 0;
Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => {
  if (window.__visThrow > 0) { window.__visThrow -= 1; throw new Error('railcheck: visibilityState threw'); }
  return window.__vis || 'visible';
} });
"""


def check_wb_job_old_browser(page) -> list[str]:
    """A browser without AbortSignal.timeout reads its job like any other: shown, the poll reaches
    the rail and the progress moves; hidden, the keep-alive reads it every KEEPALIVE_MS. And one
    keep-alive tick that throws does not end the keep-alive: hidden, the tick's read of
    document.visibilityState is made to throw once, and the reads go on. Before the fix every
    read threw before its fetch on such a browser, so no read reached the rail, the page froze at
    its first progress line, and nothing said so."""
    problems: list[str] = []
    keep_ms = wb_module_number("KEEPALIVE_MS")
    jobs = WbJobs([wb_job("running", 0), wb_job("running", 1)])
    page.add_init_script(WB_VIS_THROWS)
    page.add_init_script(WB_JOB_READS)
    page.clock.install()
    r = wb_up(page, 1300, ai_jobs=jobs)
    # A browser before Safari 16 or Chrome 103. Removed once the page is up, not by an init
    # script: Playwright's fake clock installs its own AbortSignal.timeout after every one.
    page.evaluate("() => { Object.defineProperty(AbortSignal, 'timeout', "
                  "{ value: undefined, configurable: true, writable: true }) }")
    if not page.evaluate("typeof AbortSignal.timeout === 'undefined'"):
        return ["AbortSignal.timeout is still defined, so nothing was tested"]
    wb_start(page, "variations")
    page.locator(".wb-job").wait_for(timeout=8000)
    wb_pause(page)
    wb_step(page, 4500, 1500)
    if jobs.gets < 2:
        problems.append(f"{jobs.gets} read(s) of the job reached the rail in 4.5 s on view: the "
                        "poll's reads fail before they are sent")
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("the progress line never moved past the job's first answer")
    page.evaluate("window.__setVis('hidden')")
    wb_step(page, 3000, 1500)              # the poll's next tick finds the tab hidden, and waits
    n = jobs.gets
    wb_step(page, 3 * keep_ms, keep_ms, settle_ms=300)
    if jobs.gets - n < 2:
        problems.append(f"{jobs.gets - n} keep-alive read(s) reached the rail in "
                        f"{3 * keep_ms / 1000:g} hidden seconds")
    page.evaluate("window.__visThrow = 1")
    n = jobs.gets
    wb_step(page, 4 * keep_ms, keep_ms, settle_ms=300)
    if page.evaluate("window.__visThrow"):
        problems.append("no keep-alive tick read the tab's visibility, so nothing threw")
    elif jobs.gets - n < 2:
        problems.append(f"{jobs.gets - n} read(s) in {4 * keep_ms / 1000:g} hidden seconds after one "
                        "keep-alive tick threw: the keep-alive ended")
    page.evaluate("window.__setVis('visible')")
    n = jobs.gets
    wb_step(page, 4500, 1500)
    if jobs.gets - n < 2:
        problems.append("shown again, the poll did not resume")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_job_unmounted_ends(page) -> list[str]:
    """With no page of Watch Builder mounted (the viewer is on another rail, or has signed out,
    which unmounts every rail), the keep-alive judges each read itself. An outage longer than the
    lease keeps it asking at its own pace. Back on Watch Builder while the rail is still down,
    the page cannot read the job, so it shows nothing of it (it may be someone else's), but it
    says it has lost contact, in a note that names no job, and asks on; once the rail answers,
    the note goes and the job is shown running. Before the third review the page said nothing
    at all until then. A 401, a 403 and a 404 each end it: one read is answered so and none is
    sent after it, and the page that mounts next neither reads that job nor shows it, since the
    keep-alive forgot it. Before the second review only a mounted page's 401 was tested: a
    keep-alive that never stopped on any of these with no page mounted, one that gave up during
    an outage, and one that took a 403 for a blip all passed every check."""
    problems: list[str] = []
    lease_ms = wb_jobs_number("LEASE_S") * 1000
    keep_ms = wb_module_number("KEEPALIVE_MS")
    jobs, r = wb_running_job(page)
    wb_step(page, 3000, 1500)

    # The gateway is down for twice the lease while the viewer is on another rail.
    page.evaluate("window.__unmount()")
    jobs.fail = 502
    n = len(wb_job_reads(page))
    wb_step(page, 2 * lease_ms, keep_ms, settle_ms=200)
    down = len(wb_job_reads(page)) - n
    want = int(2 * lease_ms // keep_ms)
    if down < want - 1:
        problems.append(f"{down} reads in {2 * lease_ms / 60_000:g} minutes of outage with no page "
                        f"mounted, not about {want}: the keep-alive gave up, so the rail would "
                        "stop the job")
    wb_remount(page)
    unreached = page.locator(".wb-unreached")
    if not unreached.count():
        problems.append("back on Watch Builder while the rail is down, the page does not say it "
                        "has lost contact, so it shows nothing at all of a running job")
    elif f"asks again every {keep_ms / 1000:g} seconds" not in (said := unreached.inner_text()):
        problems.append(f"the lost-contact note does not say the page keeps asking: {said!r}")
    n = len(wb_job_reads(page))
    wb_step(page, 3 * keep_ms, keep_ms, settle_ms=200)
    if len(wb_job_reads(page)) - n < 2:
        problems.append(f"{len(wb_job_reads(page)) - n} read(s) in {3 * keep_ms / 1000:g} s back on "
                        "Watch Builder with the rail down: the page stopped asking")
    if not unreached.count():
        problems.append("the lost-contact note went while the rail was still down")
    jobs.fail = None
    wb_step(page, keep_ms + 3000, 1500, settle_ms=60)
    if unreached.count():
        problems.append("the lost-contact note is still shown after the rail answered")
    if not page.locator(".wb-job", has_text="1 of 3 done").count():
        problems.append("after the outage, back on Watch Builder, the job is not shown running")

    for status, what in ((401, "a 401 (signed out)"), (403, "a 403 (no longer allowed in)"),
                         (404, "a 404 (gone)")):
        if not page.locator(".wb-job").count():
            jobs.views, jobs.gets = [wb_job("running", 1)], 0
            wb_start(page, "variations")
            page.locator(".wb-job", has_text="1 of 3 done").wait_for(timeout=8000)
            wb_step(page, 3000, 1500)
        page.evaluate("window.__unmount()")
        if status == 404:
            jobs.views, jobs.gets = [GONE], 0
        else:
            jobs.fail = status
        mark = len(jobs.served)
        wb_step(page, 2 * keep_ms, keep_ms, settle_ms=300)
        got = jobs.served[mark:]
        if not got:
            problems.append(f"no keep-alive read reached {what} with no page mounted")
        elif len(got) > 1:
            problems.append(f"{len(got)} reads answered {what} with no page mounted: the "
                            "keep-alive did not stop at the first")
        mark = len(jobs.served)
        wb_step(page, 6 * keep_ms, keep_ms, settle_ms=200)
        if len(jobs.served) > mark:
            problems.append(f"{len(jobs.served) - mark} read(s) after {what} with no page mounted")
        # The viewer comes back, or someone signs in. Were the job still kept, its read would
        # now be answered with a running view, and shown.
        jobs.fail = None
        jobs.views, jobs.gets = [wb_job("running", 1)], 0
        mark = len(jobs.served)
        wb_remount(page)
        wb_step(page, 3000, 1500)
        if len(jobs.served) > mark:
            problems.append(f"after {what}, the page that mounted next read the job again: the "
                            "keep-alive did not forget it")
        if seen := wb_shown(page):
            problems.append(f"after {what}, the page that mounted next shows {seen}")
    if jobs.posts != 3:
        problems.append(f"{jobs.posts} jobs were started, not 3")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


def check_wb_cost_says_who_pays(page) -> list[str]:
    """The cost shown before Run it: for a viewer the broker answers from Claude it claims no
    GPU, and for anyone else it names the GPU (ai.estimate's `holds_gpu`)."""
    problems: list[str] = []
    jobs = WbJobs([wb_job("running", 0)])
    r = wb_up(page, 1300, ai_jobs=jobs)
    page.get_by_role("button", name="AI", exact=True).click()
    for claude, want, never in ((True, "on Claude", "of GPU"), (False, "of GPU", "on Claude")):
        jobs.claude = claude
        page.get_by_role("button", name=re.compile(r"^Explore \d+ variations$")).click()
        cost = page.locator(".wb-cost")
        cost.wait_for(timeout=8000)
        text = cost.inner_text()
        if want not in text or never in text:
            problems.append(f"{'Claude' if claude else 'local'} estimate reads {text!r}")
        page.locator(".wb-confirm").get_by_role("button", name="Cancel", exact=True).click()
        cost.wait_for(state="detached", timeout=8000)
    if jobs.posts:
        problems.append("asking for the cost started a job")
    return problems + [f"unanswered: {u}" for u in r.unanswered]


#: This module's checks: name -> function. railcheck.py's RAILS names each one, fixes its
#: place in --list and says which frontends it mounts.
CHECKS = {
    "wb-canvas-fits": check_wb_canvas_fits,
    "wb-caption-contrast": check_wb_caption_contrast,
    "wb-live-tag-palette": check_wb_live_tag_palette,
    "wb-use-this-one-line": check_wb_use_this_one_line,
    "wb-job-progress": check_wb_job_progress,
    "wb-job-cancel": check_wb_job_cancel,
    "wb-job-lost": check_wb_job_lost,
    "wb-job-reload": check_wb_job_reload,
    "wb-job-busy-attach": check_wb_job_busy_attach,
    "wb-job-busy-finishing": check_wb_job_busy_finishing,
    "wb-job-hidden-keepalive": check_wb_job_hidden_keepalive,
    "wb-job-stopped": check_wb_job_stopped,
    "wb-job-outage": check_wb_job_outage,
    "wb-job-other-rail": check_wb_job_other_rail,
    "wb-job-hung-read": check_wb_job_hung_read,
    "wb-job-keepalive-ends": check_wb_job_keepalive_ends,
    "wb-job-signout": check_wb_job_signout,
    "wb-job-offered-later": check_wb_job_offered_later,
    "wb-job-old-browser": check_wb_job_old_browser,
    "wb-job-unmounted-ends": check_wb_job_unmounted_ends,
    "wb-cost-says-who-pays": check_wb_cost_says_who_pays,
}
