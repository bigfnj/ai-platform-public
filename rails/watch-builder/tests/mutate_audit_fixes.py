"""Mutation harness for watch-builder's 2026-10-14 audit fixes. NOT a pytest file: it rewrites
the tree.

    .venv\\Scripts\\python.exe rails\\watch-builder\\tests\\mutate_audit_fixes.py [label ...]

Two fixes, each broken in turn so the NAMED test must fail: a render stops the emulators it
started however it ends, one render at a time (sdk.render, tests/test_render_lifecycle.py), and
an image upload's decode runs off the event loop (api/app.py upload_image,
tests/test_upload_off_loop.py). Both fail silently: a QEMU process per platform left running
beside the GPU broker, and every request stalled behind a decode, neither of which shows in a
status code. A verdict needs the suite to fail, the EXPECTED test on a FAILED line, and the
witness (the child imported watch_builder from THIS tree). CONTROLS change nothing the suite can
see and must SURVIVE, or the harness is reporting its own noise.
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

RAIL = Path(__file__).resolve().parents[1]               # rails/watch-builder
SRC = RAIL / "backend"
PY = sys.executable
TIMEOUT = 300
SUITE = ["tests/test_render_lifecycle.py", "tests/test_upload_off_loop.py",
         "tests/test_colour_pipeline.py"]

SDK = "backend/watch_builder/sdk.py"
APP = "backend/watch_builder/api/app.py"
IMAGES = "backend/watch_builder/images.py"

# (label, file, old, new, the test that must fail; None for a CONTROL, which must SURVIVE)
MUTATIONS: list[tuple[str, str, str, str, str | None]] = [
    (
        "a render leaves its emulators running",
        SDK,
        "        finally:\n            kill_emulators()\n",
        "        finally:\n            pass\n",
        "test_a_render_stops_the_emulators_once_it_has_its_screenshots",
    ),
    (
        "the emulators stop only after a render that did not raise",
        SDK,
        "        finally:\n            kill_emulators()\n",
        "        except BaseException:\n            raise\n        kill_emulators()\n",
        "test_a_render_that_fails_still_stops_the_emulators",
    ),
    (
        "two renders interleave, so one's kill cuts the other's emulators",
        SDK,
        "    with _render_lock:\n",
        "    with threading.Lock():\n",
        "test_two_renders_run_one_after_the_other",
    ),
    (
        "the upload's decode runs on the event loop again",
        APP,
        "        rec = await anyio.to_thread.run_sync(\n"
        "            functools.partial(images.store, ident.user, raw, name=file.filename or \"image\"),\n"
        "            limiter=_STORE_LIMITER)\n",
        "        rec = images.store(ident.user, raw, name=file.filename or \"image\")\n",
        "test_an_image_upload_does_not_stall_a_concurrent_poll",
    ),
    # --- the delta audit (2026-10-15): stores at once ----------------------------------------
    (
        "the upload's store takes the threadpool's default limiter (no bound of its own)",
        APP,
        "            functools.partial(images.store, ident.user, raw, name=file.filename or \"image\"),\n"
        "            limiter=_STORE_LIMITER)\n",
        "            functools.partial(images.store, ident.user, raw, name=file.filename or \"image\"))\n",
        "test_a_third_upload_waits_for_a_slot_while_a_poll_is_answered",
    ),
    (
        "three stores decode at once",
        APP,
        "_STORE_LIMITER = anyio.CapacityLimiter(2)\n",
        "_STORE_LIMITER = anyio.CapacityLimiter(3)\n",
        "test_a_third_upload_waits_for_a_slot_while_a_poll_is_answered",
    ),
    (
        "every store of the same bytes shares one temp again",
        IMAGES,
        "    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=f\"{path.suffix}.tmp\")\n"
        "    os.close(fd)\n",
        "    tmp = str(path) + \".tmp\"\n",
        "test_stores_of_the_same_bytes_at_once_all_succeed",
    ),
    (
        "a write that fails leaves its temp behind",
        IMAGES,
        "    except BaseException:\n        Path(tmp).unlink(missing_ok=True)\n        raise\n",
        "    except BaseException:\n        raise\n",
        "test_a_store_that_cannot_write_leaves_no_temp_behind",
    ),
    (
        "the record is written in place again",
        IMAGES,
        "    _write_whole(meta_path, json.dumps(rec.__dict__, indent=2).encode(\"utf-8\"))\n",
        "    meta_path.write_text(json.dumps(rec.__dict__, indent=2), encoding=\"utf-8\")\n",
        "test_a_record_write_that_fails_keeps_the_previous_record",
    ),
    (
        "a rename Windows refused is not tried again",
        IMAGES,
        "            if not _WINDOWS or wait is None:\n",
        "            if True:\n",
        "test_a_refused_rename_is_tried_again_on_windows_only",
    ),
    (
        "a POSIX refusal is tried again too",
        IMAGES,
        "            if not _WINDOWS or wait is None:\n",
        "            if wait is None:\n",
        "test_a_refused_rename_is_tried_again_on_windows_only",
    ),
    (
        "CONTROL: the render lock's with-statement parenthesised",
        SDK,
        "    with _render_lock:\n        try:\n",
        "    with (_render_lock):\n        try:\n",
        None,
    ),
    (
        "CONTROL: the threadpool call's filename fallback parenthesised",
        APP,
        "raw, name=file.filename or \"image\"),\n",
        "raw, name=(file.filename or \"image\")),\n",
        None,
    ),
    (
        "CONTROL: the rename retries iterate a list, not a tuple",
        IMAGES,
        "    for wait in (*_RENAME_RETRY_S, None):\n",
        "    for wait in [*_RENAME_RETRY_S, None]:\n",
        None,
    ),
]


def _env() -> dict:
    env = dict(os.environ)
    # THIS tree first, and nothing else: an editable install elsewhere would be tested instead.
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness() -> str:
    p = mr.run_tree([PY, "-B", "-c", "import watch_builder.sdk as s; print(s.__file__)"],
                    cwd=RAIL, capture_output=True, text=True, env=_env(), timeout=60)
    return p.stdout.strip()


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header", "-rf",
                         "-p", "no:cacheprovider"],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, out + f"\nHARNESS TIMEOUT after {TIMEOUT}s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def main() -> int:
    only = mr.parse_harness_args([m for m in MUTATIONS if m[4] is not None],
                                 [m for m in MUTATIONS if m[4] is None], filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    where = witness()
    expected = (RAIL / SDK).resolve()
    if not where or Path(where).resolve() != expected:
        print(f"WITNESS FAILED: the child imports sdk.py from {where!r}, not {expected}")
        return 2
    print(f"witness: watch_builder imported from {Path(where).parent}")
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, rel, old, new, test in chosen:
        path = (RAIL / rel).resolve()
        original = path.read_bytes()
        n, mutated = mr.mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            if test is None:
                verdict = "TIMEOUT" if code == 124 else "SURVIVED" if code == 0 else "FIRED"
                ok = verdict == "SURVIVED"
            else:
                verdict = ("TIMEOUT" if code == 124 else
                           "FIRED" if code != 0 and test in failed_names(out) else
                           "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
                ok = verdict == "FIRED"
            results.append((label, verdict if ok else f"BAD:{verdict}"))
            print(f"  {verdict:17} {label}")
            if not ok:
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1].startswith("BAD:") or r[1] == "PATTERN"]
    mutations = [m for m in chosen if m[4] is not None]
    controls = [m for m in chosen if m[4] is None]
    fired = sum(1 for (label, v) in results if v == "FIRED")
    survived = sum(1 for (label, v) in results if v == "SURVIVED")
    print(f"{fired}/{len(mutations)} mutations fired the right test; "
          f"{survived}/{len(controls)} controls survived")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
