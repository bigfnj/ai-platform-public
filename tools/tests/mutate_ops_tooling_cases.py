"""mutate_ops_scripts.py's cases for the test tooling. NOT a pytest file; never run alone.

The cases for the test tooling, and the tests whose own fixtures these cases bend. Each mutates one
of: .github/scripts/run_public_suite.py, rails/watch-builder/frontend/tools/canvas-shot/shoot.ps1,
run-tests.ps1, tools/test-targets.json, tools/deploy_snapshot.py, tools/rail_conformance.py,
tools/tests/mutation_runner.py,
tools/tests/test_backup_native.py, tools/tests/test_control_plane_probe.py,
tools/tests/test_disabled_rails.py, tools/tests/test_doc_claims.py,
tools/tests/test_platform_startup_sh.py, tools/tests/test_ps_statement_as_command.py,
tools/tests/test_rail_manifest_schema.py, tools/tests/test_rebuild_stale.py, tools/venv_doctor.py.

mutate_ops_scripts.py loads MUTATIONS and CONTROLS from each case module beside it, one per family
of scripts: they were one table until 2026-10-15, when it outgrew two thirds of publish.json's
data_max_bytes. A case lives with the family of the file it mutates, under the header of the
section it was written in. Same tuple shape as the harness's: (label, file, old, new, expected
test, suite).
"""
from __future__ import annotations

RUNTESTS = "run-tests.ps1"
T_RUNTESTS = "tools/tests/test_run_tests_ps1.py"
# The plan's order alone: its fixture lists one scratch repo per engine and runs no target.
T_RUNTESTS_ORDER = T_RUNTESTS + "::test_the_plan_lists_the_rails_in_ordinal_order_under_either_engine"
# The pytest targets' directories and PYTHONPATH, which run-tests.ps1 and the public CI driver read.
TABLE = "tools/test-targets.json"
DOCTOR = "tools/venv_doctor.py"
T_DOCTOR = "tools/tests/test_venv_doctor.py"
T_REBUILD = "tools/tests/test_rebuild_stale.py"
# The lean fixture's own profile: one test, which runs the lean fixture only.
T_REBUILD_PROFILE = T_REBUILD + "::test_the_lean_runs_keep_the_helpers_log_and_temp_files_in_a_scratch_profile"
T_ABSENCE = "tools/tests/test_tooling_absence.py"
T_DISABLED = "tools/tests/test_disabled_rails.py"
# The public CI driver. .github/ is not published, so where it is absent the harness skips the two
# cases that mutate it and names them (its ABSENT); publish.json's withheld_refs_ok says why this
# shipped module may name it.
DRIVER = ".github/scripts/run_public_suite.py"
T_SUITE_PLAN = "tools/tests/test_public_suite_plan.py"
# test_backup_native.py ships, but every test in it skips where the backup script is withheld, so
# the harness skips the cases on its own fixtures (BKT) there and names them (its ABSENT).
T_BKN = "tools/tests/test_backup_native.py"
T_DOCS = "tools/tests/test_doc_claims.py"
T_STARTUP_SH = "tools/tests/test_platform_startup_sh.py"
RUNNER = "tools/tests/mutation_runner.py"
T_PSKW = "tools/tests/test_ps_statement_as_command.py"
T_CPP = "tools/tests/test_control_plane_probe.py"
T_DISABLED_PROFILE = T_DISABLED + "::test_the_script_runs_keep_their_log_and_temp_files_in_a_scratch_profile"

