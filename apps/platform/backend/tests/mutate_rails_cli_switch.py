"""Mutation harness for the Rails tab's Claude switch. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_rails_cli_switch.py
    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_rails_cli_switch.py --list

(--list names every case and the module it lives in, and runs nothing.)

Run this, not just pytest, before trusting a change to PUT /api/platform/admin/rails/{role}/cli,
to the switch fields _rails_payload puts on the tab, to the static `cli` or `stream` flag in
RAIL_MODEL_SLOTS, or to the broker capability a vision role's ON is held to (`vision`) or a
streaming role's (`streams`).

Every guard here fails in the direction that looks fine. Drop the super-admin check and a plain
admin can decide which rails' data leaves the box for the owner's subscription, with a 200. Drop
the wired check and a role nobody passes a requester for is switched on, which changes nothing
today and starts sending that rail's data off the box the day it is wired. Let the flag drift from
the rail's own `cli_slots` and the tab offers a switch that does nothing, or hides one that works.

Each mutation names the test(s) that must fail for it; a red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED.

WHERE A CASE LIVES. This file holds the cases that quote the gateway's own code (main.py, the
catalog's shared rules, the tests, conftest). A case that quotes a rail, its source or its slot
rows in the catalog, lives in cli_switch_cases/<rail>.py and goes wherever that rail goes: the
public snapshot withholds the withheld rails' modules (tools/publish.json). RAIL_MODULES names
every module; load_rail_cases() refuses one it does not name, a case filed under another rail,
and, in this repo, a missing module. In a published tree a missing module is named by its file
and skipped, and only where the tree does not carry its rail either.
"""
from __future__ import annotations

import importlib
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

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:       # imported from elsewhere, not run as a script
    sys.path.insert(0, str(HERE))
from cli_switch_cases import CAT, FLAG  # noqa: E402  (this directory)

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
BROKER = REPO / "services" / "broker"
PY = sys.executable
SUITE = ["apps/platform/backend/tests/test_rails_cli_switch.py",
         "apps/platform/backend/tests/test_rails_cli_switch_http.py",
         # The model repoint route's relay of the broker's 409 (roles.json unreadable).
         "apps/platform/backend/tests/test_rail_upstreams.py"]

MAIN = "apps/platform/backend/platform_gateway_app/main.py"
TEST = "apps/platform/backend/tests/test_rails_cli_switch.py"
REFUSES = "test_the_scan_refuses_what_it_cannot_read"
STREAM_FLAG = "test_the_stream_flag_is_each_rails_own_streamed_calls"
STREAM_REFUSED = "test_a_streaming_role_cannot_be_switched_on_on_a_broker_that_does_not_stream"
STREAM_TAB = "test_the_tab_marks_each_slot_on_a_streaming_role_with_the_routes_reason"
STREAM_HTTP = "test_a_streaming_role_on_a_broker_that_does_not_stream_is_a_409_through_the_route"

#: Every module in cli_switch_cases/ that holds a rail's cases, by file stem (the rail's directory
#: under rails/ with `-` spelled `_`).
RAIL_MODULES = ("bouquet", "course_builder", "edu_suite", "finance", "iep", "recipe_book",
                "terminal_fun")
