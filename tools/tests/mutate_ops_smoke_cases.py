"""mutate_ops_scripts.py's cases for the smoke test. NOT a pytest file; never run alone.

The cases for the smoke test's stages, what they count as run, and its e2e login. Each mutates one
of: deploy/installer/smoke-test.ps1.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

SMOKE = "deploy/installer/smoke-test.ps1"
T_SMOKE = "tools/tests/test_smoke_test_ps1.py"
T_DETECT = "tools/tests/test_runtime_detection.py"
T_SMALL = "tools/tests/test_ops_small_defects.py"
T_PRUNE_NAMED = "tools/tests/test_log_prune_fallback.py::test_what_the_warning_names_as_pruning_does_prune_deploy_logs"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    (
        "#9a the build stage loops over an empty image list and passes",
        SMOKE,
        "  if ($wanted.Count -eq 0) {",
        "  if ($false) {",
        "test_an_empty_image_render_fails_the_build_stage",
        T_SMOKE,
    ),
    (
        "#9b a skipped check is printed but not counted",
        SMOKE,
        '$script:skip++; Write-Log "SKIP $m" }',
        'Write-Log "SKIP $m" }',
        "test_a_secure_cookie_over_http_reports_the_unrun_checks_and_exits_2",
        T_SMOKE,
    ),
    (
        "#9b skips are counted but the exit code ignores them",
        SMOKE,
        "if ($script:skip -gt 0) {\n  Write-Host \"SMOKE TEST INCOMPLETE",
        "if ($false) {\n  Write-Host \"SMOKE TEST INCOMPLETE",
        "test_a_secure_cookie_over_http_reports_the_unrun_checks_and_exits_2",
        T_SMOKE,
    ),
    (
        "#9b the Secure-cookie early return goes back to warn-and-return",
        SMOKE,
        '      foreach ($c in $authChecks) { Skip "${c}: the Secure session cookie is not sent over plain HTTP" }\n',
        "",
        "test_a_secure_cookie_over_http_reports_the_unrun_checks_and_exits_2",
        T_SMOKE,
    ),
    (
        "#9b absent credentials skip the login silently",
        SMOKE,
        "    Skip 'login (auth DB intact): no PLATFORM_ADMIN_USER / PLATFORM_ADMIN_PASSWORD in deploy\\.env'\n",
        "",
        "test_absent_credentials_report_all_four_unrun_checks_and_exit_2",
        T_SMOKE,
    ),
    # --- P0-3: which runtime holds the platform -----------------------------------------------
    (
        "P0-3 smoke-test goes back to 'podman if podman is on PATH'",
        SMOKE,
        "  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\\.env')\n",
        "  $pick = @{ Mode = $(if (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' } "
        "else { 'desktop' }); Reason = 'first CLI'; Warning = '' }\n",
        "test_smoke_test_uses_the_runtime_that_holds_the_platform_and_says_why",
        T_DETECT,
    ),
    (
        "P0-3 -RuntimeMode no longer overrides detection",
        SMOKE,
        "if (-not $RuntimeMode) {\n  $pick = Resolve-PlatformRuntime",
        "if ($true) {\n  $pick = Resolve-PlatformRuntime",
        "test_runtime_mode_still_overrides_detection",
        T_DETECT,
    ),
    (
        "P0-3 the choice is made but its reason is never printed",
        SMOKE,
        'Write-Host "  runtime chosen: $RuntimeWhy" -ForegroundColor DarkGray\n',
        "",
        "test_smoke_test_uses_the_runtime_that_holds_the_platform_and_says_why",
        T_DETECT,
    ),
    # --- the runtime stage on Docker Desktop / WSL ----------------------------------------------
    (
        "the published-port probe goes back to podman-only, silently",
        SMOKE,
        "  # --- published ports ------------------------------------------------------\n"
        "  Test-PublishedPort\n",
        "  # --- published ports ------------------------------------------------------\n"
        "  if ($RuntimeMode -eq 'podman') { Test-PublishedPort }\n",
        "test_the_published_port_is_checked_on_docker_desktop_too",
        T_SMOKE,
    ),
    (
        "the podman-only checks pass on desktop without a word",
        SMOKE,
        "    NA (\"podman machine state, its Hyper-V memory reservation, machine provider and "
        "docker-compose.exe: \" +\n        \"podman-only, nothing to check on $RuntimeMode\")\n",
        "",
        "test_podman_only_checks_are_named_once_as_na_on_desktop_without_failing_the_run",
        T_SMOKE,
    ),
    (
        "n/a counts as a skip, so every desktop run exits 2",
        SMOKE,
        '$script:na++; Write-Log "n/a  $m" }',
        '$script:skip++; Write-Log "n/a  $m" }',
        "test_podman_only_checks_are_named_once_as_na_on_desktop_without_failing_the_run",
        T_SMOKE,
    ),
    # --- e2e logs out what it logged in -----------------------------------------------------------
    (
        "the e2e stage never logs out",
        SMOKE,
        "  try { Invoke-E2EChecks }\n  finally { if ($script:e2eLogin) { Close-E2ESession $script:e2eLogin } }\n",
        "  Invoke-E2EChecks\n",
        "test_e2e_logs_out_the_session_it_opened_and_proves_it_is_revoked",
        T_SMOKE,
    ),
    (
        "the login is not recorded, so nothing is logged out",
        SMOKE,
        "    $script:e2eLogin = $r          # Test-StageE2E logs it out, whichever way this function returns\n",
        "",
        "test_the_secure_cookie_early_return_logs_out_too",
        T_SMOKE,
    ),
    (
        "logout is sent without the session cookie",
        SMOKE,
        "  $ws.Cookies.Add((New-Object System.Net.Cookie($pair[0], $pair[1], '/', ([Uri]$BaseUrl).Host)))\n",
        "",
        "test_e2e_logs_out_the_session_it_opened_and_proves_it_is_revoked",
        T_SMOKE,
    ),
    (
        "a 401 after logout counts as revoked with no positive control",
        SMOKE,
        "  if ($before -ne 200) { Warn",
        "  if ($false) { Warn",
        "test_a_401_after_logout_proves_nothing_unless_the_cookie_worked_before",
        T_SMOKE,
    ),
    (
        "a logout that leaves the session live passes",
        SMOKE,
        "  else { Bad \"logged out, but the session still answers",
        "  else { Ok \"logged out, but the session still answers",
        "test_a_logout_that_does_not_revoke_the_session_fails_the_run",
        T_SMOKE,
    ),
    # --- a declared runtime beats an inferred one -----------------------------------------------
    (
        "smoke-test computes the contradiction but never prints it",
        SMOKE,
        'if ($RuntimeWarning) { Warn "runtime: $RuntimeWarning" }\n',
        "",
        "test_smoke_test_follows_a_declaration_and_warns_when_the_evidence_disagrees",
        T_DETECT,
    ),
    # --- the persistence stage on Docker Desktop / WSL -------------------------------------------
    (
        "the running-containers check goes back to podman-only",
        SMOKE,
        "  $up = @(Invoke-VolumeCli -Mode $RuntimeMode -CliArgs @('ps', '--format', '{{.Names}}') |",
        "  $up = @($(if ($RuntimeMode -eq 'podman') { Invoke-VolumeCli -Mode $RuntimeMode -CliArgs "
        "@('ps', '--format', '{{.Names}}') }) |",
        "test_desktop_persistence_checks_what_a_desktop_box_can_answer",
        T_SMOKE,
    ),
    (
        "restart policies are not checked, so a container that stays down passes",
        SMOKE,
        "  elseif ($up.Count -gt 0) {\n    # The engine restarts",
        "  elseif ($false) {\n    # The engine restarts",
        "test_a_container_that_will_not_restart_with_the_engine_fails_on_desktop",
        T_SMOKE,
    ),
    (
        "Docker Desktop's sign-in start is assumed",
        SMOKE,
        "      if ($auto -eq $true) {",
        "      if ($true) {",
        "test_docker_desktop_not_starting_at_sign_in_fails",
        T_SMOKE,
    ),
    (
        "the podman-only persistence checks pass on desktop without a word",
        SMOKE,
        "    NA (\"podman-restart.service, the podman logon shortcut",
        "    Ok (\"podman-restart.service, the podman logon shortcut",
        "test_desktop_persistence_checks_what_a_desktop_box_can_answer",
        T_SMOKE,
    ),
    (
        "a WSL box is told its keep-alive should not be there",
        SMOKE,
        "      if ($wslLnk.Count -gt 0) { Ok \"WSL keep-alive present",
        "      if ($wslLnk.Count -eq 0) { Ok \"WSL keep-alive present",
        "test_wsl_persistence_wants_its_keep_alive_not_the_absence_of_one",
        T_SMOKE,
    ),
    # --- one deploy\.env reader, on compose's dotenv rules --------------------------------------
    (
        "smoke-test logs in with the raw line (quotes and comment kept)",
        SMOKE,
        "    $p = [string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ADMIN_PASSWORD')\n",
        "    $p = (Get-Content $envFile | Where-Object { $_ -like 'PLATFORM_ADMIN_PASSWORD=*' } | Select-Object -Last 1).Substring(24)\n",
        "test_a_quoted_password_logs_in_with_the_value_compose_hands_the_gateway",
        T_SMOKE,
    ),
    # --- WDP: an elevated administrator's profile over another user's checkout (ops-e follow-up) --
    (
        "SMALL smoke-test asks podman for an answer nothing reads again",
        SMOKE,
        "    $rows = (& podman machine list 2>$null) -join ' '\n",
        "    $prov = (& podman machine inspect --format '{{.ConfigDir.Path}}' 2>$null)\n"
        "    $rows = (& podman machine list 2>$null) -join ' '\n",
        "test_no_runtime_call_s_answer_is_thrown_away",
        T_SMALL,
    ),
    # --- env-robust 4: a ~/.docker/config.json that is a directory ---------------------------
    (
        "ENV4 smoke-test never repairs a config.json directory",
        SMOKE,
        "$dockerConfig = Repair-DockerConfigEnv\n",
        "$dockerConfig = $null\n",
        "test_smoke_test_runs_its_containers_with_a_readable_docker_config",
        "tools/tests/test_docker_config_env.py",
    ),
    # --- SMK / INSTSVC: a lean box without Recipe Book; install-services without its rails --------
    (
        "SMK the data stage requires Recipe Book's volume on every box again",
        SMOKE,
        "  if (Test-BoxRunsRail 'recipe-book') { $required += 'platform_recipe_book_data' }\n",
        "  if ($true) { $required += 'platform_recipe_book_data' }\n",
        "test_a_lean_terminal_fun_box_passes_the_data_stage_without_recipe_books_volume",
        T_SMOKE,
    ),
    (
        "SMK a rail PLATFORM_DISABLED_APPS names still counts as running",
        SMOKE,
        "  if ($off -contains $Rail) { return $false }\n",
        "",
        "test_a_disabled_recipe_book_needs_no_volume",
        T_SMOKE,
    ),
    (
        "SMK a blank PLATFORM_ENABLED_APPS on the full stack runs no rail",
        SMOKE,
        "  if ($FullStack) { return $true }\n",
        "  if ($FullStack) { return $false }\n",
        "test_a_box_that_runs_recipe_book_still_requires_its_volume",
        T_SMOKE,
    ),
    # --- STEP: a script log prunes only its own rotated copies (delta audit 2026-10-15) ------------
    (
        "STEP smoke-test no longer prunes deploy\\logs as the warning says it does",
        SMOKE,
        "$null = Remove-RotatedServiceLogs -LogDir $LogDir\n",
        "",
        "test_what_the_warning_names_as_pruning_does_prune_deploy_logs",
        T_PRUNE_NAMED,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
]