# (label, file, old, new, expected test, suite)
MUTATIONS: list[tuple[str, str, str, str, str, str]] = [
    # --- #9c: run-tests.ps1 targets that checked nothing ---------------------------------------
    (
        "#9c pytest's exit 5 reads as a pass",
        RUNTESTS,
        "elseif ($code -eq 5 -and -not $t.Node) { 'NO TESTS' }",
        "elseif ($code -eq 5 -and -not $t.Node) { 'PASS' }",
        "test_a_target_whose_tests_are_all_deselected_exits_2",
        T_RUNTESTS,
    ),
    (
        "#9c a node target with no node reads as a pass",
        RUNTESTS,
        "        $result = 'NO NODE'\n      }\n    } elseif ($t.Tsc) {\n",
        "        $result = 'PASS'\n      }\n    } elseif ($t.Tsc) {\n",
        "test_a_node_target_without_node_exits_2",
        T_RUNTESTS,
    ),
    (
        "#9c not-run targets are shown but not counted",
        RUNTESTS,
        "$notRun = @($results | Where-Object { $_.Result -like 'NO *' })",
        "$notRun = @()",
        "test_a_failure_still_exits_1_and_the_unrun_targets_are_still_counted",
        T_RUNTESTS,
    ),
    (
        "#9c not-run targets are counted but the exit code ignores them",
        RUNTESTS,
        "if ($notRun.Count -gt 0) {\n  Write-Host (\"INCOMPLETE",
        "if ($false) {\n  Write-Host (\"INCOMPLETE",
        "test_a_target_whose_tests_are_all_deselected_exits_2",
        T_RUNTESTS,
    ),
    # --- #14: the venv doctor ------------------------------------------------------------------
    (
        "#14 the doctor reports but always exits 0",
        DOCTOR,
        "            \"pip install -e packages\\\\platform_core -e services\\\\broker -e apps\\\\platform\\\\backend\")\n"
        "    return 1\n",
        "            \"pip install -e packages\\\\platform_core -e services\\\\broker -e apps\\\\platform\\\\backend\")\n"
        "    return 0\n",
        "test_a_missing_declared_dependency_fails_and_gets_a_pip_line",
        T_DOCTOR,
    ),
    (
        "#14 a stale editable core install is not reported",
        DOCTOR,
        "        if err:\n            findings.append(Finding(\"core\", mod, mod, \"BROKEN\"",
        "        if False:\n            findings.append(Finding(\"core\", mod, mod, \"BROKEN\"",
        "test_a_core_package_that_will_not_import_fails",
        T_DOCTOR,
    ),
    (
        "#14 the test-dependency groups (dev) are not read",
        DOCTOR,
        "        if group in TEST_GROUPS:",
        "        if False:",
        "test_a_missing_declared_dependency_fails_and_gets_a_pip_line",
        T_DOCTOR,
    ),
    (
        "#14 every missing package counts as absent by design",
        DOCTOR,
        "        reason = ABSENT_BY_DESIGN.get(norm(name))",
        "        reason = ABSENT_BY_DESIGN.get(norm(name), 'assumed deliberate')",
        "test_a_missing_declared_dependency_fails_and_gets_a_pip_line",
        T_DOCTOR,
    ),
    (
        "#14 the declared version floor is not checked",
        DOCTOR,
        "    if spec is not None and not spec.contains(v, prereleases=True):",
        "    if False:",
        "test_a_version_below_the_declared_floor_fails_without_a_forced_reinstall",
        T_DOCTOR,
    ),
    (
        "#14 installed is trusted without importing it",
        DOCTOR,
        "    err = importer(name)",
        "    err = None",
        "test_an_installed_package_that_will_not_import_fails",
        T_DOCTOR,
    ),
    (
        "#14 vendored manifests under node_modules are read as ours",
        DOCTOR,
        "        if any(part in SKIP_DIRS for part in rel[:-1]):",
        "        if False:",
        "test_a_missing_declared_dependency_fails_and_gets_a_pip_line",
        T_DOCTOR,
    ),
    (
        "#14 httpx2 drops out of the required test tooling",
        DOCTOR,
        '    "httpx2": "Starlette\'s TestClient backend',
        '    "pytest": "Starlette\'s TestClient backend',
        "test_httpx2_is_required_test_tooling",
        T_DOCTOR,
    ),
    (
        "#14 run-tests -Doctor swallows the doctor's exit code",
        RUNTESTS,
        "  & $Py @dargs\n  exit $LASTEXITCODE\n",
        "  & $Py @dargs\n  exit 0\n",
        "test_doctor_hands_back_the_doctors_exit_code",
        T_RUNTESTS,
    ),
    (
        "#14 run-tests -Doctor ignores -Rail",
        RUNTESTS,
        "  if ($Rail) { $dargs += @('--rail', $Rail) }\n",
        "",
        "test_doctor_passes_the_rail_filter_through",
        T_RUNTESTS,
    ),
    # --- a missing test tool fails, it does not skip --------------------------------------------
    (
        "the schema test goes back to a module-level importorskip",
        "tools/tests/test_rail_manifest_schema.py",
        "\n\ndef _jsonschema():\n",
        "\njsonschema = pytest.importorskip(\"jsonschema\")\n\n\ndef _jsonschema():\n",
        "test_a_missing_test_tool_fails_the_suite_instead_of_skipping[jsonschema]",
        T_ABSENCE,
    ),
    (
        "the rebuild-plan test goes back to a module-level importorskip",
        "tools/tests/test_rebuild_stale.py",
        "\n\ndef _yaml():\n",
        "\nyaml = pytest.importorskip(\"yaml\")\n\n\ndef _yaml():\n",
        "test_a_missing_test_tool_fails_the_suite_instead_of_skipping[pyyaml]",
        T_ABSENCE,
    ),
    # --- WDP: an elevated administrator's profile over another user's checkout (ops-e follow-up) --
    (
        "SMALL read_env keeps $$ as two dollars",
        "tools/deploy_snapshot.py",
        "            if d == \"$\":\n                out.append(\"$\")\n",
        "            if d == \"$\":\n                out.append(\"$$\")\n",
        "test_read_env_gives_what_compose_gives_in_the_extra_cases",
        "tools/tests/test_dotenv_reader.py",
    ),
    (
        "SMALL read_env reads the file in text mode (a quoted CRLF becomes LF)",
        "tools/deploy_snapshot.py",
        "    text = path.read_bytes().decode(\"utf-8\")\n",
        "    text = path.read_text(encoding=\"utf-8\")\n",
        "test_read_env_gives_what_compose_gives_in_the_extra_cases",
        "tools/tests/test_dotenv_reader.py",
    ),
    (
        "SMALL rail_conformance --strict passes with no manifest at all",
        "tools/rail_conformance.py",
        "    if nothing:\n        unallowed.append(nothing)\n",
        "    if False:\n        unallowed.append(nothing)\n",
        "test_no_manifest_at_all_fails_strict",
        "tools/tests/test_rail_conformance.py::TestStrict",
    ),
    # --- run-tests.ps1's PYTHONPATH ----------------------------------------------------------
    (
        "edu-suite's PYTHONPATH names apps\\dashboard again, where nothing imports",
        TABLE,
        '"source_root_of": {"edu-suite": "apps/dashboard/src"}',
        '"source_root_of": {"edu-suite": "apps/dashboard"}',
        "test_edu_suites_package_imports_from_its_src_root",
        T_RUNTESTS,
    ),
    (
        "the public CI driver gives a rail its source root alone again (its drift until 2026-10-19)",
        DRIVER,
        '        out.append((rail.name, dirs, [src.relative_to(snap).as_posix(), *rule["shared"]]))\n',
        '        out.append((rail.name, dirs, [src.relative_to(snap).as_posix()]))\n',
        "test_the_driver_runs_every_target_run_tests_runs",
        T_SUITE_PLAN,
    ),
    (
        "the public CI driver roots edu-suite at the rail dir again (its drift until 2026-10-19)",
        DRIVER,
        '        own = rule["source_root_of"].get(rail.name)\n',
        '        own = None\n',
        "test_a_rail_targets_pythonpath_is_the_tables_rule",
        T_SUITE_PLAN,
    ),
    (
        "run-tests clears the caller's PYTHONPATH again",
        RUNTESTS,
        "  if ($null -eq $callerPythonPath) { Remove-Item -Path Env:PYTHONPATH -ErrorAction SilentlyContinue }\n"
        "  else { $env:PYTHONPATH = $callerPythonPath }\n",
        "  $env:PYTHONPATH = ''\n",
        "test_the_callers_pythonpath_comes_back_after_the_run",
        T_RUNTESTS,
    ),
    # --- the tests run from a test venv, not the broker service's ----------------------------------
    (
        "run-tests ignores .venv-test and runs in the broker service's .venv",
        RUNTESTS,
        "$VenvNames = @('.venv-test', '.venv')\n",
        "$VenvNames = @('.venv')\n",
        "test_a_test_venv_is_preferred_over_the_broker_services_venv",
        T_RUNTESTS,
    ),
    # --- run-tests type-checks every frontend -----------------------------------------------------
    (
        "TSC a frontend without node_modules passes in silence",
        RUNTESTS,
        "        $result = 'NO MODULES'\n",
        "        $code = 0\n",
        "test_a_frontend_without_node_modules_is_a_named_skip_not_a_pass",
        T_RUNTESTS,
    ),
    (
        "TSC a type error does not fail the target",
        RUNTESTS,
        "        $code = Invoke-Logged $log { & $node.Source $tscJs --noEmit -p $tsconfig --pretty false }\n",
        "        $null = Invoke-Logged $log { & $node.Source $tscJs --noEmit -p $tsconfig --pretty false }; $code = 0\n",
        "test_a_type_error_fails_the_target_and_names_the_file",
        T_RUNTESTS,
    ),
    (
        "TSC rail frontends are not discovered",
        RUNTESTS,
        r"  if (Test-Path (Join-Path $fe.FullName 'frontend\tsconfig.json')) {" "\n",
        "  if ($false) {\n",
        "test_every_frontend_with_a_tsconfig_is_a_types_target",
        T_RUNTESTS,
    ),
    (
        "TSC tsc runs without --noEmit against the default project",
        RUNTESTS,
        "        $code = Invoke-Logged $log { & $node.Source $tscJs --noEmit -p $tsconfig --pretty false }\n",
        "        $code = Invoke-Logged $log { & $node.Source $tscJs }\n",
        "test_a_clean_frontend_passes_and_tsc_is_run_no_emit_against_its_tsconfig",
        T_RUNTESTS,
    ),
    (
        "TSC the public CI driver drops the type-check targets",
        DRIVER,
        "    for n, d in tsc_targets(SOURCE):\n"
        '        place({"name": n, "paths": [d], "pypath": [], "node": False, "tsc": True},' "\n"
        '              f"{d}/tsconfig.json")' "\n",
        "",
        "test_the_driver_runs_every_target_run_tests_runs",
        T_SUITE_PLAN,
    ),
    (
        "ENC the public CI driver prints in the console's own encoding again",
        DRIVER,
        '            stream.reconfigure(encoding="utf-8", errors="replace")\n',
        "            pass\n",
        "test_a_run_echoes_what_a_cp1252_console_cannot_encode",
        T_SUITE_PLAN,
    ),
    # --- the startup.sh test cannot hang, and does not depend on PATH ----------------------------
    (
        "SHT the driver finds cygpath through PATH again (a PowerShell-launched run's PATH split)",
        T_STARTUP_SH,
        r"""        'if [ -x /usr/bin/cygpath ]; then S=$(/usr/bin/cygpath -u "$S"); B=$(/usr/bin/cygpath -u "$B"); CALLS=$(/usr/bin/cygpath -u "$CALLS"); fi\n'""" "\n",
        r"""        'if command -v cygpath >/dev/null 2>&1; then S=$(cygpath -u "$S"); B=$(cygpath -u "$B"); CALLS=$(cygpath -u "$CALLS"); fi\n'""" "\n",
        "test_the_last_enabled_line_wins_and_a_disabled_rail_gets_no_profile",
        T_STARTUP_SH,
    ),
    (
        "SHT a hung child is waited on without a bound again (plain subprocess.run)",
        T_STARTUP_SH,
        "    return mr.run_tree(cmd, timeout=kw.pop(\"timeout\", TIMEOUT), **kw)\n",
        "    return subprocess.run(cmd, timeout=kw.pop(\"timeout\", TIMEOUT), **kw)\n",
        "test_a_child_that_outlives_the_kill_cannot_hold_the_run_for_ever",
        T_STARTUP_SH,
    ),
    (
        "SHT run_tree's read after the tree kill is unbounded again (an orphan holds the run)",
        RUNNER,
        "            out, err = proc.communicate(timeout=REAP_TIMEOUT)\n"
        "        except subprocess.TimeoutExpired:\n"
        "            out = err = None\n",
        "            out, err = proc.communicate()\n"
        "        except subprocess.TimeoutExpired:\n"
        "            out = err = None\n",
        "test_a_child_that_outlives_the_kill_cannot_hold_the_run_for_ever",
        T_STARTUP_SH,
    ),
    (
        "DOCW the doc-claims walks prune dist alone again (a built dist-iep read as a doc)",
        T_DOCS,
        '    return name in ("dist", "build", "coverage", ".vite") or name.startswith("dist-")\n',
        '    return name == "dist"\n',
        "test_build_output_is_never_read_as_a_doc[dist-iep]",
        T_DOCS,
    ),
    # --- T1-b: no statement keyword runs as a command, anywhere in the tree -----------------------
    (
        "T1b the statement-keyword scan forgets try",
        T_PSKW,
        'KEYWORDS = ("try", "catch",',
        'KEYWORDS = ("catch",',
        "test_the_scan_flags_a_parenthesised_try_and_not_a_subexpression",
        T_PSKW,
    ),
    # --- PSR: no script's own param() default reads where the script lives (empty under 5.1 -File)
    (
        "PSR the canvas-shot OutDir default reads $PSScriptRoot in param() again",
        "rails/watch-builder/frontend/tools/canvas-shot/shoot.ps1",
        "  [string]$OutDir = ''\n",
        '  [string]$OutDir = "$PSScriptRoot\\shots"\n',
        "test_no_script_reads_its_own_path_in_a_param_default",
        T_PSKW,
    ),
    (
        "PSR the param-default scan forgets $PSScriptRoot",
        T_PSKW,
        "(@('PSScriptRoot', 'PSCommandPath', 'MyInvocation') -contains",
        "(@('PSCommandPath', 'MyInvocation') -contains",
        "test_the_scan_flags_a_script_path_in_a_param_default_and_not_in_the_body",
        T_PSKW,
    ),
    # --- T1-c: rebuild-stale follows the box's runtime and stack ----------------------------------
    (
        # A test's fixture, not a script: its real run pointed the helpers at the user's real Temp.
        "T1c the lean fixture runs rebuild-stale under the user's own profile, so its helpers use the real Temp",
        T_REBUILD,
        "    body = LEAN_FAKES + (f\"$env:USERPROFILE = {q(profile)}\\n\"\n",
        "    body = LEAN_FAKES + (f\"\"\n",
        "test_the_lean_runs_keep_the_helpers_log_and_temp_files_in_a_scratch_profile",
        T_REBUILD_PROFILE,
    ),
    # --- BKT: the backup's test runs keep the user's profile and Temp out (ops-e follow-up) -------
    # Fixtures, not the script: under the user's own profile they pointed DOCKER_CONFIG into its Temp.
    (
        "BKT the runs fixture runs the backup under the user's own profile again",
        T_BKN,
        "    prof, temp, body = scratch_profile(base)\n",
        "    prof, temp, _ = scratch_profile(base)\n    body = \"\"\n",
        "test_the_runs_keep_their_temp_files_in_a_profile_of_their_own",
        T_BKN,
    ),
    (
        "BKT the dry run runs the backup under the user's own profile again",
        T_BKN,
        "    body = (own + f\"$env:Path",
        "    body = (f\"$env:Path",
        "test_the_dry_run_and_the_lean_runs_have_a_profile_of_their_own",
        T_BKN,
    ),
    (
        "BKT the lean podman box runs the backup under the user's own profile again",
        T_BKN,
        "    path_ps = own + (f\"$env:Path",
        "    path_ps = (f\"$env:Path",
        "test_the_dry_run_and_the_lean_runs_have_a_profile_of_their_own",
        T_BKN,
    ),
    # --- 2026-10-15 delta-audit follow-ups: a fixture's real profile, and how the registrar is run --
    (
        # A test's fixture, not a script: its runs pointed sync-disabled-rails at the user's real Temp.
        "SDRP the sync-disabled-rails fixture runs under the user's own profile, so its log is the real Temp's",
        T_DISABLED,
        "        body = f\"$env:USERPROFILE = {q(prof)}\\n\" + SCRIPT_STUB + f\"\"\"\n",
        "        body = SCRIPT_STUB + f\"\"\"\n",
        "test_the_script_runs_keep_their_log_and_temp_files_in_a_scratch_profile",
        T_DISABLED_PROFILE,
    ),
    # --- 2026-10-19: run-tests.ps1 lists the rails in one order under either engine --------------
    (
        "ORD run-tests sorts the rails by the engine's culture again",
        RUNTESTS,
        "  [Array]::Sort($names, $dirs, [StringComparer]::Ordinal)\n",
        "  $dirs = [IO.DirectoryInfo[]]@($dirs | Sort-Object Name)\n",
        "test_the_plan_lists_the_rails_in_ordinal_order_under_either_engine",
        T_RUNTESTS_ORDER,
    ),
    (
        "ORD the rails are sorted in an untyped array, so the sort orders a copy",
        RUNTESTS,
        "  [IO.DirectoryInfo[]]$dirs = @(Get-ChildItem (Join-Path $Root 'rails') -Directory)\n",
        "  $dirs = @(Get-ChildItem (Join-Path $Root 'rails') -Directory)\n",
        "test_the_plan_lists_the_rails_in_ordinal_order_under_either_engine",
        T_RUNTESTS_ORDER,
    ),
]