#: The public snapshot withholds tools/publish.py: that file's absence is how a harness knows it
#: runs there (web/tests/browser/railchecks/harness.py reads it the same way).
PUBLISHED = not (REPO / "tools" / "publish.py").is_file()

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "a repoint the broker refuses with a 409 reaches the tab as a 502 again",
        MAIN,
        # Carries the comment line before it: since 2026-10-14 a token revoke and a model toggle
        # relay the broker's 409 with the same two lines.
        "        # the broker failing.\n"
        "        if exc.status == 409:\n"
        "            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc\n",
        "        # the broker failing.\n",
        ["test_a_repoint_the_broker_refuses_with_a_409_reaches_the_tab_as_that_409"],
    ),
    (
        "a plain admin may switch Claude on for a rail",
        MAIN,
        "    if not admin.is_superadmin:\n"
        "        raise HTTPException(status_code=403,\n"
        '                            detail="only a super-admin may switch Claude on or off for a rail")',
        "    if False:\n"
        "        raise HTTPException(status_code=403,\n"
        '                            detail="only a super-admin may switch Claude on or off for a rail")',
        ["test_a_plain_admin_cannot_switch_and_the_broker_is_not_asked"],
    ),
    (
        "a role no rail slot uses is passed to the broker",
        MAIN,
        '                            detail="only a super-admin may switch Claude on or off for a rail")\n'
        "    if role not in RAIL_SLOT_ROLES:",
        '                            detail="only a super-admin may switch Claude on or off for a rail")\n'
        "    if False:",
        ["test_a_role_no_rail_slot_uses_is_a_404"],
    ),
    # --- which kinds of slot have a switch (vision added 2026-10-09) --------------------------
    # Two cases went with the text-only rule. "a vision or image role gets a switch" is split in
    # two below (image refused, vision allowed). "a vision role counts as a text role" widened
    # CHAT_SLOT_ROLES, which no longer guarded anything in the gateway (the route reads
    # CLI_SLOT_ROLES), so no reachable input could make it fail; the CLI_SLOT_KINDS and
    # CLI_SLOT_ROLES cases below hold the rule that replaced it. CHAT_SLOT_ROLES itself went on
    # 2026-10-14 (read by nothing but tests); the last case below holds that every role set
    # rails_models computes is one main.py reads.
    (
        "an image role gets a switch",
        MAIN,
        "    if role not in CLI_SLOT_ROLES:",
        "    if False:",
        ["test_an_image_role_has_no_switch", "test_an_image_role_has_no_model_choice",
         "test_an_image_role_beside_a_vision_one_has_no_switch_nor_model",
         "test_an_image_role_is_a_400_through_the_route"],
    ),
    (
        # The rule vision switches replaced: the route refuses every vision role. Re-anchored
        # 2026-10-14: it swapped CHAT_SLOT_ROLES in for CLI_SLOT_ROLES, a set that is gone.
        "the route goes back to the text-only rule, refusing every vision role",
        MAIN,
        "    if role not in CLI_SLOT_ROLES:",
        "    if role not in CLI_SLOT_ROLES or role in VISION_SLOT_ROLES:",
        ["test_each_vision_role_in_the_catalog_has_a_switch",
         "test_a_wired_vision_role_switches_on_like_a_text_role",
         "test_an_unwired_vision_role_cannot_be_switched_on_but_can_be_switched_off",
         "test_a_vision_role_takes_a_claude_model_under_the_same_rules",
         "test_a_superadmin_switches_a_wired_vision_role_through_the_route"],
    ),
    (
        "the catalog's switchable kinds drop vision",
        CAT,
        'CLI_SLOT_KINDS: frozenset[str] = frozenset({"chat", "vision"})',
        'CLI_SLOT_KINDS: frozenset[str] = frozenset({"chat"})',
        ["test_each_vision_role_in_the_catalog_has_a_switch",
         "test_a_wired_vision_role_switches_on_like_a_text_role",
         "test_a_superadmin_switches_a_wired_vision_role_through_the_route"],
    ),
    (
        "the catalog's switchable kinds take image",
        CAT,
        'CLI_SLOT_KINDS: frozenset[str] = frozenset({"chat", "vision"})',
        'CLI_SLOT_KINDS: frozenset[str] = frozenset({"chat", "vision", "image"})',
        ["test_an_image_role_has_no_switch",
         "test_an_image_role_beside_a_vision_one_has_no_switch_nor_model",
         "test_an_image_role_is_a_400_through_the_route"],
    ),
    (
        "the switchable role set ignores CLI_SLOT_KINDS and keeps text roles only",
        CAT,
        '        "CLI_SLOT_ROLES": {s["role"] for s in slots if s["kind"] in CLI_SLOT_KINDS},',
        '        "CLI_SLOT_ROLES": {s["role"] for s in slots if s["kind"] == "chat"},',
        ["test_each_vision_role_in_the_catalog_has_a_switch",
         "test_a_wired_vision_role_switches_on_like_a_text_role"],
    ),
    (
        # The dead set as it was (2026-10-14): computed, recomputed by add_rail, read by nothing.
        "a role set comes back that the gateway never reads",
        CAT,
        '        # Roles with at least one text or vision slot: the only ones the Claude switch may touch.\n',
        '        "CHAT_SLOT_ROLES": {s["role"] for s in slots if s["kind"] == "chat"},\n'
        '        # Roles with at least one text or vision slot: the only ones the Claude switch may touch.\n',
        ["test_every_role_set_the_catalog_rules_compute_is_one_the_gateway_reads"],
    ),
    (
        "a vision slot's switch reads off whatever the broker says",
        MAIN,
        '            slot["cli_on"] = slot["role"] in on',
        '            slot["cli_on"] = slot["role"] in on and slot["kind"] == "chat"',
        ["test_a_vision_slot_carries_the_switch_fields_like_a_text_slot",
         "test_a_wired_vision_role_switches_on_like_a_text_role"],
    ),
    (
        "a vision slot's Claude model reads Default whatever the broker says",
        MAIN,
        '            slot["cli_model"] = cli["role_models"].get(slot["role"])',
        '            slot["cli_model"] = (cli["role_models"].get(slot["role"])\n'
        '                                 if slot["kind"] == "chat" else None)',
        ["test_a_vision_slot_carries_the_switch_fields_like_a_text_slot",
         "test_a_vision_role_takes_a_claude_model_under_the_same_rules"],
    ),
    (
        "a vision slot is never called wired",
        CAT,
        '                "cli_wired": s.get("cli") is True,',
        '                "cli_wired": s.get("cli") is True and s["kind"] == "chat",',
        ["test_a_vision_slot_carries_the_switch_fields_like_a_text_slot",
         "test_a_superadmin_switches_a_wired_vision_role_through_the_route"],
    ),
    (
        "the flag rule goes back to text slots only",
        TEST,
        '            elif s.get("cli") is True and s["kind"] not in ("chat", "vision"):',
        '            elif s.get("cli") is True and s["kind"] not in ("chat",):',
        ["test_the_flag_is_a_boolean_and_only_a_text_or_vision_slot_may_set_it"],
    ),
    (
        "the flag rule lets an image slot be wired",
        TEST,
        '            elif s.get("cli") is True and s["kind"] not in ("chat", "vision"):',
        '            elif s.get("cli") is True and s["kind"] not in ("chat", "vision", "image"):',
        ["test_the_flag_is_a_boolean_and_only_a_text_or_vision_slot_may_set_it"],
    ),
    (
        "the flag equality holds text slots only and passes over vision ones",
        TEST,
        '            flagged = {s["slot"] for s in catalog[rid] if s.get("cli") is True}\n'
        '            wired = {s["slot"] for s in catalog[rid] if s["slot"] in opted}\n',
        '            flagged = {s["slot"] for s in catalog[rid] if s.get("cli") is True\n'
        '                       and s["kind"] == "chat"}\n'
        '            wired = {s["slot"] for s in catalog[rid] if s["slot"] in opted\n'
        '                     and s["kind"] == "chat"}\n',
        ["test_the_flag_equality_holds_for_a_vision_slot"],
    ),
    (
        "an opted-in slot on the Rails tab with no catalog row passes, so its switch names no slot",
        TEST,
        "        for name in sorted((opted & _panel_slots(root)) - listed):\n",
        "        for name in sorted(set()):\n",
        ["test_an_opted_in_slot_on_the_rails_tab_is_a_catalog_slot"],
    ),
    (
        "the Rails-tab rule reads every manifest slot as on the tab, so a `local` tier fails it",
        TEST,
        '    return {s["slot"] for s in data.get("model_slots", []) if s.get("slot") and s.get("admin_panel")}\n',
        '    return {s["slot"] for s in data.get("model_slots", []) if s.get("slot")}\n',
        ["test_an_opted_in_slot_on_the_rails_tab_is_a_catalog_slot"],
    ),
    # --- a vision role's ON needs a broker that sends images to the CLI (added 2026-10-09) -------
    (
        "a vision role switches ON on a broker that does not say it sends images",
        MAIN,
        '        if not _cli_block(view if isinstance(view, dict) else {})["vision"]:\n',
        "        if False:\n",
        ["test_a_vision_role_cannot_be_switched_on_on_a_broker_that_cannot_send_images",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    (
        "a vision role's OFF is held to the image check",
        MAIN,
        "    if body.enabled and role in VISION_SLOT_ROLES:\n",
        "    if role in VISION_SLOT_ROLES:\n",
        ["test_switching_a_vision_role_off_and_choosing_its_model_need_no_images",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    (
        "a vision role's model choice is held to the image check",
        MAIN,
        "    if body.enabled and role in VISION_SLOT_ROLES:\n",
        '    if (body.enabled or "model" in body.model_fields_set) and role in VISION_SLOT_ROLES:\n',
        ["test_switching_a_vision_role_off_and_choosing_its_model_need_no_images"],
    ),
    (
        "a text role is held to the image check too",
        MAIN,
        "    if body.enabled and role in VISION_SLOT_ROLES:\n",
        "    if body.enabled:\n",
        ["test_a_text_role_is_not_held_to_the_image_check"],
    ),
    (
        "any truthy vision value reads as a broker that sends images",
        MAIN,
        '        "vision": raw.get("vision") is True,\n',
        '        "vision": bool(raw.get("vision")),\n',
        ["test_a_vision_role_cannot_be_switched_on_on_a_broker_that_cannot_send_images"],
    ),
    (
        "a broker that sends no vision key reads as one that sends images",
        MAIN,
        '        "vision": raw.get("vision") is True,\n',
        '        "vision": raw.get("vision") is not False,\n',
        ["test_a_vision_role_cannot_be_switched_on_on_a_broker_that_cannot_send_images",
         "test_the_tab_marks_each_slot_on_a_vision_role_with_the_routes_reason",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    (
        "the route asks a lighter read, which carries no vision, and refuses every vision ON",
        MAIN,
        "            view = await app.state.broker.roles(inventory=True)\n",
        "            view = await app.state.broker.roles()\n",
        ["test_the_route_reads_the_image_path_from_the_brokers_inventory_read"],
    ),
    (
        "a capability read that fails reads as a broker that sends images",
        MAIN,
        "        except BrokerError as exc:\n"
        "            raise HTTPException(status_code=502, detail=(\n"
        '                f"@{role} was not switched on: the gateway could not read whether the broker "\n',
        "        except BrokerError as exc:\n"
        '            view = {"cli": {"vision": True}}\n'
        "        if False:\n"
        "            raise HTTPException(status_code=502, detail=(\n"
        '                f"@{role} was not switched on: the gateway could not read whether the broker "\n',
        ["test_a_capability_read_that_fails_refuses_and_switches_nothing"],
    ),
    (
        "the image refusal is a 400 instead of the 409",
        MAIN,
        "            raise HTTPException(status_code=409, detail=(\n"
        '                f"@{role} reads images, and {CLI_NO_VISION}. Update the broker first."))\n',
        "            raise HTTPException(status_code=400, detail=(\n"
        '                f"@{role} reads images, and {CLI_NO_VISION}. Update the broker first."))\n',
        ["test_a_vision_role_cannot_be_switched_on_on_a_broker_that_cannot_send_images",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    (
        "the tab marks no vision slot, so its switch looks clickable",
        MAIN,
        '                                       and not cli["vision"] else None)\n',
        "                                       and False else None)\n",
        ["test_the_tab_marks_each_slot_on_a_vision_role_with_the_routes_reason",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    (
        "the tab marks vision slots by kind and misses a text slot on the same role",
        MAIN,
        '            slot["cli_unavailable"] = (CLI_NO_VISION if slot["role"] in VISION_SLOT_ROLES\n',
        '            slot["cli_unavailable"] = (CLI_NO_VISION if slot["kind"] == "vision"\n',
        ["test_the_tab_marks_each_slot_on_a_vision_role_with_the_routes_reason"],
    ),
    (
        "the tab's reason is not the route's",
        MAIN,
        '            slot["cli_unavailable"] = (CLI_NO_VISION if slot["role"] in VISION_SLOT_ROLES\n',
        '            slot["cli_unavailable"] = ("not available here" if slot["role"] in VISION_SLOT_ROLES\n',
        ["test_the_tab_marks_each_slot_on_a_vision_role_with_the_routes_reason",
         "test_a_vision_role_on_a_broker_that_sends_no_images_is_a_409_through_the_route"],
    ),
    # --- a streaming role's ON needs a broker that streams to the CLI (added 2026-10-14) ---------
    (
        "a streaming role switches ON on a broker that does not say it streams",
        MAIN,
        '        if not _cli_block(seen if isinstance(seen, dict) else {})["streams"]:\n',
        "        if False:\n",
        [STREAM_REFUSED, STREAM_HTTP,
         "test_each_streaming_role_in_the_catalog_is_held_to_the_stream_check"],
    ),
    (
        "the catalog's streaming role set is empty, so no ON waits for a broker that streams",
        CAT,
        '        "STREAM_SLOT_ROLES": {s["role"] for s in slots if s.get("stream") is True},\n',
        '        "STREAM_SLOT_ROLES": set(),\n',
        [STREAM_REFUSED, STREAM_HTTP],
    ),
    (
        "a streaming role's OFF is held to the stream check",
        MAIN,
        "    if body.enabled and role in STREAM_SLOT_ROLES:\n",
        "    if role in STREAM_SLOT_ROLES:\n",
        ["test_switching_a_streaming_role_off_and_choosing_its_model_need_no_stream", STREAM_HTTP],
    ),
    (
        "a streaming role's model choice is held to the stream check",
        MAIN,
        "    if body.enabled and role in STREAM_SLOT_ROLES:\n",
        '    if (body.enabled or "model" in body.model_fields_set) and role in STREAM_SLOT_ROLES:\n',
        ["test_switching_a_streaming_role_off_and_choosing_its_model_need_no_stream"],
    ),
    (
        "a role that does not stream is held to the stream check too",
        MAIN,
        "    if body.enabled and role in STREAM_SLOT_ROLES:\n",
        "    if body.enabled:\n",
        ["test_a_role_that_does_not_stream_is_not_held_to_the_stream_check"],
    ),
    (
        "any truthy streams value reads as a broker that streams",
        MAIN,
        '        "streams": raw.get("streams") is True,\n',
        '        "streams": bool(raw.get("streams")),\n',
        [STREAM_REFUSED],
    ),
    (
        "a broker that sends no streams key reads as one that streams",
        MAIN,
        '        "streams": raw.get("streams") is True,\n',
        '        "streams": raw.get("streams") is not False,\n',
        [STREAM_REFUSED, STREAM_TAB, STREAM_HTTP],
    ),
    (
        "a stream read that fails reads as a broker that streams",
        MAIN,
        "        except BrokerError as exc:\n"
        "            raise HTTPException(status_code=502, detail=(\n"
        '                f"@{role} was not switched on: the gateway could not read whether the broker streams "\n',
        "        except BrokerError as exc:\n"
        '            seen = {"cli": {"streams": True}}\n'
        "        if False:\n"
        "            raise HTTPException(status_code=502, detail=(\n"
        '                f"@{role} was not switched on: the gateway could not read whether the broker streams "\n',
        ["test_a_stream_read_that_fails_refuses_and_switches_nothing"],
    ),
    (
        "the stream check asks a lighter read, which carries no streams, and refuses every ON",
        MAIN,
        "            seen = await app.state.broker.roles(inventory=True)\n",
        "            seen = await app.state.broker.roles()\n",
        ["test_the_route_reads_the_stream_path_from_the_brokers_inventory_read"],
    ),
    (
        "the streaming refusal is a 400 instead of the 409",
        MAIN,
        "            raise HTTPException(status_code=409, detail=(\n"
        '                f"@{role} streams its answers, and {CLI_NO_STREAM}. Update the broker first."))\n',
        "            raise HTTPException(status_code=400, detail=(\n"
        '                f"@{role} streams its answers, and {CLI_NO_STREAM}. Update the broker first."))\n',
        [STREAM_REFUSED, STREAM_HTTP],
    ),
    (
        "the tab marks no streaming slot, so its switch looks clickable",
        MAIN,
        '                    and not cli["streams"]):\n',
        "                    and False):\n",
        [STREAM_TAB, STREAM_HTTP],
    ),
    (
        "the tab's streaming reason replaces the image reason on a role that is both",
        MAIN,
        '            if (slot["cli_unavailable"] is None and slot["role"] in STREAM_SLOT_ROLES\n',
        '            if (slot["role"] in STREAM_SLOT_ROLES\n',
        [STREAM_TAB],
    ),
    (
        "the tab's streaming reason is not the route's",
        MAIN,
        '                slot["cli_unavailable"] = CLI_NO_STREAM\n',
        '                slot["cli_unavailable"] = "not available here"\n',
        [STREAM_TAB, STREAM_HTTP],
    ),
    # The flag against the rails' own streamed calls: a published rail's flag dropped, a rail that
    # only buffers marked, and the scan itself.
    (
        "the catalog drops ai-playground's stream flag, though its facade streams its role",
        CAT,
        '"default": "nemotron-3-nano:4b", "cli": True,\n         "stream": True,\n',
        '"default": "nemotron-3-nano:4b", "cli": True,\n',
        [STREAM_FLAG],
    ),
    (
        "the catalog marks recipe-book's assistant as streamed, though the rail only buffers",
        CAT,
        '         "env": "RECIPE_BOOK_ASSISTANT_MODEL", "default": "gemma4*:26b", "cli": True,\n',
        '         "env": "RECIPE_BOOK_ASSISTANT_MODEL", "default": "gemma4*:26b", "cli": True,\n'
        '         "stream": True,\n',
        [STREAM_FLAG],
    ),
    (
        "the stream equality is never checked",
        TEST,
        "            if flagged != streamed:\n",
        "            if False:\n",
        # The real tree agrees, so only the synthetic disagreements can show it.
        ["test_the_stream_flag_equality_holds_on_a_synthetic_rail"],
    ),
    (
        "the stream scan stops reading a requester stored by subscript",
        TEST,
        '                        and tgt.slice.value == "requester"):\n',
        '                        and tgt.slice.value == "requester" and False):\n',
        [STREAM_FLAG, "test_the_stream_scan_reads_each_facade_form",
         "test_the_stream_scan_is_not_degenerate"],
    ),
    (
        "a stream whose role the scan cannot tell is passed over",
        TEST,
        "                if not found:\n",
        "                if False:\n",
        ["test_the_stream_scan_refuses_what_it_cannot_read"],
    ),
    (
        "the stream scan takes a buffered call for a stream",
        TEST,
        '_STREAM_PATH = re.compile(r"\\S*/v1/chat/stream")\n',
        '_STREAM_PATH = re.compile(r"\\S*/v1/chat(/stream)?")\n',
        ["test_the_stream_scan_reads_each_facade_form"],
    ),
    (
        "a streamed mark on an unwired slot is allowed",
        TEST,
        '            elif s.get("stream") is True and s.get("cli") is not True:\n',
        "            elif False:\n",
        ["test_the_stream_flag_is_a_boolean_and_only_a_wired_slot_may_set_it"],
    ),
    # --- which slots the tab draws a switch on: the gateway's one answer (added 2026-10-09) -----
    (
        "the tab is told only text slots have a switch, while the route takes vision ones",
        CAT,
        '                "cli_switchable": s["kind"] in CLI_SLOT_KINDS,\n',
        '                "cli_switchable": s["kind"] == "chat",\n',
        ["test_a_slot_has_a_switch_on_the_tab_exactly_where_the_route_takes_one"],
    ),
    (
        "the tab is told every slot has a switch, image ones too",
        CAT,
        '                "cli_switchable": s["kind"] in CLI_SLOT_KINDS,\n',
        '                "cli_switchable": True,\n',
        ["test_a_slot_has_a_switch_on_the_tab_exactly_where_the_route_takes_one"],
    ),
    (
        "the synthetic rail never reaches the route's role sets (a degenerate fixture)",
        "apps/platform/backend/tests/conftest.py",
        "        for name, roles in rails_models.role_sets(rails_models.RAIL_MODEL_SLOTS).items():\n",
        "        for name, roles in {}.items():\n",
        ["test_a_wired_vision_role_switches_on_like_a_text_role",
         "test_a_superadmin_switches_a_wired_vision_role_through_the_route"],
    ),
    (
        "an unwired role can be switched ON",
        MAIN,
        "    if body.enabled and role not in CLI_WIRED_ROLES:",
        "    if False:",
        ["test_an_unwired_role_cannot_be_switched_on_but_can_be_switched_off"],
    ),
    (
        "an unwired role can no longer be switched OFF",
        MAIN,
        "    if body.enabled and role not in CLI_WIRED_ROLES:",
        "    if role not in CLI_WIRED_ROLES:",
        ["test_an_unwired_role_cannot_be_switched_on_but_can_be_switched_off"],
    ),
    (
        "a broker 409 reaches the admin as 'broker unreachable'",
        MAIN,
        # Re-anchored 2026-10-09: the comment above the check grew a line for the model choice.
        "        # the broker failing.\n"
        "        if exc.status in (400, 409):",
        "        # the broker failing.\n"
        "        if exc.status in (400,):",
        ["test_a_broker_refusal_reaches_the_admin_in_its_own_words"],
    ),
    (
        "every admin is told the switches are theirs",
        MAIN,
        '            "cli_editable": admin is not None and admin.is_superadmin is True}',
        '            "cli_editable": admin is not None}',
        ["test_cli_editable_is_true_for_a_superadmin_only"],
    ),
    (
        "the model Apply answers without the admin, greying every switch",
        MAIN,
        "        # rides along so `cli_editable` stays what the GET said.\n"
        "        return await _rails_payload(await _disabled_set(), admin)",
        "        # rides along so `cli_editable` stays what the GET said.\n"
        "        return await _rails_payload(await _disabled_set())",
        ["test_a_model_apply_answers_with_the_same_cli_editable"],
    ),
    (
        "switching the last role off disables every switch",
        MAIN,
        '        "switch_blockers": [b for b in blockers if b not in _CLI_ROLE_SET_BLOCKERS],',
        '        "switch_blockers": list(blockers),',
        ["test_a_switch_is_ready_apart_from_which_roles_are_on"],
    ),
    (
        "a broker older than the switch reads as ready",
        MAIN,
        '        raw = {"ready": False, "blockers": ["broker_has_no_cli_switch"], "enabled_roles": [],',
        '        raw = {"ready": False, "blockers": [], "enabled_roles": [],',
        ["test_a_broker_older_than_the_switch_shows_every_switch_unavailable"],
    ),
    (
        "cli_on is read from the wiring instead of the broker",
        MAIN,
        '            slot["cli_on"] = slot["role"] in on',
        '            slot["cli_on"] = slot.get("cli_wired") is True',
        ["test_every_slot_carries_the_switch_fields"],
    ),
    (
        "cli_wired is every text slot, not the catalog's flag",
        CAT,
        '                "cli_wired": s.get("cli") is True,',
        '                "cli_wired": s["kind"] == "chat",',
        ["test_every_slot_carries_the_switch_fields"],
    ),
    # --- the scan itself: each way of reading, and each refusal ---
    (
        "the scan stops reading a bare-imported resolve (watch-builder's)",
        TEST,
        '    if isinstance(node, ast.Name) and node.id in funcs and isinstance(node.ctx, ast.Load):\n'
        '        return "bare-import"',
        '    if False:\n        return "bare-import"',
        [FLAG, "test_each_way_of_reading_is_used_by_a_real_rail"],
    ),
    (
        "the scan takes any cli_slots expression for the literal",
        TEST,
        '    if isinstance(node, ast.Name) and node.id == _SLOTS_NAME:\n        return "literal"',
        '    if True:\n        return "literal"',
        [REFUSES],
    ),
    (
        "a comprehension that yields something other than its loop variable passes as a filter",
        TEST,
        "                and node.elt.id == gen.target.id):",
        "                and True):",
        [REFUSES],
    ),
    (
        "a second binding of CLI_SLOTS is not looked for",
        TEST,
        "        if node is not decl_target and _other_binding(node):",
        "        if False:",
        [REFUSES],
    ),
    (
        "the scan stops reading CLI_SLOTS spelled as text",
        TEST,
        "        elif _named_as_text(node):",
        "        elif False:",
        [REFUSES],
    ),
    (
        "the scan stops reading an attribute write of CLI_SLOTS",
        TEST,
        "        return node.attr == _SLOTS_NAME and not isinstance(node.ctx, ast.Load)",
        "        return False",
        [REFUSES],
    ),
    (
        "the scan passes over a resolve reached through getattr",
        TEST,
        "        elif _resolve_by_getattr(node, modules):",
        "        elif False:",
        [REFUSES],
    ),
    (
        "positional arguments after a handed-over resolve are not read",
        TEST,
        "            args = call.args[[i for i, a in enumerate(call.args) if a is node][0] + 1:]",
        "            args = []",
        [REFUSES],
    ),
    (
        "a resolve the scan cannot follow is passed over",
        TEST,
        "        else:\n            problems.append(f\"{rel}:{node.lineno}: modelstate's resolve is referenced",
        "        else:\n            continue\n            problems.append(f\"{rel}:{node.lineno}: modelstate's resolve is referenced",
        [REFUSES],
    ),
    (
        "two modules may each declare a CLI_SLOTS",
        TEST,
        "    if len(declaring) > 1:",
        "    if False:",
        ["test_the_scan_refuses_two_declaring_modules"],
    ),
    # --- the route as a request reaches it (test_rails_cli_switch_http.py) -------------------
    (
        "the route path is renamed, so the shell's PUT misses it in the live app",
        MAIN,
        '@app.put("/api/platform/admin/rails/{role}/cli")',
        '@app.put("/api/platform/admin/rails/{role}/claude")',
        ["test_a_superadmin_switch_reaches_the_broker_and_answers_with_the_tab",
         "test_an_anonymous_switch_is_a_401_and_reaches_no_broker"],
    ),
    (
        "the switch body parses a string or a number as a boolean",
        MAIN,
        "    enabled: StrictBool",
        "    enabled: bool",
        ["test_a_body_that_is_not_a_json_boolean_is_a_422"],
    ),
    # --- a switch that landed is reported even when the tab cannot be re-read -----------------
    (
        "a failed re-read after a switch that landed is a 502, and the tab keeps the old box",
        MAIN,
        "    except BrokerError as exc:\n"
        "        # The switch is LIVE",
        "    except ZeroDivisionError as exc:\n"
        "        # The switch is LIVE",
        ["test_a_switch_that_landed_is_reported_even_when_the_tab_cannot_be_reread"],
    ),
    (
        "the reply after a failed re-read drops the switch's own state",
        MAIN,
        '        return {"cli": _cli_block(reply if isinstance(reply, dict) else {}),',
        '        return {"cli": _cli_block({}),',
        ["test_a_switch_that_landed_is_reported_even_when_the_tab_cannot_be_reread"],
    ),
    # --- the Claude model per role (added 2026-10-09) -----------------------------------------
    (
        "an unwired role can be given a Claude model",
        MAIN,
        "    if choose and body.model is not None and role not in CLI_WIRED_ROLES:",
        "    if False:",
        ["test_an_unwired_role_cannot_be_given_a_model_but_can_have_it_cleared"],
    ),
    (
        "clearing an unwired role's model is refused",
        MAIN,
        "    if choose and body.model is not None and role not in CLI_WIRED_ROLES:",
        "    if choose and role not in CLI_WIRED_ROLES:",
        ["test_an_unwired_role_cannot_be_given_a_model_but_can_have_it_cleared"],
    ),
    (
        "the model is never relayed to the broker",
        MAIN,
        '            role, body.enabled, **({"model": body.model} if choose else {}))',
        "            role, body.enabled)",
        ["test_a_superadmin_chooses_a_model_and_every_slot_on_the_role_carries_it",
         "test_a_superadmin_model_choice_reaches_the_broker_and_the_tab_carries_it"],
    ),
    (
        "a switch sent alone is relayed as a model clear",
        MAIN,
        '    choose = "model" in body.model_fields_set',
        "    choose = True",
        ["test_a_superadmin_switches_one_role_and_gets_the_fresh_tab"],
    ),
    (
        "an older broker's 422 reads as the broker failing",
        MAIN,
        "        if exc.status == 422 and choose:",
        "        if False:",
        ["test_a_broker_that_requires_enabled_on_every_call_is_called_older"],
    ),
    (
        "an older broker that dropped the model reads as having taken it",
        MAIN,
        '    if choose and not (isinstance(reply, dict) and isinstance(reply.get("cli"), dict)',
        '    if False and not (isinstance(reply, dict) and isinstance(reply.get("cli"), dict)',
        ["test_a_broker_that_drops_the_model_is_not_reported_as_having_taken_it"],
    ),
    (
        "the slots lose their Claude model",
        MAIN,
        '            slot["cli_model"] = cli["role_models"].get(slot["role"])',
        '            slot["cli_model"] = None',
        ["test_a_superadmin_chooses_a_model_and_every_slot_on_the_role_carries_it",
         "test_the_slots_carry_only_well_formed_models_from_the_broker"],
    ),
    (
        "the tab loses the allowlist",
        MAIN,
        '            "cli_models": _cli_models(view),',
        '            "cli_models": [],',
        ["test_a_superadmin_chooses_a_model_and_every_slot_on_the_role_carries_it"],
    ),
    (
        "a switch-only reply loses the allowlist",
        MAIN,
        '                "cli_models": _cli_models(reply if isinstance(reply, dict) else {}),',
        '                "cli_models": [],',
        ["test_a_model_that_landed_is_reported_when_the_tab_cannot_be_reread"],
    ),
    (
        "a role model that is not a string reaches the tab",
        MAIN,
        "                        if isinstance(k, str) and isinstance(v, str)}",
        "                        if isinstance(k, str)}",
        ["test_the_slots_carry_only_well_formed_models_from_the_broker"],
    ),
    (
        "the body takes one that changes nothing",
        MAIN,
        '        if self.enabled is None and "model" not in self.model_fields_set:',
        "        if False:",
        ["test_the_body_takes_a_model_string_or_null_and_something_to_change",
         "test_a_model_body_that_is_not_a_string_or_null_is_a_422"],
    ),
    (
        "the body takes a model that is not a string",
        MAIN,
        "    model: StrictStr | None = Field(default=None, max_length=128)",
        "    model: Any = None",
        ["test_the_body_takes_a_model_string_or_null_and_something_to_change",
         "test_a_model_body_that_is_not_a_string_or_null_is_a_422"],
    ),
]

#: How many of MUTATIONS are this file's own; the rail modules' cases follow them.
CORE_CASES = len(MUTATIONS)


def load_rail_cases(package: Path = HERE / "cli_switch_cases", published: bool = PUBLISHED,
                    modules: tuple[str, ...] = RAIL_MODULES) -> tuple[dict[str, list], list[str]]:
    """({module: its MUTATIONS}, withheld): every rail module's cases, and the file names of the
    modules this tree withholds.

    A module may be absent only from a published tree, and only where that tree does not carry
    its rail (rails/<the module, with _ spelled ->) either; anything else is a module lost by
    mistake. That, a module file `modules` does not name, and a case that rewrites another rail's
    file would each move cases out of every run without a word, so each stops every command,
    --list included."""
    stray = sorted(p.name for p in package.glob("*.py")
                   if p.stem not in modules and p.stem != "__init__")
    if stray:
        raise SystemExit(f"mutate_rails_cli_switch: {package.name}/{', '.join(stray)} is not in "
                         f"RAIL_MODULES, so its cases would never run")
    found: dict[str, list] = {}
    withheld: list[str] = []
    for mod in modules:
        rail = mod.replace("_", "-")
        if not (package / f"{mod}.py").is_file():
            if not published or (REPO / "rails" / rail).is_dir():
                raise SystemExit(f"mutate_rails_cli_switch: {package.name}/{mod}.py is missing, so "
                                 f"{rail}'s cases cannot run")
            withheld.append(f"{mod}.py")
            continue
        cases = importlib.import_module(f"{package.name}.{mod}").MUTATIONS
        wrong = [m[0] for m in cases if m[1] != CAT and not m[1].startswith(f"rails/{rail}/")]
        if wrong:
            raise SystemExit(f"mutate_rails_cli_switch: {package.name}/{mod}.py holds cases that "
                             f"rewrite another rail's files: {wrong}")
        found[mod] = cases
    return found, withheld


RAIL_CASES, WITHHELD = load_rail_cases()
for _cases in RAIL_CASES.values():
    MUTATIONS += _cases

# One pytest run takes a few seconds. A mutation that makes a test hang must not hang the
# harness; a run that times out is reported as TIMEOUT, never as FIRED.
RUN_TIMEOUT = 300


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first: the repo venv's editable installs point at one checkout.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE), str(BROKER)])
    # No .pyc written: a mutation and its restore can land in the same second with the same size.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import platform_gateway_app.rails_models as r, platform_gateway_app.main as m;"
         "print(r.__file__); print(m.__file__)"],
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


def main(argv: list[str] | None = None) -> int:
    listing = mr.parse_harness_args(None, argv=argv).listing
    # A module this published tree withholds, with its rail, is named by its file on every run.
    # Its cases are not: they quote the withheld rail's own code.
    for name in WITHHELD:
        print(f"  SKIPPED  cli_switch_cases/{name}: its rail is not in this published tree")
    if listing:
        # One line per case, tab-separated: where it lives, then its label. Nothing is run.
        where = ["core"] * CORE_CASES + [mod for mod, cases in RAIL_CASES.items() for _ in cases]
        for mod, m in zip(where, MUTATIONS):
            print("\t".join((mod, m[0])))
        print(f"{len(MUTATIONS)} mutations" + (f"; {len(WITHHELD)} modules withheld" if WITHHELD else ""))
        return 0
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
        if not path.is_file():
            # Each case's module ships with the rail it rewrites, so this is a tree that lost one.
            print(f"  ERROR  {label}: {relpath} is not in this tree")
            results.append((label, "MISSING"))
            continue
        original = path.read_bytes()
        n, mutated = mr.mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
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
            mr.restore_exact(path, original)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    if WITHHELD:
        print(f"skipped with their rails: cli_switch_cases/{', cli_switch_cases/'.join(WITHHELD)}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
