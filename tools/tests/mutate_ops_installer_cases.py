"""mutate_ops_scripts.py's cases for the installers and the apply engine. NOT a pytest file; never run alone.

The cases for the installers and the apply engine, and the files they write or document. Each
mutates one of: deploy/.gitignore, deploy/INSTRUCTIONS.md, deploy/activate-model-roles.ps1,
deploy/install-services.ps1, deploy/installer/apply.ps1, deploy/installer/env.lean.example,
deploy/installer/install-native.ps1, deploy/installer/install.ps1, deploy/installer/lib-apply.ps1,
deploy/installer/lib-modelplan.ps1, deploy/installer/lib-preflight.ps1, deploy/provision-speech-
venv.ps1, get.ps1, services/broker/README.md.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

APPLY = "deploy/installer/lib-apply.ps1"
T_APPLY = "tools/tests/test_apply_runtime.py"
PREFLIGHT = "deploy/installer/lib-preflight.ps1"
ACTIVATE = "deploy/activate-model-roles.ps1"
INSTALL = "deploy/installer/install.ps1"
T_ADOPT = "tools/tests/test_runtime_adoption.py"
T_ENVW = "tools/tests/test_env_writers.py"
ACTIVATE_PS = "deploy/activate-model-roles.ps1"
T_DOTENV = "tools/tests/test_dotenv_ps_reader.py"
T_LEAN = "tools/tests/test_lean_broker_auth.py"
SERVICES = "deploy/install-services.ps1"
NATIVE = "deploy/installer/install-native.ps1"
T_LOGS = "tools/tests/test_service_logs.py"
MODELPLAN = "deploy/installer/lib-modelplan.ps1"
T_ROLES = "tools/tests/test_apply_roles.py"
T_SVCENV = "tools/tests/test_service_env.py"
T_LEANDEPS = "tools/tests/test_lean_broker_dependencies.py"
GET_PS1 = "get.ps1"
DEPLOY_IGNORE = "deploy/.gitignore"
T_LBF = "tools/tests/test_lean_broker_files.py"
T_AGREE = "tools/tests/test_broker_service_agreement.py"
T_ARGS = "tools/tests/test_install_arguments.py"
T_SMALL = "tools/tests/test_ops_small_defects.py"
T_ISR = "tools/tests/test_install_services_run.py"
# test_log_prune_fallback.py, one test per suite: each runs only the fixture its test needs.
_T_PRUNE = "tools/tests/test_log_prune_fallback.py::"
T_HOOK_WARN = _T_PRUNE + "test_an_installer_warns_when_nssm_kept_no_hook"
# test_wsl_ollama_reach.py and the new rules in test_lean_broker_auth.py / test_platform_startup_sh.py,
# one test per suite.
_T_OLH = "tools/tests/test_wsl_ollama_reach.py::"
T_OLH_INSTALL = _T_OLH + "test_a_wsl_install_sets_ollama_host_before_the_app_starts"
T_OLH_INSTALL_LATE = _T_OLH + "test_install_ps1_sets_the_host_in_its_own_unelevated_process_with_the_user_default"
T_OLH_PODMAN = _T_OLH + "test_podman_and_docker_desktop_installs_are_what_they_were"
T_MIRR_INSTALL = _T_OLH + "test_a_mirrored_wsl_install_warns_and_writes_no_router_address"
# apply.ps1's wsl step (Invoke-ApplyOllamaHost, run by Invoke-Apply), one test per suite.
APPLY_PS1 = "deploy/installer/apply.ps1"
T_OLH_APPLY_SET = _T_OLH + "test_an_unelevated_apply_on_wsl_sets_the_users_variable_and_rebinds_the_app"
T_OLH_APPLY_ELEV = _T_OLH + "test_an_elevated_apply_on_wsl_sets_nothing_and_says_what_to_do"
T_OLH_APPLY_DRY = _T_OLH + "test_a_dry_apply_on_wsl_says_what_it_would_set_and_changes_nothing"
T_OLH_APPLY_OTHER = _T_OLH + "test_apply_on_any_other_runtime_says_and_sets_nothing_about_ollama"
T_OLH_APPLY_FAIL = _T_OLH + "test_a_failed_write_is_an_apply_failure_not_a_crash"
T_OLH_APPLY_WIRE = _T_OLH + "test_apply_ps1_hands_its_runtime_to_the_step"
# install.ps1's step 3: the wsl host call, and the Ollama check and start it must come before.
OLH_CALL = "    foreach ($l in @(Set-WslOllamaHost -Mode $RuntimeMode)) { Write-Log $l }\n"
OLH_UP = ("    function Test-OllamaUp { try { Invoke-WebRequest 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 "
          "-UseBasicParsing | Out-Null; return $true } catch { return $false } }\n"
          "    if (-not (Test-OllamaUp)) {\n"
          "      $app = Join-Path $env:LOCALAPPDATA 'Programs\\Ollama\\ollama app.exe'\n"
          "      if (Test-Path $app) { Write-Log 'starting the Ollama app...'; Start-Process $app | Out-Null }\n"
          "      for ($i = 0; $i -lt 15 -and -not (Test-OllamaUp); $i++) { Start-Sleep 2 }\n"
          "    }\n"
          "    if (-not (Test-OllamaUp)) { throw 'Ollama is not serving on :11434 - start the Ollama app and re-run.' }\n")
# The rule every runtime pick used before Resolve-PlatformRuntime: the first CLI installed.
OLD_RULE = ("@{ Mode = $(if (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' } "
            "elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'desktop' } else { 'wsl' }); "
            "Reason = 'the old rule' }")

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- #22: lib-apply on a Podman box --------------------------------------------------------
    (
        "#22 the image step goes back to calling docker directly",
        APPLY,
        "        $null = & $runtimeCli -CliArgs @('image', 'inspect', $Image) -Mode $Mode\n",
        "        $null = & docker image inspect $Image 2>$null\n",
        "test_apply_selftest_routes_every_runtime_to_its_own_cli",
        T_APPLY,
    ),
    (
        "#22 compose up goes back to `docker compose`",
        APPLY,
        "        & $composeCli -Arguments ((& $composeBase) + $profileArgs + @('up', '-d')) -Mode $Mode -NoLog | Out-Host\n",
        "        & docker compose @(& $composeBase) @profileArgs up -d 2>&1 | Out-Host\n",
        "test_compose_up_goes_through_docker_compose_with_the_podman_pipe",
        T_APPLY,
    ),
    (
        "#22 the compose Test counts an error line as a running container",
        APPLY,
        "        $ok -and [bool](@($lines | Where-Object { $_ -match '^platform-' }).Count)",
        "        [bool](@($lines).Count)",
        "test_the_compose_test_counts_only_a_listing_that_succeeded",
        T_APPLY,
    ),
    (
        "#22 wsl gets Windows paths it cannot read",
        APPLY,
        "        if ($Mode -eq 'wsl') { $envArg = & $toWslPath $envPath; "
        "$fileList = @($fileList | ForEach-Object { & $toWslPath $_ }) }\n",
        "",
        "test_apply_selftest_routes_every_runtime_to_its_own_cli",
        T_APPLY,
    ),
    # --- every runtime pick goes through Resolve-PlatformRuntime -------------------------------
    (
        "the preflight runtime row goes back to 'podman if podman is on PATH'",
        PREFLIGHT,
        "    $pick = Resolve-PlatformRuntime\n    # A declaration contradicted",
        f"    $pick = {OLD_RULE}\n    # A declaration contradicted",
        "test_preflight_names_the_runtime_that_holds_the_platform",
        T_ADOPT,
    ),
    (
        "activate-model-roles goes back to 'podman if podman is on PATH'",
        ACTIVATE,
        "  $pick = Resolve-PlatformRuntime -EnvFile $envFile\n  $mode = $pick.Mode",
        f"  $pick = {OLD_RULE}\n  $mode = $pick.Mode",
        "test_role_activation_recreates_rails_on_the_runtime_that_holds_them",
        T_ADOPT,
    ),
    (
        "activate-model-roles ignores -RuntimeMode",
        ACTIVATE,
        "if (-not $mode) {\n  $pick = Resolve-PlatformRuntime -EnvFile $envFile",
        "if ($true) {\n  $pick = Resolve-PlatformRuntime -EnvFile $envFile",
        "test_role_activation_runtime_mode_overrides",
        T_ADOPT,
    ),
    (
        "the installer's prereq doctor goes back to 'podman if podman is installed'",
        INSTALL,
        "          else { Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\\.env') }\n",
        f"          else {{ {OLD_RULE} }}\n",
        "test_installer_prereqs_name_the_runtime_that_holds_the_platform",
        T_ADOPT,
    ),
    (
        "the installer's prereq doctor ignores -RuntimeMode",
        INSTALL,
        "  $pick = if ($RuntimeMode) { @{ Mode = $RuntimeMode;",
        "  $pick = if ($false) { @{ Mode = $RuntimeMode;",
        "test_installer_runtime_mode_still_overrides_the_prereq_doctor",
        T_ADOPT,
    ),
    # --- a declared runtime beats an inferred one -----------------------------------------------
    (
        "apply's env step stops declaring the runtime it drives",
        APPLY,
        "    $envText = Set-EnvTextValue -Text $envText -Key 'PLATFORM_RUNTIME' -Value $Mode\n",
        "",
        "test_the_env_step_declares_the_runtime_it_was_run_for",
        T_APPLY,
    ),
    (
        "the lean template pins PLATFORM_RUNTIME, so every re-run clobbers the box's line",
        "deploy/installer/env.lean.example",
        "# PLATFORM_RUNTIME=\n",
        "PLATFORM_RUNTIME=desktop\n",
        "test_the_template_never_clobbers_a_declared_runtime",
        T_APPLY,
    ),
    # --- deploy\.env writers read UTF-8 and settle -------------------------------------------
    (
        "activate-model-roles writes its block inline again (ANSI read on 5.1, header stacked)",
        ACTIVATE_PS,
        "  Set-EnvBlock -Path $envFile -Values $roleVars -Header $roleHeader\n  Write-Host \"wrote $($roleVars.Count) role vars to $envFile\"\n",
        "  $k = Get-Content $envFile | Where-Object { $line = $_; -not ($roleVars.Keys | Where-Object { $line -match \"^\\s*$([regex]::Escape($_))\\s*=\" }) }\n"
        "  [System.IO.File]::WriteAllLines($envFile, [string[]](@($k) + @($roleHeader) + ($roleVars.GetEnumerator() | ForEach-Object { \"$($_.Key)=$($_.Value)\" })), (New-Object System.Text.UTF8Encoding($false)))\n",
        "test_ten_role_activations_leave_deploy_env_byte_identical",
        T_ENVW,
    ),
    # --- one deploy\.env reader, on compose's dotenv rules --------------------------------------
    (
        "install-services reads the broker token with its own first-match Select-String",
        SERVICES,
        "$BrokerToken = ([string](Get-DotEnvValue -Path $EnvFile -Key 'BROKER_AUTH_TOKEN')).Trim()\n",
        r"$m = Select-String -Path $EnvFile -Pattern '^\s*BROKER_AUTH_TOKEN\s*=\s*(.+)$' | Select-Object -First 1; $BrokerToken = $m.Matches[0].Groups[1].Value.Trim()" "\n",
        "test_no_deploy_script_parses_env_lines_itself",
        T_DOTENV,
    ),
    # --- a lean box's role map and disabled list are its own files, in deploy\local -------------
    (
        "LBF install.ps1 copies the lean map over the tracked roles.json again",
        INSTALL,
        "    Copy-Item (Join-Path $Installer 'roles.lean.json') $leanFiles.Roles -Force\n",
        "    Copy-Item (Join-Path $Installer 'roles.lean.json') (Join-Path $Root 'services\\broker\\roles.json') -Force\n",
        "test_install_writes_the_lean_map_to_the_box_file_and_leaves_the_tracked_one",
        T_LBF,
    ),
    (
        "LBF apply's roles step writes the tracked roles.json again",
        APPLY,
        "    $rolesPath = $brokerFiles.Roles\n",
        "    $rolesPath = $brokerFiles.TrackedRoles\n",
        "test_the_apply_roles_step_writes_the_box_file_over_what_the_broker_reads_now",
        T_LBF,
    ),
    (
        "LBF apply lays the plan over the tracked map even once the box file exists",
        APPLY,
        "    $rolesBase = if (Test-Path -LiteralPath $rolesPath) { $rolesPath } else { $brokerFiles.TrackedRoles }\n",
        "    $rolesBase = $brokerFiles.TrackedRoles\n",
        "test_the_apply_roles_step_writes_the_box_file_over_what_the_broker_reads_now",
        T_LBF,
    ),
    (
        "LBF the model plan's defaults are the tracked map on a lean box",
        MODELPLAN,
        "if (-not (Test-Path -LiteralPath $RolesJsonPath)) { $RolesJsonPath = Join-Path $__mp_repo 'services\\broker\\roles.json' }\n",
        "$RolesJsonPath = Join-Path $__mp_repo 'services\\broker\\roles.json'\n",
        "test_the_model_plan_defaults_come_from_the_box_file_when_it_exists",
        T_LBF,
    ),
    (
        "LBF get.ps1 resets a box's tracked broker edits again",
        GET_PS1,
        "  Assert-NoBrokerEdits -Git $git -Dir $Dir\n",
        "",
        "test_get_refuses_to_reset_tracked_broker_edits_and_says_how_to_keep_them",
        T_LBF,
    ),
    (
        "LBF get.ps1 sees only staged edits",
        GET_PS1,
        "diff --name-only HEAD -- services/broker",
        "diff --cached --name-only HEAD -- services/broker",
        "test_get_refuses_to_reset_tracked_broker_edits_and_says_how_to_keep_them",
        T_LBF,
    ),
    # --- the lean installer and install-services register the same broker on a lean box --------
    (
        "AGR install-native registers the lean spec alone again",
        NATIVE,
        "$ServiceEnv = @(Get-LeanBrokerServiceEnv -Spec $spec -Existing $Carried)\n",
        "$ServiceEnv = @($spec.Environment)\n",
        "test_a_lean_re_register_keeps_the_cli_settings_and_the_upstreams",
        T_AGREE,
    ),
    (
        "AGR install-services registers its literal 0.0.0.0 command line again",
        SERVICES,
        "Install-Svc 'platform-broker' $BrokerPy $BrokerPlan.AppParameters $PlatformRoot $BrokerEnv\n",
        "Install-Svc 'platform-broker' $BrokerPy '-m uvicorn app.main:app --app-dir services\\broker --host 0.0.0.0 --port 11500' $PlatformRoot $BrokerEnv\n",
        "test_install_services_registers_the_broker_the_plan_names",
        T_AGREE,
    ),
    (
        "AGR install-services never merges the lean overlay",
        SERVICES,
        "    $BrokerEnv = @(Merge-ServiceEnv -Base $BrokerEnv -Overlay $BrokerPlan.Overlay)\n",
        "",
        "test_install_services_registers_the_broker_the_plan_names",
        T_AGREE,
    ),
    (
        "AGR install-native's Invoke-Nssm restores an undefined $old again",
        NATIVE,
        "  try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $eap }\n",
        "  try { & $Nssm @args 2>&1 | Out-Null } finally { $ErrorActionPreference = $old }\n",
        "test_invoke_nssm_restores_the_preference_it_saved",
        T_AGREE,
    ),
    # --- install.ps1's children get their arguments whole, and the password off the command line --
    (
        "ARG the broker step passes its arguments as an unquoted array again",
        INSTALL,
        "-ArgumentList (Join-CommandLine -Arguments $nativeArgs)",
        "-ArgumentList $nativeArgs",
        "test_the_broker_step_gets_the_install_folder_whole",
        T_ARGS,
    ),
    (
        "ARG the GUI's child gets an unquoted array again",
        INSTALL,
        "  try { Start-Process powershell -WindowStyle Hidden -ArgumentList $line }\n",
        "  try { Start-Process powershell -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', "
        "'-File', $Script, '-Provision', '-AdminUser', $User, '-EnabledApps', $Apps, '-RuntimeMode', $Mode) }\n",
        "test_the_gui_child_gets_each_selection_whole",
        T_ARGS,
    ),
    (
        "ARG the GUI puts the password on the child's command line again",
        INSTALL,
        "'-AdminUser', $User, '-EnabledApps', $Apps,",
        "'-AdminUser', $User, '-AdminPass', $Pass, '-EnabledApps', $Apps,",
        "test_the_gui_childs_password_is_in_its_environment_not_its_command_line",
        T_ARGS,
    ),
    (
        "ARG the GUI keeps the password in its own environment after the start",
        INSTALL,
        "  finally { Remove-Item -LiteralPath \"Env:$ProvisionPassVar\" -ErrorAction SilentlyContinue }\n",
        "  finally { }\n",
        "test_the_gui_childs_password_is_in_its_environment_not_its_command_line",
        T_ARGS,
    ),
    (
        "ARG the provisioning child ignores the password in its environment",
        INSTALL,
        "    if (-not $PSBoundParameters.ContainsKey('AdminPass')) { $AdminPass = $envPass }\n",
        "",
        "test_the_password_arrives_through_the_environment_and_goes_no_further",
        T_ARGS,
    ),
    (
        "ARG the provisioning child leaves the password to everything it starts",
        INSTALL,
        "    Remove-Item -LiteralPath \"Env:$ProvisionPassVar\" -ErrorAction SilentlyContinue\n  }\n  Invoke-Provision",
        "  }\n  Invoke-Provision",
        "test_the_password_arrives_through_the_environment_and_goes_no_further",
        T_ARGS,
    ),
    # --- WDP: an elevated administrator's profile over another user's checkout (ops-e follow-up) --
    (
        "SMALL install.ps1 writes CO_WORKER_INBOX_WIN again",
        INSTALL,
        "    Set-EnvValue -Path $envFile -Key 'CO_WORKER_INBOX_MOUNT' -Value $inboxMount\n",
        "    Set-EnvValue -Path $envFile -Key 'CO_WORKER_INBOX_WIN'   -Value $inboxWin\n"
        "    Set-EnvValue -Path $envFile -Key 'CO_WORKER_INBOX_MOUNT' -Value $inboxMount\n",
        "test_every_key_the_installer_writes_into_env_is_read_by_something",
        T_SMALL,
    ),
    (
        "SMALL INSTRUCTIONS.md says XTTS has no cache override again",
        "deploy/INSTRUCTIONS.md",
        "(`OLLAMA_MODELS`, `HF_HOME`), and Coqui XTTS",
        "(`OLLAMA_MODELS`, `HF_HOME`). One caveat: Coqui XTTS has no cache env override, and Coqui XTTS",
        "test_no_one_says_xtts_re_downloads_under_the_service",
        T_SMALL,
    ),
    (
        "LBF deploy\\local is not git-ignored",
        DEPLOY_IGNORE,
        "\nlocal/\n",
        "\n",
        "test_deploy_local_is_ignored_where_the_snapshot_ships_it",
        T_LBF,
    ),
    # --- OLH / MIRR: a wsl box's containers reach the Ollama app; mirrored networking is said ------
    (
        "OLH podman and Docker Desktop installs set OLLAMA_HOST",
        INSTALL,
        "    foreach ($l in @(Set-WslOllamaHost -Mode $RuntimeMode)) { Write-Log $l }\n",
        "    foreach ($l in @(Set-WslOllamaHost -Mode wsl)) { Write-Log $l }\n",
        "test_podman_and_docker_desktop_installs_are_what_they_were",
        T_OLH_PODMAN,
    ),
    (
        "OLH install.ps1 never sets the host",
        INSTALL,
        "    foreach ($l in @(Set-WslOllamaHost -Mode $RuntimeMode)) { Write-Log $l }\n",
        "",
        "test_a_wsl_install_sets_ollama_host_before_the_app_starts",
        T_OLH_INSTALL,
    ),
    (
        "OLH install.ps1 sets the host after Ollama is up, so the app it started binds loopback",
        INSTALL,
        OLH_CALL + OLH_UP,
        OLH_UP + OLH_CALL,
        "test_a_wsl_install_sets_ollama_host_before_the_app_starts",
        T_OLH_INSTALL,
    ),
    (
        "OLH install.ps1 sets the host after Ollama is up (its placement check)",
        INSTALL,
        OLH_CALL + OLH_UP,
        OLH_UP + OLH_CALL,
        "test_install_ps1_sets_the_host_in_its_own_unelevated_process_with_the_user_default",
        T_OLH_INSTALL_LATE,
    ),
    (
        "OLH apply never sets the host",
        APPLY,
        "        foreach ($l in @(Invoke-ApplyOllamaHost -Mode $Mode -Execute:$Execute -Elevated $elevated)) { Write-Host $l }\n",
        "",
        "test_an_unelevated_apply_on_wsl_sets_the_users_variable_and_rebinds_the_app",
        T_OLH_APPLY_SET,
    ),
    (
        "OLH an elevated apply sets the variable, maybe in another account's environment",
        APPLY,
        "    if ($Elevated) {\n",
        "    if ($false) {\n",
        "test_an_elevated_apply_on_wsl_sets_nothing_and_says_what_to_do",
        T_OLH_APPLY_ELEV,
    ),
    (
        "OLH a dry apply sets the variable",
        APPLY,
        "    if (-not $Execute) {\n        if ([string](& $Store.Get) -eq $want)",
        "    if ($false) {\n        if ([string](& $Store.Get) -eq $want)",
        "test_a_dry_apply_on_wsl_says_what_it_would_set_and_changes_nothing",
        T_OLH_APPLY_DRY,
    ),
    (
        "OLH a dry apply announces a variable already set as still to set",
        APPLY,
        "        if ([string](& $Store.Get) -eq $want) {",
        "        if ($false) {",
        "test_a_dry_apply_on_wsl_says_what_it_would_set_and_changes_nothing",
        T_OLH_APPLY_DRY,
    ),
    (
        "OLH apply's step runs on podman and Docker Desktop too",
        APPLY,
        "    $want = Get-OllamaHostForMode -Mode $Mode\n    if (-not $want) { return @() }\n    if ($Elevated) {\n",
        "    $want = Get-OllamaHostForMode -Mode wsl\n    if ($Elevated) {\n",
        "test_apply_on_any_other_runtime_says_and_sets_nothing_about_ollama",
        T_OLH_APPLY_OTHER,
    ),
    (
        "OLH a failed write is swallowed, so apply reports success",
        APPLY,
        "    catch { Write-Host \"        error: setting OLLAMA_HOST: $_\"; $failed++ }\n",
        "    catch { }\n",
        "test_a_failed_write_is_an_apply_failure_not_a_crash",
        T_OLH_APPLY_FAIL,
    ),
    (
        "OLH apply.ps1 hands the step a fixed runtime, not the one it resolved",
        APPLY_PS1,
        "$rc = Invoke-Apply -Actions $actions -Execute:$Execute -Mode $RuntimeMode\n",
        "$rc = Invoke-Apply -Actions $actions -Execute:$Execute -Mode wsl\n",
        "test_apply_ps1_hands_its_runtime_to_the_step",
        T_OLH_APPLY_WIRE,
    ),
    (
        "MIRR install.ps1 logs no mirrored-mode warning",
        INSTALL,
        "        if ($wslNet.Warning) { Write-Log \"WARNING: $($wslNet.Warning)\" }\n",
        "",
        "test_a_mirrored_wsl_install_warns_and_writes_no_router_address",
        T_MIRR_INSTALL,
    ),
    (
        "activate-model-roles' go/no-go sends no token again",
        ACTIVATE_PS,
        "-TimeoutSec 30 -Headers $brokerHeaders `\n",
        "-TimeoutSec 30 `\n",
        "test_every_host_side_call_to_a_broker_route_sends_headers",
        T_LEAN,
    ),
    # --- the apply engine's services step is the lean one; no impossible media-venv step ---------
    (
        "the services step is done whenever a service of that name exists",
        APPLY,
        "        & $serviceCurrent -Spec $brokerSpec\n",
        "        [bool](Get-Service platform-broker -ErrorAction SilentlyContinue)\n",
        "test_the_env_step_writes_a_token_the_services_check_expects",
        T_LEAN,
    ),
    (
        "the services step runs the full stack's install-services.ps1 again",
        APPLY,
        r"        $script = Join-Path $Root 'deploy\installer\install-native.ps1'" "\n",
        r"        $script = Join-Path $Root 'deploy\install-services.ps1'" "\n",
        "test_the_services_step_runs_the_lean_installer_script_with_the_runtimes_bind",
        T_LEAN,
    ),
    (
        "a media-venv step comes back for a plan with an image model",
        APPLY,
        "    $serviceCurrent = ${function:Test-BrokerServiceCurrent}\n",
        "    if ($Plan.needs_media_venv) { $actions += New-ApplyAction -Id 'media-venv' -Label 'media venv' -Test { $false } -Do { } }\n"
        "    $serviceCurrent = ${function:Test-BrokerServiceCurrent}\n",
        "test_there_is_no_media_venv_step_even_when_the_plan_wants_one",
        T_LEAN,
    ),
    (
        "the env step writes no token when the box has none",
        APPLY,
        "    if (-not $brokerToken) {\n        $brokerToken = New-BrokerToken\n",
        "    if ($false) {\n        $brokerToken = New-BrokerToken\n",
        "test_the_env_step_writes_a_token_the_services_check_expects",
        T_LEAN,
    ),
    # --- NSSM logs rotate online and are pruned -------------------------------------------------
    (
        # Re-anchored 2026-10-14: the loop keeps the hook's exit code now.
        "install-native sets AppRotateFiles by hand again, no hook",
        NATIVE,
        """    if (Test-NssmHookSetting $a) { $hookExit = $code }\n  }\n""",
        """    if (Test-NssmHookSetting $a) { $hookExit = $code }\n  }\n  Invoke-Nssm set $Name AppRotateFiles 1 | Out-Null\n""",
        "test_no_installer_sets_service_log_options_of_its_own",
        T_LOGS,
    ),
    # --- PRUNE / HOOK: NSSM 2.24 has no rotate hook, so the watchdog prunes and the installers say so
    (
        "HOOK install-services never says the hook is missing",
        SERVICES,
        "    if ($hookWarning) { Write-Warning $hookWarning }\n",
        "",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    (
        "HOOK install-native never says the hook is missing",
        NATIVE,
        "  if ($hookWarning) { Write-Warning $hookWarning }\n",
        "",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    (
        "HOOK install-services drops the hook's exit code into Out-Null again",
        SERVICES,
        "        $code = Invoke-Nssm @a\n        if (Test-NssmHookSetting $a) { $hookExit = $code }\n",
        "        Invoke-Nssm @a | Out-Null\n",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    (
        "HOOK install-services tells a box with no watchdog that the watchdog prunes",
        SERVICES,
        "    $watchdog = [bool](Get-Service -Name 'platform-watchdog' -ErrorAction SilentlyContinue)\n",
        "    $watchdog = $true\n",
        "test_an_installer_warns_when_nssm_kept_no_hook",
        T_HOOK_WARN,
    ),
    # --- the installer doctor requires the broker's Python ---------------------------------------
    (
        "PY311 the doctor accepts Python 3.10 again",
        INSTALL,
        "    if ($v -match '\\d+\\.\\d+') { $pyDetail = $v.Trim(); $pyOk = Test-PythonVersionText -Text $v -Minimum $pyMin }\n",
        "    if ($v -match '\\d+\\.\\d+') { $pyDetail = $v.Trim(); $pyOk = Test-PythonVersionText -Text $v -Minimum ([version]'3.10') }\n",
        "test_the_installer_doctor_requires_the_brokers_python",
        T_ADOPT,
    ),
    # --- recorded items (engineering-record 2026-09-10/11) in these scripts -----------------------
    (
        "REC the apply engine writes the plan's roles ALONE again (the box's generic roles dropped)",
        APPLY,
        "    foreach ($k in @($cur.Keys)) { $out[$k] = $cur[$k] }\n",
        "",
        "test_the_written_map_keeps_every_role_the_box_had",
        T_ROLES,
    ),
    (
        "REC the roles step's Test ignores case again",
        APPLY,
        "        (Test-Path $rolesPath) -and ((Get-Content -Raw -Encoding UTF8 -Path $rolesPath).TrimEnd() -ceq $rolesText.TrimEnd())\n",
        "        (Test-Path $rolesPath) -and ((Get-Content -Raw -Encoding UTF8 -Path $rolesPath).TrimEnd() -eq $rolesText.TrimEnd())\n",
        "test_the_roles_check_is_case_sensitive_like_the_broker",
        T_ROLES,
    ),
    (
        "REC Get-RoleDefaults takes annotation and Title-case keys as roles again",
        MODELPLAN,
        "        if ($p.Name -cnotmatch '^[a-z0-9][a-z0-9-]*$') { continue }\n",
        "",
        "test_role_defaults_are_read_as_the_broker_reads_them",
        T_ROLES,
    ),
    (
        "REC the image step pulls a tag that is in no registry again",
        APPLY,
        "        & $composeCli -Arguments ((& $composeBase) + @('build', 'gateway')) -Mode $Mode -NoLog | Out-Host\n",
        "        & $runtimeCli -CliArgs @('pull', $Image) -Mode $Mode | ForEach-Object { [string]$_ } | Out-Host\n",
        "test_the_image_step_asks_podman_and_reads_its_exit_code",
        T_APPLY,
    ),
    (
        "REC activate-model-roles goes back to a hand list that misses rails",
        ACTIVATE_PS,
        "$roleVars = $composeRoles.Vars\n",
        "$roleVars = [ordered]@{ 'EDU_LLM_MODEL' = '@edu'; 'IEP_LLM_MODEL' = '@iep' }\n",
        "test_the_role_block_is_every_role_variable_the_stack_declares",
        T_ENVW,
    ),
    # --- env-robust 1: temp cleanup under an 8.3 short TEMP --------------------------------------
    (
        "ENV1 apply's self-test builds its temp dir from $env:TEMP as given again",
        "deploy/installer/apply.ps1",
        "    $tmp = Join-Path ([IO.Path]::GetFullPath($env:TEMP)) (\"ai-apply-selftest-\"",
        "    $tmp = Join-Path $env:TEMP (\"ai-apply-selftest-\"",
        "test_apply_selftest_routes_every_runtime_to_its_own_cli[powershell.exe-short-temp]",
        T_APPLY,
    ),
    (
        # Re-anchored 2026-10-14 when the probe got a name of its run's own. Same mutation: the
        # probe's folder is $env:TEMP as given, not its long form.
        "ENV1 provision-speech-venv writes its probe under $env:TEMP as given again",
        "deploy/provision-speech-venv.ps1",
        "$tmp = Join-Path ([IO.Path]::GetFullPath($env:TEMP)) ('speech_venv_verify-",
        "$tmp = Join-Path $env:TEMP ('speech_venv_verify-",
        "test_verify_only_reaches_its_verdict_and_removes_its_probe[powershell.exe-short-temp]",
        "tools/tests/test_speech_venv_verify.py",
    ),
    (
        "SPV the speech probe has one fixed name for every run again",
        "deploy/provision-speech-venv.ps1",
        "('speech_venv_verify-{0}-{1}.py' -f $PID, [guid]::NewGuid().ToString('N').Substring(0, 12))",
        "'speech_venv_verify.py'",
        "test_a_run_never_touches_another_runs_probe",
        "tools/tests/test_speech_venv_verify.py",
    ),
    (
        "SPV the speech probe is never removed",
        "deploy/provision-speech-venv.ps1",
        "finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }\n",
        "finally { }\n",
        "test_verify_only_reaches_its_verdict_and_removes_its_probe",
        "tools/tests/test_speech_venv_verify.py",
    ),
    # --- env-robust 4: a ~/.docker/config.json that is a directory ---------------------------
    (
        "ENV4 install.ps1 never repairs a config.json directory (its host-address probe runs podman)",
        INSTALL,
        "    $null = Repair-DockerConfigEnv\n",
        "",
        "test_install_probes_the_host_address_with_a_readable_docker_config",
        "tools/tests/test_docker_config_env.py",
    ),
    # --- T1-e: install-services -BrokerCliEnvFile, and no blank in any service environment --------
    (
        "T1e Install-Svc hands NSSM a service's list unfiltered (a trailing blank ends it)",
        SERVICES,
        "    $EnvExtra = @(Merge-ServiceEnv -Base $EnvExtra)\n",
        "",
        "test_install_svc_never_hands_nssm_a_blank_element",
        T_SVCENV,
    ),
    (
        "T1e install-services applies the env file without checking its paths",
        SERVICES,
        "    $CliProblems = @(Test-BrokerCliEnv -Entries $CliFileEnv -RepoRoot $PlatformRoot)\n",
        "    $CliProblems = @()\n",
        "test_the_env_file_is_read_and_checked_before_anything_is_torn_down",
        T_SVCENV,
    ),
    # --- the lean broker venv is installed from the broker's pyproject (added 2026-10-14) -------
    (
        "LEANDEPS install-native runs pip with a list of its own beside the install set, which "
        "the dry run never reports",
        NATIVE,
        "      & $BrokerPy -m pip @a\n",
        "      & $BrokerPy -m pip @a\n"
        "      & $BrokerPy -m pip install --quiet \"python-multipart>=0.0.9\"\n",
        "test_every_pip_run_in_the_lean_installer_comes_from_the_broker_install_set",
        T_LEANDEPS,
    ),
    # --- CLIM: a BROKER_CLI_* value that is a comment, or a model the list does not allow --------
    (
        "CLIM the broker README's example puts the comment back on the model's own line",
        "services/broker/README.md",
        "# An empty value REMOVES the setting from the service. A comment goes on a line of its own:\n"
        "# after `KEY=` it would be the value, and BROKER_CLI_MODEL's value is passed as `--model`.\n"
        "BROKER_CLI_MODEL=\n",
        "BROKER_CLI_MODEL=                # an empty value REMOVES the setting from the service\n",
        "test_the_readme_example_removes_the_model_instead_of_setting_it_to_a_comment",
        T_SVCENV,
    ),
    # --- SMK / INSTSVC: a lean box without Recipe Book; install-services without its rails --------
    (
        "INSTSVC course-builder's pip exit code is ignored again",
        SERVICES,
        "    if ($LASTEXITCODE -ne 0) {\n        Write-Warning \"pip could not install course-builder",
        "    if ($false) {\n        Write-Warning \"pip could not install course-builder",
        "test_every_pip_install_in_install_services_is_judged_by_its_exit_code",
        T_SVCENV,
    ),
    # --- PWQ: the admin's name and password go into deploy\.env quoted (delta audit 2026-10-15) ----
    (
        "PWQ install.ps1 fills the template bare again",
        INSTALL,
        "    $tmpl = Expand-LeanEnvTemplate -Text $tmpl -AdminUser $AdminUser -AdminPassword $AdminPass -EnabledApps $EnabledApps\n",
        "    $tmpl = $tmpl.Replace('{{ADMIN_USER}}', $AdminUser).Replace('{{ADMIN_PASSWORD}}', $AdminPass)"
        ".Replace('{{ENABLED_APPS}}', $EnabledApps)\n",
        "test_the_password_arrives_through_the_environment_and_goes_no_further",
        T_ARGS,
    ),
    (
        "PWQ lib-apply's env step fills the template bare again",
        APPLY,
        "    Expand-LeanEnvTemplate -Text $text -AdminUser $AdminUser -AdminPassword $AdminPassword -EnabledApps ($Rails -join ',')\n",
        "    $text.Replace('{{ADMIN_USER}}', $AdminUser).Replace('{{ADMIN_PASSWORD}}', $AdminPassword)"
        ".Replace('{{ENABLED_APPS}}', ($Rails -join ','))\n",
        "test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed",
        T_ARGS,
    ),
    # --- PWB: a blank admin password is apply's default, and the gateway generates one (ops-e) -----
    (
        "PWB Get-InstallerEnvText refuses a blank password again",
        APPLY,
        "        [Parameter(Mandatory)][AllowEmptyString()][string]$AdminPassword,\n        [string]$Template\n",
        "        [Parameter(Mandatory)][string]$AdminPassword,\n        [string]$Template\n",
        "test_apply_with_no_password_plans_every_step",
        T_APPLY,
    ),
    (
        "PWB Write-InstallerEnv refuses a blank password again",
        APPLY,
        "[AllowEmptyString()][string]$AdminPassword,   # blank: the gateway generates one\n"
        "        [Parameter(Mandatory)][string]$Path,\n",
        "[string]$AdminPassword,\n        [Parameter(Mandatory)][string]$Path,\n",
        "test_a_blank_password_reaches_the_gateway_blank_so_it_generates_one",
        T_APPLY,
    ),
    (
        "PWB Get-ApplyActions refuses a blank password again",
        APPLY,
        "[AllowEmptyString()][string]$AdminPassword,   # blank: the gateway generates one\n"
        "        # The container runtime",
        "[string]$AdminPassword,\n        # The container runtime",
        "test_apply_with_no_password_plans_every_step",
        T_APPLY,
    ),
    # --- LMAP: install-services on a lean box whose deploy\local maps were wiped (delta audit) ---
    (
        "LMAP a lean box carries its deploy\\local maps as flags again",
        SERVICES,
        "    if ($BrokerPlan.Stack -eq 'lean') { $carry = @(, @('UpstreamsFile', 'BROKER_UPSTREAMS_FILE')) }\n",
        "",
        "test_a_lean_box_with_its_deploy_local_maps_wiped_reregisters_and_seeds_them",
        T_ISR,
    ),
    (
        "LMAP a lean box's missing maps are not seeded before the broker reads them",
        SERVICES,
        "    if ($BrokerPlan.Stack -eq 'lean') {\n        foreach ($s in @(Initialize-LeanBrokerFiles -Root $PlatformRoot)) {\n",
        "    if ($false) {\n        foreach ($s in @(Initialize-LeanBrokerFiles -Root $PlatformRoot)) {\n",
        "test_a_lean_box_with_its_deploy_local_maps_wiped_reregisters_and_seeds_them",
        T_ISR,
    ),
    (
        "LMAP the seed is not said to be the tracked map",
        SERVICES,
        "            Write-Host \"  box-local broker file was missing, seeded from the tracked map (this box's own edits are not in it; "
        "a backup's deploy\\local copy has them): $s\" -ForegroundColor Yellow\n",
        "            Write-Host \"  box-local broker file: $s\"\n",
        "test_a_lean_box_with_its_deploy_local_maps_wiped_reregisters_and_seeds_them",
        T_ISR,
    ),
    (
        "LMAP the full stack stops carrying its own role map",
        SERVICES,
        "    $carry = @(@('RolesFile', 'BROKER_ROLES_FILE'), @('UpstreamsFile', 'BROKER_UPSTREAMS_FILE'),\n"
        "               @('DisabledFile', 'BROKER_DISABLED_FILE'))\n",
        "    $carry = @(, @('UpstreamsFile', 'BROKER_UPSTREAMS_FILE'))\n",
        "test_the_full_stack_still_carries_and_checks_its_own_map",
        T_ISR,
    ),
    # --- FFM: the broker service is given the ffmpeg its installing shell finds (AI_VOICE_FFMPEG) ---
    (
        "FFM install-services gives the broker no AI_VOICE_FFMPEG",
        SERVICES,
        "        $BrokerEnv += \"AI_VOICE_FFMPEG=$($Ffmpeg.Path)\"\n",
        "",
        "test_the_broker_is_given_the_ffmpeg_its_installing_shell_finds",
        T_ISR,
    ),
    (
        "FFM no ffmpeg found leaves the variable unset in silence",
        SERVICES,
        "        Write-Host (\"  voice engines' ffmpeg: $($Ffmpeg.Note), so AI_VOICE_FFMPEG is left unset and RVC voices \" +\n"
        "                    'need ffmpeg on the MACHINE PATH') -ForegroundColor Yellow\n",
        "",
        "test_no_ffmpeg_in_the_installing_shell_sets_nothing_and_says_so",
        T_ISR,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        "CONTROL ENV1 apply expands TEMP through Get-Item instead of GetFullPath",
        "deploy/installer/apply.ps1",
        "    $tmp = Join-Path ([IO.Path]::GetFullPath($env:TEMP)) (\"ai-apply-selftest-\"",
        "    $tmp = Join-Path (Get-Item -LiteralPath $env:TEMP).FullName (\"ai-apply-selftest-\"",
        "test_apply_selftest_routes_every_runtime_to_its_own_cli",
        T_APPLY,
    ),
    (
        "CONTROL OLH apply indents Set-WslOllamaHost's lines in a pipeline, not a loop",
        APPLY,
        "    foreach ($l in @(Set-WslOllamaHost -Mode $Mode -Store $Store)) { $lines += \"        $l\" }\n",
        "    $lines += @(Set-WslOllamaHost -Mode $Mode -Store $Store | ForEach-Object { \"        $_\" })\n",
        "test_an_unelevated_apply_on_wsl_sets_the_users_variable_and_rebinds_the_app",
        T_OLH_APPLY_SET,
    ),
    (
        "CONTROL LMAP the installed broker's environment read inline from the registry, as before",
        SERVICES,
        "    $ExistingBrokerEnv = @(Get-InstalledBrokerEnv)\n",
        "    $ExistingBrokerEnv = @((Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Services\\platform-broker\\Parameters' "
        "-ErrorAction SilentlyContinue).AppEnvironmentExtra)\n",
        "test_a_lean_box_with_its_deploy_local_maps_wiped_reregisters_and_seeds_them",
        T_ISR,
    ),
    (
        "CONTROL PWB the blank allowed with the attributes in the other order",
        APPLY,
        "        [Parameter(Mandatory)][AllowEmptyString()][string]$AdminPassword,   # blank: the gateway generates one\n"
        "        # The container runtime",
        "        [AllowEmptyString()][Parameter(Mandatory)][string]$AdminPassword,   # blank: the gateway generates one\n"
        "        # The container runtime",
        "test_apply_with_no_password_plans_every_step",
        T_APPLY,
    ),
]
