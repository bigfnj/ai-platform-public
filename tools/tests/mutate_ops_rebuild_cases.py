"""mutate_ops_scripts.py's cases for rebuild-stale.ps1. NOT a pytest file; never run alone.

The cases for rebuild-stale.ps1: the plan it takes from compose, and the runtime and stack it
follows. Each mutates one of: deploy/rebuild-stale.ps1.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

REBUILD = "deploy/rebuild-stale.ps1"
T_REBUILD = "tools/tests/test_rebuild_stale.py"
# test_log_prune_fallback.py, one test per suite: each runs only the fixture its test needs.
_T_PRUNE = "tools/tests/test_log_prune_fallback.py::"
T_FPRUNE_DRY = _T_PRUNE + "test_a_dry_rebuild_prunes_nothing"
T_FPRUNE_REAL = _T_PRUNE + "test_a_full_stack_rebuild_prunes_the_services_rotated_logs_to_ten"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- rebuild-stale.ps1: the plan comes from compose ------------------------------------------
    (
        "rebuild-stale goes back to a hardcoded service list",
        REBUILD,
        "    $build = $allBuild\n    $recreate = $allRecreate\n",
        "    $build = @($allBuild | Where-Object { $_ -in @('dashboard', 'iep-goals', 'job-aid', 'finance', "
        "'recipe-book', 'bouquet', 'workstation', 'terminal-fun', 'ai-playground', 'ai-voice', 'co-worker', "
        "'smb-partner-enablement', 'gemini-cx', 'meeting-atlas', 'gateway') })\n    $recreate = $allRecreate\n",
        "test_every_service_the_compose_file_builds_is_in_the_plan",
        T_REBUILD,
    ),
    (
        "rebuild-stale builds every service with an image, pulled ones included",
        REBUILD,
        "    if ($p.Value.PSObject.Properties.Name -contains 'build') {\n        $allBuild += $p.Name",
        "    if ($p.Value.PSObject.Properties.Name -contains 'image') {\n        $allBuild += $p.Name",
        "test_every_service_the_compose_file_builds_is_in_the_plan",
        T_REBUILD,
    ),
    (
        "rebuild-stale stops recreating the services that run a built image",
        REBUILD,
        "    if ($img -and $builtBy.ContainsKey($img)) {",
        "    if ($false) {",
        "test_a_shared_image_builds_first_and_the_front_door_last",
        T_REBUILD,
    ),
    (
        "rebuild-stale builds a shared image in alphabetical order, after its sharer's turn",
        REBUILD,
        "$first = @($allBuild | Where-Object { $_ -in $sharer.Values })",
        "$first = @()",
        "test_a_shared_image_builds_first_and_the_front_door_last",
        T_REBUILD,
    ),
    (
        "rebuild-stale stops putting the front door last",
        REBUILD,
        "$allBuild = @($first) + @($allBuild | Where-Object { $_ -notin $first -and $_ -ne $frontDoor }) +\n"
        "            @($allBuild | Where-Object { $_ -eq $frontDoor })",
        "$allBuild = @($first) + @($allBuild | Where-Object { $_ -notin $first })",
        "test_a_shared_image_builds_first_and_the_front_door_last",
        T_REBUILD,
    ),
    (
        "rebuild-stale plans nothing and exits 0 when compose cannot render",
        REBUILD,
        "if ($svcProps.Count -eq 0) {",
        "if ($false) {",
        "test_a_failed_compose_render_fails_instead_of_planning_nothing",
        T_REBUILD,
    ),
    # --- a rail disabled on this box is not running on this box --------------------------------
    (
        "rebuild-stale rebuilds (and so starts) a disabled rail",
        REBUILD,
        "$allBuild = @($allBuild | Where-Object { $_ -notin $offServices })",
        "$allBuild = @($allBuild)",
        "test_a_rail_disabled_on_this_box_is_left_out_of_the_plan_and_named",
        T_REBUILD,
    ),
    (
        "rebuild-stale builds a disabled rail when asked for it by name",
        REBUILD,
        "    if ($asked.Count -gt 0) {\n        # `up -d --build` would START",
        "    if ($false) {\n        # `up -d --build` would START",
        "test_asking_for_a_disabled_rail_by_name_is_refused",
        T_REBUILD,
    ),
    # --- FPRUNE: a full-stack box (no watchdog) prunes when rebuild-stale runs, at most once a day --
    (
        "FPRUNE rebuild-stale never prunes",
        REBUILD,
        "    $prune = Invoke-LogPruneFromStamp -LogDir (Join-Path $deploy 'logs') -Now ([datetime]::UtcNow)\n",
        "    $prune = @{ Ran = $false; Removed = 0 }\n",
        "test_a_full_stack_rebuild_prunes_the_services_rotated_logs_to_ten",
        T_FPRUNE_REAL,
    ),
    (
        "FPRUNE rebuild-stale prunes its own -LogDir, not the services' deploy\\logs",
        REBUILD,
        "    $prune = Invoke-LogPruneFromStamp -LogDir (Join-Path $deploy 'logs') -Now ([datetime]::UtcNow)\n",
        "    $prune = Invoke-LogPruneFromStamp -LogDir $LogDir -Now ([datetime]::UtcNow)\n",
        "test_a_full_stack_rebuild_prunes_the_services_rotated_logs_to_ten",
        T_FPRUNE_REAL,
    ),
    (
        "FPRUNE a dry run prunes",
        REBUILD,
        "if (-not $WhatIfOnly) {\n    $prune = Invoke-LogPruneFromStamp",
        "if ($true) {\n    $prune = Invoke-LogPruneFromStamp",
        "test_a_dry_rebuild_prunes_nothing",
        T_FPRUNE_DRY,
    ),
    # --- T1-c: rebuild-stale follows the box's runtime and stack ----------------------------------
    (
        "T1c rebuild-stale composes the lean stack without the box's override (FileArgs dropped)",
        REBUILD,
        "    $stackArgs = @('--env-file', $paths.Env) + $paths.FileArgs + @(Get-ComposeProfiles -Apps ($enabled -join ','))\n",
        "    $stackArgs = @('--env-file', $paths.Env) + @(Get-ComposeProfiles -Apps ($enabled -join ','))\n",
        "test_a_lean_podman_box_renders_and_builds_with_its_own_files_and_profiles",
        T_REBUILD,
    ),
    (
        "T1c rebuild-stale ignores the stack the gateway was created from",
        REBUILD,
        "    $Stack = $picked.Stack\n",
        "    $Stack = 'full'\n",
        "test_a_lean_podman_box_renders_and_builds_with_its_own_files_and_profiles",
        T_REBUILD,
    ),
    (
        "T1c rebuild-stale parks disabled rails through docker again (-Mode desktop)",
        REBUILD,
        "                              -Mode $RuntimeMode -DryRun:$DryRun\n",
        "                              -Mode desktop -DryRun:$DryRun\n",
        "test_a_lean_podman_box_parks_disabled_rails_with_podman_and_never_calls_docker",
        T_REBUILD,
    ),
    (
        "T1c rebuild-stale rebuilds a service built from outside the checkout",
        REBUILD,
        "$allBuild = @($allBuild | Where-Object { $_ -notin $externalSkipped })\n",
        "",
        "test_a_service_built_outside_the_checkout_is_skipped_unless_named",
        T_REBUILD,
    ),
    (
        "T1c rebuild-stale builds a param default from $PSScriptRoot again (empty under 5.1 -File)",
        REBUILD,
        "    [string]$LogDir,         # default: deploy\\logs\n",
        "    [string]$LogDir = (Join-Path $PSScriptRoot 'logs'),\n",
        "test_run_with_file_finds_its_own_folder[powershell.exe]",
        T_REBUILD,
    ),
    # --- a guessed runtime is refused; podman with no label is the lean stack -------------------
    (
        "RTG rebuild-stale follows a guessed runtime again",
        REBUILD,
        "    if ($pick.Basis -eq 'guess') {\n        Write-Host (\"[FAIL] the runtime is a guess",
        "    if ($false) {\n        Write-Host (\"[FAIL] the runtime is a guess",
        "test_an_undeclared_runtime_no_cli_proves_is_refused",
        T_REBUILD,
    ),
    # --- RTW / RTD (delta audit 2026-10-15): wsl with no label is lean; docker alone is Desktop ----
    (
        "RTD rebuild-stale refuses anything a container or a declaration did not prove",
        REBUILD,
        "    if ($pick.Basis -eq 'guess') {\n        Write-Host (\"[FAIL] the runtime is a guess",
        "    if ($pick.Basis -notin 'container', 'declared') {\n        Write-Host (\"[FAIL] the runtime is a guess",
        "test_docker_as_the_only_engine_cli_plans_docker_desktop_with_no_gateway",
        T_REBUILD,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        "CONTROL RTG rebuild-stale's refusal compares the other way round",
        REBUILD,
        "    if ($pick.Basis -eq 'guess') {\n",
        "    if ('guess' -eq $pick.Basis) {\n",
        "test_an_undeclared_runtime_no_cli_proves_is_refused",
        T_REBUILD,
    ),
]
