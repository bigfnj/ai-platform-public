"""install.ps1 hands its children their arguments intact, and the admin password off the command line.

The GUI started its provisioning child with `Start-Process -ArgumentList` holding the admin username
and password as bare elements, and Start-Process joins an array with spaces and quotes nothing: a
value with a space reached the child as several arguments (measured: 3 argv entries) and the window
waited forever. `-PlatformRoot $Root` (and `-File`) to the elevated broker step went the same way, so
an install folder with a space broke it. And the password travelled on a process command line, which
any process listing and every command-line audit log reads. Now every such launch is ONE command
line built by lib-runtime's Join-CommandLine, and the password goes through the child's environment.

Each check starts REAL child processes: Start-Process is shadowed by a function that records what it
was asked and starts the same command line with the real cmdlet (minus -Verb RunAs), and the child
script it starts is a recorder. Nothing is elevated, installed or pulled.

The last stop is deploy\\.env, which compose reads by its dotenv rules before it hands the gateway the
password: both are written there as quoted values now, and read back with the reader held to compose.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
INSTALL = INSTALLER / "install.ps1"
# The Python reader held to `docker compose config` (test_dotenv_reader.py): what compose makes of
# deploy\.env, and so what the gateway is handed.
_spec = importlib.util.spec_from_file_location("_args_deploy_snapshot", REPO / "tools" / "deploy_snapshot.py")
ds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ds)

VALUES = ["plain", "two words", 'say "hi" now', "trailing\\", 'x\\"y', "C:\\Program Files\\x\\", "it's", "",
          "  padded  ", 'end"', "tab\there", "-looks-like-a-switch"]


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def ps_list(items) -> str:
    return "@(" + ", ".join(q(i) for i in items) + ")"


@pytest.fixture(scope="module")
def roundtrip(run_ps, shell, tmp_path_factory):
    """Join-CommandLine's output parsed back by a Python child (the C runtime's rules) and by a
    powershell.exe -File child (the consumer install.ps1 starts)."""
    d = tmp_path_factory.mktemp("argv")
    (d / "rec.py").write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    (d / "rec.ps1").write_text("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
                               "ConvertTo-Json -InputObject @($args | ForEach-Object { [string]$_ }) -Compress\n",
                               encoding="utf-8-sig")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$vals = {ps_list(VALUES)}
$line = Join-CommandLine -Arguments $vals
$py = & {{ $psi = [Diagnostics.ProcessStartInfo]::new({q(sys.executable)}, ('-I ' + (Join-CommandLine -Arguments @({q(d / 'rec.py')})) + ' ' + $line))
          $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true
          $p = [Diagnostics.Process]::Start($psi); $o = $p.StandardOutput.ReadToEnd(); $p.WaitForExit(); $o }}
$ps = & {{ $psi = [Diagnostics.ProcessStartInfo]::new('powershell.exe', ('-NoProfile -ExecutionPolicy Bypass -File ' + (Join-CommandLine -Arguments @({q(d / 'rec.ps1')})) + ' ' + $line))
          $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true
          $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
          $p = [Diagnostics.Process]::Start($psi); $o = $p.StandardOutput.ReadToEnd(); $p.WaitForExit(); $o }}
@{{ line = $line; py = $py.Trim(); ps = $ps.Trim(); simple = (Join-CommandLine -Arguments @('-NoProfile', '-File', 'C:\\a\\b.ps1')) }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_every_argument_comes_back_whole_under_the_c_runtime_rules(roundtrip):
    assert json.loads(roundtrip["py"]) == VALUES, roundtrip


def test_every_argument_comes_back_whole_through_powershell_file(roundtrip):
    """The child install.ps1 starts is powershell.exe -File; its own parser agrees (measured: every
    value whole, the empty one included). A value starting with '-' comes back whole here only
    because this recorder declares no parameters: a script that does would bind it as a parameter
    NAME, one more reason the password, which may start with one, is not passed this way."""
    assert json.loads(roundtrip["ps"]) == VALUES, roundtrip


def test_a_plain_argument_is_passed_as_it_is(roundtrip):
    assert roundtrip["simple"] == "-NoProfile -File C:\\a\\b.ps1", roundtrip


# --- the real install.ps1 -Provision, from a folder whose path has a space and a quote ----------

RECORDER = r"""param([string]$PlatformRoot, [switch]$SkipOllama, [string]$BrokerHost, [switch]$OpenWslFirewall)
$rec = [ordered]@{ PlatformRoot = $PlatformRoot; SkipOllama = [bool]$SkipOllama; BrokerHost = $BrokerHost
                   Extra = @($args); PassInEnv = [Environment]::GetEnvironmentVariable('AIPLATFORM_INSTALL_ADMIN_PASS') }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'native-called.json'), ($rec | ConvertTo-Json -Compress))
