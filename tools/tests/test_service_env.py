"""install-services.ps1 -BrokerCliEnvFile, and service environments that never carry a blank.

The broker's Claude CLI settings (BROKER_CLI_*) were only ever carried forward from the installed
service: the script had no way to write a new one. And an NSSM AppEnvironmentExtra list is a
REG_MULTI_SZ, which a blank element terminates: NSSM silently ignores every entry after one, and
the lists read back from the registry on the podman box end in exactly such a blank. lib-runtime's
Read-ServiceEnvFile / Merge-ServiceEnv / Test-BrokerCliEnv run here under both engines, and the
script's own Install-Svc is lifted out with the parser and driven with a fake nssm.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "deploy" / "installer" / "lib-runtime.ps1"
SCRIPT = REPO / "deploy" / "install-services.ps1"
SECRET = "sk-ant-oat01-not-a-real-token-but-a-secret-shape"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _ps(run_ps, shell, body: str):
    r = run_ps(shell, f"$ErrorActionPreference = 'Stop'\n. {q(LIB)}\n" + body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_a_registry_list_and_a_file_merge_to_new_keys_with_no_blank(run_ps, shell, tmp_path):
    env = tmp_path / "broker-cli.env"
    env.write_text("# this box's CLI backend\nBROKER_CLI_OWNERS=owner-one  # exact, case too\n"
                   "BROKER_CLI_TIMEOUT=\"300\"\nBROKER_CLI_MODEL=\n", encoding="utf-8")
    got = _ps(run_ps, shell, f"""
# As Get-ItemProperty hands back AppEnvironmentExtra on the podman box: ends in a blank element.
$registry = @('HF_HOME=C:\\x\\hf', 'BROKER_CLI_ENABLED=true', 'BROKER_CLI_OWNERS=old-owner', 'BROKER_CLI_MODEL=claude-x', '')
$file = @(Read-ServiceEnvFile -Path {q(env)} -Prefix 'BROKER_CLI_')
@{{ file = $file; merged = @(Merge-ServiceEnv -Base $registry -Overlay $file)
   middle = @(Merge-ServiceEnv -Base @('A=1', '', '   ', 'junk', '=x', 'B=2', 'a=3')) }} | ConvertTo-Json -Compress
""")
    assert got["file"] == ["BROKER_CLI_OWNERS=owner-one", "BROKER_CLI_TIMEOUT=300", "BROKER_CLI_MODEL="], got
    assert got["merged"] == ["HF_HOME=C:\\x\\hf", "BROKER_CLI_ENABLED=true", "BROKER_CLI_OWNERS=owner-one",
                             "BROKER_CLI_TIMEOUT=300"], got          # new key in, MODEL= removed it
    assert "" not in got["merged"] and "" not in got["middle"], got
    assert got["middle"] == ["A=3", "B=2"], got                         # last wins, case-insensitively


def test_a_key_without_the_prefix_is_refused_by_name_and_never_by_value(run_ps, shell, tmp_path):
    env = tmp_path / "broker-cli.env"
    env.write_text(f"BROKER_CLI_ENABLED=true\nBROKER_AUTH_TOKEN={SECRET}\nPATH=C:\\elsewhere\n", encoding="utf-8")
    unparsable = tmp_path / "broken.env"
    unparsable.write_text(f"BROKER_CLI_OAUTH_TOKEN=\"{SECRET}\n", encoding="utf-8")   # unterminated quote
    got = _ps(run_ps, shell, f"""
