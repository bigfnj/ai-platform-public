"""Real-browser checks against a rail's BUILT federated bundle, mounted the way the shell mounts it.

    <toolbox python> web/tests/browser/railcheck.py                 # every check
    <toolbox python> web/tests/browser/railcheck.py --build         # npm ci + build first
    <toolbox python> web/tests/browser/railcheck.py xss-finance     # one check (or a prefix: xss)
    <toolbox python> web/tests/browser/railcheck.py --list

<toolbox python> is any interpreter with Playwright and its Chromium, e.g. the DevToolbox venv
(%LOCALAPPDATA%\\DevToolbox\\python\\.venv\\Scripts\\python.exe). The repo venv deliberately has no
browser, so this is NOT a run-tests.ps1 target: it needs `npm run build` in each rail it checks,
and it says which dist is missing rather than skipping.

WHY A BROWSER, AND WHY THE BUILT BUNDLE. The repo has no React test runner, and the defects these
checks pin live in exactly the places a node test of a pure .ts module cannot see: what a
sanitiser does to markup inside a real DOM (DOMPurify returns its input UNCHANGED when it has no
DOM), and what survives a component unmount. So each check loads the rail's own
`dist/assets/remoteEntry.js`, takes `./module` from it, and renders it with the rail's own React
copy, which is what the platform shell does at runtime. The bytes under test are the ones the
gateway serves. Nothing touches a network: every request is answered by `page.route` from the
fixtures in railchecks/, and an unanswered `/api/` call is recorded and printed, never sent
anywhere.

WHERE A CHECK LIVES. This file is the command line. RAILS below names every check, fixes the
order --list prints and a run follows, and says which frontends each one mounts.
railchecks/harness.py is the harness every check shares; railchecks/<rail>.py (with `-` spelled
`_`) holds one rail's fixtures and checks, and its CHECKS maps each name to its function. A check
lives in the module of the FIRST frontend its RAILS entry names, so the shell checks, which also
mount recipe-book, are in railchecks/shell.py. load() refuses a module and a RAILS table that
disagree, and a module file RAILS never reaches, so no check can drop out of a run unnamed.

The public snapshot withholds the modules of the rails it withholds (tools/publish.json). There
a check whose module is absent is still listed, and is skipped and named like any other check
whose rail is not in that tree.
"""
from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:       # imported from elsewhere (tools/tests), not run as a script
    sys.path.insert(0, str(HERE))

from railchecks.harness import ORIGIN, frontend_dir, published_tree  # noqa: E402