# Controls: each must SURVIVE (see the harness).
CONTROLS: list[tuple[str, str, str, str, str, str]] = [
    (
        # The busy host the old stopwatch failed on (2026-10-14), made deterministic: the kill
        # starts 10 s late, still inside the runner's bounds, so the test must stay green.
        "CONTROL SHT the tree kill is 10 s slow to start, as on a loaded host",
        RUNNER,
        '        if sys.platform == "win32":\n'
        '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n',
        '        __import__("time").sleep(10)\n'
        '        if sys.platform == "win32":\n'
        '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n',
        "test_a_child_that_outlives_the_kill_cannot_hold_the_run_for_ever",
        T_STARTUP_SH,
    ),
    (
        "CONTROL MRT the tree kill is 20 s slow to start, as on a loaded host",
        RUNNER,
        '        if sys.platform == "win32":\n'
        '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n',
        '        __import__("time").sleep(20)\n'
        '        if sys.platform == "win32":\n'
        '            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],\n',
        "test_a_grandchild_holding_the_pipe_cannot_keep_the_runner_waiting",
        "tools/tests/test_mutation_runner.py",
    ),
    (
        "CONTROL WDOG the slow podman answers 5 s later, still well inside the confirming wait",
        T_CPP,
        '    "slow15": ("ping -n 16 127.0.0.1 >nul\\r\\nexit /b 0", "-ProbeSeconds 2"),\n',
        '    "slow15": ("ping -n 21 127.0.0.1 >nul\\r\\nexit /b 0", "-ProbeSeconds 2"),\n',
        "test_a_podman_that_answers_in_15_s_is_not_dead",
        T_CPP,
    ),
    (
        "CONTROL BKT the scratch TMP set from the scratch TEMP",
        T_BKN,
        "; $env:TMP = {q(temp)}\\n\"\n",
        "; $env:TMP = $env:TEMP\\n\"\n",
        "test_the_runs_keep_their_temp_files_in_a_profile_of_their_own",
        T_BKN,
    ),
    (
        "CONTROL ORD the ordinal comparer named by its full type name",
        RUNTESTS,
        "  [Array]::Sort($names, $dirs, [StringComparer]::Ordinal)\n",
        "  [System.Array]::Sort($names, $dirs, [System.StringComparer]::Ordinal)\n",
        "test_the_plan_lists_the_rails_in_ordinal_order_under_either_engine",
        T_RUNTESTS_ORDER,
    ),
]
