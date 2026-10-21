"""Mutation harness for the gateway-only identity check. NOT a pytest file.

    .venv\\Scripts\\python.exe rails\\openmaic\\tests\\mutate_peer_guard.py [label-substring ...]

Run this, not just pytest, before trusting a change to api/gateway_only.py (GatewayOnlyIdentity,
GatewayPeers, _canonical_ip, _forwarded_for, _DropLog), its registration and the healthz line in
api/app.py, config.py's gateway_host, or the image's --no-proxy-headers.

Every one of these fails SILENTLY. A check that is gone, or that skips one path, lets any
container on the compose network be anybody to this rail again: keep the owner's name on the
shim's calls through the proxy's ledger, or use the shim with no token at all. A check that drops
too much breaks openmaic-app's generation, and one that forgets the gateway after a DNS hiccup
logs every user out. None of it shows in a status code anybody is watching.

Each case names the test that must fail. CONTROLS are rewrites that change nothing the suite
can see, and must SURVIVE. Its sibling, the original of this check, is
rails/terminal-fun/backend/tests/mutate_peer_guard.py; the published-token guard is in
mutate_cli_requester.py beside this file.
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

REPO = Path(__file__).resolve().parents[3]
RAIL = REPO / "rails" / "openmaic"
RAIL_SRC = RAIL / "backend"
PY = sys.executable
SUITE = ["tests/test_gateway_peer.py", "tests/test_auth.py"]
RUN_TIMEOUT = 300

G = "rails/openmaic/backend/openmaic_app/api/gateway_only.py"
A = "rails/openmaic/backend/openmaic_app/api/app.py"
C = "rails/openmaic/backend/openmaic_app/config.py"
D = "rails/openmaic/deploy/Dockerfile"

_ENTRY = '        if scope["type"] not in ("http", "websocket") or standalone():'
_FILTER = ('        scope["headers"] = [(name, value) for name, value in headers\n'
           '                            if not name.lower().startswith(_PLATFORM_HEADER)]')

# (label, file, old, new, expected test)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- the check, and every path it must cover ----------------------------------------------
    (
        "the check is not installed: every peer's identity is believed again",
        A,
        "app.add_middleware(GatewayOnlyIdentity, peers=gateway_peers)\n",
        "",
        "test_a_non_gateway_peers_identity_is_not_believed",
    ),
    (
        "the proxy is exempt, so a peer's visit feeds the shim's ledger",
        G,
        _ENTRY,
        _ENTRY[:-1] + ' or scope.get("path", "").startswith("/api/app"):',
        "test_a_non_gateway_peer_never_reaches_the_ledger",
    ),
    (
        "the shim mount is exempt, so a peer's X-Platform-User is a credential again",
        G,
        _ENTRY,
        _ENTRY[:-1] + ' or scope.get("path", "").startswith("/api/llm"):',
        "test_a_peer_cannot_use_a_platform_identity_as_the_shims_credential",
    ),
    (
        "standalone is checked too, so dev without a gateway loses its identity",
        G,
        _ENTRY,
        '        if scope["type"] not in ("http", "websocket"):',
        "test_standalone_keeps_todays_behaviour",
    ),
    (
        "a request with no platform header is looked up anyway",
        G,
        "        if not any(name.lower().startswith(_PLATFORM_HEADER) for name, _ in headers):\n"
        "            await self.app(scope, receive, send)\n            return\n",
        "",
        "test_a_request_with_no_platform_header_costs_no_lookup",
    ),
    # --- what is dropped ------------------------------------------------------------------------
    (
        "only X-Platform-User is dropped; the rest of the family reaches the app",
        G,
        _FILTER,
        '        scope["headers"] = [(name, value) for name, value in headers\n'
        '                            if name.lower() != b"x-platform-user"]',
        "test_every_platform_header_from_a_stranger_is_dropped_and_nothing_else",
    ),
    (
        "over-correction: everything a stranger sends is dropped, the shim's token included",
        G,
        _FILTER,
        '        scope["headers"] = []',
        "test_every_platform_header_from_a_stranger_is_dropped_and_nothing_else",
    ),
    # --- who counts as the gateway --------------------------------------------------------------
    (
        "the X-Forwarded-For echo check is gone",
        G,
        "        elif _canonical_ip(peer) in _forwarded_for(headers):",
        "        elif False:",
        "test_x_forwarded_for_from_loopback_cannot_claim_the_gateway",
    ),
    (
        "IPv4-mapped peers are not folded, so a dual-stack listener's gateway is a stranger",
        G,
        "    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:\n"
        "        ip = ip.ipv4_mapped\n",
        "",
        "test_the_gateway_is_matched_whichever_way_the_address_is_spelled",
    ),
    (
        "a host name is matched as if it were an address",
        G,
        "    except ValueError:\n        return None\n",
        "    except ValueError:\n        return raw or None\n",
        "test_a_peer_that_is_not_an_address_is_never_trusted",
    ),
    (
        "a failed lookup forgets the gateway, so a DNS hiccup drops every user's identity",
        G,
        "                         f\"{sorted(self.addresses) or 'nobody'}\")\n            return\n",
        "                         f\"{sorted(self.addresses) or 'nobody'}\")\n"
        "            self.addresses = frozenset()\n            return\n",
        "test_a_failed_lookup_keeps_the_last_answer",
    ),
    (
        "every miss re-resolves: a loop of forged requests is a loop of lookups",
        G,
        "        if self._resolved_at is not None and now - self._resolved_at < self._interval():\n"
        "            return False\n",
        "",
        "test_a_miss_re_resolves_at_most_once_per_interval",
    ),
    (
        "a miss never re-resolves, so a recreated gateway stays a stranger",
        G,
        "        self.refresh(now)\n        return ip in self.addresses",
        "        if self._resolved_at is None:\n            self.refresh(now)\n"
        "        return ip in self.addresses",
        "test_a_recreated_gateway_is_recognised_after_its_ip_changes",
    ),
    (
        "a lookup adds to the set, so an address the gateway gave up keeps counting",
        G,
        "        self.addresses = found\n",
        "        self.addresses = self.addresses | found\n",
        "test_a_recreated_gateway_is_recognised_after_its_ip_changes",
    ),
    (
        "the check ignores the configured name",
        G,
        "gateway_peers = GatewayPeers(lambda: settings.gateway_host,",
        'gateway_peers = GatewayPeers(lambda: "gateway",',
        "test_the_configured_gateway_host_is_the_name_looked_up",
    ),
    (
        "the default name is not the gateway's compose service",
        C,
        '    gateway_host: str = "gateway"',
        '    gateway_host: str = "localhost"',
        "test_the_rail_looks_up_the_gateway_by_its_service_name_on_a_shared_network",
    ),
    # --- saying what it is doing ----------------------------------------------------------------
    (
        "a gateway resolving to loopback is not reported degraded",
        G,
        '        return "degraded" if self.local else "on"',
        '        return "on"',
        "test_loopback_is_believed_only_when_the_gateway_resolves_to_it",
    ),
    (
        "this container's own address is not seen as degraded",
        G,
        "    return ipaddress.ip_address(addr).is_loopback or addr in _own_addresses()",
        "    return ipaddress.ip_address(addr).is_loopback",
        "test_the_containers_own_address_is_degraded_too",
    ),
    (
        "mode() reports without looking the gateway up",
        G,
        "        if self._resolved_at is None:\n            self.refresh(self._clock())\n"
        "        if not self.addresses:",
        "        if not self.addresses:",
        "test_the_mode_looks_the_gateway_up_before_it_reports",
    ),
    (
        "healthz stops reporting the check",
        A,
        '            "peer_check": gateway_peers.mode()}',
        "            }",
        "test_healthz_reports_the_check_as_on",
    ),
    (
        "the drop log is not rate limited",
        G,
        "        if self._last is not None and now - self._last < 1.0:",
        "        if False:",
        "test_drops_are_logged_but_at_most_once_a_second",
    ),
    # --- the image ------------------------------------------------------------------------------
    (
        "the image trusts X-Forwarded-For from loopback again",
        D,
        ', "--no-proxy-headers"]',
        "]",
        "test_the_image_runs_uvicorn_without_proxy_headers",
    ),
    # --- the suite's own fixture (2026-10-16) ---------------------------------------------------
    # The gateway fixture stops the own-address lookup; without it the first test to need the
    # rail's own addresses looks the box's hostname up, which the offline guard refuses. That test
    # PASSES and ERRORs at its teardown, so this case is also the proof that the verdict reads
    # pytest's ERROR lines (the FAILED-only reader this harness had would call it FIRED-WRONG-TEST).
    (
        "the gateway fixture looks this box's own hostname up again",
        "rails/openmaic/tests/conftest.py",
        '    monkeypatch.setattr(gateway_only, "_own_addresses", lambda: frozenset())\n',
        "",
        "test_a_non_gateway_peers_identity_is_not_believed",    # the first, in this SUITE's order
    ),
]

#: Rewrites the suite cannot see. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "the drop line's keys in another order",
        G,
        '"event": "identity_dropped", "suppressed": held, **fields}))',
        '"suppressed": held, "event": "identity_dropped", **fields}))',
    ),
    (
        "the header filter built through list() instead of a comprehension",
        G,
        _FILTER,
        '        scope["headers"] = list((name, value) for name, value in headers\n'
        '                                if not name.lower().startswith(_PLATFORM_HEADER))',
    ),
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = str(RAIL_SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for var in ("PLATFORM_STANDALONE", "OPENMAIC_GATEWAY_HOST", "OPENMAIC_GATEWAY_RESOLVE_INTERVAL",
                "OPENMAIC_CLI_REQUESTER", "OPENMAIC_SHIM_TOKEN"):
        env.pop(var, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import openmaic_app.api.gateway_only as g, openmaic_app.api.app as a;"
         "print(g.__file__); print(a.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header", "-rfE",
                         "-p", "no:cacheprovider", *extra],
                        cwd=RAIL, capture_output=True, text=True, encoding="utf-8",
                        errors="replace", env=_env(), timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _apply(rel: str, old: str, new: str, label: str):
    """(path, original bytes, mutated bytes), or None after reporting a PATTERN."""
    path = REPO / rel
    original = path.read_bytes()
    n, mutated = mr.mutate_bytes(original, old, new)
    if n != 1:
        print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
        return None
    return path, original, mutated


def main() -> int:
    only = mr.parse_harness_args(MUTATIONS, CONTROLS, filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    controls = [c for c in CONTROLS if not only or any(o in c[0] for o in only)]
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from pytest's summary of failures and
    # errors can mean anything.
    _, collected = run_suite(("--collect-only",))
    missing = sorted({m[4] for m in chosen if m[4] not in collected})
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
    for label, rel, old, new, expected in chosen:
        applied = _apply(rel, old, new, label)
        if applied is None:
            results.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            fired = code not in (0, -1)
            named = mr.named_failed(out, expected)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and named else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    survived: list[tuple[str, str]] = []
    for label, rel, old, new in controls:
        applied = _apply(rel, old, new, label)
        if applied is None:
            survived.append((label, "PATTERN"))
            continue
        path, original, mutated = applied
        try:
            path.write_bytes(mutated)
            code, out = run_suite()
            verdict = "TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED"
            survived.append((label, verdict))
            print(f"  control {verdict:9} {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in survived if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(survived) - len(bad_controls)}/{len(survived)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