#: Every check, in the order --list prints and a run follows: name -> the frontends it mounts,
#: joined by "+". The first one names the module that defines the check (see the header).
RAILS: dict[str, str] = {
    "xss-finance": "finance",
    "xss-recipe-book": "recipe-book",
    "xss-bouquet": "bouquet",
    "bq-inventory": "bouquet",
    "bq-job-ends-with-tab": "bouquet",
    "bq-blob-revoked": "bouquet",
    "xss-nodom-finance": "finance",
    "finance-requests": "finance",
    "finance-amazon-categorize": "finance",
    "finance-amazon-copy": "finance",
    "finance-panel-picks": "finance",
    "finance-panel-labels": "finance",
    "finance-panels-where": "finance",
    "finance-amazon-overflow": "finance",
    "finance-chip-contrast": "finance",
    "finance-overview-legend": "finance",
    "finance-payroll-meta": "finance",
    "finance-data-fields": "finance",
    "finance-card-fit": "finance",
    "finance-insights-recurring": "finance",
    "viewas-recipe-book": "recipe-book",
    "css-recipe-book": "recipe-book",
    "css-bouquet": "bouquet",
    "css-ai-playground": "ai-playground",
    "ap-dictate-pill": "ai-playground",
    "job-aid-jobs-table": "job-aid",
    "job-aid-skill-remove": "job-aid",
    "job-aid-skill-target": "job-aid",
    "cb-condensation-radios": "course-builder",
    "cb-offsite-claude": "course-builder",
    "cb-offsite-follows-chip": "course-builder",
    "cb-blueprints": "course-builder",
    "cb-folder-picker": "course-builder",
    "cb-layout-fits": "course-builder",
    "cb-build-reattach": "course-builder",
    "cb-index-reattach": "course-builder",
    "cb-build-cancel": "course-builder",
    "cb-index-cancel": "course-builder",
    "job-aid-runs": "job-aid",
    "ap-controls-row": "ai-playground",
    "bq-history-delete": "bouquet",
    "bq-disabled-primary": "bouquet",
    "rb-planner-double-accept": "recipe-book",
    "dictate-one-mic": "ai-voice",
    "rb-kitchen-count": "recipe-book",
    "rb-fav-keeps-grid": "recipe-book",
    "rb-search-notice": "recipe-book",
    "rb-add-spirit": "recipe-book",
    "rb-card-counts": "recipe-book",
    "rb-unset-controls": "recipe-book",
    "rb-facet-heading": "recipe-book",
    "rb-shopping-width": "recipe-book",
    "iep-stale-select": "iep",
    "course-builder-admin": "course-builder",
    "edu-library-actions": "edu-suite",
    "edu-job-reveal": "edu-suite",
    "edu-vocab-row": "edu-suite",
    "edu-jobs-column": "edu-suite",
    "edu-row-keyboard": "edu-suite",
    "edu-job-switch": "edu-suite",
    # IEP Present Levels: the same source tree's second bundle (dist-iep, EXTRA_BUILDS below).
    "edu-iep-stages": "edu-suite",
    "job-aid-zip-basics": "job-aid",
    "iep-edit-student": "iep",
    "iep-sse-reattach": "iep",
    "iep-delete-refused": "iep",
    "iep-earlier-goals": "iep",
    "iep-empty-align": "iep",
    "iep-no-grade": "iep",
    "ma-activity-chart": "meeting-atlas",
    "cw-brief-lens-switch": "co-worker",
    "cw-prompt-rows": "co-worker",
    "cw-prompt-templates": "co-worker",
    # web-core's read-aloud chip (SpeakButton), through the co-worker rail that renders one.
    "cw-speak-stop-loading": "co-worker",
    "smb-why-aside": "smb-partner-enablement",
    "smb-mobile-safe-area": "smb-partner-enablement",
    "smb-device-icons": "smb-partner-enablement",
    "smb-device-menus": "smb-partner-enablement",
    "wb-canvas-fits": "watch-builder",
    "wb-caption-contrast": "watch-builder",
    "wb-live-tag-palette": "watch-builder",
    "wb-use-this-one-line": "watch-builder",
    "wb-job-progress": "watch-builder",
    "wb-job-cancel": "watch-builder",
    "wb-job-lost": "watch-builder",
    "wb-job-reload": "watch-builder",
    "wb-job-busy-attach": "watch-builder",
    "wb-job-busy-finishing": "watch-builder",
    "wb-job-hidden-keepalive": "watch-builder",
    "wb-job-stopped": "watch-builder",
    "wb-job-outage": "watch-builder",
    "wb-job-other-rail": "watch-builder",
    "wb-job-hung-read": "watch-builder",
    "wb-job-keepalive-ends": "watch-builder",
    "wb-job-signout": "watch-builder",
    "wb-job-offered-later": "watch-builder",
    "wb-job-old-browser": "watch-builder",
    "wb-job-unmounted-ends": "watch-builder",
    "wb-cost-says-who-pays": "watch-builder",
    "om-frame-theme": "openmaic",
    "om-frame-boot": "openmaic",
    # The shell checks mount recipe-book as the active rail, so they need both dists.
    "shell-session-lost": "shell+recipe-book",
    "shell-hidden": "shell+recipe-book",
    "shell-rail-rerender": "shell+recipe-book",
    "shell-unload-error": "shell+recipe-book",
    "shell-admin-rails": "shell+recipe-book",
    "shell-admin-rails-cli": "shell+recipe-book",
    "shell-admin-rails-stream": "shell+recipe-book",
    "shell-admin-refusals": "shell+recipe-book",
    "shell-workspace-lock": "shell+recipe-book",
    "shell-run-now": "shell+recipe-book",
    "shell-broker-refused": "shell+recipe-book",
    "shell-schedule-inputs": "shell+recipe-book",
    "shell-deskpet-bubble": "shell+recipe-book",
    "shell-models-columns": "shell+recipe-book",
    # Two rails under the theme menu: terminal-fun's tiles and recipe-book's cards.
    "shell-topbar-layers": "shell+recipe-book+terminal-fun",
}

#: Files in railchecks/ that hold no rail's checks.
SHARED = ("__init__", "harness")

#: A frontend that builds a second bundle beside dist/: npm script -> the directory it fills.
#: edu-suite's dashboard source is also IEP Present Levels (`build:iep` -> dist-iep), and a
#: check of that bundle mounts it from there. --build runs these too, and a run needs them built.
EXTRA_BUILDS: dict[str, dict[str, str]] = {"edu-suite": {"build:iep": "dist-iep"}}


def module_of(spec: str) -> str:
    """The railchecks module that defines a check whose RAILS entry is `spec`."""
    return spec.split("+")[0].replace("-", "_")


