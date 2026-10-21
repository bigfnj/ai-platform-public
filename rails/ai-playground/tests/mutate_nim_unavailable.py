"""Mutation harness for the NVIDIA model-retirement path. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\ai-playground\\tests\\mutate_nim_unavailable.py

Run this, not just pytest, before trusting a change to nim.ModelUnavailable, nim._complete,
the NIM chat-model default, tools/tests/test_nim_model_consistency.py, the weekly
/api/maintenance/nim-check route, or its entry in the gateway's scheduler_tasks.py.

Every guard here fails QUIETLY. Drop the 410 translation and the toggle still fails, just with
`Error code: 410 - {...}` again, which is how a nine-day outage reads as "NVIDIA is broken"
instead of "change one env var". Echo NVIDIA's 404 detail and the account id goes to the
browser, with every test of the message's helpfulness still green. Let the .env.example example
drift and nothing breaks until an operator copies it. Answer the weekly check with a 200 over a
retired model, or drop its schedule entry, and Admin > Schedule shows a green row (or none) for
a dead demo path, which is the 16-day failure all over again. Each guard below is broken on
purpose to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py), for its command line: --list names the
# cases and exits before anything is touched. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
RAIL = REPO / "rails" / "ai-playground"
RAIL_SRC = RAIL / "src"
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
PY = sys.executable
# Three pytest runs, not one: the rail, the tools and the gateway suites each have their own
# conftest, rootdir and import roots, and mixing them in one invocation makes collection depend
# on argument order. (cwd, files, PYTHONPATH): THIS tree's roots only, because the repo venv's
# editable installs point at one checkout and a worktree run would test somebody else's code.
SUITES = [
    (RAIL, ["tests/test_nim_model_unavailable.py", "tests/test_nim_client.py",
            "tests/test_nim_maintenance_check.py"], [RAIL_SRC]),
    (REPO, ["tools/tests/test_nim_model_consistency.py"], [RAIL_SRC]),
    (REPO, ["apps/platform/backend/tests/test_scheduler_nim_check.py"], [BACKEND, CORE]),
]
# One run takes a few seconds. A mutation that makes a test hang must not hang the harness.
RUN_TIMEOUT = 300

N = "rails/ai-playground/src/ai_playground/nim.py"
A = "rails/ai-playground/src/ai_playground/api/app.py"
S = "apps/platform/backend/platform_gateway_app/scheduler_tasks.py"
FULL = "deploy/docker-compose.yml"
LEAN = "deploy/installer/docker-compose.installer.yml"
EXAMPLE = "deploy/.env.example"
NOW = "meta/llama-3.2-11b-vision-instruct"
GONE = "mistralai/mistral-nemotron"


def _check_entry() -> str:
    """The weekly check's whole registry entry, read from the file, so "the task is removed" is
    a real removal and the pattern cannot go stale when its description is reworded. Missing
    anchor -> "" , which the exactly-once rule then reports as PATTERN rather than crashing."""
    text = (REPO / S).read_text(encoding="utf-8")
    anchor = '        "rail": "ai-playground", "task_id": "nim-check"'
    if anchor not in text:
        return ""
    head = text.index(anchor)
    start = text.rindex("    {\n", 0, head)
    end = text.index("    },\n", head) + len("    },\n")
    return text[start:end]

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "a 410 is no longer translated, so the operator gets the bare 'Error code: 410'",
        [(N, "    if exc.status_code == 410:\n", "    if exc.status_code == 4100:\n")],
        ["test_a_retired_model_names_itself_and_the_fix",
         "test_the_streaming_path_says_so_too",
         "test_the_probe_route_shows_the_retirement_where_the_toggle_can_read_it"],
    ),
    (
        "the entitlement 404 is matched case-sensitively, so NVIDIA's real wording misses it",
        [(N, '"not found for account" in detail.lower():', '"not found for account" in detail:')],
        ["test_an_unentitled_model_says_so_without_the_account_id",
         "test_the_streaming_path_names_the_entitlement_trap_too"],
    ),
    (
        "every 404 is called an entitlement problem, so a typo is sent to the wrong fix",
        [(N, '    if exc.status_code == 404 and "not found for account" in detail.lower():\n',
             "    if exc.status_code == 404:\n")],
        ["test_a_plain_404_is_left_alone"],
    ),
    (
        "the 404 message echoes NVIDIA's detail, account id and all, toward the browser",
        [(N, 'may still list it. {_FIX}")', 'may still list it. {_FIX} {detail}")')],
        ["test_an_unentitled_model_says_so_without_the_account_id"],
    ),
    (
        "NVIDIA's own end-of-life date is dropped from the 410 message",
        [(N, '        said = f" NVIDIA said: {detail}" if detail else ""\n',
             '        said = ""\n')],
        ["test_a_retired_model_names_itself_and_the_fix"],
    ),
    (
        "the 410 message no longer says which variable to change",
        [(N, "(410 Gone). {_FIX}{said}\")", "(410 Gone).{said}\")")],
        ["test_a_retired_model_names_itself_and_the_fix"],
    ),
    (
        "the model id and cause are buried past the 160 characters the toggle shows",
        [(N, "f\"NVIDIA has retired the cloud model '{CHAT_MODEL}' (410 Gone). {_FIX}{said}\")",
             "f\"{_FIX}{said} NVIDIA has retired the cloud model '{CHAT_MODEL}' (410 Gone).\")")],
        ["test_the_probe_route_shows_the_retirement_where_the_toggle_can_read_it"],
    ),
    (
        "the streaming path calls the client directly, bypassing the translation",
        [(N, "        stream = await _complete(client, messages=messages, stream=True,\n",
             "        stream = await client.chat.completions.create(model=CHAT_MODEL, "
             "messages=messages, stream=True,\n")],
        ["test_the_streaming_path_says_so_too",
         "test_the_streaming_path_names_the_entitlement_trap_too"],
    ),
    (
        "the probe calls the client directly, bypassing the translation",
        [(N, '        await _complete(client, max_tokens=1, messages=[{"role": "user", '
             '"content": "ping"}])\n',
             '        await client.chat.completions.create(model=CHAT_MODEL, max_tokens=1, '
             'messages=[{"role": "user", "content": "ping"}])\n')],
        ["test_a_retired_model_names_itself_and_the_fix",
         "test_an_unentitled_model_says_so_without_the_account_id",
         "test_the_probe_route_shows_the_retirement_where_the_toggle_can_read_it"],
    ),
    (
        "every other failure is relabelled a model problem",
        [(N, "        if plain is None:\n            raise\n        raise plain from exc\n",
             "        raise (plain or ModelUnavailable(str(exc))) from exc\n")],
        ["test_every_other_failure_passes_through_unchanged",
         "test_a_plain_404_is_left_alone"],
    ),
    (
        "the original NVIDIA error is not chained, so the log loses the raw answer",
        [(N, "        raise plain from exc\n", "        raise plain from None\n")],
        ["test_a_retired_model_names_itself_and_the_fix"],
    ),
    (
        "the .env.example example drifts back to a retired model",
        [(EXAMPLE, f"# AI_PLAYGROUND_NVIDIA_CHAT_MODEL={NOW}",
                   f"# AI_PLAYGROUND_NVIDIA_CHAT_MODEL={GONE}")],
        ["test_all_four_sites_name_the_same_model"],
    ),
    (
        "all four sites agree on the SECOND retired model, which only RETIRED can catch",
        [(N, f'"AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "{NOW}")',
             f'"AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "{GONE}")'),
         (FULL, f"${{AI_PLAYGROUND_NVIDIA_CHAT_MODEL:-{NOW}}}",
                f"${{AI_PLAYGROUND_NVIDIA_CHAT_MODEL:-{GONE}}}"),
         (LEAN, f"${{AI_PLAYGROUND_NVIDIA_CHAT_MODEL:-{NOW}}}",
                f"${{AI_PLAYGROUND_NVIDIA_CHAT_MODEL:-{GONE}}}"),
         (EXAMPLE, f"# AI_PLAYGROUND_NVIDIA_CHAT_MODEL={NOW}",
                   f"# AI_PLAYGROUND_NVIDIA_CHAT_MODEL={GONE}")],
        ["test_a_retired_model_cannot_come_back"],
    ),
    # --- the weekly check: /api/maintenance/nim-check and its scheduler entry ---------------
    (
        "the weekly check reports a retired model as ok, so the Schedule row stays green",
        [(A, "        except Exception as exc:  # noqa: BLE001 — ModelUnavailable's text is "
             "the point\n"
             "            raise HTTPException(status_code=502, detail=str(exc)) from exc\n",
             "        except Exception as exc:  # noqa: BLE001 — ModelUnavailable's text is "
             "the point\n"
             '            return {"ok": True, "model": nim.CHAT_MODEL, "latency_s": 0.0}\n')],
        ["test_a_retired_model_fails_the_run_with_the_plain_message"],
    ),
    (
        "the weekly check fails with a generic detail, so the record never names the model",
        [(A, "ModelUnavailable's text is the point\n"
             "            raise HTTPException(status_code=502, detail=str(exc)) from exc\n",
             "ModelUnavailable's text is the point\n"
             '            raise HTTPException(status_code=502, detail="NVIDIA probe failed") '
             "from exc\n")],
        ["test_a_retired_model_fails_the_run_with_the_plain_message"],
    ),
    (
        "no key fails the run, so a local-only box shows a red row every week",
        [(A, '            return {"ok": True, "skipped": "no NVIDIA key configured", '
             '"model": nim.CHAT_MODEL}\n',
             '            raise HTTPException(status_code=503, detail="no NVIDIA key configured")\n')],
        ["test_no_key_is_a_skip_and_spends_nothing"],
    ),
    (
        "a network failure reads as a verdict on the model",
        [(A, "            raise HTTPException(status_code=503,\n",
             "            raise HTTPException(status_code=502,\n")],
        ["test_no_answer_at_all_is_503_not_a_model_verdict"],
    ),
    (
        "any named caller can fire the paid check",
        [(A, "async def maintenance_nim_check(ident: Identity = Depends(deps.require_admin))",
             "async def maintenance_nim_check(ident: Identity = Depends(deps.identity))")],
        ["test_admin_only_and_a_refusal_spends_nothing"],
    ),
    (
        "the weekly check is not charged to the probe limiter",
        [(A, "        _charge_nim(_NIM_PROBE_LIMIT, ident.user)\n        started = time.perf_counter()\n",
             "        started = time.perf_counter()\n")],
        ["test_it_shares_the_toggle_probes_rate_limit"],
    ),
    (
        "the weekly check gets a budget of its own, doubling what one caller can spend",
        [(A, "        _charge_nim(_NIM_PROBE_LIMIT, ident.user)\n        started = time.perf_counter()\n",
             "        ratelimit.RateLimit(limit=config.NIM_PROBE_LIMIT, window_seconds=60, "
             'name="x").check(ident.user)\n'
             "        started = time.perf_counter()\n")],
        ["test_it_shares_the_toggle_probes_rate_limit"],
    ),
    (
        "the weekly task is removed from the registry, so nothing ever fires the check",
        [(S, _check_entry(), "")],
        ["test_the_task_is_registered_synchronous_and_weekly",
         "test_it_is_seeded_on_a_deployment_with_the_rail",
         "test_the_platform_scheduler_fires_this_route"],
    ),
    (
        "the weekly task is fire-and-forget, so the record says triggered, never the verdict",
        [(S, '"path": "/api/maintenance/nim-check",\n',
             '"path": "/api/maintenance/nim-check", "async": True,\n')],
        ["test_the_task_is_registered_synchronous_and_weekly",
         "test_a_working_model_reads_ok_with_model_and_latency",
         "test_the_platform_scheduler_fires_this_route"],
    ),
]


# Decode child output as UTF-8 whatever the console code page; see PYTHONIOENCODING below.
_TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}


def _env(roots: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree's roots, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = os.pathsep.join(str(r) for r in roots)
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # The registry carries emoji icons, and a failing assertion prints them. Without this the
    # child writes cp1252 on Windows, the reader thread dies on the first emoji, and the run
    # comes back with stdout=None instead of a verdict.
    env["PYTHONIOENCODING"] = "utf-8"
    # A deployment's own override would make the default under test irrelevant.
    for var in ("AI_PLAYGROUND_NVIDIA_CHAT_MODEL", "AI_PLAYGROUND_NVIDIA_API_KEY",
                "AI_PLAYGROUND_NVIDIA_BASE_URL"):
        env.pop(var, None)
    return env


# (module, the root it must import from, that root's PYTHONPATH)
_WITNESSES = [
    ("ai_playground.nim", RAIL_SRC, [RAIL_SRC]),
    ("ai_playground.api.app", RAIL_SRC, [RAIL_SRC]),
    ("platform_gateway_app.scheduler_tasks", BACKEND, [BACKEND, CORE]),
]


def witness() -> str | None:
    """Prove each module under test resolves to THIS tree before any verdict means anything."""
    for module, root, roots in _WITNESSES:
        p = mr.run_tree([PY, "-B", "-c", f"import {module} as m; print(m.__file__)"],
                        cwd=REPO, capture_output=True, env=_env(roots), **_TEXT,
                        timeout=RUN_TIMEOUT)
        if p.returncode != 0:
            return f"import of {module} failed:\n{p.stdout}{p.stderr}"
        f = p.stdout.strip().splitlines()[-1]
        if not Path(f).resolve().is_relative_to(root):
            return f"imported {f}, which is not under {root}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    """Every suite; the worst exit code wins. -1 means a run timed out."""
    worst, out = 0, ""
    for cwd, files, roots in SUITES:
        try:
            p = mr.run_tree([PY, "-B", "-m", "pytest", *files, "-q", "--no-header",
                             "-p", "no:cacheprovider", *extra],
                            cwd=cwd, capture_output=True, env=_env(roots), **_TEXT,
                            timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return -1, out + partial + f"\nTIMEOUT after {RUN_TIMEOUT} s"
        out += (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0 and worst == 0:
            worst = p.returncode
    return worst, out


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def _apply(path: Path, old: str, new: str) -> int:
    """Replace `old` once, in the file's own line endings, as bytes. Returns the match count.

    Bytes, not text: read_text/write_text translate line endings on Windows, so a "restore"
    could rewrite an LF file as CRLF (or this CRLF checkout as LF) and leave a diff behind.
    """
    raw = path.read_bytes()
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    o = old.encode("utf-8").replace(b"\n", eol)
    n = raw.count(o)
    if n == 1:
        path.write_bytes(raw.replace(o, new.encode("utf-8").replace(b"\n", eol)))
    return n


def main() -> int:
    mr.parse_harness_args(MUTATIONS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from a FAILED line can mean anything: a
    # renamed test would otherwise turn every mutation naming it into a silent SURVIVED.
    _, collected = run_suite(("--collect-only",))
    named = {t for _, _, tests in MUTATIONS for t in tests}
    missing = sorted(t for t in named if t not in collected)
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print("baseline green: " + " | ".join(
        ln for ln in out.splitlines() if " passed" in ln) + "\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        originals: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                originals.setdefault(path, path.read_bytes())
                n = _apply(path, old, new)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
            if not pattern_ok:
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
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
            for path, raw in originals.items():
                path.write_bytes(raw)
                assert path.read_bytes() == raw, f"restore of {path} did not round-trip"

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