exit 7
"""

FORWARD = r"""
$global:started = New-Object System.Collections.Generic.List[object]
function global:Start-Process {
  param([Parameter(Position = 0)][string]$FilePath, $ArgumentList, [switch]$Wait, [switch]$PassThru,
        [string]$Verb, [string]$WindowStyle)
  $global:started.Add([pscustomobject]@{ File = $FilePath; Args = $ArgumentList; Verb = $Verb })
  $p = Microsoft.PowerShell.Management\Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -Wait -PassThru -NoNewWindow
  if ($PassThru) { $p }
}
"""

USER = "adm in"
# Awkward for the command line (a blank, a quote) AND for compose's dotenv rules, which deploy\.env
# is read by: `$$` is one `$` there and ` #` starts a comment in a bare value, so this came back as
# `p@ss "w` until install.ps1 wrote it quoted.
PASS = 'p@ss "w #d" $$x\\'


@pytest.fixture(scope="module")
def provision(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("args-install")
    root = base / "it's an install" / "repo"
    inst = root / "deploy" / "installer"
    inst.mkdir(parents=True)
    for f in ("install.ps1", "lib-runtime.ps1", "env.lean.example", "roles.lean.json"):
        shutil.copy2(INSTALLER / f, inst / f)
    (inst / "install-native.ps1").write_text(RECORDER, encoding="utf-8-sig")
    (root / "services" / "broker").mkdir(parents=True)
    prof = base / "profile"
    (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
    body = FORWARD + f"""