def load(rails: dict[str, str], package: Path, published: bool, present: Callable[[str], bool],
         shared: tuple[str, ...] = SHARED) -> tuple[dict[str, tuple[str, Callable]], list[str]]:
    """(CHECKS, withheld): name -> (frontends, check) in `rails` order, from every module of
    `package` (a directory on sys.path), and the names whose module is absent because the tree
    withholds its rail.

    A module may be absent only from a published tree, and only when each of its checks mounts a
    frontend that `present` says is absent too: the snapshot withholds that rail and its module
    with it. Anything else is a module lost by mistake. That, a module whose CHECKS and `rails`
    disagree, and a module file no `rails` entry reaches would each leave a check out of every
    run without a word, so each stops every command, --list included."""
    names_in: dict[str, list[str]] = {}
    for name, spec in rails.items():
        names_in.setdefault(module_of(spec), []).append(name)
    stray = sorted(p.name for p in package.glob("*.py")
                   if p.stem not in names_in and p.stem not in shared)
    if stray:
        raise SystemExit(f"railcheck: no RAILS entry reaches {package.name}/{', '.join(stray)}, "
                         f"so its checks would never run")
    found: dict[str, Callable] = {}
    withheld: list[str] = []
    for mod, names in names_in.items():
        if not (package / f"{mod}.py").is_file():
            runnable = [n for n in names if all(present(p) for p in rails[n].split("+"))]
            if not published or runnable:
                raise SystemExit(f"railcheck: {package.name}/{mod}.py is missing, so "
                                 f"{', '.join(runnable or names)} cannot run")
            withheld += names
            continue
        defined = importlib.import_module(f"{package.name}.{mod}").CHECKS
        unlisted = sorted(set(defined) - set(names))
        undefined = sorted(set(names) - set(defined))
        if unlisted or undefined:
            raise SystemExit(f"railcheck: {package.name}/{mod}.py and RAILS disagree: "
                             f"defined but not in RAILS {unlisted}, in RAILS but not defined {undefined}")
        found.update(defined)
    return {n: (spec, found[n]) for n, spec in rails.items() if n in found}, withheld


CHECKS, WITHHELD = load(RAILS, HERE / "railchecks", published_tree(),
                        lambda part: frontend_dir(part).is_dir())


def build(frontend: Path, extra: tuple[str, ...] = ()) -> None:
    npm = "npm.cmd" if sys.platform == "win32" else "npm"
    if not (frontend / "node_modules").is_dir():
        subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--loglevel=error"], cwd=frontend, check=True)
    for script in ("build", *extra):
        subprocess.run([npm, "run", script], cwd=frontend, check=True, stdout=subprocess.DEVNULL)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("only", nargs="*", help="check names or prefixes (default: all)")
    ap.add_argument("--build", action="store_true", help="npm ci (if needed) and build each rail first")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)
    names = [n for n in RAILS if not a.only or any(n == o or n.startswith(o) for o in a.only)]
    if a.list:
        print("\n".join(names))
        return 0
    if not names:
        print(f"no check matches {a.only}; --list shows them")
        return 2
    if published_tree():
        # A check whose rail the snapshot withholds is skipped and NAMED. In this repo nothing
        # is skipped: a missing frontend is the "no built bundle" error below. load() has
        # already proved that every check in WITHHELD is one of these.
        gone = [n for n in names if any(not frontend_dir(part).is_dir() for part in RAILS[n].split("+"))]
        for n in gone:
            print(f"SKIP {n}: its rail is not in this published tree")
        names = [n for n in names if n not in gone]
        if not names:
            print("nothing left to run in this published tree")
            return 0
    needs = sorted({part for n in names for part in RAILS[n].split("+")})
    for name in needs:
        fe = frontend_dir(name)
        extra = EXTRA_BUILDS.get(name, {})
        if a.build:
            build(fe, tuple(extra))
        marker = fe / "dist" / ("index.html" if name == "shell" else "assets/remoteEntry.js")
        if not marker.is_file():
            print(f"{name}: no built bundle at {fe / 'dist'}; run with --build (or npm run build there)")
            return 2
        for script, out in extra.items():
            if not (fe / out / "assets" / "remoteEntry.js").is_file():
                print(f"{name}: no built bundle at {fe / out}; run with --build (or npm run {script} there)")
                return 2

    from playwright.sync_api import sync_playwright

    failed = 0
    with sync_playwright() as p:
        # A fake microphone, granted without a prompt, and the harness origin treated as secure
        # (getUserMedia exists only in a secure context): the dictation checks need all three.
        browser = p.chromium.launch(headless=not a.headed, channel="chromium", args=[
            "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
            f"--unsafely-treat-insecure-origin-as-secure={ORIGIN}"])
        for n in names:
            ctx = browser.new_context()
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            t0 = time.monotonic()
            try:
                problems = CHECKS[n][1](page)
            except Exception as e:  # a check that cannot reach its state is a failure, not a pass
                problems = [f"could not run: {type(e).__name__}: {str(e).splitlines()[0]}"]
            problems += [f"page error: {e}" for e in errors]
            ms = (time.monotonic() - t0) * 1000
            if problems:
                failed += 1
                print(f"FAIL {n} ({ms:.0f} ms)")
                for pr in problems:
                    print(f"     {pr}")
            else:
                print(f"ok   {n} ({ms:.0f} ms)")
            ctx.close()
        browser.close()
    print(f"\n{len(names) - failed} of {len(names)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
