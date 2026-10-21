"""Mutation harness for terminal-fun's isolation. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\terminal-fun\\backend\\tests\\mutate_peer_guard.py

Run this, not just pytest, before trusting a change to any of:
  * main.py's GatewayOnlyIdentity / GatewayPeers / _canonical_ip / _forwarded_for / _RefusalLog,
    and the healthz `peer_check` field;
  * config.py's gateway_host;
  * the `--no-proxy-headers` on the image's CMD (deploy/Dockerfile);
  * the `networks:` keys of terminal-fun and the gateway in BOTH compose files;

The fleet smoke sweep's side (its probe route and the pinned peer_check) is mutated by a harness
kept beside that sweep, in maintainer tooling that the public snapshot does not carry.

Two independent layers keep a Terminal Fun player's shell from being any user it likes: the
network (terminal-fun shares one only with the gateway) and the backend (identity headers count
only from the gateway's address). Every guard either layer rests on fails QUIETLY when it is
lost: the rail keeps serving, the gateway keeps working, and the forged header simply starts
being believed again. Each is broken on purpose below to prove a test notices.

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
RAIL_SRC = REPO / "rails" / "terminal-fun" / "backend"
PY = sys.executable
# Two invocations, not one: the rail and tools/tests have their own rootdir and conftest, and
# mixing them in one run makes collection depend on argument order.
SUITES = [
    (["rails/terminal-fun/backend/tests"], [RAIL_SRC]),
    (["tools/tests/test_terminal_fun_network.py"], []),
]
RUN_TIMEOUT = 300

M = "rails/terminal-fun/backend/terminal_fun_app/main.py"
CFG = "rails/terminal-fun/backend/terminal_fun_app/config.py"
DF = "rails/terminal-fun/deploy/Dockerfile"
FULL = "deploy/docker-compose.yml"
LEAN = "deploy/installer/docker-compose.installer.yml"

FORGED_HTTP = "test_a_forged_identity_from_a_non_gateway_peer_is_refused"
FORGED_WS = "test_a_forged_identity_on_the_terminal_socket_is_closed_before_accept"
SPELLINGS = "test_the_gateway_is_matched_whichever_way_the_address_is_spelled"
RECREATED = "test_a_recreated_gateway_is_recognised_after_its_ip_changes"
XFF = "test_x_forwarded_for_from_loopback_cannot_claim_the_gateway"
OWN_NET = "test_terminal_fun_is_on_its_own_network_only"
BOTH_NETS = "test_the_gateway_joins_both_networks"
BRIDGE = "test_the_isolated_network_is_an_ordinary_bridge"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    # --- the backend half: the middleware itself ---------------------------------------------
    (
        "the middleware is never installed (the defect, on this backend)",
        [(M, "app.add_middleware(GatewayOnlyIdentity, peers=gateway_peers)\n", "")],
        [FORGED_HTTP, FORGED_WS],
    ),
    (
        "only HTTP is checked, so a forged websocket handshake opens a PTY",
        [(M, '        if scope["type"] not in ("http", "websocket") or standalone():\n',
             '        if scope["type"] != "http" or standalone():\n')],
        [FORGED_WS],
    ),
    (
        "the forged handshake is accepted first and closed after",
        [(M, '            await receive()\n'
             '            await send({"type": "websocket.close", "code": 4403})\n',
             '            await receive()\n'
             '            await send({"type": "websocket.accept"})\n'
             '            await send({"type": "websocket.close", "code": 4403})\n')],
        [FORGED_WS],
    ),
    (
        "over-reach: a request with no platform header is refused too",
        [(M, "        if not any(name.lower().startswith(_PLATFORM_HEADER) for name, _ in headers):\n",
             "        if False:\n")],
        ["test_a_request_with_no_platform_header_is_untouched"],
    ),
    (
        "only X-Platform-User counts, so a forged Admin/Peers/Rooms passes",
        [(M, '_PLATFORM_HEADER = b"x-platform-"\n', '_PLATFORM_HEADER = b"x-platform-user"\n')],
        ["test_any_platform_header_counts_not_just_the_user"],
    ),
    (
        "standalone no longer bypasses the check",
        [(M, '        if scope["type"] not in ("http", "websocket") or standalone():\n',
             '        if scope["type"] not in ("http", "websocket"):\n')],
        ["test_standalone_keeps_todays_behaviour"],
    ),
    (
        "the refusal log has no rate limit",
        [(M, "        if self._last is not None and now - self._last < 1.0:\n",
             "        if False:\n")],
        ["test_refusals_are_logged_but_at_most_once_a_second"],
    ),
    (
        "healthz stops reporting the check",
        [(M, ',\n            "peer_check": gateway_peers.mode()}', "}")],
        ["test_healthz_reports_the_check_as_on"],
    ),
    # --- addresses -------------------------------------------------------------------------
    (
        "an IPv4-mapped peer is not folded to IPv4",
        [(M, "    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:\n"
             "        ip = ip.ipv4_mapped\n", "")],
        [SPELLINGS],
    ),
    (
        "an IPv6 scope id is kept as part of the address",
        [(M, '    raw = raw.split("%", 1)[0]\n', "")],
        [SPELLINGS],
    ),
    (
        "a peer that is not an address is compared as a string",
        [(M, "    except ValueError:\n        return None\n",
             "    except ValueError:\n        return raw or None\n")],
        ["test_a_peer_that_is_not_an_address_is_refused"],
    ),
    (
        "a loopback or own-address gateway is not reported as degraded",
        [(M, '        return "degraded" if self.local else "on"\n', '        return "on"\n')],
        ["test_loopback_is_trusted_only_when_the_gateway_resolves_to_it",
         "test_the_containers_own_address_is_degraded_too"],
    ),
    # --- the lookup ------------------------------------------------------------------------
    (
        "the gateway is resolved once and never again (a redeploy locks it out)",
        [(M, "        if self._resolved_at is not None and now - self._resolved_at < self._interval():\n",
             "        if self._resolved_at is not None:\n")],
        [RECREATED],
    ),
    (
        "every miss re-resolves: a loop of forged requests is a loop of lookups",
        [(M, "        if self._resolved_at is not None and now - self._resolved_at < self._interval():\n",
             "        if False:\n")],
        ["test_a_miss_re_resolves_at_most_once_per_interval",
         "test_inside_the_interval_a_new_gateway_waits_for_the_next_lookup"],
    ),
    (
        "a lookup adds to the set, so an address the gateway gave up stays trusted",
        [(M, "        self.addresses = found\n",
             "        self.addresses = self.addresses | found\n")],
        [RECREATED],
    ),
    (
        "a failed lookup empties the set and cuts the gateway off",
        [(M, "                         f\"{sorted(self.addresses) or 'nobody'}\")\n            return\n",
             "                         f\"{sorted(self.addresses) or 'nobody'}\")\n"
             "            self.addresses = frozenset()\n            return\n")],
        ["test_a_failed_lookup_keeps_the_last_answer"],
    ),
    # --- X-Forwarded-For -------------------------------------------------------------------
    (
        "the X-Forwarded-For echo check is gone",
        [(M, "        elif _canonical_ip(peer) in _forwarded_for(headers):\n"
             '            reason = "the peer address came from X-Forwarded-For"\n', "")],
        [XFF],
    ),
    (
        "an X-Forwarded-For host:port is not parsed",
        [(M, '            elif item.count(":") == 1:\n'
             '                item = item.split(":", 1)[0]\n', "")],
        [XFF],
    ),
    (
        "the image runs uvicorn with its default proxy headers again",
        [(DF, '"--port", "8730", "--no-proxy-headers"]', '"--port", "8730"]')],
        ["test_the_image_runs_uvicorn_without_proxy_headers"],
    ),
    (
        "the backend looks the gateway up by a name that is not the gateway's service",
        [(CFG, '    gateway_host: str = "gateway"\n', '    gateway_host: str = "localhost"\n')],
        ["test_terminal_fun_looks_up_the_gateway_by_the_gateways_service_name"],
    ),
    # --- the network half, full stack -------------------------------------------------------
    (
        "full stack: terminal-fun is back on the default network as well (the defect)",
        [(FULL, "    networks:\n      - terminal_fun_net\n    # Sandbox: no new privileges",
                "    networks:\n      - terminal_fun_net\n      - default\n"
                "    # Sandbox: no new privileges")],
        [OWN_NET],
    ),
    (
        "full stack: terminal-fun loses its networks key, so compose puts it on default",
        [(FULL, "    networks:\n      - terminal_fun_net\n    # Sandbox: no new privileges",
                "    # Sandbox: no new privileges")],
        [OWN_NET],
    ),
    (
        "full stack: the gateway is not on terminal-fun's network (Terminal Fun goes dark)",
        [(FULL, "      - default\n      - terminal_fun_net\n    volumes:                     # host-built",
                "      - default\n    volumes:                     # host-built")],
        [BOTH_NETS],
    ),
    (
        "full stack: the gateway lists networks without default (caddy loses it)",
        [(FULL, "      - default\n      - terminal_fun_net\n    volumes:                     # host-built",
                "      - terminal_fun_net\n    volumes:                     # host-built")],
        [BOTH_NETS],
    ),
    (
        "full stack: the network is made internal (the broker is cut off)",
        [(FULL, "\n  terminal_fun_net:\n", "\n  terminal_fun_net:\n    internal: true\n")],
        [BRIDGE],
    ),
    (
        "full stack: another rail joins terminal-fun's network",
        [(FULL, '    expose:\n      - "8830"\n',
                '    networks:\n      - default\n      - terminal_fun_net\n'
                '    expose:\n      - "8830"\n')],
        ["test_nothing_else_joins_the_isolated_network_or_changes_networks"],
    ),
    (
        "full stack: the network is keyed by a service name, which RC033 would misread",
        [(FULL, "      - terminal_fun_net\n    # Sandbox", "      - terminal-fun\n    # Sandbox"),
         (FULL, "      - terminal_fun_net\n    volumes:                     # host-built",
                "      - terminal-fun\n    volumes:                     # host-built"),
         (FULL, "\n  terminal_fun_net:\n", "\n  terminal-fun:\n")],
        ["test_no_network_is_named_after_a_service"],
    ),
    (
        "full stack: a sidecar borrows the gateway's network namespace",
        [(FULL, '    expose:\n      - "8830"\n',
                '    network_mode: "service:gateway"\n    expose:\n      - "8830"\n')],
        ["test_no_service_borrows_another_ones_network_namespace"],
    ),
    # --- healthz's peer_check says what an identity request would meet ------------------------
    (
        "peer_check reads 'on' until something with a platform header triggers a lookup",
        [(M, "        if self._resolved_at is None:\n            self.refresh(self._clock())\n", "")],
        ["test_the_mode_looks_the_gateway_up_before_it_reports"],
    ),
    (
        "a gateway name that never resolved reads 'on'",
        [(M, '        if not self.addresses:\n            return "unresolved"\n', "")],
        ["test_a_gateway_name_that_never_resolved_reports_unresolved"],
    ),
    # --- the network half, the lean installer ------------------------------------------------
    (
        "lean install: terminal-fun is back on the default network as well",
        [(LEAN, "    networks:\n      - terminal_fun_net\n    # The uid split",
                "    networks:\n      - terminal_fun_net\n      - default\n    # The uid split")],
        [OWN_NET],
    ),
    (
        "lean install: the gateway is not on terminal-fun's network",
        [(LEAN, "      - default\n      - terminal_fun_net\n    volumes:\n      - gateway_data:/data",
                "      - default\n    volumes:\n      - gateway_data:/data")],
        [BOTH_NETS],
    ),
    (
        "lean install: the network is made internal",
        [(LEAN, "\n  terminal_fun_net:\n", "\n  terminal_fun_net:\n    internal: true\n")],
        [BRIDGE],
    ),
]


def _env(roots: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree's roots, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = os.pathsep.join(str(r) for r in roots)
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import terminal_fun_app.main as m, terminal_fun_app.config as c;"
         "print(m.__file__); print(c.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env([RAIL_SRC]), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    """Every suite; the worst exit code wins. -1 means a run timed out."""
    worst, out = 0, ""
    for files, roots in SUITES:
        try:
            p = mr.run_tree([PY, "-B", "-m", "pytest", *files, "-q", "--no-header",
                             "-p", "no:cacheprovider", *extra],
                            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=_env(roots), timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return -1, out + partial + f"\nTIMEOUT after {RUN_TIMEOUT} s"
        out += (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0 and worst == 0:
            worst = p.returncode
    return worst, out


def failed_names(out: str) -> set[str]:
    """Test names on pytest's FAILED lines (Class::test for a method), parametrize ids dropped."""
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
    summaries = [ln for ln in out.splitlines() if " passed" in ln or " skipped" in ln]
    skipped = [ln for ln in summaries if "skipped" in ln]
    if skipped:
        print("BASELINE SKIPPED TESTS -- " + " | ".join(skipped))
        return 2
    print("baseline green: " + " | ".join(summaries) + "\n")

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
