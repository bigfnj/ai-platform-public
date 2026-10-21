"""Mutation harness for tools/check_commit_domains.py. NOT a pytest file.

    .venv\\Scripts\\python.exe tools\\tests\\mutate_check_commit_domains.py

Copies the tool and its test into a temp directory with the same layout, breaks the COPY one
way at a time, and runs the copied test there. This tree is never written.

Every mutation is a way the guard fails OPEN: it still runs, still prints a tidy summary, and
lets a commit through that it exists to stop. A guard like that is indistinguishable from a
working one until the day it matters, so each must turn the suite red and name its test.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PY = sys.executable
G = "tools/check_commit_domains.py"
TEST = "tools/tests/test_check_commit_domains.py"
FILES = (G, TEST)
EXPECTED_PASSING = 15       # the witness: the copied suite really collected and ran
TIMEOUT_S = 300

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "only the author is checked, so a foreign committer slips through",
        G,
        '        for role, email in (("author", author), ("committer", committer)):',
        '        for role, email in (("author", author),):',
        "test_a_foreign_committer_domain_fails",
    ),
    (
        "a suffix match accepts any domain that ends in an allowed one",
        G,
        "    return domain_of(email) in ALLOWED_DOMAINS",
        "    return any(domain_of(email).endswith(d) for d in ALLOWED_DOMAINS)",
        "test_lookalike_domains_are_refused",
    ),
    (
        "a substring match accepts an allowed name used as a prefix",
        G,
        "    return domain_of(email) in ALLOWED_DOMAINS",
        "    return any(d in domain_of(email) for d in ALLOWED_DOMAINS)",
        "test_lookalike_domains_are_refused",
    ),
    (
        "the domain is compared case-sensitively",
        G,
        '    return email.rsplit("@", 1)[1].strip().lower() if "@" in email else ""',
        '    return email.rsplit("@", 1)[1].strip() if "@" in email else ""',
        "test_domain_case_is_ignored",
    ),
    (
        "the whole history is judged, so every push fails on commits that predate the guard",
        G,
        '    return ([f"{base}..{head}"], None)',
        "    return ([head], None)",
        "test_only_commits_in_the_range_are_judged",
    ),
    (
        "only first-parent commits are checked, so a merged branch's commits are skipped",
        G,
        '    out = _git(repo, "log", "--format=%H%x1f%ae%x1f%ce", *revs)',
        '    out = _git(repo, "log", "--first-parent", "--format=%H%x1f%ae%x1f%ce", *revs)',
        "test_a_merge_brings_its_side_branch_into_the_range",
    ),
    (
        "a degraded run (unknown base) says nothing about being degraded",
        G,
        "    if degraded:\n        lines.append(",
        "    if False:\n        lines.append(",
        "test_an_unknown_base_checks_the_tip_and_says_so",
    ),
    (
        "a degraded run checks nothing at all",
        G,
        '        return ([head, "--no-walk"],',
        '        return ([head, "--no-walk", "--max-count=0"],',
        "test_a_degraded_run_still_fails_a_foreign_tip",
    ),
    (
        "a violation still exits 0",
        G,
        "    return (1 if bad else 0), lines",
        "    return 0, lines",
        "test_a_foreign_author_domain_fails_and_names_only_the_domain",
    ),
    (
        "the full address is printed instead of the domain",
        G,
        "                             f\"{domain_of(email) or '(no domain)'}\")",
        '                             f"{email}")',
        "test_a_foreign_author_domain_fails_and_names_only_the_domain",
    ),
    (
        "a git failure is reported but exits 0",
        G,
        '        print(f"ERROR: cannot check commit domains: {exc}")\n        return 2',
        '        print(f"ERROR: cannot check commit domains: {exc}")\n        return 0',
        "test_a_bad_head_is_an_error_not_a_pass",
    ),
    (
        "the allowlist quietly grows a third domain",
        G,
        'ALLOWED_DOMAINS = ("gmail.com", "users.noreply.github.com")',
        'ALLOWED_DOMAINS = ("gmail.com", "users.noreply.github.com", "corp.example")',
        "test_the_allowlist_is_exactly_the_two_domains",
    ),
]


def run_suite(root: Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)          # nothing imports a platform package
    try:
        p = mr.run_tree([PY, "-m", "pytest", TEST, "-v", "--no-header", "-p", "no:cacheprovider"],
                        cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        return 124, f"TIMEOUT after {TIMEOUT_S}s\n{exc.stdout or ''}{exc.stderr or ''}"
    return p.returncode, p.stdout + p.stderr


def fresh_copy(dest: Path) -> None:
    for rel in FILES:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dest / rel)


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    real = mr.snapshot(REPO / rel for rel in {*FILES, *(m[1] for m in MUTATIONS)}
                       if (REPO / rel).is_file())
    with tempfile.TemporaryDirectory(prefix="mutate-commit-domains-") as tmp:
        root = Path(tmp) / "tree"
        fresh_copy(root)
        code, out = run_suite(root)
        passed = out.count(" PASSED")
        if code != 0 or passed != EXPECTED_PASSING or "test_check_commit_domains.py::" not in out:
            print(f"BASELINE IS NOT GREEN ({passed}/{EXPECTED_PASSING} passed, exit {code}) -- "
                  "fix that before trusting any mutation result.\n")
            print(out[-2500:])
            return 2
        print(f"baseline green in {root}: {passed}/{EXPECTED_PASSING} passed\n")

        results: list[tuple[str, str]] = []
        for label, relpath, old, new, expected in MUTATIONS:
            fresh_copy(root)
            path = root / relpath
            original = path.read_text(encoding="utf-8")
            n = original.count(old)
            if n != 1:
                print(f"  ERROR             {label}: pattern found {n} times in {relpath}, expected 1")
                results.append((label, "PATTERN"))
                continue
            path.write_text(original.replace(old, new), encoding="utf-8", newline="")
            code, out = run_suite(root)
            fired = code != 0
            named = mr.named_failed(out, expected)
            verdict = "FIRED" if fired and named else ("FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))

    # Proven, not assumed: every real file this harness copies or mutates is byte-identical
    # to what it found (raises TREE IS DIRTY otherwise).
    mr.assert_unchanged(real)
    print(f"real tree: untouched ({len(real)} files byte-identical)")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired the right test "
          f"(the real tree was never written)")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