$ErrorActionPreference = 'Continue'
$env:USERPROFILE = {q(prof)}
$env:AIPLATFORM_INSTALL_ADMIN_PASS = {q(PASS)}
& {q(inst / 'install.ps1')} -Provision -RuntimeMode desktop -AdminUser {q(USER)} -EnabledApps terminal-fun *>&1 | Out-Null
@{{ verbs = [string[]]@($global:started | ForEach-Object {{ [string]$_.Verb }}); left = [Environment]::GetEnvironmentVariable('AIPLATFORM_INSTALL_ADMIN_PASS') }} | ConvertTo-Json -Depth 4 -Compress
"""
    r = run_ps(shell, body, timeout=240)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    called = inst / "native-called.json"
    got["native"] = json.loads(called.read_text(encoding="utf-8-sig")) if called.exists() else None
    got["env"] = (root / "deploy" / ".env").read_text(encoding="utf-8")
    got["env_path"] = root / "deploy" / ".env"
    fail = prof / "AppData" / "Local" / "Temp" / "ai-platform-install.fail"
    got["fail"] = fail.read_text(encoding="utf-8-sig") if fail.exists() else ""
    got["root"] = str(root)
    return got


def test_the_broker_step_gets_the_install_folder_whole(provision):
    n = provision["native"]
    assert n is not None, provision                          # the child ran: -File was not split
    assert n["PlatformRoot"] == provision["root"] and n["Extra"] == [], n
    assert n["SkipOllama"] is True and n["BrokerHost"] == "127.0.0.1", n
    assert "install-native.ps1 failed (exit 7)" in provision["fail"], provision["fail"]
    assert provision["verbs"][:1] == ["RunAs"], provision["verbs"]


def test_the_password_arrives_through_the_environment_and_goes_no_further(provision):
    # Read back the way compose reads deploy\.env before it hands the gateway the password.
    got = ds.read_env(provision["env_path"], environ={})
    assert got["PLATFORM_ADMIN_PASSWORD"] == PASS, provision["env"]
    assert got["PLATFORM_ADMIN_USER"] == USER, provision["env"]
    assert provision["native"]["PassInEnv"] is None, provision["native"]   # the elevated step never sees it
    assert provision["left"] is None, provision                              # nor anything else started later


# --- the admin's name and password, as compose reads them back from deploy\.env -----------------
# install.ps1 and lib-apply's env step wrote both bare into `KEY={{...}}`, and compose's dotenv rules
# then changed the password before the gateway seeded the admin with it, so the one the user typed
# was refused at first login. The audit's own (measured with read_env): `P@$$w0rd` came back
# `P@$w0rd`, `Tr0ub4dor&$Horse` `Tr0ub4dor&`, `abc #1` `abc`, `x${Y}z` `xz`, and a leading `'` made
# compose refuse the file.

AUDIT_VALUES = ["P@$$w0rd", "Tr0ub4dor&$Horse", "abc #1", "'quoted", "x${Y}z", "${PLATFORM_ADMIN_USER}",
                "two\nlines", "cr\rhere", "back\\n slash"]
AWKWARD = VALUES + AUDIT_VALUES


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


@pytest.fixture(scope="module")
def filled(run_ps, shell, tmp_path_factory):
    """Each awkward value as the admin's password and as their name, filled into the real
    env.lean.example by lib-runtime's Expand-LeanEnvTemplate (install.ps1's filler) and by
    lib-apply's Get-InstallerEnvText (the apply engine's), each written as a deploy\\.env is.
    Values travel base64-encoded, so no quoting or line ending of this test's own can change them.
    Both fillers are asked for the empty value too: a blank password is apply.ps1's default, and
    the gateway generates one for it (lib-apply's Mandatory parameters refused it until they took
    [AllowEmptyString()]). A filler that throws writes nothing, and its test names the value."""
    d = tmp_path_factory.mktemp("env-fill")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-apply.ps1')}
$tmpl = Get-Content -Raw -Encoding UTF8 -LiteralPath {q(INSTALLER / 'env.lean.example')}
$enc = New-Object System.Text.UTF8Encoding($false)
$vals = @({', '.join(q(_b64(v)) for v in AWKWARD)})
function Put([string]$Name, [scriptblock]$Fill) {{
  try {{ [IO.File]::WriteAllText((Join-Path {q(d)} $Name), (& $Fill), $enc) }} catch {{ Write-Host "$Name : $_" }}
}}
for ($i = 0; $i -lt $vals.Count; $i++) {{
  $v = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($vals[$i]))
  Put "$i-runtime-pass.env" {{ Expand-LeanEnvTemplate -Text $tmpl -AdminUser 'admin' -AdminPassword $v -EnabledApps 'terminal-fun' }}
  Put "$i-runtime-user.env" {{ Expand-LeanEnvTemplate -Text $tmpl -AdminUser $v -AdminPassword 'pw' -EnabledApps 'terminal-fun' }}
  Put "$i-apply-pass.env" {{ Get-InstallerEnvText -Rails @('terminal-fun') -AdminUser 'admin' -AdminPassword $v }}
  if ($v -ne '') {{
    # The NAME stays Mandatory without [AllowEmptyString()]: apply.ps1 defaults it to 'admin'.
    Put "$i-apply-user.env" {{ Get-InstallerEnvText -Rails @('terminal-fun') -AdminUser $v -AdminPassword 'pw' }}
  }}
}}
'written'
"""
    r = run_ps(shell, body)
    assert r.returncode == 0 and "written" in r.stdout, r.stdout + r.stderr
    return d


@pytest.mark.parametrize("filler", ["runtime", "apply"])
def test_the_admin_password_reads_back_from_deploy_env_exactly_as_typed(filled, filler):
    wrong = {}
    for i, v in enumerate(AWKWARD):
        f = filled / f"{i}-{filler}-pass.env"
        if not f.exists():
            wrong[v] = "not written (the filler threw)"
            continue
        try:
            got = ds.read_env(f, environ={})
        except ds.DotEnvError as e:
            wrong[v] = f"compose refuses the file: {e}"
            continue
        if got.get("PLATFORM_ADMIN_PASSWORD") != v or got.get("PLATFORM_ADMIN_USER") != "admin":
            wrong[v] = got.get("PLATFORM_ADMIN_PASSWORD")
    assert wrong == {}, wrong


@pytest.mark.parametrize("filler", ["runtime", "apply"])
def test_the_admin_name_reads_back_from_deploy_env_exactly_as_typed(filled, filler):
    wrong = {}
    for i, v in enumerate(AWKWARD):
        f = filled / f"{i}-{filler}-user.env"
        if filler == "apply" and v == "":
            continue
        if not f.exists():
            wrong[v] = "not written (the filler threw)"
            continue
        try:
            got = ds.read_env(f, environ={})
        except ds.DotEnvError as e:
            wrong[v] = f"compose refuses the file: {e}"
            continue
        if got.get("PLATFORM_ADMIN_USER") != v or got.get("PLATFORM_ADMIN_PASSWORD") != "pw":
            wrong[v] = got.get("PLATFORM_ADMIN_USER")
    assert wrong == {}, wrong


