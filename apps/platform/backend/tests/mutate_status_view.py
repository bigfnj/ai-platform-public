"""Mutation harness for what a non-admin sees of the broker status. NOT a pytest file -- it
rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_status_view.py

Run this, not just pytest, before trusting a change to status_view.py or to the
/api/platform/status route. mutate_cli_view.py covers the cli block inside the same response.

Both directions fail SILENTLY. Pass the raw status through and the widget keeps working, every
request is a 200, and every logged-in browser receives the host's interpreter paths. Filter
one key too many and the GPU meter quietly stops showing the job queue for plain users while
every admin, who gets everything, sees it work.

Each mutation names the test(s) that must fail for it, as `file::test`. A red run that fails
some OTHER test is reported as FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that
the guard is held.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
PY = sys.executable
SUITE = ["apps/platform/backend/tests/test_status_view.py",
         "apps/platform/backend/tests/test_cli_view.py"]
# One pytest run takes ~10 s here. A mutation that makes a test hang must not hang the harness.
RUN_TIMEOUT = 300

V = "apps/platform/backend/platform_gateway_app/status_view.py"
MAIN = "apps/platform/backend/platform_gateway_app/main.py"
T = "test_status_view.py::"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "the admin check is removed, so every user gets the broker's whole status",
        V,
        "    admin = is_admin is True\n",
        "    admin = True\n",
        [T + "test_no_operator_field_reaches_a_non_admin",
         T + "test_no_machine_path_reaches_a_non_admin_browser"],
    ),
    (
        "a merely truthy admin flag gets the operator view",
        V,
        "    admin = is_admin is True\n",
        "    admin = bool(is_admin)\n",
        [T + "test_only_a_literal_true_is_admin"],
    ),
    (
        "a top-level key nobody admitted is passed through to non-admins",
        V,
        "        elif key in _SHOWN:\n"
        "            out[key] = _SHOWN[key](value)",
        "        else:\n"
        "            out[key] = _SHOWN.get(key, lambda v: v)(value)",
        [T + "test_a_field_the_broker_adds_reaches_admins_only",
         T + "test_no_operator_field_reaches_a_non_admin"],
    ),
    (
        "the media block, with its interpreter paths, is admitted for non-admins",
        V,
        '    "fallbacks": _fallbacks,\n}',
        '    "fallbacks": _fallbacks,\n    "media": lambda v: v,\n}',
        [T + "test_no_operator_field_reaches_a_non_admin",
         T + "test_no_machine_path_reaches_a_non_admin_browser"],
    ),
    (
        "a job entry is passed through whole",
        V,
        "    out = _pick(job, _JOB_KEYS)\n",
        "    out = dict(job)\n",
        [T + "test_no_operator_field_reaches_a_non_admin",
         T + "test_a_field_the_broker_adds_reaches_admins_only"],
    ),
    (
        "a resident model keeps its expiry time",
        V,
        '_LOADED_KEYS = ("name", "class", "size_vram")',
        '_LOADED_KEYS = ("name", "class", "size_vram", "expires_at")',
        [T + "test_no_operator_field_reaches_a_non_admin"],
    ),
    (
        "fallbacks.last is passed through whole",
        V,
        '        out["last"] = _fallback(v["last"])',
        '        out["last"] = v["last"]',
        [T + "test_a_field_the_broker_adds_reaches_admins_only"],
    ),
    (
        "an allowed leaf name may carry a nested object",
        V,
        "    return {k: v for k, v in d.items() if k in keys and _scalar(v)}",
        "    return {k: v for k, v in d.items() if k in keys}",
        [T + "test_an_allowed_name_cannot_carry_a_nested_object"],
    ),
    (
        "the filter starves the widget: plain users lose the job queue",
        V,
        '    "jobs": lambda v: _each(v, _job),\n',
        "",
        [T + "test_a_non_admin_gets_exactly_what_the_widget_draws",
         T + "test_every_status_field_the_widget_reads_is_admitted"],
    ),
    (
        "an admin's view shares the broker client's objects instead of copying them",
        V,
        "            out[key] = copy.deepcopy(value)",
        "            out[key] = value",
        [T + "test_an_admin_gets_the_whole_status_as_a_copy"],
    ),
    (
        "the cli block moves to the end of the response",
        V,
        '        if key == "cli":\n'
        "            view = cli_view(value, username, admin)\n"
        "            if view is not None:\n"
        "                out[key] = view\n"
        "        elif admin:",
        '        if key == "cli":\n'
        "            continue\n"
        "        elif admin:",
        # EXTRA (below) re-adds the block after the loop, so this is "moved", not "gone".
        [T + "test_cli_keeps_its_place_in_the_brokers_order"],
    ),
    (
        "the status route spreads the raw broker status again",
        MAIN,
        "    return status_view(status, user.username, bool(user.is_admin))",
        "    return dict(status)",
        [T + "test_no_machine_path_reaches_a_non_admin_browser"],
    ),
    (
        "the broker-down detail goes to every user",
        MAIN,
        '        return {"broker_reachable": False,\n'
        '                **_status_for({"detail": str(exc), **_EMPTY_STATUS}, user)}',
        '        return {"broker_reachable": False, "detail": str(exc), **_EMPTY_STATUS}',
        [T + "test_the_broker_down_detail_reaches_admins_only"],
    ),
]

# The "cli moves to the end" mutation needs a second edit to be the defect it names rather than
# "cli vanishes": append the block after the loop. Keyed by label so the table above stays flat.
EXTRA = {
    "the cli block moves to the end of the response": (
        V,
        "            out[key] = _SHOWN[key](value)\n"
        "    return out\n",
        "            out[key] = _SHOWN[key](value)\n"
        '    if "cli" in status:\n'
        '        view = cli_view(status["cli"], username, admin)\n'
        "        if view is not None:\n"
        '            out["cli"] = view\n'
        "    return out\n",
    ),
}


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first. The repo venv carries editable installs that point at one checkout; run
    # from a worktree without this and pytest imports the gateway from somewhere else, so every
    # mutation "survives" against code the run never touched.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    # No .pyc written: a mutation and its restore can share an mtime and a size, and the import
    # system would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import platform_gateway_app.status_view as v, platform_gateway_app.main as m;"
         "print(v.__file__); print(m.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(BACKEND):
            return f"imported {f}, which is not under {BACKEND}"
    return None


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    """`file::test` for each pytest FAILED line, parametrize ids dropped. File-qualified because
    both suites have a test_only_a_literal_true_is_admin, and the other file's failing must not
    pass for this one's."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            path, rest = ln[len("FAILED "):].split("::", 1)
            names.add(f"{Path(path).name}::{rest.split(' ', 1)[0].split('[', 1)[0]}")
    return names


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        edits = [(relpath, old, new)] + ([EXTRA[label]] if label in EXTRA else [])
        originals: dict[Path, str] = {}
        original_bytes: dict[Path, bytes] = {}
        try:
            bad = False
            for rel, o, n in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                originals.setdefault(path, path.read_text(encoding="utf-8"))
                current = path.read_text(encoding="utf-8")
                count = current.count(o)
                if count != 1:
                    print(f"  ERROR  {label}: pattern found {count} times in {rel}, expected 1")
                    bad = True
                    break
                mr.write_like(path, current.replace(o, n), original_bytes[path])
            if bad:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code != 0 and code != -1
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path in originals:
                mr.restore_exact(path, original_bytes[path])

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
