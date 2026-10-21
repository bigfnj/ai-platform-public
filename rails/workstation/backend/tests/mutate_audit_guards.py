"""Mutation harness for the host-shell rail's hardening. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\workstation\\backend\\tests\\mutate_audit_guards.py

Run this, not just pytest, before trusting a change to main.py's dimension clamp, its app-wide
identity gate and disabled docs, the audit logger's degrade path and its own file handler (one
per audit file, whatever other handlers the logger carries), the socket's own 4401
backstop, or config.py's one-time parse of the JSON overrides.

This rail opens a shell on the host, and every one of these fails QUIETLY: an unclamped size
reaches the host's conpty as sent, an un-gated healthz tells the compose network whether host
keys are checked, a malformed override answers 500 on every call while the rail looks up. Each
guard is broken on purpose below to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held.
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
RAIL_SRC = REPO / "rails" / "workstation" / "backend"
CORE = REPO / "packages" / "platform_core"
PY = sys.executable
SUITE = ["rails/workstation/backend/tests"]
RUN_TIMEOUT = 300

M = "rails/workstation/backend/workstation_app/main.py"
C = "rails/workstation/backend/workstation_app/config.py"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "the query-string size reaches the host unclamped again",
        [(M, '    cols = _clamp_dim(ws.query_params.get("cols", 80), 80)\n',
             '    cols = max(1, int(ws.query_params.get("cols", 80)))\n')],
        ["test_the_query_string_size_is_clamped_before_it_reaches_the_host"],
    ),
    (
        "the resize frame reaches the host unclamped again",
        [(M, '                        process.change_terminal_size(_clamp_dim(size["cols"], cols),\n',
             '                        process.change_terminal_size(int(size["cols"]),\n')],
        ["test_a_resize_frame_is_clamped_too"],
    ),
    (
        "the clamp loses its upper bound",
        [(M, "        return max(1, min(_MAX_DIM, int(value)))", "        return max(1, int(value))")],
        ["test_the_query_string_size_is_clamped_before_it_reaches_the_host",
         "test_a_resize_frame_is_clamped_too"],
    ),
    (
        "the app-wide identity gate is dropped, so healthz answers anyone again",
        [(M, "              dependencies=[Depends(identity)])\n", "              )\n")],
        ["test_healthz_requires_identity_too",
         "test_every_http_route_refuses_a_caller_with_no_identity"],
    ),
    (
        "the interactive docs are published again",
        [(M, "              docs_url=None, redoc_url=None, openapi_url=None,\n", "")],
        ["test_interactive_docs_are_not_served_even_to_a_named_caller"],
    ),
    (
        "an unopenable audit log crashes the boot again",
        [(M, "        except OSError as exc:\n"
             '            _log.warning("audit disabled: cannot open %s/sessions.log (%s)",\n'
             "                         settings.audit_dir, exc)\n"
             "            return None\n",
             "        except ImportError:\n            return None\n")],
        ["test_an_unopenable_audit_log_degrades_to_no_audit"],
    ),
    (
        "the socket's own refusal is removed (the dependency becomes the only gate)",
        [(M, '               error="no platform identity on the handshake")\n'
             "        await ws.close(code=4401)\n        return\n",
             '               error="no platform identity on the handshake")\n')],
        ["test_the_socket_refuses_on_its_own_too"],
    ),
    (
        "the presets override is trusted unvalidated (500 per request on a bad entry)",
        [(C, '            self._presets = parse_entries(self.presets_json, "WORKSTATION_PRESETS_JSON",\n'
             '                                          ("id", "label"))\n',
             "            self._presets = json.loads(self.presets_json)\n")],
        ["test_a_bad_presets_override_stops_the_rail_at_startup"],
    ),
    (
        "the override is parsed again on every request",
        [(C, "        if self._presets is not None:\n            return self._presets\n",
             "        if self._presets is not None:\n            return json.loads(self.presets_json)\n")],
        ["test_a_good_override_is_parsed_once_and_icon_is_optional"],
    ),
    (
        "an RDP app without an alias is accepted (KeyError in the launcher)",
        [(C, '                                           ("id", "label", "alias"))\n',
             '                                           ("id", "label"))\n')],
        ["test_a_bad_rdp_override_stops_the_rail_at_startup"],
    ),
    (
        "a preset without an icon 500s the presets route again",
        [(M, '"icon": p.get("icon", "")}', '"icon": p["icon"]}')],
        ["test_a_good_override_is_parsed_once_and_icon_is_optional"],
    ),
    # -- the 2026-10-14 audit: a refused handshake is audited where it is refused
    (
        "a refused handshake is not audited again",
        [(M, '    if conn.scope.get("type") == "websocket":\n', "    if False:\n")],
        ["test_a_refused_handshake_writes_an_audit_line"],
    ),
    (
        "an HTTP 401 is audited as a refused handshake",
        [(M, '    if conn.scope.get("type") == "websocket":\n', "    if True:\n")],
        ["test_a_refused_handshake_writes_an_audit_line"],
    ),
    (
        "the refusal's audit line names no preset",
        [(M, '               preset=conn.path_params.get("preset", "-"), status=exc.status_code,\n',
             '               preset="-", status=exc.status_code,\n')],
        ["test_a_refused_handshake_writes_an_audit_line"],
    ),
    (
        "the handler answers a refused handshake itself instead of with FastAPI's 401",
        [(M, "    return await http_exception_handler(conn, exc)  # type: ignore[arg-type]\n",
             "    return Response(status_code=403)\n")],
        ["test_websocket_without_identity_is_refused",
         "test_a_refused_handshake_writes_an_audit_line"],
    ),
    # -- 2026-10-14: the audit file is opened when the logger has no file handler of its own
    (
        "the file handler is added only when the logger has no handler at all, as before",
        [(M, "    if not any(h.baseFilename == path for h in files):\n",
             "    if not log.handlers:\n")],
        ["test_a_refused_handshake_reaches_this_test_s_audit_file"],
    ),
    (
        "any file handler counts as this audit dir's own",
        [(M, "    if not any(h.baseFilename == path for h in files):\n",
             "    if not files:\n")],
        ["test_a_refused_handshake_reaches_this_test_s_audit_file"],
    ),
    (
        "an earlier app's file handler is kept beside the new one, so both files get every line",
        [(M, "        for stale in files:\n            log.removeHandler(stale)\n"
             "            stale.close()\n", "")],
        ["test_a_refused_handshake_reaches_this_test_s_audit_file"],
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = os.pathsep.join([str(RAIL_SRC), str(CORE)])
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "WORKSTATION_PRESETS_JSON", "WORKSTATION_RDP_APPS_JSON"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import workstation_app.main as m, workstation_app.config as c;"
         "print(m.__file__); print(c.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider", *extra],
                        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


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
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original_bytes: dict[Path, bytes] = {}
        try:
            pattern_ok = True
            for rel, old, new in edits:
                path = REPO / rel
                original_bytes.setdefault(path, path.read_bytes())
                current = path.read_text(encoding="utf-8")
                n = current.count(old)
                if n != 1:
                    print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
                    pattern_ok = False
                    break
                mr.write_like(path, current.replace(old, new), original_bytes[path])
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
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