def test_each_written_value_is_one_line_and_the_rest_of_the_template_is_untouched(filled):
    """A CR or LF goes in as an escape, so the line-based writers (Set-EnvValue, Add-EnvCarryOver)
    still see one assignment per line, and only the three placeholders change."""
    tmpl = (INSTALLER / "env.lean.example").read_text(encoding="utf-8").replace("\r\n", "\n")
    for i, v in enumerate(AWKWARD):
        f = filled / f"{i}-runtime-pass.env"
        assert f.exists(), f"not written (the filler threw) for {v!r}"
        text = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert text.count("\n") == tmpl.count("\n"), (v, text)
        lines = [ln for ln in text.split("\n") if ln.startswith("PLATFORM_ADMIN_PASSWORD=")]
        assert len(lines) == 1 and lines[0].startswith('PLATFORM_ADMIN_PASSWORD="') and lines[0].endswith('"'), lines
    assert "PLATFORM_ENABLED_APPS=terminal-fun\n" in text and "{{" not in text, text


# --- the GUI's provisioning child --------------------------------------------------------------

def test_an_unquoted_argument_list_splits_a_value_with_a_space(run_ps, shell, tmp_path):
    """The defect, through the same forwarding Start-Process: the old array form hands the child a
    username with a space as two arguments. A control for the checks below, which would pass on
    nothing if this fixture could not see a split."""
    child = tmp_path / "child.ps1"
    child.write_text(CHILD, encoding="utf-8-sig")
    body = FORWARD + f"""
Start-Process powershell -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', {q(child)}, '-AdminUser', {q(USER)})
"""
    r = run_ps(shell, body, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    c = json.loads((tmp_path / "child.json").read_text(encoding="utf-8-sig"))
    # "in" binds to the next positional parameter (here -AdminPass), or lands in $args.
    assert c["AdminUser"] == "adm" and (c["AdminPassBound"] or c["Extra"] == ["in"]), c


CHILD = r"""param([switch]$Provision, [string]$AdminUser, [string]$AdminPass, [string]$EnabledApps, [string]$RuntimeMode)
$rec = [ordered]@{ Provision = [bool]$Provision; AdminUser = $AdminUser; AdminPassBound = $PSBoundParameters.ContainsKey('AdminPass')
                   EnabledApps = $EnabledApps; RuntimeMode = $RuntimeMode; Extra = @($args)
                   PassInEnv = [Environment]::GetEnvironmentVariable('AIPLATFORM_INSTALL_ADMIN_PASS')
                   CommandLine = [Environment]::CommandLine }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'child.json'), ($rec | ConvertTo-Json -Compress))
"""


def _function_text(script: Path, name: str, var: str) -> str:
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("needs pwsh for the AST read")
    cmd = (f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(script)}, [ref]$null, [ref]$null)\n"
           f"$f = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq '{name}' }}, $true)\n"
           f"$v = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '${var}' }}, $true)\n"
           "@{ f = $f.Extent.Text; v = $v.Extent.Text } | ConvertTo-Json -Compress\n")
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["f"] and got["v"], f"install.ps1 has no {name} / ${var}"
    return got["v"] + "\n" + got["f"]


@pytest.fixture(scope="module")
def gui_child(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("args gui")
    child = d / "child script.ps1"
    child.write_text(CHILD, encoding="utf-8-sig")
    fn = _function_text(INSTALL, "Start-ProvisionChild", "ProvisionPassVar")
    body = FORWARD + f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
{fn}
Start-ProvisionChild -Script {q(child)} -User {q(USER)} -Pass {q(PASS)} -Apps 'terminal-fun,recipe-book' -Mode podman
@{{ left = [Environment]::GetEnvironmentVariable('AIPLATFORM_INSTALL_ADMIN_PASS'); args = [string]$global:started[0].Args }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    got["child"] = json.loads((d / "child.json").read_text(encoding="utf-8-sig"))
    return got


def test_the_gui_child_gets_each_selection_whole(gui_child):
    c = gui_child["child"]
    assert c["Provision"] is True and c["AdminUser"] == USER and c["Extra"] == [], c
    assert c["EnabledApps"] == "terminal-fun,recipe-book" and c["RuntimeMode"] == "podman", c


def test_the_gui_childs_password_is_in_its_environment_not_its_command_line(gui_child):
    c = gui_child["child"]
    assert c["AdminPassBound"] is False and c["PassInEnv"] == PASS, c
    assert "p@ss" not in c["CommandLine"] and "p@ss" not in gui_child["args"], gui_child
    assert gui_child["left"] is None, gui_child                  # cleared in the GUI process after the start
