"""mutate_ops_scripts.py's cases for the startup scripts and the watchdog. NOT a pytest file; never run alone.

The cases for what runs at logon and after it: the startup scripts, the disabled-rail sync, the
watchdog and its registrar. Each mutates one of: deploy/installer/WATCHDOG.md,
deploy/installer/platform-startup.ps1, deploy/installer/platform-startup.sh,
deploy/installer/platform-watchdog.ps1, deploy/installer/register-watchdog.ps1,
deploy/installer/sync-disabled-rails.ps1.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

STARTUP = "deploy/installer/platform-startup.ps1"
T_ADOPT = "tools/tests/test_runtime_adoption.py"
T_DISABLED = "tools/tests/test_disabled_rails.py"
T_DOTENV = "tools/tests/test_dotenv_ps_reader.py"
T_LOCK = "tools/tests/test_startup_lock.py"
T_DISABLED_STARTUP = ("tools/tests/test_disabled_rails.py::"
                      "test_platform_startup_parks_after_compose_up_and_brings_up_only_enabled_profiles")
STARTUP_SH = "deploy/installer/platform-startup.sh"
T_STARTUP_SH = "tools/tests/test_platform_startup_sh.py"
T_PSKW = "tools/tests/test_ps_statement_as_command.py"
WATCHDOG = "deploy/installer/platform-watchdog.ps1"
T_CPP = "tools/tests/test_control_plane_probe.py"
# test_watchdog_shutdown.py, one test per suite: the decision's tests run its fixture only, the
# watchdog's the five scratch watchdog runs.
_T_SHUT = "tools/tests/test_watchdog_shutdown.py::"
T_SHUT_COLD = _T_SHUT + "test_a_cold_start_during_a_shutdown_starts_nothing_and_says_so"
T_SHUT_HEALTHY = _T_SHUT + "test_a_healthy_cycle_never_reads_the_event_log"
T_SHUT_DEAD = _T_SHUT + "test_a_dead_transport_during_a_shutdown_is_not_force_stopped"
T_SMALL = "tools/tests/test_ops_small_defects.py"
REGWD = "deploy/installer/register-watchdog.ps1"
T_REGX = "tools/tests/test_register_watchdog_exit.py"
# The registrar's doc checks, one test per suite: they read text, so none of that file's scratch runs.
T_REGX_GUI = T_REGX + "::test_no_doc_runs_the_gui_launcher_and_every_runas_line_is_the_console_form"
T_REGX_LEAD = T_REGX + "::test_each_procedure_leads_with_an_elevated_powershell_and_calls_runas_optional"
T_PRUNE_NAMED = "tools/tests/test_log_prune_fallback.py::test_what_the_warning_names_as_pruning_does_prune_deploy_logs"
# test_log_prune_fallback.py, one test per suite: each runs only the fixture its test needs.
_T_PRUNE = "tools/tests/test_log_prune_fallback.py::"
T_PRUNE_LOOP = _T_PRUNE + "test_the_watchdog_prunes_rotated_logs_on_its_first_cycle_and_not_again_that_day"
T_PRUNE_FAIL = _T_PRUNE + "test_a_failed_prune_is_logged_and_the_loop_goes_on"
T_HOOK_REG = _T_PRUNE + "test_register_watchdog_logs_and_warns_when_nssm_kept_no_hook"
T_MIRR_SH = T_STARTUP_SH + "::test_mirrored_networking_leaves_windows_host_alone_and_says_why"
# The rule every runtime pick used before Resolve-PlatformRuntime: the first CLI installed.
OLD_RULE = ("@{ Mode = $(if (Get-Command podman -ErrorAction SilentlyContinue) { 'podman' } "
            "elseif (Get-Command docker -ErrorAction SilentlyContinue) { 'desktop' } else { 'wsl' }); "
            "Reason = 'the old rule' }")

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- every runtime pick goes through Resolve-PlatformRuntime -------------------------------
    (
        "platform-startup goes back to 'podman if podman is on PATH'",
        STARTUP,
        "  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\\.env')\n  # This script drives podman",
        f"  $pick = {OLD_RULE}\n  # This script drives podman",
        "test_platform_startup_drives_the_runtime_that_holds_the_platform",
        T_ADOPT,
    ),
    (
        "platform-startup ignores -RuntimeMode",
        STARTUP,
        "if (-not $RuntimeMode) {\n  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\\.env')\n  # This",
        "if ($true) {\n  $pick = Resolve-PlatformRuntime -EnvFile (Join-Path $Root 'deploy\\.env')\n  # This",
        "test_platform_startup_runtime_mode_still_overrides",
        T_ADOPT,
    ),
    # --- a declared runtime beats an inferred one -----------------------------------------------
    (
        "platform-startup -ShowRuntime hides the contradiction",
        STARTUP,
        '  if ($RuntimeWarning) { Write-Host "WARNING: $RuntimeWarning" }\n',
        "",
        "test_platform_startup_follows_the_declaration_and_warns",
        T_ADOPT,
    ),
    # --- a rail disabled on this box is not running on this box --------------------------------
    (
        "platform-startup parks before compose up, which then starts the container again",
        STARTUP,
        "Invoke-Compose -Arguments ($cargs + @('up', '-d')) -Mode $RuntimeMode\n"
        "if ($LASTEXITCODE -ne 0) { Write-Log \"compose up failed (exit $LASTEXITCODE)\"; Stop-Startup 1 }\n",
        "",
        "test_platform_startup_parks_after_compose_up_and_brings_up_only_enabled_profiles",
        T_DISABLED_STARTUP,
    ),
    (
        "platform-startup brings the disabled rails' profiles up",
        STARTUP,
        "$cargs += Get-ComposeProfiles -Apps $upApps",
        "$cargs += Get-ComposeProfiles -Apps $apps",
        "test_platform_startup_parks_after_compose_up_and_brings_up_only_enabled_profiles",
        T_DISABLED,
    ),
    (
        "sync-disabled-rails.ps1 -DryRun acts anyway",
        "deploy/installer/sync-disabled-rails.ps1",
        "-Mode $RuntimeMode -DryRun:$DryRun",
        "-Mode $RuntimeMode",
        "test_the_script_dry_run_names_the_disabled_rail_and_changes_nothing",
        T_DISABLED,
    ),
    (
        "sync-disabled-rails.ps1 exits 0 when an action failed",
        "deploy/installer/sync-disabled-rails.ps1",
        "if ($res.Failed -gt 0) {",
        "if ($false) {",
        "test_the_script_parks_the_rail_and_reports_a_failure_in_its_exit_code",
        T_DISABLED,
    ),
    # --- one deploy\.env reader, on compose's dotenv rules --------------------------------------
    (
        "platform-startup parses PLATFORM_ENABLED_APPS itself again",
        STARTUP,
        "$apps = ([string](Get-DotEnvValue -Path $envFile -Key 'PLATFORM_ENABLED_APPS')).Trim()\n",
        r"foreach ($l in Get-Content $envFile) { if ($l -match '^\s*PLATFORM_ENABLED_APPS\s*=\s*(.*)$') { $apps = $Matches[1].Trim() } }" "\n",
        "test_no_deploy_script_parses_env_lines_itself",
        T_DOTENV,
    ),
    # --- the small ops-tooling defects of the 2026-10-14 audit ---------------------------------
    (
        "SMALL platform-startup never rotates startup.log",
        STARTUP,
        "$null = Step-ScriptLog -Path $LogFile\nWrite-Log \"=== AI-Platform startup",
        "Write-Log \"=== AI-Platform startup",
        "test_startup_rotates_its_log",
        T_SMALL,
    ),
    (
        "SMALL platform-startup composes the lean stack over a full-stack box",
        STARTUP,
        "if ($stackPick.Stack -eq 'full' -and $stackPick.Proven) {\n",
        "if ($false) {\n",
        "test_startup_does_nothing_on_a_full_stack_box",
        T_SMALL,
    ),
    # --- OLH / MIRR: a wsl box's containers reach the Ollama app; mirrored networking is said ------
    (
        "MIRR platform-startup.sh writes the router's address under mirrored networking",
        STARTUP_SH,
        "if [ \"$NET_MODE\" = \"mirrored\" ]; then\n    log \"WARNING: WSL runs in mirrored",
        "if false; then\n    log \"WARNING: WSL runs in mirrored",
        "test_mirrored_networking_leaves_windows_host_alone_and_says_why",
        T_MIRR_SH,
    ),
    (
        "MIRR platform-startup.sh ignores a loopback0 interface",
        STARTUP_SH,
        "if [ -z \"$NET_MODE\" ] && ip link show loopback0 >/dev/null 2>&1; then NET_MODE=mirrored; fi\n",
        "",
        "test_mirrored_networking_leaves_windows_host_alone_and_says_why",
        T_MIRR_SH,
    ),
    (
        "MIRR platform-startup.sh never asks wslinfo",
        STARTUP_SH,
        "if command -v wslinfo >/dev/null 2>&1; then\n",
        "if false; then\n",
        "test_mirrored_networking_leaves_windows_host_alone_and_says_why",
        T_MIRR_SH,
    ),
    # --- the WSL logon script reads deploy/.env as compose does, and honours the disabled list
    (
        "the FIRST assignment wins in the WSL startup's reader",
        STARTUP_SH,
        '        found="$val"; assigned=1\n',
        '        [ -n "$assigned" ] || found="$val"; assigned=1\n',
        "test_the_last_enabled_line_wins_and_a_disabled_rail_gets_no_profile",
        T_STARTUP_SH,
    ),
    (
        "an unquoted value keeps its trailing comment in the WSL startup's reader",
        STARTUP_SH,
        '            *)   val="${val%% #*}"; val="${val%"${val##*[![:space:]]}"}" ;;\n',
        '            *)   val="${val%"${val##*[![:space:]]}"}" ;;\n',
        "test_dotenv_get_gives_composes_answer",
        T_STARTUP_SH,
    ),
    (
        "the WSL startup brings a disabled rail's profile up",
        STARTUP_SH,
        r"""    if printf '%s\n' "$DISABLED" | grep -qx -- "$APP"; then continue; fi""" "\n",
        "",
        "test_the_last_enabled_line_wins_and_a_disabled_rail_gets_no_profile",
        T_STARTUP_SH,
    ),
    (
        "the WSL startup never parks the disabled rails",
        STARTUP_SH,
        'if [ -n "$SYNC_PS" ] && command -v powershell.exe >/dev/null 2>&1; then\n',
        "if false; then\n",
        "test_disabled_rails_are_parked_through_the_shared_powershell_step",
        T_STARTUP_SH,
    ),
    (
        "no interop skips the parking in silence",
        STARTUP_SH,
        '    log "WARNING: no Windows interop (powershell.exe / wslpath), so the disabled rails were NOT parked: ${DISABLED_SAID:-none}"\n',
        "    :\n",
        "test_without_interop_the_unparked_rails_are_named_not_skipped_in_silence",
        T_STARTUP_SH,
    ),
    # --- PRUNE / HOOK: NSSM 2.24 has no rotate hook, so the watchdog prunes and the installers say so
    (
        "PRUNE the watchdog never prunes the rotated logs",
        WATCHDOG,
        "            $prune = Invoke-LogPruneIfDue -LogDir $LogDir -Last $pruneLast -Now ([datetime]::UtcNow)\n",
        "            $prune = @{ Ran = $false; Removed = 0; Last = $pruneLast }\n",
        "test_the_watchdog_prunes_rotated_logs_on_its_first_cycle_and_not_again_that_day",
        T_PRUNE_LOOP,
    ),
    (
        "PRUNE the watchdog forgets its last prune, so it prunes every cycle",
        WATCHDOG,
        "            $pruneLast = $prune.Last\n",
        "",
        "test_the_watchdog_prunes_rotated_logs_on_its_first_cycle_and_not_again_that_day",
        T_PRUNE_LOOP,
    ),
    (
        "PRUNE a failed prune ends the watchdog's loop",
        WATCHDOG,
        "        catch {\n            $pruneLast = [datetime]::UtcNow\n"
        "            Write-Log \"log prune failed (next try in 24 h): $($_.Exception.Message)\"\n        }\n",
        "        catch { throw }\n",
        "test_a_failed_prune_is_logged_and_the_loop_goes_on",
        T_PRUNE_FAIL,
    ),
    # --- FPRUNE: a full-stack box (no watchdog) prunes when rebuild-stale runs, at most once a day --
    (
        "HOOK register-watchdog never reads the hook back",
        REGWD,
        "    $warning = Get-NssmHookWarning -Name $Name -HookExit $hookExit -WatchdogRegistered $true\n",
        "    $warning = ''\n",
        "test_register_watchdog_logs_and_warns_when_nssm_kept_no_hook",
        T_HOOK_REG,
    ),
    (
        "HOOK register-watchdog warns on screen but not in its log",
        REGWD,
        "        \"WARNING: $warning\" | Out-File $Out -Append -Encoding utf8\n",
        "",
        "test_register_watchdog_logs_and_warns_when_nssm_kept_no_hook",
        T_HOOK_REG,
    ),
    (
        "HOOK register-watchdog keeps no exit code for the hook",
        REGWD,
        "        if (Test-NssmHookSetting $a) { $hookExit = $LASTEXITCODE }\n",
        "",
        "test_register_watchdog_logs_and_warns_when_nssm_kept_no_hook",
        T_HOOK_REG,
    ),
    # --- recorded items (engineering-record 2026-09-10/11) in these scripts -----------------------
    (
        "REC a terminating error leaves the startup lock behind again",
        STARTUP,
        '''trap { Write-Log "startup failed: $_"; Exit-StartupLock; break }\n''',
        "",
        "test_a_terminating_error_releases_the_lock",
        T_LOCK,
    ),
    (
        "REC an early exit leaves the startup lock behind again",
        STARTUP,
        r'''if (-not (Test-Path $envFile)) { Write-Log "no deploy\.env - is the platform installed? aborting."; Stop-Startup 1 }''' "\n",
        r'''if (-not (Test-Path $envFile)) { Write-Log "no deploy\.env - is the platform installed? aborting."; exit 1 }''' "\n",
        "test_an_early_exit_releases_the_lock",
        T_LOCK,
    ),
    # --- env-robust 4: a ~/.docker/config.json that is a directory ---------------------------
    (
        "ENV4 platform-startup never repairs a config.json directory",
        STARTUP,
        "$null = Repair-DockerConfigEnv\n",
        "",
        "test_platform_startup_repairs_the_config_before_it_runs_a_container",
        "tools/tests/test_docker_config_env.py",
    ),
    # --- WDOG: the control-plane probe, and the watchdog's force-stop on its word (2026-10-14) -
    (
        "WDOG the VM is force-stopped on two missed fast probes, with no confirming one",
        WATCHDOG,
        "                $confirm = Get-ControlPlaneProbe -TimeoutMs ($ConfirmProbeSeconds * 1000)\n",
        "                $confirm = @{ Alive = $false; Detail = 'not asked' }\n",
        "test_a_podman_that_answers_in_15_s_is_not_dead",
        T_CPP,
    ),
    (
        "WDOG the confirming probe waits no longer than the fast one",
        WATCHDOG,
        "                $confirm = Get-ControlPlaneProbe -TimeoutMs ($ConfirmProbeSeconds * 1000)\n",
        "                $confirm = Get-ControlPlaneProbe -TimeoutMs ($ProbeSeconds * 1000)\n",
        "test_a_podman_that_answers_in_15_s_is_not_dead",
        T_CPP,
    ),
    (
        "WDOG the shipped confirming wait is the fast probe's 10 s",
        WATCHDOG,
        "    [int]$ConfirmProbeSeconds = 60\n",
        "    [int]$ConfirmProbeSeconds = 10\n",
        "test_a_podman_that_answers_in_15_s_is_not_dead",
        T_CPP,
    ),
    (
        "WDOG a missed probe is logged without its reason",
        WATCHDOG,
        'Write-Log "control-plane probe failed ($cpFailCount consecutive cycle(s)): $($cp.Detail)"',
        'Write-Log "control-plane probe failed ($cpFailCount consecutive cycle(s))"',
        "test_a_podman_that_answers_in_15_s_is_not_dead",
        T_CPP,
    ),
    (
        "WDOG a transport the confirming probe cannot reach either is never restarted",
        WATCHDOG,
        "                    $handoffDone = $false\n                    Invoke-ForceRestartMachine\n",
        "                    $handoffDone = $false\n",
        "test_a_podman_that_never_answers_is_force_stopped_after_the_confirming_probe",
        T_CPP,
    ),
    # --- SHUT: the watchdog starts nothing while Windows is shutting down or restarting ----------
    (
        "SHUT the watchdog starts the platform during a shutdown",
        WATCHDOG,
        "    if (Test-ShuttingDown $why) { return }\n",
        "",
        "test_a_cold_start_during_a_shutdown_starts_nothing_and_says_so",
        T_SHUT_COLD,
    ),
    (
        "SHUT a control-plane recovery force-stops the VM during a shutdown",
        WATCHDOG,
        "    if (Test-ShuttingDown 'control-plane recovery') { return }\n",
        "",
        "test_a_dead_transport_during_a_shutdown_is_not_force_stopped",
        T_SHUT_DEAD,
    ),
    (
        "SHUT a skipped start is not logged",
        WATCHDOG,
        "    if ($s.InProgress) { Write-Log \"$why - $($s.Detail); not starting anything\"; return $true }\n",
        "    if ($s.InProgress) { return $true }\n",
        "test_a_cold_start_during_a_shutdown_starts_nothing_and_says_so",
        T_SHUT_COLD,
    ),
    (
        "SHUT the event log is read every cycle",
        WATCHDOG,
        "        if (Test-ComposeBusy) { continue }\n\n",
        "        $null = Get-ShutdownState -Now (Get-Date)\n        if (Test-ComposeBusy) { continue }\n\n",
        "test_a_healthy_cycle_never_reads_the_event_log",
        T_SHUT_HEALTHY,
    ),
    # --- SDR: a parsed compose render is stdout only; LEANC: the lean stack's pass-throughs ----
    (
        "SDR sync-disabled-rails renders through Invoke-Compose again, so a stderr warning is in its JSON",
        "deploy/installer/sync-disabled-rails.ps1",
        "$render = Invoke-ComposeConfigJson -Arguments $cargs -Mode $RuntimeMode\n",
        "$render = @{ Code = 0; Json = (@(Invoke-Compose -Arguments ($cargs + @('config', '--format', 'json')) "
        "-Mode $RuntimeMode -NoLog) -join \"`n\"); Err = @() }\n",
        "test_a_compose_warning_on_stderr_does_not_stop_the_dry_run",
        T_DISABLED,
    ),
    (
        "SDR sync-disabled-rails never repairs a config.json directory",
        "deploy/installer/sync-disabled-rails.ps1",
        "$dockerConfig = Repair-DockerConfigEnv\n",
        "$dockerConfig = $null\n",
        "test_the_script_runs_its_clis_with_a_readable_docker_config",
        T_DISABLED,
    ),
    (
        "SDR platform-startup renders the parking map through Invoke-Compose again",
        STARTUP,
        "$render = Invoke-ComposeConfigJson -Arguments $mapArgs -Mode $RuntimeMode\n",
        "$render = @{ Code = 0; Json = (@(Invoke-Compose -Arguments ($mapArgs + @('config', '--format', 'json')) "
        "-Mode $RuntimeMode -NoLog) -join \"`n\"); Err = @() }\n",
        "test_no_script_parses_a_render_that_carries_compose_stderr",
        T_DISABLED,
    ),
    # --- REGX: register-watchdog's refusals reach the console and the exit code (delta audit) -----
    (
        "REGX the profile refusal writes its log and returns again (exit 0, a silent console)",
        REGWD,
        "    if ($prof.Problem) { Stop-Registration $prof.Problem }\n",
        "    if ($prof.Problem) { \"ERROR: $($prof.Problem)\" | Out-File $out -Append -Encoding utf8; "
        "\"FAILED\" | Out-File $out -Append -Encoding utf8; return }\n",
        "test_a_refused_profile_exits_1_and_says_why_on_the_console",
        T_REGX,
    ),
    (
        "REGX a refusal says nothing on the console",
        REGWD,
        "    Write-Host \"register-watchdog FAILED: $Message (log: $out)\" -ForegroundColor Red\n",
        "",
        "test_a_refused_profile_exits_1_and_says_why_on_the_console",
        T_REGX,
    ),
    (
        "REGX a refusal exits 0",
        REGWD,
        " -ForegroundColor Red\n    exit 1\n}\n",
        " -ForegroundColor Red\n    exit 0\n}\n",
        "test_a_refused_profile_exits_1_and_says_why_on_the_console",
        T_REGX,
    ),
    (
        "REGX a missing nssm is not refused",
        REGWD,
        "        if (-not (Test-Path $p)) { Stop-Registration \"Required file not found: $p\" }\n",
        "",
        "test_a_missing_nssm_exits_1_and_names_the_file",
        T_REGX,
    ),
    (
        "REGX the catch writes FAILED and nothing else again",
        REGWD,
        "} catch {\n    Stop-Registration \"$_\"\n}\n",
        "} catch {\n    \"ERROR: $_\" | Out-File $out -Append -Encoding utf8\n"
        "    \"FAILED\" | Out-File $out -Append -Encoding utf8\n}\n",
        "test_an_error_inside_the_registration_exits_1_and_says_it",
        T_REGX,
    ),
    (
        "REGX a registration that ran says nothing on the console",
        REGWD,
        "    Write-Host \"register-watchdog DONE: platform-watchdog is registered, service status: $status (log: $out)\"\n",
        "",
        "test_a_registration_that_ran_exits_0_after_done_and_says_so",
        T_REGX,
    ),
    # --- STEP: a script log prunes only its own rotated copies (delta audit 2026-10-15) ------------
    (
        "STEP platform-startup no longer prunes deploy\\logs as the warning says it does",
        STARTUP,
        "$null = Remove-RotatedServiceLogs -LogDir $LogDir\n",
        "",
        "test_what_the_warning_names_as_pruning_does_prune_deploy_logs",
        T_PRUNE_NAMED,
    ),
    # --- 2026-10-15 delta-audit follow-ups: a fixture's real profile, and how the registrar is run --
    (
        "WDOC the registrar's header tells a shell to start the GUI launcher again",
        REGWD,
        "    RunAsHelper /capture /timeout:300 /ps:5 /as:system \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "    & \"C:\\Program Files\\RunAsHelper\\RunAsHelper.exe\" /as:system \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "test_no_doc_runs_the_gui_launcher_and_every_runas_line_is_the_console_form",
        T_REGX_GUI,
    ),
    (
        "WDOC the registrar's RunAS example wraps the script in powershell.exe -File",
        REGWD,
        "    RunAsHelper /capture /timeout:300 /ps:5 /as:system \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "    RunAsHelper /capture /timeout:300 /as:system powershell.exe -File \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "test_no_doc_runs_the_gui_launcher_and_every_runas_line_is_the_console_form",
        T_REGX_GUI,
    ),
    (
        "WDOC removing the service leads with RunAS Helper again",
        "deploy/installer/WATCHDOG.md",
        "From an elevated PowerShell:\n\n```powershell\n$nssm = '<repo>\\deploy\\bin\\nssm.exe'\n"
        "& $nssm stop platform-watchdog confirm; & $nssm remove platform-watchdog confirm\n```\n\n",
        "",
        "test_each_procedure_leads_with_an_elevated_powershell_and_calls_runas_optional",
        T_REGX_LEAD,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        "CONTROL PSR sync-disabled-rails resolves its deploy folder in the body another way",
        "deploy/installer/sync-disabled-rails.ps1",
        "if (-not $DeployDir) { $DeployDir = Split-Path $PSScriptRoot -Parent }\n",
        "if (-not $DeployDir) { $DeployDir = (Get-Item -LiteralPath $PSScriptRoot).Parent.FullName }\n",
        "test_no_script_reads_its_own_path_in_a_param_default",
        T_PSKW,
    ),
    (
        "CONTROL WDOC the header's RunAS example allows a longer timeout",
        REGWD,
        "    RunAsHelper /capture /timeout:300 /ps:5 /as:system \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "    RunAsHelper /capture /timeout:600 /ps:5 /as:system \"<repo>\\deploy\\installer\\register-watchdog.ps1\"\n",
        "test_no_doc_runs_the_gui_launcher_and_every_runas_line_is_the_console_form",
        T_REGX_GUI,
    ),
    (
        "CONTROL REGX a refusal's console line written by Write-Output",
        REGWD,
        "    Write-Host \"register-watchdog FAILED: $Message (log: $out)\" -ForegroundColor Red\n",
        "    Write-Output \"register-watchdog FAILED: $Message (log: $out)\"\n",
        "test_a_refused_profile_exits_1_and_says_why_on_the_console",
        T_REGX,
    ),
]
