"""mutate_ops_scripts.py's cases for the compose files. NOT a pytest file; never run alone.

The cases for the compose files of both stacks, the lean Caddyfile and the bundled gateway's image.
Each mutates one of: deploy/Dockerfile.gateway.bundled, deploy/docker-compose.yml,
deploy/installer/Caddyfile, deploy/installer/docker-compose.installer.yml.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

T_LEAN = "tools/tests/test_lean_broker_auth.py"
LEAN_COMPOSE = "deploy/installer/docker-compose.installer.yml"
FULL_COMPOSE = "deploy/docker-compose.yml"
T_TMPFS = "tools/tests/test_compose_tmpfs.py"
# terminal-fun's sized /tmp in the full compose, with the comment line above it: 256m is iep-goals'
# and finance's size too, and their rails' harnesses anchor theirs the same way.
TF_TMP = ("    # Crawl tree; a full /tmp fails a session (ENOSPC), not the VM. The lean compose's is the same.\n"
          "    tmpfs:\n      - /tmp:size=256m\n")
T_LEANC = "tools/tests/test_lean_compose.py"
T_SMALL = "tools/tests/test_ops_small_defects.py"
LEAN_CADDY = "deploy/installer/Caddyfile"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- WDP: an elevated administrator's profile over another user's checkout (ops-e follow-up) --
    (
        "SMALL the lean Caddyfile serves no 127.0.0.1 again",
        LEAN_CADDY,
        "http://platform.localhost, http://localhost, http://127.0.0.1 {",
        "http://platform.localhost, http://localhost {",
        "test_127_0_0_1_is_served_by_the_localhost_site",
        T_SMALL,
    ),
    # --- OLH / MIRR: a wsl box's containers reach the Ollama app; mirrored networking is said ------
    (
        # Re-anchored 2026-10-14 when the gateway's broker URL and timeout came to follow its
        # token. Same mutation: the gateway's own token line, and only that one, is dropped.
        "the lean gateway is not handed the token",
        LEAN_COMPOSE,
        "      BROKER_AUTH_TOKEN: ${BROKER_AUTH_TOKEN:-}\n"
        "      # The same broker the image's ENV names, stated here as on the full stack, with the full\n",
        "      # The same broker the image's ENV names, stated here as on the full stack, with the full\n",
        "test_every_lean_service_that_reaches_the_host_carries_the_token",
        T_LEAN,
    ),
    # --- recorded items (engineering-record 2026-09-10/11) in these scripts -----------------------
    (
        "REC lean terminal-fun keeps /data in the container layer again",
        LEAN_COMPOSE,
        "      - terminal_fun_data:/data\n",
        "",
        "test_every_named_volume_the_full_stack_mounts_the_lean_stack_mounts_too",
        T_LEANC,
    ),
    (
        "REC2 lean terminal-fun runs its games on the backend's uid again (no user: 0:0)",
        LEAN_COMPOSE,
        '    user: "0:0"\n    tmpfs:\n',
        "    tmpfs:\n",
        "test_terminal_fun_has_the_full_stacks_uid_split",
        T_LEANC,
    ),
    (
        "REC2 lean terminal-fun is not granted the two drop-privilege capabilities",
        LEAN_COMPOSE,
        "    cap_add:\n      - SETUID\n      - SETGID\n    expose:\n      - \"8730\"\n",
        "    expose:\n      - \"8730\"\n",
        "test_terminal_fun_has_the_full_stacks_uid_split",
        T_LEANC,
    ),
    # --- SDR: a parsed compose render is stdout only; LEANC: the lean stack's pass-throughs ----
    (
        "LEANC the lean gateway goes without PLATFORM_OLLAMA_URL again, so Models > Delete 502s",
        LEAN_COMPOSE,
        "      PLATFORM_OLLAMA_URL: http://host.docker.internal:11434\n",
        "",
        "test_every_setting_the_full_stack_hands_a_service_the_lean_stack_hands_it_too",
        T_LEANC,
    ),
    (
        "LEANC lean recipe-book's broker client gives up at its own 600 s again",
        LEAN_COMPOSE,
        "      RECIPE_BOOK_BROKER_TIMEOUT: ${RECIPE_BOOK_BROKER_TIMEOUT:-1320}\n",
        "",
        "test_every_setting_the_full_stack_hands_a_service_the_lean_stack_hands_it_too",
        T_LEANC,
    ),
    (
        "LEANC an omitted gateway dist is neither baked into the image nor for an absent rail",
        "deploy/Dockerfile.gateway.bundled",
        "    PLATFORM_TERMINAL_FUN_DIST=/app/dist/terminal-fun \\\n",
        "",
        "test_the_gateway_settings_the_lean_stack_leaves_out_are_baked_in_or_for_an_absent_rail",
        T_LEANC,
    ),
    # --- TMPFS: every tmpfs in both compose files has a size (ops-e follow-up) ---------------------
    (
        "TMPFS workstation's /tmp is unsized again",
        FULL_COMPOSE,
        "      - /tmp:size=16m\n",
        "      - /tmp\n",
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS terminal-fun's /tmp is unsized again",
        FULL_COMPOSE,
        TF_TMP,
        TF_TMP.replace("/tmp:size=256m", "/tmp"),
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS the lean terminal-fun's /tmp is unsized again",
        LEAN_COMPOSE,
        "      - /tmp:size=256m   # session homes",
        "      - /tmp   # session homes",
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS workstation's /tmp sized 0, which tmpfs takes as no limit",
        FULL_COMPOSE,
        "      - /tmp:size=16m\n",
        "      - /tmp:size=0\n",
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS terminal-fun's /tmp sized as a share of the VM's memory",
        FULL_COMPOSE,
        TF_TMP,
        TF_TMP.replace("/tmp:size=256m", "/tmp:size=50%"),
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS a long-form tmpfs volume arrives unsized",
        LEAN_COMPOSE,
        "      - terminal_fun_data:/data\n",
        "      - terminal_fun_data:/data\n      - type: tmpfs\n        target: /scratch\n",
        "test_every_tmpfs_in_both_compose_files_has_a_size",
        T_TMPFS,
    ),
    (
        "TMPFS terminal-fun's /tmp sized past its share",
        FULL_COMPOSE,
        TF_TMP,
        TF_TMP.replace("/tmp:size=256m", "/tmp:size=4g"),
        "test_terminal_fun_tmp_is_sized_for_its_session_homes_on_both_stacks",
        T_TMPFS,
    ),
    (
        "TMPFS workstation's /tmp sized past anything it writes",
        FULL_COMPOSE,
        "      - /tmp:size=16m\n",
        "      - /tmp:size=256m\n",
        "test_workstation_tmp_is_sized_for_what_it_writes",
        T_TMPFS,
    ),
    (
        "TMPFS the lean terminal-fun's /tmp is no longer the full stack's",
        LEAN_COMPOSE,
        "      - /tmp:size=256m   # session homes",
        "      - /tmp:size=128m   # session homes",
        "test_terminal_fun_tmp_is_sized_for_its_session_homes_on_both_stacks",
        T_TMPFS,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        "CONTROL SMALL the lean Caddyfile names 127.0.0.1 first",
        LEAN_CADDY,
        "http://platform.localhost, http://localhost, http://127.0.0.1 {",
        "http://127.0.0.1, http://platform.localhost, http://localhost {",
        "test_127_0_0_1_is_served_by_the_localhost_site",
        T_SMALL,
    ),
    (
        "CONTROL TMPFS workstation's size written in kilobytes",
        FULL_COMPOSE,
        "      - /tmp:size=16m\n",
        "      - /tmp:size=16384k\n",
        "test_workstation_tmp_is_sized_for_what_it_writes",
        T_TMPFS,
    ),
    (
        "CONTROL TMPFS terminal-fun's tmpfs mode given beside its size",
        FULL_COMPOSE,
        TF_TMP,
        TF_TMP.replace("/tmp:size=256m", "/tmp:size=256m,mode=1777"),
        "test_terminal_fun_tmp_is_sized_for_its_session_homes_on_both_stacks",
        T_TMPFS,
    ),
]