$out = [ordered]@{{}}
foreach ($p in {q(env)}, {q(unparsable)}) {{
  try {{ $null = Read-ServiceEnvFile -Path $p -Prefix 'BROKER_CLI_'; $out[$p] = 'ACCEPTED' }}
  catch {{ $out[$p] = $_.Exception.Message }}
}}
$out | ConvertTo-Json -Compress
""")
    refused, broken = got[str(env)], got[str(unparsable)]
    assert "BROKER_AUTH_TOKEN" in refused and "PATH" in refused, refused
    assert "BROKER_CLI_ENABLED" not in refused.split("for:")[-1], refused
    assert "does not parse" in broken and "line 1" in broken, broken
    assert SECRET not in refused and SECRET not in broken, got


def test_the_cli_paths_are_checked_and_a_workdir_under_a_claude_md_is_refused(run_ps, shell, tmp_path):
    repo = tmp_path / "repo"
    (repo / "deploy").mkdir(parents=True)
    exe = tmp_path / "bin" / "claude.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    shim = tmp_path / "bin" / "claude.cmd"
    shim.write_text("@echo off\n", encoding="ascii")
    token = tmp_path / "token.txt"
    token.write_text("x", encoding="utf-8")
    planted = tmp_path / "above" / "CLAUDE.md"
    (tmp_path / "above" / "work").mkdir(parents=True)
    planted.write_text("an agent guide\n", encoding="utf-8")
    clean = tmp_path / "clean" / "work"
    clean.mkdir(parents=True)
    onedrive = tmp_path / "OneDrive - Example" / "work"
    onedrive.mkdir(parents=True)
    cases = {
        "exe-ok": f"BROKER_CLI_EXE={exe}", "exe-shim": f"BROKER_CLI_EXE={shim}",
        "exe-missing": f"BROKER_CLI_EXE={tmp_path / 'bin' / 'nope.exe'}", "exe-relative": "BROKER_CLI_EXE=bin\\claude.exe",
        "token-ok": f"BROKER_CLI_OAUTH_TOKEN_FILE={token}", "token-missing": f"BROKER_CLI_OAUTH_TOKEN_FILE={tmp_path / 'gone.txt'}",
        "workdir-under-claude-md": f"BROKER_CLI_WORKDIR={tmp_path / 'above' / 'work'}",
        "workdir-clean": f"BROKER_CLI_WORKDIR={clean}",
        "workdir-in-repo": f"BROKER_CLI_WORKDIR={repo / 'deploy'}",
        "workdir-onedrive": f"BROKER_CLI_WORKDIR={onedrive}",
        "workdir-missing": f"BROKER_CLI_WORKDIR={tmp_path / 'nowhere'}",
        "roles-in-repo": f"BROKER_CLI_ROLES_FILE={repo / 'deploy' / 'cli-roles.json'}",
        "roles-outside": f"BROKER_CLI_ROLES_FILE={tmp_path / 'cli-roles.json'}",
        "removal": "BROKER_CLI_WORKDIR=",
        "token-value": f"BROKER_CLI_OAUTH_TOKEN={SECRET}",
        # Each CLI call's own TEMP is made under this root; it need not exist yet.
        "temp-clean": f"BROKER_CLI_TEMP_ROOT={tmp_path / 'clean' / 'cli-tmp'}",
        "temp-under-claude-md": f"BROKER_CLI_TEMP_ROOT={tmp_path / 'above' / 'cli-tmp'}",
        "temp-in-repo": f"BROKER_CLI_TEMP_ROOT={repo / 'deploy' / 'cli-tmp'}",
        "temp-onedrive": f"BROKER_CLI_TEMP_ROOT={onedrive.parent / 'cli-tmp'}",
        "temp-relative": "BROKER_CLI_TEMP_ROOT=cli-tmp",
        "temp-a-file": f"BROKER_CLI_TEMP_ROOT={token}",
        "temp-in-workdir": [f"BROKER_CLI_WORKDIR={clean}", f"BROKER_CLI_TEMP_ROOT={clean / 'tmp'}"],
        "temp-beside-workdir": [f"BROKER_CLI_WORKDIR={clean}",
                                f"BROKER_CLI_TEMP_ROOT={clean.parent / 'cli-tmp'}"],
    }
    lines = "\n".join(
        f"$c[{q(k)}] = @(Test-BrokerCliEnv -Entries @({', '.join(q(e) for e in (v if isinstance(v, list) else [v]))}) "
        f"-RepoRoot {q(repo)})" for k, v in cases.items())
    got = _ps(run_ps, shell, f"$c = [ordered]@{{}}\n{lines}\n$c | ConvertTo-Json -Compress\n")
    got = {k: ([] if v is None else [v] if isinstance(v, str) else v) for k, v in got.items()}
    for ok in ("exe-ok", "token-ok", "roles-outside", "removal", "token-value"):
        assert got[ok] == [], (ok, got[ok])
    assert "not an .exe" in got["exe-shim"][0], got
    assert "does not exist" in got["exe-missing"][0] and "not an absolute path" in got["exe-relative"][0], got
    assert "does not exist" in got["token-missing"][0], got
    assert str(planted) in got["workdir-under-claude-md"][0], got     # the nearest one above it
    # A workdir with none planted above it: accepted, unless this box keeps a CLAUDE.md higher up
    # (a user profile can), and then that one is named.
    above = [p / "CLAUDE.md" for p in clean.parents if (p / "CLAUDE.md").is_file()]
    if above:
        assert len(got["workdir-clean"]) == 1 and str(above[0]) in got["workdir-clean"][0], got
    else:
        assert got["workdir-clean"] == [], got
    assert "inside the repo" in got["workdir-in-repo"][0], got
    assert "OneDrive" in got["workdir-onedrive"][0], got
    assert "does not exist" in got["workdir-missing"][0], got
    assert "inside the repo" in got["roles-in-repo"][0], got
    # BROKER_CLI_TEMP_ROOT: the workdir's rules, a root that is a file, and never in the workdir.
    temp_above = [p / "CLAUDE.md" for p in (clean / "cli-tmp").parents if (p / "CLAUDE.md").is_file()]
    if temp_above:
        assert len(got["temp-clean"]) == 1 and str(temp_above[0]) in got["temp-clean"][0], got
    else:
        assert got["temp-clean"] == [], got
    assert str(planted) in got["temp-under-claude-md"][0], got         # the nearest one above it
    assert "inside the repo" in got["temp-in-repo"][0], got
    assert "OneDrive" in got["temp-onedrive"][0], got
    assert "not an absolute path" in got["temp-relative"][0], got
    assert "is a file" in got["temp-a-file"][0], got
    assert [p for p in got["temp-in-workdir"] if "BROKER_CLI_TEMP_ROOT" in p and "inside it" in p], got
    assert not [p for p in got["temp-beside-workdir"] if "BROKER_CLI_TEMP_ROOT" in p and "inside it" in p], got
    assert SECRET not in json.dumps(got), got


README = REPO / "services" / "broker" / "README.md"


def readme_cli_env() -> str:
    """The broker-cli.env example in the broker README: from its `# C:\\platform\\broker-cli.env`
    line to the first blank line (the install-services call follows it in the same block)."""
    lines = README.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("# C:\\platform\\broker-cli.env"))
    end = next(i for i in range(start, len(lines)) if not lines[i].strip())
    return "\n".join(lines[start:end]) + "\n"


def test_the_readme_example_removes_the_model_instead_of_setting_it_to_a_comment(run_ps, shell, tmp_path):
    """`BROKER_CLI_MODEL=   # an empty value REMOVES ...` read, under deploy\\.env's rules, as the
    comment text itself (the key's `=[ \\t]*` takes the spaces, so no ` #` is left to cut at), and
    that text was passed as `--model` on every call the switch file gave no model of its own."""
    env = tmp_path / "broker-cli.env"
    env.write_text(readme_cli_env(), encoding="utf-8")
    got = _ps(run_ps, shell, f"""
$file = @(Read-ServiceEnvFile -Path {q(env)} -Prefix 'BROKER_CLI_')
@{{ file = $file; problems = @(Test-BrokerCliEnv -Entries $file -RepoRoot {q(tmp_path)}) }} | ConvertTo-Json -Compress
""")
    models = [e for e in got["file"] if e.startswith("BROKER_CLI_MODEL=")]
    assert models == ["BROKER_CLI_MODEL="], got["file"]
    # The example's paths are placeholders that do not exist here; only the model is judged.
    problems = [got["problems"]] if isinstance(got["problems"], str) else got["problems"] or []
    assert not [p for p in problems if "BROKER_CLI_MODEL" in p], problems


def test_a_model_that_is_a_comment_or_not_allowed_is_refused(run_ps, shell, tmp_path):
    cases = {
        "comment": ["BROKER_CLI_MODEL=# an empty value REMOVES the setting from the service"],
        "comment-elsewhere": ["BROKER_CLI_OWNERS=# the owners, comma-separated"],
        "not-allowed": ["BROKER_CLI_MODELS=claude-haiku-5-5,claude-sonnet-5-5", "BROKER_CLI_MODEL=claude-opus-5-5"],
        "not-allowed-either-order": ["BROKER_CLI_MODEL=claude-opus-5-5", "BROKER_CLI_MODELS=claude-haiku-5-5"],
        "allowed": ["BROKER_CLI_MODELS=claude-haiku-5-5, claude-sonnet-5-5", "BROKER_CLI_MODEL=claude-sonnet-5-5"],
        "last-models-line-wins": ["BROKER_CLI_MODELS=claude-haiku-5-5", "BROKER_CLI_MODEL=claude-opus-5-5",
                                  "BROKER_CLI_MODELS=claude-opus-5-5"],
        "model-alone": ["BROKER_CLI_MODEL=claude-sonnet-5-5"],
        "models-removed": ["BROKER_CLI_MODELS=", "BROKER_CLI_MODEL=claude-opus-5-5"],
        "removal": ["BROKER_CLI_MODEL="],
    }
    lines = "\n".join(
        f"$c[{q(k)}] = @(Test-BrokerCliEnv -Entries @({', '.join(q(e) for e in v)}) -RepoRoot {q(tmp_path)})"
        for k, v in cases.items())
    got = _ps(run_ps, shell, f"$c = [ordered]@{{}}\n{lines}\n$c | ConvertTo-Json -Compress\n")
    got = {k: ([] if v is None else [v] if isinstance(v, str) else v) for k, v in got.items()}
    for ok in ("allowed", "last-models-line-wins", "model-alone", "models-removed", "removal"):
        assert got[ok] == [], (ok, got[ok])
    assert len(got["comment"]) == 1 and "BROKER_CLI_MODEL" in got["comment"][0], got
    assert "comment" in got["comment"][0] and "line of its own" in got["comment"][0], got
    assert "REMOVES" not in got["comment"][0], got                 # names the key, never the value
    assert len(got["comment-elsewhere"]) == 1 and "BROKER_CLI_OWNERS" in got["comment-elsewhere"][0], got
    for case in ("not-allowed", "not-allowed-either-order"):
        assert len(got[case]) == 1 and "claude-opus-5-5" in got[case][0] and "BROKER_CLI_MODELS" in got[case][0], got


def test_a_placeholder_left_in_a_path_is_named_not_thrown_on(run_ps, shell, tmp_path):
    """The README's `C:\\Users\\<owner>\\...`, copied as it stands: under 5.1 GetExtension and
    GetFullPath throw on `<`, so the whole check died with "Illegal characters in path"."""
    clean = tmp_path / "clean"
    clean.mkdir()
    cases = {
        "exe": ["BROKER_CLI_EXE=C:\\Users\\<owner>\\AppData\\Roaming\\npm\\claude.exe"],
        "workdir-then-temp": ["BROKER_CLI_WORKDIR=C:\\Users\\<owner>\\cli-work",
                              f"BROKER_CLI_TEMP_ROOT={clean / 'cli-tmp'}"],
        "token-file": ['BROKER_CLI_OAUTH_TOKEN_FILE=C:\\x\\"token".txt'],
    }
    lines = "\n".join(
        f"$c[{q(k)}] = @(Test-BrokerCliEnv -Entries @({', '.join(q(e) for e in v)}) -RepoRoot {q(tmp_path / 'repo')})"
        for k, v in cases.items())
    got = _ps(run_ps, shell, f"$c = [ordered]@{{}}\n{lines}\n$c | ConvertTo-Json -Compress\n")
    got = {k: ([] if v is None else [v] if isinstance(v, str) else v) for k, v in got.items()}
    for key, case in (("BROKER_CLI_EXE", "exe"), ("BROKER_CLI_WORKDIR", "workdir-then-temp"),
                      ("BROKER_CLI_OAUTH_TOKEN_FILE", "token-file")):
        assert [p for p in got[case] if p.startswith(key) and "placeholder" in p], (case, got[case])


def test_every_pip_install_in_install_services_is_judged_by_its_exit_code(run_ps, shell):
    """course-builder's `pip install` result was never read: in a checkout without the rail (the
    public snapshot) it failed, and the service was registered anyway, to die at import and be
    restarted by NSSM for ever. The statement right after each pip run must test $LASTEXITCODE."""
    got = _ps(run_ps, shell, f"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(SCRIPT)}, [ref]$null, [ref]$null)
$pips = @($ast.FindAll({{ param($a) $a -is [System.Management.Automation.Language.CommandAst] -and
  ($t = @($a.CommandElements | ForEach-Object {{ $_.Extent.Text }})) -and $t -contains 'pip' -and $t -contains 'install' }}, $true))
$out = @(foreach ($p in $pips) {{
  $st = $p
  while ($st.Parent -and -not ($st.Parent -is [System.Management.Automation.Language.StatementBlockAst] -or
                               $st.Parent -is [System.Management.Automation.Language.NamedBlockAst])) {{ $st = $st.Parent }}
  $sts = @($st.Parent.Statements); $i = [array]::IndexOf($sts, $st)
  $next = if ($i -ge 0 -and $i + 1 -lt $sts.Count) {{ $sts[$i + 1].Extent.Text }} else {{ '' }}
  @{{ line = $p.Extent.StartLineNumber; judged = [bool]($next -match '^if \\(\\$LASTEXITCODE -ne 0\\)') }}
}})
@{{ pips = $out }} | ConvertTo-Json -Depth 3 -Compress
""")
    pips = got["pips"] if isinstance(got["pips"], list) else [got["pips"]]
    assert pips, "install-services runs no pip install any more; this test is pointless"
    assert all(p["judged"] for p in pips), pips


