"""Mutation harness for the image upload's bounds (test_images.py). NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\watch-builder\\tests\\mutate_upload_bound.py

Copies rails/watch-builder/backend and rails/watch-builder/tests into a temp directory, breaks
the COPY one way at a time, and runs the copied test_images.py there. This tree is never
written.

POST /api/images used to read the whole body before the 12 MB check (`await file.read()`),
FastAPI had spooled all of it before that, and Image.open tried every decoder Pillow has
before the format allowlist was looked at. Three bounds replaced that, and each is broken
here to prove a test stands behind it: the ASGI ceiling in front of the form parser (both of
its branches, the declared length and the running count), the stepped read in the route (and
its boundary), and `formats=` on Image.open. A no-op edit is the control: it must SURVIVE,
or the harness is reporting the suite's noise rather than the mutations.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree.
# Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

RAIL = Path(__file__).resolve().parents[1]              # rails/watch-builder
REPO = RAIL.parents[1]
PY = sys.executable
COPY = ("rails/watch-builder/backend", "rails/watch-builder/tests")
GUARD = "tools/tests/offline_guard.py"
TEST = "rails/watch-builder/tests/test_images.py"
APP = "rails/watch-builder/backend/watch_builder/api/app.py"
LIMIT = "rails/watch-builder/backend/watch_builder/api/body_limit.py"
IMAGES = "rails/watch-builder/backend/watch_builder/images.py"
TIMEOUT_S = 600

ROUTE = "test_the_route_stops_reading_an_upload_at_the_cap"
EXACT = "test_an_upload_exactly_at_the_cap_reaches_the_decoder"
DECLARED = "test_a_declared_length_over_the_limit_is_refused_before_one_byte_is_read"
CHUNKED = "test_a_body_with_no_length_is_cut_off_at_the_limit"
DECODER = "test_a_format_off_the_allowlist_never_reaches_its_decoder"

# (label, file, old_exact, new, tests that must fail; [] for the control, which must SURVIVE)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "Image.open tries every decoder again (formats= dropped)",
        IMAGES,
        "Image.open(io.BytesIO(raw), formats=sorted(ACCEPTED))",
        "Image.open(io.BytesIO(raw))",
        [DECODER],
    ),
    (
        "the route reads the whole upload again",
        APP,
        "    raw = await _read_capped(file, images.MAX_UPLOAD_BYTES)\n",
        "    raw = await file.read()\n",
        [ROUTE],
    ),
    (
        "off by one: an upload exactly at the cap is refused",
        APP,
        "        if total > cap:\n",
        "        if total >= cap:\n",
        [EXACT],
    ),
    (
        "the body ceiling is removed",
        APP,
        'app.add_middleware(BodyLimit, method="POST", path="/api/images",\n',
        'if False: app.add_middleware(BodyLimit, method="POST", path="/api/images",\n',
        [DECLARED, CHUNKED],
    ),
    (
        "the ceiling ignores a declared length and only counts",
        LIMIT,
        "        if declared is not None and declared > limit:\n",
        "        if False:\n",
        [DECLARED],
    ),
    (
        "the ceiling trusts the declared length and stops counting",
        LIMIT,
        "                if received > limit:\n",
        "                if False:\n",
        [CHUNKED],
    ),
    (
        "CONTROL: a no-op edit to the stepped read",
        APP,
        "    parts: list[bytes] = []\n",
        "    parts: list = []\n",
        [],
    ),
]


def run_suite(root: Path) -> tuple[int, str]:
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONIOENCODING="utf-8",
               PYTHONPATH=str(root / "rails" / "watch-builder" / "backend"))
    env.pop("PLATFORM_STANDALONE", None)
    try:
        p = mr.run_tree([PY, "-m", "pytest", TEST, "-v", "--no-header", "-p", "no:cacheprovider"],
                        cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return 124, f"TIMEOUT after {TIMEOUT_S}s\n{out}"
    return p.returncode, p.stdout + p.stderr


def fresh_copy(dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info", ".pytest_cache", "data")
    for rel in COPY:
        target = dest / rel
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(REPO / rel, target, ignore=ignore)
    # The suite's conftest takes the network guard from tools/tests, found by walking up.
    (dest / GUARD).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO / GUARD, dest / GUARD)


def failed(out: str) -> set[str]:
    return set(re.findall(r"::(\w+) FAILED", out))


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    with tempfile.TemporaryDirectory(prefix="mutate-upload-bound-") as tmp:
        root = Path(tmp) / "tree"
        fresh_copy(root)
        code, out = run_suite(root)
        passed = len(re.findall(r"::\S+ PASSED", out))
        if code != 0 or not passed:
            print(f"BASELINE IS NOT GREEN ({passed} passed, exit {code}) -- fix that before "
                  "trusting any mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green in {root}: {passed} passed\n")

        results: list[tuple[str, str]] = []
        for label, relpath, old, new, expected in MUTATIONS:
            fresh_copy(root)
            path = root / relpath
            n, mutated = mr.mutate_bytes(path.read_bytes(), old, new)
            if n != 1:
                print(f"  ERROR             {label}: pattern found {n} times in {relpath}, "
                      f"expected 1")
                results.append((label, "PATTERN"))
                continue
            path.write_bytes(mutated)
            code, out = run_suite(root)
            red = failed(out)
            if code == 124:
                verdict = "TIMEOUT"
            elif not expected:
                verdict = "SURVIVED" if code == 0 else "FIRED"
            else:
                missing = [t for t in expected if t not in red]
                verdict = ("FIRED" if code != 0 and not missing
                           else "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            ok = verdict == ("SURVIVED" if not expected else "FIRED")
            results.append((label, "OK" if ok else verdict))
            print(f"  {verdict:17} {label}")
            if not ok:
                if expected:
                    print(f"      expected to fail: {', '.join(expected)}; failed: "
                          f"{', '.join(sorted(red)) or 'nothing'}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))

    bad = [r for r in results if r[1] != "OK"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations behaved as required "
          f"(the real tree was never written)")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
