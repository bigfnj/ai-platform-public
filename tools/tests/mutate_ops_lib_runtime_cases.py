"""mutate_ops_scripts.py's cases for the installer's shared library. NOT a pytest file; never run alone.

The cases for the installer's shared library: the runtime pick, the deploy\\.env reader and
writers, the lean broker, NSSM and the service logs, the watchdog's helpers. Each mutates one of:
deploy/installer/lib-runtime.ps1.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

RUNTIME = "deploy/installer/lib-runtime.ps1"
T_DETECT = "tools/tests/test_runtime_detection.py"
T_STARTUP = "tools/tests/test_platform_startup_selftest.py"
T_APPLY = "tools/tests/test_apply_runtime.py"
T_REBUILD = "tools/tests/test_rebuild_stale.py"
T_ADOPT = "tools/tests/test_runtime_adoption.py"
T_DISABLED = "tools/tests/test_disabled_rails.py"
T_ENVW = "tools/tests/test_env_writers.py"
T_DOTENV = "tools/tests/test_dotenv_ps_reader.py"
T_LEAN = "tools/tests/test_lean_broker_auth.py"
T_OVERRIDE = "tools/tests/test_compose_override.py"
# One test of test_disabled_rails.py: its smoke-sweep parity cases need the compose-semantics
# version of that sweep too, and a red baseline would refuse every mutation in the suite.
T_DISABLED_FORMS = "tools/tests/test_disabled_rails.py::test_the_powershell_reader_reads_every_form_the_gateway_accepts"
T_SUMS = "tools/tests/test_backup_sums.py"
T_LOGS = "tools/tests/test_service_logs.py"
T_DISABLED_EMPTY = ("tools/tests/test_disabled_rails.py::"
                    "test_a_podman_that_reports_no_restart_policy_as_empty_is_still_idempotent")
T_STALE = "tools/tests/test_stale_podman_proxy.py"
T_SVCENV = "tools/tests/test_service_env.py"
T_LEANDEPS = "tools/tests/test_lean_broker_dependencies.py"
T_CPP = "tools/tests/test_control_plane_probe.py"
# test_watchdog_shutdown.py, one test per suite: the decision's tests run its fixture only, the
# watchdog's the five scratch watchdog runs.
_T_SHUT = "tools/tests/test_watchdog_shutdown.py::"
T_SHUT_IS = _T_SHUT + "test_a_1074_with_no_cancel_or_boot_after_it_is_a_shutdown"
T_SHUT_NOT = _T_SHUT + "test_anything_else_is_no_shutdown"
T_SHUT_SINCE = _T_SHUT + "test_the_log_is_read_from_the_later_of_the_last_boot_and_the_window"
T_SHUT_UNREAD = _T_SHUT + "test_a_log_that_cannot_be_read_is_no_shutdown_and_says_so"
T_SHUT_READER = _T_SHUT + "test_the_real_reader_asks_the_system_log_for_the_three_events_only"
T_LBF = "tools/tests/test_lean_broker_files.py"
T_AGREE = "tools/tests/test_broker_service_agreement.py"
T_ARGS = "tools/tests/test_install_arguments.py"
T_SMALL = "tools/tests/test_ops_small_defects.py"
T_ISR = "tools/tests/test_install_services_run.py"
# test_log_prune_fallback.py, one test per suite: each runs only the fixture its test needs.
_T_PRUNE = "tools/tests/test_log_prune_fallback.py::"
T_PRUNE_DAY = _T_PRUNE + "test_the_prune_runs_at_most_once_a_day"
T_PRUNE_BACK = _T_PRUNE + "test_a_clock_that_went_back_does_not_hold_the_prune_off"
T_HOOK_WARN = _T_PRUNE + "test_an_installer_warns_when_nssm_kept_no_hook"
T_HOOK_READ = _T_PRUNE + "test_an_installer_reads_the_hook_back_rather_than_trusting_the_exit_code"
T_HOOK_QUIET = _T_PRUNE + "test_an_installer_says_nothing_when_the_hook_is_there"
T_FPRUNE_STAMP = _T_PRUNE + "test_a_script_run_prunes_once_a_day_through_its_stamp"
T_FPRUNE_GARBLED = _T_PRUNE + "test_a_clock_set_back_or_a_garbled_stamp_is_due"
T_FPRUNE_AGAIN = _T_PRUNE + "test_a_second_rebuild_the_same_day_leaves_the_newer_logs"
# test_wsl_ollama_reach.py and the new rules in test_lean_broker_auth.py / test_platform_startup_sh.py,
# one test per suite.
_T_OLH = "tools/tests/test_wsl_ollama_reach.py::"
T_OLH_OTHER = _T_OLH + "test_every_other_runtime_reads_sets_and_restarts_nothing"
T_OLH_INHERIT = _T_OLH + "test_wsl_sets_the_variable_and_an_app_started_next_inherits_it"
T_OLH_RESTART = _T_OLH + "test_wsl_restarts_a_loopback_app_and_its_server_and_nothing_else"
T_OLH_WIDE = _T_OLH + "test_an_app_already_on_every_interface_is_left_running"
T_OLH_FOREIGN = _T_OLH + "test_a_server_the_app_did_not_start_is_named_not_stopped"
T_OLH_STUCK = _T_OLH + "test_a_restart_that_keeps_the_loopback_bind_is_said"
T_MIRR_INSTALL = _T_OLH + "test_a_mirrored_wsl_install_warns_and_writes_no_router_address"
T_MIRR_PROBE = _T_OLH + "test_the_probe_reads_the_mode_and_the_address"
T_LEAN_OLLAMA = T_LEAN + "::test_wsl_scopes_ollamas_port_as_it_scopes_the_brokers"
T_LEAN_RULE_NAME = T_LEAN + "::test_the_widened_block_rule_has_a_name_of_its_own"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- P0-3: which runtime holds the platform -----------------------------------------------
    (
        "P0-3 a stopped copy of the stack counts as much as a running one",
        RUNTIME,
        "  $running = @($seen | Where-Object { $_.State -eq 'running' })",
        "  $running = @()",
        "test_the_runtime_holding_the_gateway_is_chosen",
        T_DETECT,
    ),
    (
        "P0-3 any container whose name merely contains the gateway's counts",
        RUNTIME,
        "if ($f.Count -ge 1 -and $f[0] -eq $Container)",
        "if ($f.Count -ge 1 -and $f[0] -like \"*$Container*\")",
        "test_the_runtime_holding_the_gateway_is_chosen",
        T_DETECT,
    ),
    (
        "P0-3 a hung CLI is waited on for ever",
        RUNTIME,
        "    if (-not $p.WaitForExit($TimeoutMs)) {",
        "    if (-not $p.WaitForExit(600000)) {",
        "test_a_hanging_cli_is_cut_off_at_the_timeout",
        T_DETECT,
    ),
    (
        # Still a timeout, still reported as one, still a tree kill: only the cut-off's own
        # bound, timed from the probe's start, can see that it came 15 s late.
        "P0-3 a hung CLI is cut off at the 15 s default, not at its caller's timeout",
        RUNTIME,
        "    if (-not $p.WaitForExit($TimeoutMs)) {",
        "    if (-not $p.WaitForExit(15000)) {",
        "test_a_hanging_cli_is_cut_off_at_the_timeout",
        T_DETECT,
    ),
    (
        "P0-3 a failing 'ps' is read as an empty runtime",
        RUNTIME,
        "    if ($p.ExitCode -ne 0) {\n      $first",
        "    if ($false) {\n      $first",
        "test_a_failing_cli_is_present_but_not_ok_and_says_why",
        T_DETECT,
    ),
    # --- #20: the Podman machine-start memory fallbacks (platform-startup.ps1 -SelfTest) ---------
    (
        "#20 a static VM is sent down the reservation fallback that cannot work",
        RUNTIME,
        "      if ($memMode -eq 'static') {",
        "      if ($false) {",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 Ollama is unloaded for ANY failed start, not only Hyper-V's memory refusal",
        RUNTIME,
        "      if ($rc -ne 0 -and (Test-HyperVMemoryStartFailure -Output $start.Output)) {",
        "      if ($rc -ne 0) {",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 Hyper-V's memory refusal is no longer recognised",
        RUNTIME,
        "-match 'Not enough memory in the system to start the virtual machine|0x8007000E')",
        "-match 'no such message ever')",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 models are unloaded but the start is not retried",
        RUNTIME,
        "$unloaded.Count, ($unloaded -join ', '), $before, (Get-FreeMemoryMb))\n"
        "          $start = Start-PodmanMachineOnce\n",
        "$unloaded.Count, ($unloaded -join ', '), $before, (Get-FreeMemoryMb))\n",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 the unload keeps the model resident",
        RUNTIME,
        "      $body = @{ model = $n; keep_alive = 0 } | ConvertTo-Json -Compress",
        "      $body = @{ model = $n; keep_alive = '5m' } | ConvertTo-Json -Compress",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 a static VM's failure no longer prints the one-time fix",
        RUNTIME,
        "'cannot be set; skipping that fallback. To stop this recurring, ' + (Get-StaticMemoryFix))",
        "'cannot be set; skipping that fallback.')",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    (
        "#20 the printed fix caps a 12 GiB VM at a fixed 8 GB",
        RUNTIME,
        "    try { $StartupMb = [int]((Get-VMMemory -VMName $VmName -ErrorAction Stop).Startup / 1MB) } catch {}\n",
        "",
        "test_platform_startup_selftest_passes",
        T_STARTUP,
    ),
    # --- #22: lib-apply on a Podman box --------------------------------------------------------
    (
        "#22 Invoke-VolumeCli inherits EAP=Stop, so 5.1 throws on a CLI's stderr",
        RUNTIME,
        "  $ErrorActionPreference = 'Continue'   # same reason as Invoke-Compose: judged by exit code\n",
        "",
        "test_the_image_step_asks_podman_and_reads_its_exit_code",
        T_APPLY,
    ),
    (
        "#22 Invoke-Compose inherits EAP=Stop, so 5.1 dies on compose's progress output",
        RUNTIME,
        "  $ErrorActionPreference = 'Continue'\n  $c = Get-ComposeCommand -Mode $Mode\n",
        "  $c = Get-ComposeCommand -Mode $Mode\n",
        "test_compose_up_goes_through_docker_compose_with_the_podman_pipe",
        T_APPLY,
    ),
    # --- every runtime pick goes through Resolve-PlatformRuntime -------------------------------
    (
        "a stopped leftover outvotes a runtime that could not be asked",
        RUNTIME,
        "    if ($silent.Count -eq 0) {",
        "    if ($true) {",
        "test_the_runtime_holding_the_gateway_is_chosen",
        T_DETECT,
    ),
    # --- a timed-out CLI dies with its children -------------------------------------------------
    (
        "a timed-out CLI is killed alone and its child keeps the pipe",
        RUNTIME,
        "      Stop-ProcessTree -Process $p\n",
        "      try { $p.Kill() } catch {}\n",
        "test_a_hung_cli_is_killed_with_its_children",
        T_DETECT,
    ),
    # --- a declared runtime beats an inferred one -----------------------------------------------
    (
        "the declaration in deploy\\.env is ignored",
        RUNTIME,
        "  if ($declared) {\n    $warning = ''",
        "  if ($false) {\n    $warning = ''",
        "test_a_declared_runtime_is_read_first_and_a_contradiction_is_named",
        T_DETECT,
    ),
    (
        "a declaration contradicted by a running gateway elsewhere is followed in silence",
        RUNTIME,
        "    if ($others.Count -gt 0) {\n      $warning =",
        "    if ($false) {\n      $warning =",
        "test_a_declared_runtime_is_read_first_and_a_contradiction_is_named",
        T_DETECT,
    ),
    (
        "a value that is no runtime is taken at its word",
        RUNTIME,
        "  if ($declared -and $declared -notin @('podman', 'desktop', 'wsl')) {",
        "  if ($false) {",
        "test_a_declared_runtime_is_read_first_and_a_contradiction_is_named",
        T_DETECT,
    ),
    # --- a rail disabled on this box is not running on this box --------------------------------
    (
        "the reader ignores the JSON-array form the gateway accepts",
        RUNTIME,
        "  if ($raw.StartsWith('[')) {",
        "  if ($false) {",
        "test_the_powershell_reader_reads_every_form_the_gateway_accepts[powershell.exe-json-array]",
        T_DISABLED_FORMS,
    ),
    (
        "the reader keeps the quotes rail_smoke strips",
        RUNTIME,
        "    if ($q -eq [char]39 -or $q -eq [char]34) {\n",
        "    if ($false) {\n",
        "test_the_powershell_reader_reads_every_form_the_gateway_accepts[powershell.exe-quoted]",
        T_DISABLED_FORMS,
    ),
    (
        "the reader ignores the environment, which compose and rail_smoke let win",
        RUNTIME,
        "  $raw = [string][Environment]::GetEnvironmentVariable($Key)\n",
        "  $raw = ''\n",
        "test_the_powershell_reader_reads_every_form_the_gateway_accepts[powershell.exe-environment-wins]",
        T_DISABLED_FORMS,
    ),
    (
        "a disabled rail is stopped but left to restart at the next boot",
        RUNTIME,
        "        & $act @('update', '--restart=no', $c.Name)",
        "        & $act @('inspect', $c.Name)",
        "test_disabled_rails_are_stopped_and_kept_from_restarting",
        T_DISABLED,
    ),
    (
        "a disabled rail's running container is left running",
        RUNTIME,
        "      if ($c.State -eq 'running') { & $act @('stop', $c.Name)",
        "      if ($false) { & $act @('stop', $c.Name)",
        "test_disabled_rails_are_stopped_and_kept_from_restarting",
        T_DISABLED,
    ),
    (
        "the ai-platform.rail label is ignored, so openmaic-app keeps running",
        RUNTIME,
        "    foreach ($s in @($Services)) { if ($s.Rail -and $s.Rail -eq $app) {",
        "    foreach ($s in @($Services)) { if ($false) {",
        "test_a_service_that_exists_only_for_a_disabled_rail_is_parked_with_it",
        T_DISABLED,
    ),
    (
        "compose_service is ignored, so edu-suite parks a service named edu-suite",
        RUNTIME,
        "      $own[[string]$d.id] = if ($props -contains 'compose_service' -and $d.compose_service)",
        "      $own[[string]$d.id] = if ($false)",
        "test_a_rails_compose_service_and_its_extra_tiles_map_from_the_manifests",
        T_DISABLED,
    ),
    (
        "a re-enabled rail stays parked",
        RUNTIME,
        "      if ($policy[$c.Name] -ne 'no') { continue }\n      & $act @('update', \"--restart=$($s.Restart)\"",
        "      continue\n      & $act @('update', \"--restart=$($s.Restart)\"",
        "test_re_enabling_a_rail_brings_its_container_back",
        T_DISABLED,
    ),
    (
        "a dry run acts",
        RUNTIME,
        "    if ($DryRun) { $lines.Add(\"would $Said\"); return }",
        "    if ($false) { $lines.Add(\"would $Said\"); return }",
        "test_a_dry_run_changes_nothing_and_says_what_it_would_do",
        T_DISABLED,
    ),
    # --- deploy\.env writers read UTF-8 and settle -------------------------------------------
    (
        "the env reader goes back to Get-Content, which 5.1 decodes as ANSI",
        RUNTIME,
        "  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)\n  $nl = if",
        "  $text = (Get-Content -LiteralPath $Path) -join \"`r`n\"\n  $nl = if",
        "test_non_ascii_survives_set_env_value[powershell.exe]",
        T_ENVW,
    ),
    (
        "the env writer forces CRLF and a rewrite every call",
        RUNTIME,
        "  if ($null -ne $Previous -and $text -ceq $Previous) { return }\n",
        "  $text = $text.Replace(\"`r`n\", \"`n\").Replace(\"`n\", \"`r`n\")\n",
        "test_the_files_own_line_ending_is_kept_and_a_bom_is_dropped",
        T_ENVW,
    ),
    (
        "role activation keeps the old header and adds a new one every run",
        RUNTIME,
        "    if ($l -eq $Header) { continue }\n",
        "",
        "test_ten_role_activations_leave_deploy_env_byte_identical",
        T_ENVW,
    ),
    # --- one deploy\.env reader, on compose's dotenv rules --------------------------------------
    (
        "an unquoted value is cut at any hash, not only at ' #'",
        RUNTIME,
        "      $h = $raw.IndexOf(' #', [System.StringComparison]::Ordinal); if ($h -ge 0) { $raw = $raw.Substring(0, $h) }\n",
        "      $h = $raw.IndexOf('#', [System.StringComparison]::Ordinal); if ($h -ge 0) { $raw = $raw.Substring(0, $h) }\n",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:hash with no space before it]",
        T_DOTENV,
    ),
    (
        "the FIRST assignment of a key wins again",
        RUNTIME,
        "    $vars[$key] = $value\n    $i = $eol + 1; $line++\n",
        "    if (-not $vars.Contains($key)) { $vars[$key] = $value }\n    $i = $eol + 1; $line++\n",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:last line wins]",
        T_DOTENV,
    ),
    (
        "the reader decodes the file as the ANSI code page (5.1's Default)",
        RUNTIME,
        "  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)\n  return (ConvertFrom-DotEnvText",
        "  $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::Default)\n  return (ConvertFrom-DotEnvText",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:utf-8 value]",
        T_DOTENV,
    ),
    (
        "a single-quoted value is expanded like a double-quoted one",
        RUNTIME,
        "      if ($q -eq [char]34) { $value = Expand-DotEnvValue",
        "      if ($true) { $value = Expand-DotEnvValue",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:no expansion in single quotes]",
        T_DOTENV,
    ),
    (
        "no $-expansion at all (an unquoted pa$sword read whole, unlike compose)",
        RUNTIME,
        "  if ($Raw.IndexOf([char]36) -lt 0 -and -not ($DoubleQuoted -and $Raw.IndexOf([char]92) -ge 0)) { return $Raw }\n",
        "  return $Raw\n",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:expansion of an earlier key]",
        T_DOTENV,
    ),
    (
        "the expansion never asks the process environment",
        RUNTIME,
        "  $v = [Environment]::GetEnvironmentVariable($Name)\n",
        "  $v = $null\n",
        "test_the_reader_gives_composes_answer[powershell.exe-cases:expansion from the process environment]",
        T_DOTENV,
    ),
    (
        "an unterminated quote is read to the end of the file instead of refused",
        RUNTIME,
        '      if ($close -lt 0) { throw "${Source}: line ${line}: unterminated quoted value for $key" }\n',
        "      if ($close -lt 0) { $close = $n }\n",
        "test_the_reader_gives_composes_answer[powershell.exe-extra:an unterminated double quote]",
        T_DOTENV,
    ),
    (
        "Get-DeclaredAppList takes the FIRST line again, comment and all",
        RUNTIME,
        "  if (-not $raw -and $EnvFile) { $raw = ([string](Get-DotEnvValue -Path $EnvFile -Key $Key)).Trim() }\n",
        "  if (-not $raw -and $EnvFile -and (Test-Path -LiteralPath $EnvFile)) { foreach ($l in Get-Content -LiteralPath $EnvFile -Encoding UTF8) { if ($l.StartsWith(\"$Key=\")) { $raw = $l.Substring($Key.Length + 1).Trim().Trim('\"'); break } } }\n",
        "test_the_powershell_reader_reads_every_form_the_gateway_accepts[powershell.exe-trailing-comment]",
        T_DISABLED_FORMS,
    ),
    (
        "a value no path can hold reaches IsPathRooted (a 5.1 exception, not Missing)",
        RUNTIME,
        "  if ($override.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) { return @{ Files = @($Compose); Missing = $override } }\n",
        "",
        "test_a_double_quoted_windows_path_is_read_as_compose_reads_it",
        T_OVERRIDE,
    ),
    (
        "Get-DeclaredRuntime stops reading deploy\\.env",
        RUNTIME,
        "  return [string](Get-DotEnvValue -Path $EnvFile -Key 'PLATFORM_RUNTIME')\n",
        "  return ''\n",
        "test_wsl_binds_every_interface_and_the_firewall_scopes_it_to_the_wsl_range",
        T_LEAN,
    ),
    # --- the lean broker is authenticated and, outside wsl, bound to loopback -------------------
    (
        "the broker binds 0.0.0.0 on every runtime again",
        RUNTIME,
        "  if ($Mode -eq 'wsl') { return '0.0.0.0' }\n  return '127.0.0.1'\n",
        "  return '0.0.0.0'\n",
        "test_the_broker_binds_loopback_outside_wsl",
        T_LEAN,
    ),
    (
        # Re-anchored 2026-10-14 when the spec came to name the box's own map files on the next
        # line. Same mutation: the token entry, and only that one, is dropped.
        "the service environment carries no token",
        RUNTIME,
        "    Environment   = @('BROKER_MEDIA_ENABLED=false', \"BROKER_AUTH_TOKEN=$Token\",\n",
        "    Environment   = @('BROKER_MEDIA_ENABLED=false',\n",
        "test_a_lean_install_generates_a_token_into_env_and_the_service_environment",
        T_LEAN,
    ),
    # --- a lean box's role map and disabled list are its own files, in deploy\local -------------
    (
        "LBF the lean broker is registered on the tracked map files again",
        RUNTIME,
        "\"BROKER_ROLES_FILE=$($files.Roles)\", \"BROKER_DISABLED_FILE=$($files.Disabled)\")\n",
        "\"BROKER_ROLES_FILE=$($files.TrackedRoles)\", \"BROKER_DISABLED_FILE=$($files.TrackedDisabled)\")\n",
        "test_the_lean_broker_reads_its_maps_from_deploy_local",
        T_LBF,
    ),
    (
        "LBF a missing box file starts empty instead of from what the broker reads now",
        RUNTIME,
        "    if (Test-Path -LiteralPath $pair[1] -PathType Leaf) {\n      Copy-Item",
        "    if ($false) {\n      Copy-Item",
        "test_a_missing_box_file_is_seeded_from_what_the_broker_reads_now",
        T_LBF,
    ),
    (
        "LBF an existing box file is seeded over",
        RUNTIME,
        "    if (Test-Path -LiteralPath $pair[0]) { continue }\n",
        "",
        "test_an_existing_box_file_is_never_overwritten",
        T_LBF,
    ),
    # --- the lean installer and install-services register the same broker on a lean box --------
    (
        "AGR a re-register carries no BROKER_CLI_* setting",
        RUNTIME,
        "(($_ -like 'BROKER_CLI_*=*') -or ",
        "(($false) -or ",
        "test_a_lean_re_register_keeps_the_cli_settings_and_the_upstreams",
        T_AGREE,
    ),
    (
        "AGR what is carried wins over the lean spec",
        RUNTIME,
        "Merge-ServiceEnv -Base @(Select-CarriedBrokerEnv -Existing $Existing) -Overlay $Spec.Environment",
        "Merge-ServiceEnv -Base $Spec.Environment -Overlay @(Select-CarriedBrokerEnv -Existing $Existing)",
        "test_the_lean_spec_wins_over_what_is_carried",
        T_AGREE,
    ),
    (
        "AGR install-services binds a lean broker on every interface again",
        RUNTIME,
        "  $bind = Get-BrokerBindHost -Mode $declared\n",
        "  $bind = '0.0.0.0'\n",
        "test_the_lean_bind_follows_the_declared_runtime",
        T_AGREE,
    ),
    (
        "AGR install-services never takes a box for a lean one",
        RUNTIME,
        "    if (@($Existing) -contains 'BROKER_MEDIA_ENABLED=false') {\n",
        "    if ($false) {\n",
        "test_a_lean_box_gets_the_lean_installers_broker",
        T_AGREE,
    ),
    (
        "AGR a lean box takes -RolesFile",
        RUNTIME,
        "  if (@($FileParams).Count) {\n",
        "  if ($false) {\n",
        "test_a_lean_box_refuses_a_roles_file_and_a_missing_token",
        T_AGREE,
    ),
    # --- install.ps1's children get their arguments whole, and the password off the command line --
    (
        "ARG Join-CommandLine does not double the backslashes before its closing quote",
        RUNTIME,
        "    if ($bs) { [void]$sb.Append([char]92, 2 * $bs) }\n",
        "    if ($bs) { [void]$sb.Append([char]92, $bs) }\n",
        "test_every_argument_comes_back_whole_under_the_c_runtime_rules",
        T_ARGS,
    ),
    (
        "ARG Join-CommandLine leaves a double quote unescaped",
        RUNTIME,
        "[void]$sb.Append([char]92, 2 * $bs + 1).Append([char]34)",
        "[void]$sb.Append([char]92, 2 * $bs).Append([char]34)",
        "test_every_argument_comes_back_whole_under_the_c_runtime_rules",
        T_ARGS,
    ),
    (
        "ARG Join-CommandLine passes an empty argument bare, so it vanishes",
        RUNTIME,
        "    if ($a -ne '' -and $a -notmatch '[\\s\"]') { $parts.Add($a); continue }\n",
        "    if ($a -notmatch '[\\s\"]') { $parts.Add($a); continue }\n",
        "test_every_argument_comes_back_whole_under_the_c_runtime_rules",
        T_ARGS,
    ),
    # --- the small ops-tooling defects of the 2026-10-14 audit ---------------------------------
    (
        "SMALL a script's own log is never rotated",
        RUNTIME,
        "    if ($f.Length -ge $MaxBytes) {\n      $to = Join-Path",
        "    if ($false) {\n      $to = Join-Path",
        "test_a_script_log_past_its_bound_is_rotated_and_its_copies_pruned",
        T_SMALL,
    ),
    (
        "SMALL a script log's rotated copies are never pruned",
        RUNTIME,
        "  $null = Remove-RotatedServiceLogs -LogDir (Split-Path -Parent $Path) -Keep $Keep "
        "-Base ([IO.Path]::GetFileNameWithoutExtension($Path))\n  return $to\n",
        "  return $to\n",
        "test_a_script_log_past_its_bound_is_rotated_and_its_copies_pruned",
        T_SMALL,
    ),
    (
        "SMALL rebuild-stale's per-run logs are never pruned",
        RUNTIME,
        "Sort-Object Name -Descending | Select-Object -Skip $Keep)) {\n    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed++ } catch {}\n  }\n  return $removed",
        "Sort-Object Name -Descending | Select-Object -Skip 100000)) {\n    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed++ } catch {}\n  }\n  return $removed",
        "test_a_per_run_log_family_keeps_its_newest",
        T_SMALL,
    ),
    (
        "SMALL NSSM's download folder is left in TEMP",
        RUNTIME,
        "  finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }\n",
        "  finally { }\n",
        "test_nssm_is_installed_from_the_zip_and_nothing_stays_in_temp",
        T_SMALL,
    ),
    (
        "SMALL a pinned NSSM hash is never checked",
        RUNTIME,
        "    if ($Sha256 -and $got -ne $Sha256.ToLowerInvariant()) {\n",
        "    if ($false) {\n",
        "test_a_pinned_hash_that_does_not_match_is_refused_and_installs_nothing",
        T_SMALL,
    ),
    # --- PIN: NSSM is pinned to the 2.24 win64 exe (2026-10-14) ----------------------------------
    (
        "PIN the NSSM pin is empty again",
        RUNTIME,
        "$NssmExeSha256 = 'f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97'\n",
        "$NssmExeSha256 = ''\n",
        "test_nssm_is_pinned_to_the_2_24_win64_exe",
        T_SMALL,
    ),
    (
        "PIN Install-NssmExe's default ignores the pin, so the installers fetch unverified",
        RUNTIME,
        "        [AllowEmptyString()][string]$Sha256 = $NssmExeSha256)\n",
        "        [AllowEmptyString()][string]$Sha256 = '')\n",
        "test_an_installer_fetch_refuses_an_exe_that_is_not_the_pinned_one",
        T_SMALL,
    ),
    (
        "PIN the pin is the hash of the zip, not of the exe in it",
        RUNTIME,
        "    $got = Get-Sha256Hex -Path $exe\n",
        "    $got = Get-Sha256Hex -Path $zip\n",
        "test_nssm_is_installed_from_the_zip_and_nothing_stays_in_temp",
        T_SMALL,
    ),
    (
        # Re-anchored by the ops-f lane: the USERPROFILE branch now holds the refusal below.
        "SMALL the watchdog is given SYSTEM's own profile again",
        RUNTIME,
        "  if (-not (& $isService $EnvProfile)) {\n",
        "  if ($EnvProfile) {\n",
        "test_the_watchdog_never_gets_systems_profile",
        T_SMALL,
    ),
    # --- WDP: an elevated administrator's profile over another user's checkout (ops-e follow-up) --
    (
        "WDP an elevated admin's profile is taken over another user's checkout again",
        RUNTIME,
        "    if ($parentIsProfile -and $parent -ine $own) {\n",
        "    if ($false) {\n",
        "test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout",
        T_SMALL,
    ),
    (
        "WDP the caller's own profile is compared case-sensitively, so its own checkout is refused",
        RUNTIME,
        "    if ($parentIsProfile -and $parent -ine $own) {\n",
        "    if ($parentIsProfile -and $parent -cne $own) {\n",
        "test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout",
        T_SMALL,
    ),
    (
        "WDP the caller's own profile keeps a trailing separator, so its own checkout is refused",
        RUNTIME,
        "    $own = [IO.Path]::GetFullPath($EnvProfile).TrimEnd('\\')\n",
        "    $own = [IO.Path]::GetFullPath($EnvProfile)\n",
        "test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout",
        T_SMALL,
    ),
    (
        "WDP the refusal does not name the -WatchdogUser to pass",
        RUNTIME,
        "Pass -WatchdogUser $(Split-Path $parent -Leaf) (or set \" +\n"
        "                           \"WATCHDOG_USER)",
        "Set \" +\n                           \"WATCHDOG_USER",
        "test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout",
        T_SMALL,
    ),
    (
        "SMALL a declared runtime's own CLI is asked again",
        RUNTIME,
        "    if ($declared -eq $c.Mode) {\n      $seen +=",
        "    if ($false) {\n      $seen +=",
        "test_a_declared_runtime_asks_only_the_other_cli_briefly",
        T_DETECT,
    ),
    (
        "SMALL the other CLI gets the full timeout on a declared box",
        RUNTIME,
        "[Math]::Min($TimeoutMs, $DeclaredProbeMs)",
        "$TimeoutMs",
        "test_a_declared_runtime_asks_only_the_other_cli_briefly",
        T_DETECT,
    ),
    (
        "a box's own token is replaced by a new one on every run",
        RUNTIME,
        "  if ($t) { return @{ Token = $t; Generated = $false } }\n",
        "  if ($false) { return @{ Token = $t; Generated = $false } }\n",
        "test_a_box_that_has_a_token_keeps_it",
        T_LEAN,
    ),
    (
        # Re-anchored 2026-10-14: the block rule has a new name and covers :11434 too (OLH below).
        "wsl's block rule is gone: 0.0.0.0 is open to any python allow rule",
        RUNTIME,
        ",\n    @{ DisplayName = 'AI-Platform: broker/ollama, block all but WSL and loopback'; Action = 'Block'; LocalPort = @(11500, 11434)\n"
        "       RemoteAddress = @('0.0.0.0-126.255.255.255', '128.0.0.0-172.15.255.255', '172.32.0.0-255.255.255.255') }\n",
        "\n",
        "test_wsl_binds_every_interface_and_the_firewall_scopes_it_to_the_wsl_range",
        T_LEAN,
    ),
    (
        "wsl's block rule also blocks loopback",
        RUNTIME,
        "'0.0.0.0-126.255.255.255', '128.0.0.0-172.15.255.255'",
        "'0.0.0.0-172.15.255.255'",
        "test_wsl_binds_every_interface_and_the_firewall_scopes_it_to_the_wsl_range",
        T_LEAN,
    ),
    # --- OLH / MIRR: a wsl box's containers reach the Ollama app; mirrored networking is said ------
    (
        "OLH the block rule covers :11500 alone again, so the LAN reaches a 0.0.0.0 Ollama",
        RUNTIME,
        "block all but WSL and loopback'; Action = 'Block'; LocalPort = @(11500, 11434)\n",
        "block all but WSL and loopback'; Action = 'Block'; LocalPort = @(11500)\n",
        "test_wsl_scopes_ollamas_port_as_it_scopes_the_brokers",
        T_LEAN_OLLAMA,
    ),
    (
        "OLH the widened block rule keeps the old name, which install-native finds and keeps",
        RUNTIME,
        "@{ DisplayName = 'AI-Platform: broker/ollama, block all but WSL and loopback'; Action = 'Block'",
        "@{ DisplayName = 'AI-Platform: broker, block all but WSL and loopback'; Action = 'Block'",
        "test_the_widened_block_rule_has_a_name_of_its_own",
        T_LEAN_RULE_NAME,
    ),
    (
        "OLH every runtime gets OLLAMA_HOST and a restarted app",
        RUNTIME,
        "  if ($Mode -eq 'wsl') { return $WslOllamaHost }\n  return ''\n",
        "  return $WslOllamaHost\n",
        "test_every_other_runtime_reads_sets_and_restarts_nothing",
        T_OLH_OTHER,
    ),
    (
        # Caught by the suites' own check that the store they swap out is the User one: no test may
        # write the real user's environment to see this behaviourally.
        "OLH the user's variable is written to the process only, so the next logon's app binds loopback",
        RUNTIME,
        "Set = { param($v) [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $v, 'User') } }",
        "Set = { param($v) [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $v, 'Process') } }",
        "test_wsl_sets_the_variable_and_an_app_started_next_inherits_it",
        T_OLH_INHERIT,
    ),
    (
        "OLH the value is never put in this process, so the app started next binds loopback",
        RUNTIME,
        "  $env:OLLAMA_HOST = $want\n",
        "",
        "test_wsl_sets_the_variable_and_an_app_started_next_inherits_it",
        T_OLH_INHERIT,
    ),
    (
        "OLH a running app is never restarted",
        RUNTIME,
        "  $apps = @(Get-Process -Name 'ollama app' -ErrorAction SilentlyContinue)\n",
        "  $apps = @()\n",
        "test_wsl_restarts_a_loopback_app_and_its_server_and_nothing_else",
        T_OLH_RESTART,
    ),
    (
        "OLH every ollama.exe is stopped, an `ollama run` in a terminal too",
        RUNTIME,
        "               Where-Object { [string]$_.CommandLine -match '\\sserve(\\s|$)' })\n",
        "               Where-Object { $true })\n",
        "test_wsl_restarts_a_loopback_app_and_its_server_and_nothing_else",
        T_OLH_RESTART,
    ),
    (
        "OLH the app's server is left running, holding the loopback bind",
        RUNTIME,
        " + @($servers | ForEach-Object { [int]$_.ProcessId }))\n",
        ")\n",
        "test_wsl_restarts_a_loopback_app_and_its_server_and_nothing_else",
        T_OLH_RESTART,
    ),
    (
        "OLH an app already on 0.0.0.0 is restarted anyway",
        RUNTIME,
        "  if ($now -contains '0.0.0.0') { $lines.Add(\"Ollama already listens on 0.0.0.0:$Port\"); return $lines.ToArray() }\n",
        "",
        "test_an_app_already_on_every_interface_is_left_running",
        T_OLH_WIDE,
    ),
    (
        "OLH a server the app did not start is stopped and replaced",
        RUNTIME,
        "  if ($apps.Count -eq 0) {\n",
        "  if ($false) {\n",
        "test_a_server_the_app_did_not_start_is_named_not_stopped",
        T_OLH_FOREIGN,
    ),
    (
        "OLH a restart that keeps the loopback bind reports success",
        RUNTIME,
        "    if (@(& $listening) -contains '0.0.0.0') { $lines.Add(\"restarted the Ollama app; it listens",
        "    if ($true) { $lines.Add(\"restarted the Ollama app; it listens",
        "test_a_restart_that_keeps_the_loopback_bind_is_said",
        T_OLH_STUCK,
    ),
    (
        "MIRR mirrored mode names the LAN router as WINDOWS_HOST",
        RUNTIME,
        "  if ($mode -ne 'mirrored') { return @{ Mode = $mode; Host = $gateway; Warning = ''; From = $from } }\n",
        "  return @{ Mode = $mode; Host = $gateway; Warning = ''; From = $from }\n",
        "test_a_mirrored_wsl_install_warns_and_writes_no_router_address",
        T_MIRR_INSTALL,
    ),
    (
        "MIRR a loopback0 interface is not read as mirrored",
        RUNTIME,
        "  if (-not $mode -and $loopback0) {",
        "  if ($false) {",
        "test_the_probe_reads_the_mode_and_the_address",
        T_MIRR_PROBE,
    ),
    (
        "MIRR .wslconfig is never read",
        RUNTIME,
        "    if ($sect -in @('wsl2', 'experimental') -and $t -match",
        "    if ($false -and $t -match",
        "test_the_probe_reads_the_mode_and_the_address",
        T_MIRR_PROBE,
    ),
    (
        "MIRR a trailing comment hides .wslconfig's networkingMode",
        RUNTIME,
        "      $t = ($l -replace '[#;].*$', '').Trim()\n",
        "      $t = $l.Trim()\n",
        "test_the_probe_reads_the_mode_and_the_address",
        T_MIRR_PROBE,
    ),
    (
        "MIRR wslinfo's answer is never read, so a stale .wslconfig wins",
        RUNTIME,
        "    if ($section -ne 'route:' -and -not $mode -and $t -match",
        "    if ($false -and $t -match",
        "test_the_probe_reads_the_mode_and_the_address",
        T_MIRR_PROBE,
    ),
    (
        "the auth header is the bare token, not a Bearer",
        RUNTIME,
        '  if ($t) { return @{ Authorization = "Bearer $t" } }\n',
        "  if ($t) { return @{ Authorization = $t } }\n",
        "test_the_auth_header_is_the_env_token_as_compose_reads_it",
        T_LEAN,
    ),
    # --- the apply engine's services step is the lean one; no impossible media-venv step ---------
    (
        "the service check ignores the service's environment (any token passes)",
        RUNTIME,
        "  foreach ($e in $Spec.Environment) { if ($have -notcontains $e) { return $false } }\n",
        "",
        "test_a_service_registered_open_or_with_another_token_is_not_done",
        T_LEAN,
    ),
    # --- SHA256SUMS.txt that GNU sha256sum -c reads -----------------------------------------------
    (
        "the sums are written with Set-Content again (CRLF, a BOM on 5.1)",
        RUNTIME,
        "  [System.IO.File]::WriteAllText((Join-Path $root 'SHA256SUMS.txt'), $text, (New-Object System.Text.UTF8Encoding($false)))\n",
        "  $lines | Set-Content (Join-Path $root 'SHA256SUMS.txt') -Encoding utf8\n",
        "test_the_file_is_lf_without_a_bom_with_forward_slashes_and_right_hashes",
        T_SUMS,
    ),
    (
        "the sums keep Windows backslash paths",
        RUNTIME,
        r"                    ($_.FullName.Substring($root.Length + 1) -replace '\\', '/')" "\n",
        "                    ($_.FullName.Substring($root.Length + 1))\n",
        "test_the_file_is_lf_without_a_bom_with_forward_slashes_and_right_hashes",
        T_SUMS,
    ),
    # --- NSSM logs rotate online and are pruned -------------------------------------------------
    (
        "the prune keeps every rotated copy",
        RUNTIME,
        "    foreach ($f in @($g.Group | Sort-Object Name -Descending | Select-Object -Skip $Keep)) {\n",
        "    foreach ($f in @()) {\n",
        "test_rotated_copies_beyond_the_newest_ten_are_removed_and_nothing_else",
        T_LOGS,
    ),
    (
        "the prune removes the NEWEST copies",
        RUNTIME,
        "    foreach ($f in @($g.Group | Sort-Object Name -Descending | Select-Object -Skip $Keep)) {\n",
        "    foreach ($f in @($g.Group | Sort-Object Name | Select-Object -Skip $Keep)) {\n",
        "test_rotated_copies_beyond_the_newest_ten_are_removed_and_nothing_else",
        T_LOGS,
    ),
    (
        "no online rotation",
        RUNTIME,
        "    , @('set', $Name, 'AppRotateOnline', '1')\n",
        "",
        "test_every_service_rotates_online_and_runs_the_prune_hook",
        T_LOGS,
    ),
    (
        "the hook's command quotes nothing (a space in the path breaks it)",
        RUNTIME,
        """  $lit = { param($s) "'" + ($s -replace "'", "''") + "'" }\n""",
        "  $lit = { param($s) $s }\n",
        "test_the_hook_command_line_prunes_when_run_as_nssm_runs_it",
        T_LOGS,
    ),
    # --- PRUNE / HOOK: NSSM 2.24 has no rotate hook, so the watchdog prunes and the installers say so
    (
        "PRUNE the prune is due on every call",
        RUNTIME,
        "  if ($Now -ge $Last -and ($Now - $Last) -lt $Interval) { return @{ Ran = $false; Removed = 0; Last = $Last } }\n",
        "",
        "test_the_prune_runs_at_most_once_a_day",
        T_PRUNE_DAY,
    ),
    (
        "PRUNE a clock that went back holds the prune off",
        RUNTIME,
        "  if ($Now -ge $Last -and ($Now - $Last) -lt $Interval) {",
        "  if (($Now - $Last) -lt $Interval) {",
        "test_a_clock_that_went_back_does_not_hold_the_prune_off",
        T_PRUNE_BACK,
    ),
    (
        "PRUNE the daily prune keeps five, not the configured count",
        RUNTIME,
        "        [Parameter(Mandatory)][datetime]$Now, [int]$Keep = $NssmLogKeep,\n",
        "        [Parameter(Mandatory)][datetime]$Now, [int]$Keep = 5,\n",
        "test_the_prune_runs_at_most_once_a_day",
        T_PRUNE_DAY,
    ),
    (
        "HOOK no argument list is recognised as the hook's",
        RUNTIME,
        "  return ($Arguments.Count -ge 3 -and $Arguments[2] -eq 'AppEvents')\n",
        "  return $false\n",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    (
        "HOOK the exit code alone decides and the registry is never read",
        RUNTIME,
        "  if (Test-NssmRotateHook -Name $Name) { return '' }\n",
        "  if ($HookExit -eq 0) { return '' }\n",
        "test_an_installer_reads_the_hook_back_rather_than_trusting_the_exit_code",
        T_HOOK_READ,
    ),
    (
        "HOOK the registry is read one key too high",
        RUNTIME,
        "\\Parameters\\AppEvents\\Rotate\" `\n",
        "\\Parameters\\AppEvents\" `\n",
        "test_an_installer_says_nothing_when_the_hook_is_there",
        T_HOOK_QUIET,
    ),
    (
        # Re-anchored 2026-10-14: the no-watchdog sentence is the else branch now, and names what
        # prunes such a box instead of saying nothing does (FPRUNE below).
        "HOOK the warning tells a box with no watchdog that the watchdog prunes",
        RUNTIME,
        "  if ($WatchdogRegistered) {\n    $msg += (\" The platform-watchdog service prunes",
        "  if ($true) {\n    $msg += (\" The platform-watchdog service prunes",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    # --- FPRUNE: a full-stack box (no watchdog) prunes when rebuild-stale runs, at most once a day --
    (
        "FPRUNE the no-watchdog warning stops naming rebuild-stale",
        RUNTIME,
        "'runs: deploy\\rebuild-stale.ps1 (at most once a day), deploy\\installer\\smoke-test.ps1, or the ' +",
        "'runs: deploy\\installer\\smoke-test.ps1, or the ' +",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    (
        "FPRUNE the stamp is never written, so every run prunes",
        RUNTIME,
        "    try { [IO.File]::WriteAllText($StampFile, $r.Last.ToString('o'), (New-Object System.Text.UTF8Encoding($false))) } catch {}\n",
        "",
        "test_a_second_rebuild_the_same_day_leaves_the_newer_logs",
        T_FPRUNE_AGAIN,
    ),
    (
        "FPRUNE the stamp is never read, so every run prunes",
        RUNTIME,
        "    $last = [datetime]::Parse(([IO.File]::ReadAllText($StampFile)).Trim(),",
        "    $null = [datetime]::Parse(([IO.File]::ReadAllText($StampFile)).Trim(),",
        "test_a_script_run_prunes_once_a_day_through_its_stamp",
        T_FPRUNE_STAMP,
    ),
    (
        "FPRUNE a missing or garbled stamp holds the prune off",
        RUNTIME,
        "  } catch { $last = [datetime]::MinValue }\n",
        "  } catch { return @{ Ran = $false; Removed = 0; Last = $Now } }\n",
        "test_a_clock_set_back_or_a_garbled_stamp_is_due",
        T_FPRUNE_GARBLED,
    ),
    # --- the installer doctor requires the broker's Python ---------------------------------------
    (
        "PY311 the minimum stops reading the broker's pyproject",
        RUNTIME,
        "    if ($t -match 'requires-python\\s*=\\s*\"\\s*>=\\s*(\\d+)\\.(\\d+)') { return [version]\"$($Matches[1]).$($Matches[2])\" }\n",
        "    return [version]'3.10'\n",
        "test_the_installer_doctor_requires_the_brokers_python",
        T_ADOPT,
    ),
    # --- recorded items (engineering-record 2026-09-10/11) in these scripts -----------------------
    (
        "REC an empty podman restart policy is read as missing again",
        RUNTIME,
        "        $policy[$Matches[1].TrimStart('/')] = $(if ($Matches[2]) { $Matches[2] } else { 'no' })\n",
        "        if ($Matches[2]) { $policy[$Matches[1].TrimStart('/')] = $Matches[2] }\n",
        "test_a_podman_that_reports_no_restart_policy_as_empty_is_still_idempotent",
        T_DISABLED_EMPTY,
    ),
    # --- env-robust 4: a ~/.docker/config.json that is a directory ---------------------------
    (
        "ENV4 the directory test is inverted",
        RUNTIME,
        "  if (-not (Test-Path -LiteralPath $stray -PathType Container)) { return $null }\n",
        "  if (Test-Path -LiteralPath $stray -PathType Container) { return $null }\n",
        "test_a_config_json_directory_gets_an_empty_config_of_ours",
        "tools/tests/test_docker_config_env.py",
    ),
    (
        "ENV4 a DOCKER_CONFIG the user set is overridden",
        RUNTIME,
        "  if ($env:DOCKER_CONFIG -or -not $UserProfile) { return $null }\n",
        "  if (-not $UserProfile) { return $null }\n",
        "test_a_readable_or_absent_config_and_a_set_docker_config_are_left_alone",
        "tools/tests/test_docker_config_env.py",
    ),
    (
        "ENV4 the empty config is written by Set-Content -Encoding UTF8 (a BOM under 5.1)",
        RUNTIME,
        "    [System.IO.File]::WriteAllText($cfg, '{}', (New-Object System.Text.UTF8Encoding($false)))\n",
        "    Set-Content -LiteralPath $cfg -Value '{}' -Encoding UTF8\n",
        "test_a_config_json_directory_gets_an_empty_config_of_ours",
        "tools/tests/test_docker_config_env.py",
    ),
    # --- T1-a: the watchdog's stale-proxy reap runs instead of throwing ---------------------------
    (
        "T1a the stale-proxy filter calls try as a command again",
        RUNTIME,
        "    Get-Process podman -ErrorAction SilentlyContinue | Where-Object { Test-StalePodmanProcess $_ }\n",
        "    Get-Process podman -ErrorAction SilentlyContinue | Where-Object {\n"
        "      $_.Id -ne $PID -and (try { $_.StartTime -lt (Get-Date).AddSeconds(-30) } catch { $true })\n"
        "    }\n",
        "test_the_orphaned_proxy_is_reaped_and_nothing_in_flight_is",
        T_STALE,
    ),
    (
        "T1a a podman started a moment ago counts as the orphan",
        RUNTIME,
        "[datetime]$Before = (Get-Date).AddSeconds(-30))",
        "[datetime]$Before = (Get-Date).AddSeconds(30))",
        "test_the_orphaned_proxy_is_reaped_and_nothing_in_flight_is",
        T_STALE,
    ),
    # --- T1-c: rebuild-stale follows the box's runtime and stack ----------------------------------
    (
        "T1c Resolve-PlatformStack reads the installer's file as the full stack",
        RUNTIME,
        "  if ($leaf -eq 'docker-compose.installer.yml') { return @{ Stack = 'lean';",
        "  if ($leaf -eq 'docker-compose.installer.yml') { return @{ Stack = 'full';",
        "test_resolve_platform_stack_reads_the_gateways_compose_label",
        T_REBUILD,
    ),
    # --- a guessed runtime is refused; podman with no label is the lean stack -------------------
    (
        "RTG the first CLI on PATH is not called a guess",
        RUNTIME,
        "so the first CLI on PATH ($what)\"; Warning = ''; Basis = 'guess' }",
        "so the first CLI on PATH ($what)\"; Warning = ''; Basis = 'container' }",
        "test_a_pick_says_whether_a_container_proved_it",
        T_DETECT,
    ),
    (
        "RTG a stopped gateway is not taken as evidence",
        RUNTIME,
        "lists $Container, not running ($what)\"; Warning = ''; Basis = 'container' }",
        "lists $Container, not running ($what)\"; Warning = ''; Basis = 'guess' }",
        "test_a_pick_says_whether_a_container_proved_it",
        T_DETECT,
    ),
    (
        "RTG podman with no label means the full stack again",
        RUNTIME,
        "  $fallback = if ($Mode -eq 'podman' -or $Mode -eq 'wsl') { 'lean' } else { 'full' }\n",
        "  $fallback = if ($Mode -eq 'wsl') { 'lean' } else { 'full' }\n",
        "test_without_a_label_podman_means_the_lean_stack",
        T_REBUILD,
    ),
    (
        "RTG docker with no label means the lean stack",
        RUNTIME,
        "  $fallback = if ($Mode -eq 'podman' -or $Mode -eq 'wsl') { 'lean' } else { 'full' }\n",
        "  $fallback = 'lean'\n",
        "test_without_a_label_docker_still_means_the_full_stack",
        T_REBUILD,
    ),
    # --- RTW / RTD (delta audit 2026-10-15): wsl with no label is lean; docker alone is Desktop ----
    (
        "RTW wsl with no label means the full stack again",
        RUNTIME,
        "  $fallback = if ($Mode -eq 'podman' -or $Mode -eq 'wsl') { 'lean' } else { 'full' }\n",
        "  $fallback = if ($Mode -eq 'podman') { 'lean' } else { 'full' }\n",
        "test_without_a_label_wsl_means_the_lean_stack",
        T_REBUILD,
    ),
    (
        "RTW wsl's fallback gives no reason of its own",
        RUNTIME,
        "    'wsl'    { 'so the lean stack, the only one a WSL engine runs' }\n",
        "",
        "test_without_a_label_wsl_means_the_lean_stack",
        T_REBUILD,
    ),
    (
        "RTD docker as the only engine CLI is a guess again (rebuild-stale refuses it)",
        RUNTIME,
        "  if ($clis.Count -eq 1 -and $clis[0] -eq 'docker') {\n",
        "  if ($false) {\n",
        "test_docker_as_the_only_engine_cli_plans_docker_desktop_with_no_gateway",
        T_REBUILD,
    ),
    (
        "RTD podman beside docker counts as docker alone",
        RUNTIME,
        "  if ($clis.Count -eq 1 -and $clis[0] -eq 'docker') {\n",
        "  if ($clis -contains 'docker') {\n",
        "test_an_undeclared_runtime_no_cli_proves_is_refused",
        T_REBUILD,
    ),
    (
        "RTD docker alone is reported as a guess",
        RUNTIME,
        "and docker is the only engine CLI on PATH, so Docker Desktop ($what)\"; Warning = ''; Basis = 'only-cli' }",
        "and docker is the only engine CLI on PATH, so Docker Desktop ($what)\"; Warning = ''; Basis = 'guess' }",
        "test_a_pick_says_whether_a_container_proved_it",
        T_DETECT,
    ),
    (
        "RTG a missing runtime CLI throws out of Resolve-PlatformStack",
        RUNTIME,
        "  catch {\n    return @{ Stack = $fallback; Reason = \"no $Container to read ($($_.Exception.Message)), $so\"; Proven = $false }\n  }\n",
        "  catch { throw }\n",
        "test_resolve_platform_stack_reads_the_gateways_compose_label",
        T_REBUILD,
    ),
    (
        "RTG a fallback stack is reported as read off a label",
        RUNTIME,
        "carries no compose config_files label, $so\"; Proven = $false }",
        "carries no compose config_files label, $so\"; Proven = $true }",
        "test_resolve_platform_stack_reads_the_gateways_compose_label",
        T_REBUILD,
    ),
    # --- T1-e: install-services -BrokerCliEnvFile, and no blank in any service environment --------
    (
        "T1e the service env file takes any key (the prefix check is gone)",
        RUNTIME,
        "  $bad = @($vars.Keys | Where-Object { -not ([string]$_).StartsWith($Prefix, [StringComparison]::Ordinal) })\n",
        "  $bad = @()\n",
        "test_a_key_without_the_prefix_is_refused_by_name_and_never_by_value",
        T_SVCENV,
    ),
    (
        "T1e KEY= in the file sets an empty value instead of removing the setting",
        RUNTIME,
        "    if ($v -eq '') {\n      if ($value.Remove($k))",
        "    if ($false) {\n      if ($value.Remove($k))",
        "test_a_registry_list_and_a_file_merge_to_new_keys_with_no_blank",
        T_SVCENV,
    ),
    (
        # Re-anchored 2026-10-14 when the walk moved into $claudeMdAbove, which
        # BROKER_CLI_TEMP_ROOT's rule shares. Same mutation.
        "T1e only the workdir itself is searched for a CLAUDE.md, not the folders above it",
        RUNTIME,
        "      $up = Split-Path $d -Parent\n",
        "      $up = $null\n",
        "test_the_cli_paths_are_checked_and_a_workdir_under_a_claude_md_is_refused",
        T_SVCENV,
    ),
    (
        "T1e BROKER_CLI_TEMP_ROOT is taken unchecked, so a root inside the workdir or under a "
        "CLAUDE.md is applied",
        RUNTIME,
        "      'BROKER_CLI_TEMP_ROOT' {\n",
        "      'BROKER_CLI_TEMP_ROOT_UNCHECKED' {\n",
        "test_the_cli_paths_are_checked_and_a_workdir_under_a_claude_md_is_refused",
        T_SVCENV,
    ),
    (
        "T1e BROKER_CLI_TEMP_ROOT may sit inside BROKER_CLI_WORKDIR, which must stay empty",
        RUNTIME,
        "        if ($workdir -and (& $inside $full $workdir)) {\n",
        "        if ($false) {\n",
        "test_the_cli_paths_are_checked_and_a_workdir_under_a_claude_md_is_refused",
        T_SVCENV,
    ),
    # --- the lean broker venv is installed from the broker's pyproject (added 2026-10-14) -------
    (
        "LEANDEPS the broker venv is installed from a hand-written list again, without "
        "python-multipart, so the lean broker dies at import",
        RUNTIME,
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'services\broker'))" "\n",
        "    , @('install', '--quiet', 'fastapi>=0.111', 'uvicorn[standard]>=0.30', 'httpx>=0.27', "
        "'pydantic>=2.7', 'pydantic-settings>=2.2', 'anyio>=4')\n",
        "test_the_install_set_covers_every_dependency_the_broker_declares",
        T_LEANDEPS,
    ),
    (
        "LEANDEPS the broker is installed before platform_core, which pip cannot fetch from an index",
        RUNTIME,
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'packages\platform_core'))" "\n"
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'services\broker'))" "\n",
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'services\broker'))" "\n"
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'packages\platform_core'))" "\n",
        "test_the_install_set_covers_every_dependency_the_broker_declares",
        T_LEANDEPS,
    ),
    (
        "LEANDEPS the dry run lists no dependencies, whatever the pyproject declares",
        RUNTIME,
        r"  if ($t -notmatch '(?ms)^dependencies\s*=\s*\[(.*?)^\]') { return @() }" "\n",
        "  return @()\n",
        "test_the_dry_run_lists_what_the_broker_declares",
        T_LEANDEPS,
    ),
    (
        "LEANDEPS the import probe answers yes whatever the import did, so a venv built from the "
        "old list is never repaired",
        RUNTIME,
        "    return ($LASTEXITCODE -eq 0)\n  }\n  finally { $ErrorActionPreference = $eap }\n",
        "    return $true\n  }\n  finally { $ErrorActionPreference = $eap }\n",
        "test_the_import_probe_tells_a_complete_venv_from_one_that_is_not",
        T_LEANDEPS,
    ),
    (
        "LEANDEPS the import probe keeps the caller's EAP Stop, so 5.1 throws on the failed "
        "import's traceback instead of answering no",
        RUNTIME,
        "  $eap = $ErrorActionPreference; $ErrorActionPreference = 'Continue'\n"
        "  try {\n"
        "    & $Python -c 'import sys;",
        "  $eap = $ErrorActionPreference\n"
        "  try {\n"
        "    & $Python -c 'import sys;",
        "test_the_import_probe_tells_a_complete_venv_from_one_that_is_not",
        T_LEANDEPS,
    ),
    # --- WDOG: the control-plane probe, and the watchdog's force-stop on its word (2026-10-14) -
    (
        "WDOG the probe's pipes are redirected and never read again, so 8 KB of volumes times out",
        RUNTIME,
        "    $drainOut = $proc.StandardOutput.ReadToEndAsync()\n"
        "    $drainErr = $proc.StandardError.ReadToEndAsync()\n",
        "",
        "test_a_podman_that_prints_8_kb_answers_the_probe",
        T_CPP,
    ),
    (
        "WDOG a timed-out probe kills podman alone, and its child keeps running",
        RUNTIME,
        "      Stop-ProcessTree -Process $proc\n",
        "      try { $proc.Kill() } catch {}\n",
        "test_a_hung_probe_times_out_and_its_tree_is_killed",
        T_CPP,
    ),
    # --- SHUT: the watchdog starts nothing while Windows is shutting down or restarting ----------
    (
        "SHUT a 1075 does not cancel the shutdown",
        RUNTIME,
        "        ($_.ProviderName -eq 'User32' -and $_.Id -in @(1074, 1075)) -or\n",
        "        ($_.ProviderName -eq 'User32' -and $_.Id -in @(1074)) -or\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT a boot after the 1074 is not read, so a fast-startup boot still counts as a shutdown",
        RUNTIME,
        "        ($_.ProviderName -eq 'Microsoft-Windows-Kernel-Boot' -and $_.Id -eq 27)) } |\n",
        "        $false) } |\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT any provider's event 27 reads as a boot",
        RUNTIME,
        "        ($_.ProviderName -eq 'Microsoft-Windows-Kernel-Boot' -and $_.Id -eq 27)) } |\n",
        "        ($_.Id -eq 27)) } |\n",
        "test_a_1074_with_no_cancel_or_boot_after_it_is_a_shutdown",
        T_SHUT_IS,
    ),
    (
        "SHUT any provider's 1074 reads as a shutdown",
        RUNTIME,
        "        ($_.ProviderName -eq 'User32' -and $_.Id -in @(1074, 1075)) -or\n",
        "        ($_.Id -in @(1074, 1075)) -or\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT the last boot is ignored, so the shutdown this boot followed still counts",
        RUNTIME,
        "  if ($LastBoot -is [datetime] -and $LastBoot -gt $since) { $since = $LastBoot }\n",
        "",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT the window is ignored, so an abandoned shutdown holds every start off until a boot",
        RUNTIME,
        "  $since = $Now.AddMinutes(-$WindowMinutes)\n",
        "  $since = [datetime]::MinValue\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT the events are not held to the window, only the query is",
        RUNTIME,
        "      $_ -and $_.TimeCreated -ge $since -and (\n",
        "      $_ -and (\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT the oldest event decides",
        RUNTIME,
        "    Sort-Object -Property TimeCreated, RecordId -Descending | Select-Object -First 1\n",
        "    Sort-Object -Property TimeCreated, RecordId | Select-Object -First 1\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT a log that cannot be read is taken for a shutdown",
        RUNTIME,
        "      return @{ InProgress = $false; Detail = \"the System log could not be read",
        "      return @{ InProgress = $true; Detail = \"the System log could not be read",
        "test_a_log_that_cannot_be_read_is_no_shutdown_and_says_so",
        T_SHUT_UNREAD,
    ),
    (
        "SHUT Get-WinEvent's no-events error is read as an unreadable log",
        RUNTIME,
        "    if ([string]$_.FullyQualifiedErrorId -notlike 'NoMatchingEventsFound*') {\n",
        "    if ($true) {\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "SHUT the log is read from the window's start, never the last boot",
        RUNTIME,
        "  if ($LastBoot -is [datetime] -and $LastBoot -gt $since) { $since = $LastBoot }\n",
        "",
        "test_the_log_is_read_from_the_later_of_the_last_boot_and_the_window",
        T_SHUT_SINCE,
    ),
    (
        "SHUT the real reader swallows its errors, so an unreadable log reads as none",
        RUNTIME,
        "                                   Id = @(1074, 1075, 27); StartTime = $Since } -ErrorAction Stop\n",
        "                                   Id = @(1074, 1075, 27); StartTime = $Since } -ErrorAction SilentlyContinue\n",
        "test_the_real_reader_asks_the_system_log_for_the_three_events_only",
        T_SHUT_READER,
    ),
    # --- SDR: a parsed compose render is stdout only; LEANC: the lean stack's pass-throughs ----
    (
        "SDR the render helper hands compose's stderr back inside the JSON",
        RUNTIME,
        "    Json = (@($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | "
        "ForEach-Object { [string]$_ }) -join \"`n\")\n",
        "    Json = (@($all | ForEach-Object { [string]$_ }) -join \"`n\")\n",
        "test_the_render_keeps_stderr_out_of_the_json",
        T_DISABLED,
    ),
    # --- CLIM: a BROKER_CLI_* value that is a comment, or a model the list does not allow --------
    (
        "CLIM a value that starts with # is taken as the value again",
        RUNTIME,
        "    if ($v.TrimStart().StartsWith('#')) {\n",
        "    if ($false) {\n",
        "test_a_model_that_is_a_comment_or_not_allowed_is_refused",
        T_SVCENV,
    ),
    (
        "CLIM BROKER_CLI_MODEL is not held to the BROKER_CLI_MODELS the same file sets",
        RUNTIME,
        "        if ($null -ne $models -and $models -cnotcontains $v.Trim()) {\n",
        "        if ($false) {\n",
        "test_a_model_that_is_a_comment_or_not_allowed_is_refused",
        T_SVCENV,
    ),
    (
        "CLIM the FIRST BROKER_CLI_MODELS line wins, unlike every other key",
        RUNTIME,
        "      $models = if ($l.Trim()) { @($l -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { $null }\n",
        "      $models = if ($l.Trim()) { @($l -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) } else { $null }\n"
        "      break\n",
        "test_a_model_that_is_a_comment_or_not_allowed_is_refused",
        T_SVCENV,
    ),
    (
        "CLIM a placeholder in a path reaches GetExtension again, which 5.1 throws on",
        RUNTIME,
        "    if ($pathKeys -contains $k -and (& $unpathable $v)) {\n",
        "    if ($false) {\n",
        "test_a_placeholder_left_in_a_path_is_named_not_thrown_on[powershell.exe]",
        T_SVCENV,
    ),
    # --- PWQ: the admin's name and password go into deploy\.env quoted (delta audit 2026-10-15) ----
    (
        "PWQ the password is written bare by the shared filler",
        RUNTIME,
        "  $out = $out.Replace('{{ADMIN_PASSWORD}}', (ConvertTo-DotEnvQuoted -Value $AdminPassword))\n",
        "  $out = $out.Replace('{{ADMIN_PASSWORD}}', $AdminPassword)\n",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "PWQ the name is written bare by the shared filler",
        RUNTIME,
        "  $out = $Text.Replace('{{ADMIN_USER}}', (ConvertTo-DotEnvQuoted -Value $AdminUser))\n",
        "  $out = $Text.Replace('{{ADMIN_USER}}', $AdminUser)\n",
        "test_the_admin_name_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "PWQ a $ is left unescaped inside the quotes",
        RUNTIME,
        r".Replace('$', '\$')",
        "",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "PWQ a backslash is left unescaped inside the quotes",
        RUNTIME,
        "$Value.Replace('\\', '\\\\').Replace('\"', '\\\"')",
        "$Value.Replace('\"', '\\\"')",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "PWQ backslashes are escaped after the quotes, doubling the quote's own escape",
        RUNTIME,
        "$Value.Replace('\\', '\\\\').Replace('\"', '\\\"')",
        "$Value.Replace('\"', '\\\"').Replace('\\', '\\\\')",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "PWQ a line break goes into deploy\\.env raw",
        RUNTIME,
        ".Replace(\"`r\", '\\r').Replace(\"`n\", '\\n')",
        "",
        "test_each_written_value_is_one_line_and_the_rest_of_the_template_is_untouched",
        T_ARGS,
    ),
    # --- FFM: the broker service is given the ffmpeg its installing shell finds (AI_VOICE_FFMPEG) ---
    (
        "FFM a toolbox shim is handed to the service as it is",
        RUNTIME,
        "  if ($src -notmatch '\\.(cmd|bat)$') { return @{ Path = $src; Note = \"the ffmpeg on PATH, $src\" } }\n",
        "  return @{ Path = $src; Note = \"the ffmpeg on PATH, $src\" }\n",
        "test_each_shape_of_ffmpeg_on_path_resolves_to_the_exe_it_runs",
        T_ISR,
    ),
    (
        "FFM a shim's %~dp0 is not expanded",
        RUNTIME,
        "    $p = [Environment]::ExpandEnvironmentVariables($p.Replace('%~dp0', $dir + '\\'))\n",
        "    $p = [Environment]::ExpandEnvironmentVariables($p)\n",
        "test_each_shape_of_ffmpeg_on_path_resolves_to_the_exe_it_runs",
        T_ISR,
    ),
    (
        "FFM an exe a shim names is handed over though it is not there",
        RUNTIME,
        "    if (Test-Path -LiteralPath $p -PathType Leaf) {\n      return @{ Path = [IO.Path]::GetFullPath($p)",
        "    if ($true) {\n      return @{ Path = [IO.Path]::GetFullPath($p)",
        "test_no_ffmpeg_or_a_shim_naming_nothing_gives_no_path_and_says_why",
        T_ISR,
    ),
    # --- STEP: a script log prunes only its own rotated copies (delta audit 2026-10-15) ------------
    (
        "STEP Step-ScriptLog prunes every rotated family in its log's folder again",
        RUNTIME,
        "-Keep $Keep -Base ([IO.Path]::GetFileNameWithoutExtension($Path))\n",
        "-Keep $Keep\n",
        "test_a_script_log_prunes_only_its_own_rotated_copies",
        T_SMALL,
    ),
    (
        "STEP Remove-RotatedServiceLogs ignores -Base",
        RUNTIME,
        "  if ($Base) { $rotated = @($rotated | Where-Object { [regex]::Match($_.Name, $rx).Groups['base'].Value -eq $Base }) }\n",
        "",
        "test_a_script_log_prunes_only_its_own_rotated_copies",
        T_SMALL,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        "CONTROL ENV4 the empty config is written by Set-Content -NoNewline -Encoding ascii",
        RUNTIME,
        "    [System.IO.File]::WriteAllText($cfg, '{}', (New-Object System.Text.UTF8Encoding($false)))\n",
        "    Set-Content -LiteralPath $cfg -Value '{}' -NoNewline -Encoding ascii\n",
        "test_a_config_json_directory_gets_an_empty_config_of_ours",
        "tools/tests/test_docker_config_env.py",
    ),
    (
        "CONTROL LEANDEPS pip's quiet flag is spelled short in the install set",
        RUNTIME,
        r"    , @('install', '--quiet', '-e', (Join-Path $Root 'services\broker'))" "\n",
        r"    , @('install', '-q', '-e', (Join-Path $Root 'services\broker'))" "\n",
        "test_the_install_set_covers_every_dependency_the_broker_declares",
        T_LEANDEPS,
    ),
    # The same busy host for the two tests that bounded a run from its start with the kill inside
    # (2026-10-14): each now times the cut-off up to the kill and the return from the kill's end.
    (
        "CONTROL P0-3 Stop-ProcessTree is 10 s slow to start, as on a loaded host",
        RUNTIME,
        "  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)\n  try {\n",
        "  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)\n"
        "  Start-Sleep -Seconds 10\n  try {\n",
        "test_a_hanging_cli_is_cut_off_at_the_timeout",
        T_DETECT,
    ),
    # The control-plane probe's tests time nothing; these prove they do not need to.
    (
        "CONTROL WDOG the probe drains stdout by copying it to a null stream instead",
        RUNTIME,
        "    $drainOut = $proc.StandardOutput.ReadToEndAsync()\n",
        "    $drainOut = $proc.StandardOutput.BaseStream.CopyToAsync([System.IO.Stream]::Null)\n",
        "test_a_podman_that_prints_8_kb_answers_the_probe",
        T_CPP,
    ),
    (
        # Thread.Sleep, not Start-Sleep: the watchdog half of that suite shadows Start-Sleep.
        "CONTROL WDOG Stop-ProcessTree is 10 s slow to start, as on a loaded host",
        RUNTIME,
        "  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)\n  try {\n",
        "  param([Parameter(Mandatory)][System.Diagnostics.Process]$Process)\n"
        "  [System.Threading.Thread]::Sleep(10000)\n  try {\n",
        "test_a_podman_that_never_answers_is_force_stopped_after_the_confirming_probe",
        T_CPP,
    ),
    (
        "CONTROL SDR the render helper sorts stderr out with a foreach instead of a pipeline",
        RUNTIME,
        "    Err  = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | "
        "ForEach-Object { [string]$_ })\n",
        "    Err  = @(foreach ($x in $all) { if ($x -is [System.Management.Automation.ErrorRecord]) { [string]$x } })\n",
        "test_the_render_keeps_stderr_out_of_the_json",
        T_DISABLED,
    ),
    (
        "CONTROL CLIM the comment test trims both ends instead of the start",
        RUNTIME,
        "    if ($v.TrimStart().StartsWith('#')) {\n",
        "    if ($v.Trim().StartsWith('#')) {\n",
        "test_a_model_that_is_a_comment_or_not_allowed_is_refused",
        T_SVCENV,
    ),
    (
        "CONTROL OLH the runtime is compared case-sensitively (every caller passes it lower-case)",
        RUNTIME,
        "  if ($Mode -eq 'wsl') { return $WslOllamaHost }\n",
        "  if ($Mode -ceq 'wsl') { return $WslOllamaHost }\n",
        "test_wsl_sets_the_variable_and_an_app_started_next_inherits_it",
        T_OLH_INHERIT,
    ),
    (
        "CONTROL SHUT the window written in hours",
        RUNTIME,
        "  $since = $Now.AddMinutes(-$WindowMinutes)\n",
        "  $since = $Now.AddHours(-$WindowMinutes / 60)\n",
        "test_anything_else_is_no_shutdown",
        T_SHUT_NOT,
    ),
    (
        "CONTROL SHUT the shutdown ids matched with -contains",
        RUNTIME,
        "        ($_.ProviderName -eq 'User32' -and $_.Id -in @(1074, 1075)) -or\n",
        "        ($_.ProviderName -eq 'User32' -and @(1074, 1075) -contains $_.Id) -or\n",
        "test_a_1074_with_no_cancel_or_boot_after_it_is_a_shutdown",
        T_SHUT_IS,
    ),
    (
        "CONTROL MIRR the gateway's octets are matched as runs of one to three digits",
        RUNTIME,
        "$t -match '^default\\s+via\\s+(\\d+\\.\\d+\\.\\d+\\.\\d+)'",
        "$t -match '^default\\s+via\\s+(\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3})'",
        "test_the_probe_reads_the_mode_and_the_address",
        T_MIRR_PROBE,
    ),
    (
        "CONTROL FPRUNE the stamp is written by Set-Content as ASCII with no newline",
        RUNTIME,
        "    try { [IO.File]::WriteAllText($StampFile, $r.Last.ToString('o'), (New-Object System.Text.UTF8Encoding($false))) } catch {}\n",
        "    try { Set-Content -LiteralPath $StampFile -Value $r.Last.ToString('o') -NoNewline -Encoding ascii -ErrorAction Stop } catch {}\n",
        "test_a_script_run_prunes_once_a_day_through_its_stamp",
        T_FPRUNE_STAMP,
    ),
    (
        "CONTROL PRUNE the interval is written as 24 hours",
        RUNTIME,
        "        [timespan]$Interval = [timespan]::FromDays(1))\n",
        "        [timespan]$Interval = [timespan]::FromHours(24))\n",
        "test_the_prune_runs_at_most_once_a_day",
        T_PRUNE_DAY,
    ),
    (
        "CONTROL HOOK the hook key is read with -Path instead of -LiteralPath",
        RUNTIME,
        "  $p = Get-ItemProperty -LiteralPath \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\$Name\\Parameters\\AppEvents\\Rotate\" `\n",
        "  $p = Get-ItemProperty -Path \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\$Name\\Parameters\\AppEvents\\Rotate\" `\n",
        "test_an_installer_says_nothing_when_the_hook_is_there",
        T_HOOK_QUIET,
    ),
    (
        "CONTROL PIN the pin is written in upper case",
        RUNTIME,
        "$NssmExeSha256 = 'f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97'\n",
        "$NssmExeSha256 = 'F689EE9AF94B00E9E3F0BB072B34CAAF207F32DCB4F5782FC9CA351DF9A06C97'\n",
        "test_nssm_is_pinned_to_the_2_24_win64_exe",
        T_SMALL,
    ),
    (
        "CONTROL SMALL the per-run log prune sorts by full name",
        RUNTIME,
        "-Filter $Filter | Sort-Object Name -Descending",
        "-Filter $Filter | Sort-Object FullName -Descending",
        "test_a_per_run_log_family_keeps_its_newest",
        T_SMALL,
    ),
    (
        "CONTROL ARG Join-CommandLine writes an escaped quote's backslashes as one string",
        RUNTIME,
        "[void]$sb.Append([char]92, 2 * $bs + 1).Append([char]34)",
        "[void]$sb.Append(('\\' * (2 * $bs + 1))).Append([char]34)",
        "test_every_argument_comes_back_whole_under_the_c_runtime_rules",
        T_ARGS,
    ),
    (
        "CONTROL AGR the carried BROKER_CLI_* match is spelled as a regex",
        RUNTIME,
        "(($_ -like 'BROKER_CLI_*=*') -or ",
        "(($_ -match '^BROKER_CLI_[^=]*=') -or ",
        "test_a_lean_re_register_keeps_the_cli_settings_and_the_upstreams",
        T_AGREE,
    ),
    (
        "CONTROL LBF the seed copies through .NET instead of Copy-Item",
        RUNTIME,
        "      Copy-Item -LiteralPath $pair[1] -Destination $pair[0]\n",
        "      [System.IO.File]::Copy($pair[1], $pair[0])\n",
        "test_a_missing_box_file_is_seeded_from_what_the_broker_reads_now",
        T_LBF,
    ),
    (
        "CONTROL PWQ the line breaks are escaped LF first",
        RUNTIME,
        ".Replace(\"`r\", '\\r').Replace(\"`n\", '\\n')",
        ".Replace(\"`n\", '\\n').Replace(\"`r\", '\\r')",
        "test_each_written_value_is_one_line_and_the_rest_of_the_template_is_untouched",
        T_ARGS,
    ),
    (
        "CONTROL STEP the log's base compared case-sensitively (it is the same file name)",
        RUNTIME,
        "[regex]::Match($_.Name, $rx).Groups['base'].Value -eq $Base }) }\n",
        "[regex]::Match($_.Name, $rx).Groups['base'].Value -ceq $Base }) }\n",
        "test_a_script_log_prunes_only_its_own_rotated_copies",
        T_SMALL,
    ),
    (
        "CONTROL FFM the first ffmpeg taken with Select-Object -First 1",
        RUNTIME,
        "  $cmd = @(Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue)[0]\n",
        "  $cmd = Get-Command ffmpeg -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1\n",
        "test_each_shape_of_ffmpeg_on_path_resolves_to_the_exe_it_runs",
        T_ISR,
    ),
    (
        "CONTROL RTD the CLIs are counted through Measure-Object",
        RUNTIME,
        "  if ($clis.Count -eq 1 -and $clis[0] -eq 'docker') {\n",
        "  if (@($clis | Measure-Object).Count -eq 1 -and $clis[0] -eq 'docker') {\n",
        "test_docker_as_the_only_engine_cli_plans_docker_desktop_with_no_gateway",
        T_REBUILD,
    ),
    (
        "CONTROL RTW the lean fallback's runtimes matched with -in",
        RUNTIME,
        "  $fallback = if ($Mode -eq 'podman' -or $Mode -eq 'wsl') { 'lean' } else { 'full' }\n",
        "  $fallback = if ($Mode -in 'podman', 'wsl') { 'lean' } else { 'full' }\n",
        "test_without_a_label_wsl_means_the_lean_stack",
        T_REBUILD,
    ),
    (
        "CONTROL PWQ the quotes are added by String.Concat",
        RUNTIME,
        "  return ('\"' + $Value.Replace(",
        "  return [string]::Concat('\"', $Value.Replace(",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    (
        "CONTROL WDP the two profiles compared with -not -ieq",
        RUNTIME,
        "    if ($parentIsProfile -and $parent -ine $own) {\n",
        "    if ($parentIsProfile -and -not ($parent -ieq $own)) {\n",
        "test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout",
        T_SMALL,
    ),
]