def test_install_svc_never_hands_nssm_a_blank_element(run_ps, shell):
    got = _ps(run_ps, shell, f"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(SCRIPT)}, [ref]$null, [ref]$null)
$f = $ast.Find({{ param($a) $a -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $a.Name -eq 'Install-Svc' }}, $true)
# A function made from text has no script file, so its $PSScriptRoot is empty: name the folder.
. ([scriptblock]::Create($f.Extent.Text.Replace('$PSScriptRoot', "'C:\\x\\deploy'")))
$global:nssm = New-Object System.Collections.Generic.List[object]
function Invoke-Nssm {{ $global:nssm.Add(@($args | ForEach-Object {{ [string]$_ }})); $global:LASTEXITCODE = 0; return 0 }}
function Get-Service {{ param([string]$Name, $ErrorAction) }}
function Write-Host {{ }}
$LogDir = 'C:\\x\\logs'
Install-Svc 'svc' 'C:\\x\\py.exe' '-m app' 'C:\\x' @('A=1', 'B=2', '')
$set = @($global:nssm | Where-Object {{ $_.Count -ge 3 -and $_[2] -eq 'AppEnvironmentExtra' }})
@{{ calls = $set.Count; env = @($set[0] | Select-Object -Skip 3) }} | ConvertTo-Json -Compress
""")
    assert got["calls"] == 1, got
    assert got["env"] == ["A=1", "B=2"], got


def test_the_env_file_is_read_and_checked_before_anything_is_torn_down(run_ps, shell):
    """Statement order IS execution order at a script's top level, so the file's read and its check
    must both come before the first Install-Svc call (whose first act is `nssm stop` + `remove`),
    the NSSM download (lib-runtime's Install-NssmExe since 2026-10-14) and the broker import probe,
    and neither may hide inside a function."""
    got = _ps(run_ps, shell, f"""
$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(SCRIPT)}, [ref]$null, [ref]$null)
$cmds = @($ast.FindAll({{ param($a) $a -is [System.Management.Automation.Language.CommandAst] }}, $true))
# The offset of the first call to $name that is not inside a function definition, or -1.
function First([string]$name) {{
  $hit = @($cmds | Where-Object {{ $_.GetCommandName() -eq $name }} | Where-Object {{
    $p = $_.Parent; $inFn = $false
    while ($p) {{ if ($p -is [System.Management.Automation.Language.FunctionDefinitionAst]) {{ $inFn = $true }}; $p = $p.Parent }}
    -not $inFn }} | Sort-Object {{ $_.Extent.StartOffset }})
  if ($hit.Count) {{ $hit[0].Extent.StartOffset }} else {{ -1 }}
}}
$teardown = @($cmds | Where-Object {{ $_.GetCommandName() -eq 'Invoke-Nssm' -and $_.CommandElements.Count -ge 2 -and
                                      @('stop', 'remove') -contains $_.CommandElements[1].Extent.Text }}).Count
@{{ read = (First 'Read-ServiceEnvFile'); check = (First 'Test-BrokerCliEnv'); svc = (First 'Install-Svc')
   fetch = (First 'Install-NssmExe'); teardown_calls = $teardown }} | ConvertTo-Json -Compress
""")
    assert got["read"] > 0 and got["check"] > got["read"], got
    assert got["teardown_calls"] >= 2, got                              # the stop/remove pair exists
    assert got["svc"] > got["check"] and got["fetch"] > got["check"], got
    text = SCRIPT.read_text(encoding="utf-8-sig")
    probe = text.index("import app.main, uvicorn")
    assert text.index("Read-ServiceEnvFile -Path $BrokerCliEnvFile") < probe, "the file is read after the import probe"
