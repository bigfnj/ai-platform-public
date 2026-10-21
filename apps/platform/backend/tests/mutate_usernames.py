"""Mutation harness for the username rules. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_usernames.py

Run this, not just pytest, before trusting a change to login, user creation or the startup
username check in main.py.

These guards fail OPEN and quietly. Drop the case-insensitive duplicate check and the admin
console still creates users with a 200; the second spelling of a name simply exists, and with
it a second account behind one login box. Before the broker's owner check became exact, that
second spelling also spent the owner's Claude subscription. Nothing on screen changes. So does
the login's equal cost for an unknown name (added 2026-10-14): skip its Argon2 and every login
still works, only faster for names that do not exist, which is what tells them apart.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
reported as FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
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
SUITE = ["apps/platform/backend/tests/test_usernames.py"]

MAIN = "apps/platform/backend/platform_gateway_app/main.py"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # --- login timing (added 2026-10-14) ---------------------------------------------------------
    (
        "an unknown name skips Argon2 again, so the refusal's timing says who exists",
        MAIN,
        "    ok = verify_password(body.password, user.password_hash if user is not None else "
        "_DUMMY_HASH)\n    if user is None or not ok:",
        "    if user is None or not verify_password(body.password, user.password_hash):",
        ["test_an_unknown_name_costs_the_same_argon2_as_a_known_one"],
    ),
    (
        "the dummy is no Argon2 hash, so verifying an unknown name costs nothing",
        MAIN,
        "_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))",
        '_DUMMY_HASH = "not-a-hash"',
        ["test_an_unknown_name_costs_the_same_argon2_as_a_known_one"],
    ),
    (
        "login matches case exactly again, so Bob cannot sign in as bob",
        MAIN,
        "        func.lower(User.username) == func.lower(body.username))).scalar_one_or_none()",
        "        User.username == body.username)).scalar_one_or_none()",
        ["test_login_ignores_case_and_signs_in_the_stored_account"],
    ),
    (
        "the create route's duplicate check is exact again",
        MAIN,
        "        func.lower(User.username) == func.lower(uname))).first()",
        "        User.username == uname)).first()",
        ["test_the_route_refuses_a_case_variant_without_the_index_too"],
    ),
    (
        "a create that races past the check surfaces the IntegrityError as a 500",
        MAIN,
        "    try:\n"
        "        db.commit()\n"
        "    except IntegrityError:\n",
        "    try:\n"
        "        db.commit()\n"
        "    except ZeroDivisionError:\n",
        ["test_a_create_that_races_past_the_check_gets_409_not_500"],
    ),
    (
        "the folded unique index is never built, so the database allows a case variant",
        MAIN,
        "        if not groups:\n"
        "            conn.exec_driver_sql(",
        "        if not groups:\n"
        "            return\n"
        "            conn.exec_driver_sql(",
        ["test_the_database_itself_refuses_a_case_variant",
         "test_startup_passes_and_is_repeatable_on_a_clean_database"],
    ),
    (
        "the startup check only prints, so a colliding pair keeps running",
        MAIN,
        "    raise RuntimeError(\n"
        '        f"refusing to start:',
        "    print(\n"
        '        f"refusing to start:',
        ["test_startup_refuses_colliding_accounts_and_names_them_by_id_only"],
    ),
    (
        "the startup check reports names instead of ids",
        MAIN,
        "        groups = [conn.execute(select(User.id).where(folded == k)",
        "        groups = [conn.execute(select(User.username).where(folded == k)",
        ["test_startup_refuses_colliding_accounts_and_names_them_by_id_only"],
    ),
    (
        "the startup check counts every name as a collision",
        MAIN,
        "group_by(folded).having(func.count() > 1)",
        "group_by(folded).having(func.count() > 0)",
        ["test_startup_passes_and_is_repeatable_on_a_clean_database"],
    ),
    (
        "the lifespan never runs the startup check",
        MAIN,
        "    _refuse_case_colliding_usernames(db)\n"
        "    app.state.db = db\n",
        "    app.state.db = db\n",
        ["test_the_real_lifespan_runs_the_check_before_anything_else_touches_users"],
    ),
    (
        "the ASCII rule is dropped, so an accented name is created and 500s on every rail",
        MAIN,
        '    if len(uname) > _USERNAME_MAX or not all(" " <= ch <= "~" for ch in uname):',
        "    if len(uname) > _USERNAME_MAX:",
        ["test_a_name_a_header_cannot_carry_is_refused"],
    ),
    (
        "the length rule is dropped, so a name wider than the column is created",
        MAIN,
        '    if len(uname) > _USERNAME_MAX or not all(" " <= ch <= "~" for ch in uname):',
        '    if not all(" " <= ch <= "~" for ch in uname):',
        ["test_a_name_a_header_cannot_carry_is_refused"],
    ),
]

# One pytest run takes a few seconds. A mutation that makes a test hang must not hang the
# harness; a run that times out is reported as TIMEOUT, never as FIRED.
RUN_TIMEOUT = 300


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first. The repo venv carries editable installs that point at one checkout; run
    # from a worktree without this and pytest imports the gateway from somewhere else, so every
    # mutation "survives" against code the run never touched.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    # No .pyc written. A mutation and its restore can land in the same second with the same
    # file size, and the import system would then trust a stale cached compile of the OTHER one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def witness() -> str | None:
    """Prove the module under test resolves to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c", "import platform_gateway_app.main as m; print(m.__file__)"],
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
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
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
        path = REPO / relpath
        original = path.read_text(encoding="utf-8")
        original_bytes = path.read_bytes()
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            mr.write_like(path, original.replace(old, new), original_bytes)
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = "TIMEOUT" if code == -1 else "FIRED" if fired and not missing else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original_bytes)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
