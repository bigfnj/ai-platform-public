"""Mutation harness for "one Rails-tab load, one probe per remote". NOT a pytest file -- it
rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_rails_probe.py

Spans two packages, because the double probe did: the gateway asked three questions and the
broker answered each by dialling the remotes again. Every mutation here puts one extra round
trip back (or drops a box the picker needs), and none of them changes what the tab SHOWS, which
is why only a count can catch them. The count is taken over real HTTP by
apps/platform/backend/tests/test_rails_tab_probe_count.py; the broker-side shape by
test_upstream_delegation.py.

Runs from the repo root with THIS tree's broker, gateway and platform_core first on the path.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

REPO = Path(__file__).resolve().parents[3]
PY = sys.executable
SUITE = ["apps/platform/backend/tests/test_rails_tab_probe_count.py",
         "apps/platform/backend/tests/test_rail_upstreams.py",
         "services/broker/tests/test_upstream_delegation.py",
         "packages/platform_core/tests/test_broker_client_roles.py",
         "apps/platform/backend/tests/test_rail_toggle_message.py"]

B = "services/broker/app/broker.py"
G = "apps/platform/backend/platform_gateway_app/main.py"
C = "packages/platform_core/platform_core/broker_client.py"
E2E = "test_a_rails_tab_load_probes_each_remote_once"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the gateway stops asking for the inventory, so it reads each box itself again",
        G,
        "    view = await broker.roles(inventory=True)",
        "    view = await broker.roles()",
        E2E,
    ),
    (
        "the inventory re-asks each box for its health",
        B,
        '            upstreams.append({"name": name, "url": spec["url"], "healthy": p.healthy})',
        '            upstreams.append({"name": name, "url": spec["url"],\n'
        '                              "healthy": await self._upstream(name).healthy()})',
        E2E,
    ),
    (
        "the inventory re-reads each box's models",
        B,
        "            models[name] = p.raw_models or []",
        "            models[name] = (await self._upstream(name).models()) if p.healthy else []",
        E2E,
    ),
    (
        # Re-anchored 2026-10-14 when the inventory began handing the sweep its registry.
        "an idle box is left out of the sweep, so the picker cannot offer its models",
        B,
        "            self._roles_sweep(also_probe=set(registry), tags_read=tags_read, registry=registry),",
        "            self._roles_sweep(tags_read=tags_read, registry=registry),",
        "test_the_inventory_probes_every_registered_box_exactly_once",
    ),
    (
        "an idle box's residency is read too, a round trip nothing uses",
        B,
        "            if name in delegated_names:\n"
        "                reads[\"status\"] = _bounded(client.status())",
        "            if True:\n"
        "                reads[\"status\"] = _bounded(client.status())",
        E2E,
    ),
    (
        "every roles_view poll probes idle boxes, so each rail's chip refresh pays for the tab",
        B,
        "        rows, _probes = await self._roles_sweep()",
        "        rows, _probes = await self._roles_sweep(also_probe=set(self.settings.upstreams()))",
        "test_roles_view_alone_still_probes_only_the_boxes_a_role_runs_on",
    ),
    (
        "the gateway trusts the inventory keys blindly, so an older broker breaks the tab",
        G,
        '    if isinstance(view.get("upstreams"), list) and isinstance(view.get("models"), dict):',
        "    if True:",
        # A pre-inventory broker's view has no "upstreams" key, so every test of the Rails tab
        # file that builds the payload fails on that broker; this one is the tab's own shape.
        "test_models_is_keyed_by_upstream_and_upstreams_are_reported[pre-inventory-broker]",
    ),
    (
        "the client asks for the inventory on every roles() call, so every caller pays for it",
        C,
        '        params = {"inventory": "true"} if inventory else None',
        '        params = {"inventory": "true"}',
        "test_roles_sends_no_query_by_default",
    ),
    # --- the Models tab asks about this card only, so it dials no remote --------------------
    (
        "the Models tab fetches roles with the remote probe again",
        G,
        '        roles = (await broker.roles(local_only=True)).get("roles", [])',
        '        roles = (await broker.roles()).get("roles", [])',
        "test_the_models_tab_dials_no_remote_box",
    ),
    (
        "a model Delete waits on the remote probe again before its in-use check",
        G,
        '        roles = (await app.state.broker.roles(local_only=True)).get("roles", [])',
        '        roles = (await app.state.broker.roles()).get("roles", [])',
        "test_a_model_delete_check_dials_no_remote_box",
    ),
    (
        "the local view still probes every delegated box",
        B,
        "        names = [] if local_only else sorted(delegated_names | set(also_probe))",
        "        names = sorted(delegated_names | set(also_probe))",
        "test_the_local_view_dials_no_remote_and_leaves_delegated_rows_out",
    ),
    (
        "the local view reports delegated rows it never checked",
        B,
        "                    if local_only:\n"
        "                        continue",
        "                    if False:\n"
        "                        continue",
        "test_the_local_view_dials_no_remote_and_leaves_delegated_rows_out",
    ),
    (
        "the route ignores local_only and serves the probing view",
        "services/broker/app/main.py",
        "    if local_only:\n"
        '        return {"roles": await get_broker().roles_view_local()}',
        "    if False:\n"
        '        return {"roles": await get_broker().roles_view_local()}',
        "test_the_models_tab_dials_no_remote_box",
    ),
    (
        "local_only plus inventory is accepted, so one of them is silently ignored",
        "services/broker/app/main.py",
        "    if inventory and local_only:",
        "    if False:",
        "test_local_only_and_inventory_together_are_refused",
    ),
    (
        "the client never sends local_only",
        C,
        "        if local_only:\n"
        '            params = {**(params or {}), "local_only": "true"}',
        "        if False:\n"
        '            params = {**(params or {}), "local_only": "true"}',
        "test_roles_asks_for_local_only_when_told_to",
    ),
    # --- the Rail Manager's refusal names the setting that is actually in the way -----------
    # Re-anchored 2026-10-14: since 319872f4 the rule is one line of `_rail_blocked_by`, which
    # the card and the toggle share, and the old `if` matched nothing. Its twin, a rail
    # PLATFORM_DISABLED_APPS switched off told to join PLATFORM_ENABLED_APPS, is
    # mutate_gateway_upkeep.py's "every rail off the box is blamed on PLATFORM_ENABLED_APPS
    # again", the same rewrite of the same line, so it is not repeated here.
    (
        "a rail that was never enabled is sent to PLATFORM_DISABLED_APPS",
        G,
        '    return "disabled_apps" if rid in set(settings.disabled_apps) else "not_enabled"',
        '    return "disabled_apps"',
        "test_a_rail_never_enabled_still_names_platform_enabled_apps",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(REPO / p) for p in (
        "services/broker", "apps/platform/backend", "packages/platform_core"))
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=REPO, env=env)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        # A baseline that ran out of time is its own verdict, and nothing below can mean
        # anything after it.
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        # Bytes, not text: read_text/write_text translate line endings on Windows, so a
        # "restore" could rewrite an LF file as CRLF. The pattern is matched in the file's own
        # line endings and only its span replaced (mutate_bytes); restore_exact proves the
        # original bytes are back.
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected)
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
