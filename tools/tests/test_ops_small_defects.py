"""The small ops-tooling defects of the 2026-10-14 audit, one check each where one is cheap.

- the scripts' own logs (startup.log, smoke-test.log, install.log, rebuild-stale-<stamp>.log) were
  never rotated or pruned;
- NSSM's download left %TEMP%\\nssm-2.24.zip and its extract folder behind, and nothing could pin
  what it ran as LocalSystem;
- register-watchdog.ps1, run as SYSTEM through the RunAS Helper gate, baked SYSTEM's own profile
  into the watchdog service;
- install.ps1 wrote CO_WORKER_INBOX_WIN, which nothing reads, and smoke-test ran a podman call whose
  answer nothing used;
- neither Caddyfile served http://127.0.0.1 (an empty 200);
- platform-startup.ps1 composed the lean stack over a full-stack box;
- deploy/INSTRUCTIONS.md said XTTS has no cache override and re-downloads under the service.

PowerShell runs under both engines from scratch trees with every runtime CLI a function.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# --- the scripts' own logs -------------------------------------------------------------------------

@pytest.fixture(scope="module")
def logs(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("logs")
    (d / "startup.log").write_bytes(b"x" * 2048)                       # past the bound used below
    (d / "smoke-test.log").write_bytes(b"y" * 10)                      # under it
    for i in range(12):                                               # 12 old rotated startup logs
        (d / f"startup-200101{i + 1:02d}T000000.000.log").write_bytes(b"old")
    for i in range(14):
        (d / f"rebuild-stale-202610{i + 1:02d}-120000.log").write_bytes(b"run")
    (d / "platform-broker.out.log").write_bytes(b"live")
    # Another program's logs in NSSM's rotated shape, as the user's TEMP can hold (install.ps1 keeps
    # its log there when deploy\logs cannot be made): not this log's family, so never pruned.
    for i in range(13):
        (d / f"someapp-2026{i % 12 + 1:02d}{i // 12 + 1:02d}T000000.000.log").write_bytes(b"theirs")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$a = Step-ScriptLog -Path {q(d / 'startup.log')} -MaxBytes 1024 -Keep 10
$b = Step-ScriptLog -Path {q(d / 'smoke-test.log')} -MaxBytes 1024
$c = Step-ScriptLog -Path {q(d / 'absent.log')} -MaxBytes 1024
$r = Remove-OldRunLogs -LogDir {q(d)} -Filter 'rebuild-stale-*.log' -Keep 9
@{{ a = $a; b = $b; c = $c; r = $r }} | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), d


def test_a_script_log_past_its_bound_is_rotated_and_its_copies_pruned(logs):
    got, d = logs
    assert got["a"] and Path(got["a"]).name.startswith("startup-") and Path(got["a"]).is_file(), got
    assert not (d / "startup.log").exists()                           # the run appends to a fresh one
    rotated = sorted(p.name for p in d.glob("startup-*.log"))
    assert len(rotated) == 10 and Path(got["a"]).name in rotated, rotated   # the newest ten, new one in
    assert got["b"] == "" and (d / "smoke-test.log").read_bytes() == b"y" * 10
    assert got["c"] == ""
    assert (d / "platform-broker.out.log").read_bytes() == b"live"   # a live log is never touched


def test_a_script_log_prunes_only_its_own_rotated_copies(logs):
    """Step-ScriptLog pruned every rotated-shaped family in its log's folder, so a log in the user's
    TEMP cut another program's logs to ten (delta audit 2026-10-15). Its own family still goes to ten
    (the test above); these thirteen stay."""
    _, d = logs
    assert len(list(d.glob("someapp-*.log"))) == 13


def test_a_per_run_log_family_keeps_its_newest(logs):
    got, d = logs
    kept = sorted(p.name for p in d.glob("rebuild-stale-*.log"))
    assert got["r"] == 5 and len(kept) == 9 and kept[0] == "rebuild-stale-20261006-120000.log", kept


@pytest.mark.parametrize("script,call", [
    ("deploy/installer/platform-startup.ps1", "Step-ScriptLog -Path $LogFile"),
    ("deploy/installer/smoke-test.ps1", "Step-ScriptLog -Path $LogFile"),
    ("deploy/installer/install.ps1", "Step-ScriptLog -Path $LogFile"),
    ("deploy/rebuild-stale.ps1", "Remove-OldRunLogs -LogDir $LogDir -Filter 'rebuild-stale-*.log'"),
])
def test_each_script_bounds_its_own_log(script, call):
    assert call in (REPO / script).read_text(encoding="utf-8"), script


# --- NSSM: fetched into a folder of the run's own, verified when pinned -----------------------------

def fake_nssm_zip(exe: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("nssm-2.24/win64/nssm.exe", exe)
        z.writestr("nssm-2.24/win32/nssm.exe", b"32-bit")
        z.writestr("nssm-2.24/README.txt", b"readme")
    return buf.getvalue()


@pytest.fixture(scope="module")
def nssm(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("nssm")
    exe = b"MZ fake nssm 2.24 win64"
    (d / "nssm-2.24.zip").write_bytes(fake_nssm_zip(exe))
    temp = d / "temp"
    temp.mkdir()
    good = hashlib.sha256(exe).hexdigest()
    body = f"""
$ErrorActionPreference = 'Stop'
$env:TEMP = {q(temp)}; $env:TMP = {q(temp)}
. {q(INSTALLER / 'lib-runtime.ps1')}
function Invoke-WebRequest {{ param([string]$Uri, [string]$OutFile, [switch]$UseBasicParsing)
  if ($Uri -like '*fail*') {{ throw 'stub: the download failed' }}
  Copy-Item -LiteralPath {q(d / 'nssm-2.24.zip')} -Destination $OutFile }}
$out = [ordered]@{{}}
$out.pin = $NssmExeSha256
$out.unpinned = Install-NssmExe -Dest {q(d / 'a.exe')} -Sha256 '' 6>$null
$out.pinned = Install-NssmExe -Dest {q(d / 'b.exe')} -Sha256 '{good.upper()}'
try {{ $null = Install-NssmExe -Dest {q(d / 'c.exe')} -Sha256 ('0' * 64); $out.wrong = 'installed' }} catch {{ $out.wrong = "$_" }}
try {{ $null = Install-NssmExe -Dest {q(d / 'd.exe')} -Url 'https://fail.invalid/nssm.zip' -Sha256 ''; $out.fail = 'installed' }} catch {{ $out.fail = "$_" }}
# What both installers call: no -Sha256, so the shipped pin, which this fake exe cannot meet.
try {{ $null = Install-NssmExe -Dest {q(d / 'e.exe')}; $out.tampered = 'installed' }} catch {{ $out.tampered = "$_" }}
$out.left = @(Get-ChildItem -LiteralPath {q(temp)} -Force | ForEach-Object {{ $_.Name }})
$out | ConvertTo-Json -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), d, exe, good


def test_nssm_is_installed_from_the_zip_and_nothing_stays_in_temp(nssm):
    got, d, exe, good = nssm
    assert (d / "a.exe").read_bytes() == exe and got["unpinned"] == good, got
    assert (d / "b.exe").read_bytes() == exe and got["pinned"] == good, got
    assert got["left"] == [], got                                    # no zip, no extract folder


def test_a_pinned_hash_that_does_not_match_is_refused_and_installs_nothing(nssm):
    got, d, _, _ = nssm
    assert "refusing to install it" in got["wrong"] and not (d / "c.exe").exists(), got
    assert "the download failed" in got["fail"] and not (d / "d.exe").exists(), got


# SHA-256 of NSSM 2.24's win64 nssm.exe (the zip's nssm-2.24/win64/nssm.exe), taken from the
# deploy\bin\nssm.exe a box has run its services with: FileVersion 2.24, 331,264 bytes, linked
# 2014-08-31, unsigned. nssm.cc publishes no checksum (engineering record: "NSSM is pinned to the
# 2.24 build a box already runs").
NSSM_2_24_WIN64_SHA256 = "f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97"


def test_nssm_is_pinned_to_the_2_24_win64_exe(nssm):
    """$NssmExeSha256 was empty, so a fetch ran whatever nssm.cc served as LocalSystem."""
    got = nssm[0]
    assert got["pin"].lower() == NSSM_2_24_WIN64_SHA256, got     # Install-NssmExe lower-cases it


def test_an_installer_fetch_refuses_an_exe_that_is_not_the_pinned_one(nssm):
    """Both installers call Install-NssmExe -Dest alone, so the default pin is what guards them."""
    got, d, _, good = nssm
    assert good != NSSM_2_24_WIN64_SHA256
    assert "refusing to install it" in got["tampered"], got
    assert NSSM_2_24_WIN64_SHA256 in got["tampered"].lower(), got
    assert not (d / "e.exe").exists(), got
    assert got["left"] == [], got


def test_no_installer_downloads_nssm_its_own_way():
    for script in ("deploy/install-services.ps1", "deploy/installer/install-native.ps1"):
        text = (REPO / script).read_text(encoding="utf-8")
        assert "Install-NssmExe -Dest $Nssm" in text, script
        assert "nssm-2.24.zip" not in text and "Expand-Archive" not in text, script


# --- register-watchdog's profile ------------------------------------------------------------------

@pytest.fixture(scope="module")
def watchdog_profiles(run_ps, shell, tmp_path_factory):
    users = tmp_path_factory.mktemp("wd") / "Users"
    (users / "owner" / "ai-platform").mkdir(parents=True)
    (users / "admin").mkdir()
    elsewhere = users.parent / "D" / "projects" / "ai-platform"
    elsewhere.mkdir(parents=True)
    system = r"C:\Windows\System32\config\systemprofile"
    checkout = users / "owner" / "ai-platform"
    cases = {
        "user-shell": (str(users / "owner"), checkout, ""),
        "as-system": (system, checkout, ""),
        "as-system-elsewhere": (system, elsewhere, ""),
        "watchdog-user": (system, elsewhere, "owner"),
        "no-profile": ("", checkout, ""),
        # An elevated shell that is ANOTHER account's (an administrator's credentials at the
        # prompt), over the owner's checkout: two candidate users, so neither is taken.
        "admin-over-owner": (str(users / "admin"), checkout, ""),
        "admin-over-owner-named": (str(users / "admin"), checkout, "owner"),
        "admin-elsewhere": (str(users / "admin"), elsewhere, ""),
        # The owner's own shell, its USERPROFILE spelled in another case and with a trailing
        # separator: the same folder, so no second user.
        "owner-spelled-otherwise": (str(users / "OWNER") + "\\", checkout, ""),
    }
    body = f"$ErrorActionPreference = 'Stop'\n. {q(INSTALLER / 'lib-runtime.ps1')}\n$out = [ordered]@{{}}\n"
    for name, (prof, root, user) in cases.items():
        body += (f"$out['{name}'] = Resolve-WatchdogProfile -EnvProfile {q(prof)} -Root {q(root)} "
                 f"-WatchdogUser {q(user)} -ProfilesDir {q(users)}\n")
    body += "$out | ConvertTo-Json -Compress\n"
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1]), users


def test_the_watchdog_never_gets_systems_profile(watchdog_profiles):
    got, users = watchdog_profiles
    owner = str(users / "owner")
    assert got["user-shell"]["Profile"] == owner, got
    assert got["as-system"]["Profile"] == owner and "service account" in got["as-system"]["Why"], got
    assert got["no-profile"]["Profile"] == owner, got
    assert got["watchdog-user"]["Profile"] == owner and got["watchdog-user"]["Why"] == "WATCHDOG_USER", got
    p = got["as-system-elsewhere"]
    assert p["Profile"] == "" and "WATCHDOG_USER" in p["Problem"], p
    for name, v in got.items():
        assert "systemprofile" not in v["Profile"], (name, v)


def test_an_elevated_admins_profile_is_not_taken_over_another_users_checkout(watchdog_profiles):
    """Ops-e follow-up (2026-10-15): run from an elevated shell that is an administrator's, the
    registrar baked that administrator's USERPROFILE into the watchdog, whose podman then found no
    machine. Over another user's checkout it refuses, naming both folders and what to pass."""
    got, users = watchdog_profiles
    p = got["admin-over-owner"]
    assert p["Profile"] == "" and "WATCHDOG_USER" in p["Problem"], p
    assert str(users / "admin") in p["Problem"] and str(users / "owner") in p["Problem"], p
    assert "-WatchdogUser owner" in p["Problem"], p
    named = got["admin-over-owner-named"]
    assert named["Profile"] == str(users / "owner") and named["Why"] == "WATCHDOG_USER", named
    # Unchanged: a checkout outside every profile folder takes the caller's own profile.
    assert got["admin-elsewhere"] == {"Profile": str(users / "admin"), "Why": "USERPROFILE", "Problem": ""}, got
    same = got["owner-spelled-otherwise"]
    assert same["Problem"] == "" and same["Why"] == "USERPROFILE", same


def test_register_watchdog_takes_its_profile_from_the_resolver_and_refuses_on_a_problem():
    text = (INSTALLER / "register-watchdog.ps1").read_text(encoding="utf-8-sig")
    assert "$prof = Resolve-WatchdogProfile -EnvProfile $env:USERPROFILE -Root $Root -WatchdogUser $WatchdogUser" in text
    assert "param([string]$WatchdogUser = $env:WATCHDOG_USER)" in text
    assert "$UserProfile = $prof.Profile" in text
    # The refusal itself (FAILED in the log, the console line, exit 1): test_register_watchdog_exit.py.
    assert re.search(r"if \(\$prof\.Problem\) \{ Stop-Registration \$prof\.Problem \}", text), "no refusal"
    assert not re.search(r"\$UserProfile\s*=\s*if \(\$env:USERPROFILE\)", text), "the old default is back"


# --- dead code the audit named ---------------------------------------------------------------------

PRUNE = {".git", "node_modules", "__pycache__", "dist", ".venv", ".venv-test", ".test-logs"}


def _own_checkout() -> bool:
    """REPO is its own git top level. CI's dry run checks the public snapshot as a built
    directory, which is no checkout (git there exits 128, and this check used to die of it)."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO


def _tracked(*dirs: str) -> list[Path]:
    """Every tracked file under `dirs`: `git ls-files` in a checkout, a pruned walk in a built
    tree that is not its own git top level (the public snapshot), as test_no_get_event_loop
    reads it. -z, read as UTF-8, so a path with a non-ASCII character is not quoted past use."""
    if _own_checkout():
        out = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "--", *dirs],
                             capture_output=True, check=True, timeout=120).stdout
        return [REPO / p for p in out.decode("utf-8").split("\0") if p]
    found = []
    for d in dirs:
        for root, sub, files in os.walk(REPO / d):
            sub[:] = [s for s in sub if s not in PRUNE and not s.startswith(".venv")]
            found += [Path(root) / f for f in files]
    return found


def test_every_key_the_installer_writes_into_env_is_read_by_something():
    """CO_WORKER_INBOX_WIN was written beside CO_WORKER_INBOX_MOUNT and read by nothing."""
    keys = set(re.findall(r"Set-EnvValue -Path \$envFile -Key '([A-Z0-9_]+)'",
                          (INSTALLER / "install.ps1").read_text(encoding="utf-8")))
    assert {"PLATFORM_RUNTIME", "CO_WORKER_INBOX_MOUNT", "WINDOWS_HOST"} <= keys, keys
    readers = [p for p in _tracked("deploy", "apps", "rails", "services", "packages")
               if p.suffix in {".yml", ".ps1", ".py", ".sh", ".ts"} and p.name != "install.ps1"]
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in readers)
    unread = sorted(k for k in keys if k not in text)
    assert unread == [], f"written into deploy\\.env by install.ps1 and read nowhere: {unread}"


def test_a_tree_that_is_no_checkout_is_read_by_a_walk(tmp_path, monkeypatch):
    """The public snapshot as CI's dry run checks it: a plain directory, where git has no list."""
    for rel in ("deploy/docker-compose.yml", "rails/x/src/app.py", "rails/x/node_modules/y/z.py",
                "packages/p/.venv/Lib/site.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert not _own_checkout()
    got = sorted(p.relative_to(tmp_path).as_posix() for p in _tracked("deploy", "rails", "packages"))
    assert got == ["deploy/docker-compose.yml", "rails/x/src/app.py"], got


def test_the_walk_misses_no_file_git_tracks(monkeypatch):
    """Where both can run, the walk a snapshot gets finds every file git lists, so the snapshot
    reads at least what the private tree reads."""
    if not _own_checkout():
        pytest.skip("this tree is not its own git checkout (CI's dry run of the public snapshot), "
                    "so there is no git list to hold the walk to; this runs in the private repo")
    dirs = ("deploy", "apps", "rails", "services", "packages")
    listed = set(_tracked(*dirs))
    monkeypatch.setattr(sys.modules[__name__], "_own_checkout", lambda: False)
    walked = set(_tracked(*dirs))
    assert listed and sorted(p for p in listed - walked if p.exists()) == []


def _ast_json(script: Path, query: str):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("needs pwsh for the AST read")
    cmd = (f"$ast = [System.Management.Automation.Language.Parser]::ParseFile({q(script)}, [ref]$null, [ref]$null)\n"
           + query)
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1] or "[]")


DEAD_RUNTIME_CALLS = r"""
$runtime = @('podman', 'docker', 'docker-compose', 'wsl', 'wsl.exe')
$dead = @()
$assigns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $n.Left -is [System.Management.Automation.Language.VariableExpressionAst] }, $true)
foreach ($a in $assigns) {
  $calls = @($a.Right.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true) |
             Where-Object { $runtime -contains $_.GetCommandName() })
  if (-not $calls.Count) { continue }
  $name = $a.Left.VariablePath.UserPath
  $reads = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.VariableExpressionAst] -and
      $n.VariablePath.UserPath -eq $name -and $n -ne $a.Left }, $true))
  if (-not $reads.Count) { $dead += "$($a.Extent.StartLineNumber): $($a.Extent.Text)" }
}
ConvertTo-Json -InputObject @($dead) -Compress
"""


@pytest.mark.parametrize("script", ["smoke-test.ps1", "platform-startup.ps1", "install.ps1", "lib-runtime.ps1"])
def test_no_runtime_call_s_answer_is_thrown_away(script):
    """smoke-test ran `podman machine inspect` into a variable nothing read (a call to the machine
    for nothing, on every run)."""
    assert _ast_json(INSTALLER / script, DEAD_RUNTIME_CALLS) == []


# --- Caddy serves http://127.0.0.1 as it serves localhost -------------------------------------------

def _sites(caddyfile: Path) -> list[list[str]]:
    """Each site block's addresses, from the lines that open one."""
    out = []
    for line in caddyfile.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.endswith("{") and not line[:1].isspace() and not s.startswith("#") and s != "{":
            out.append([a.strip() for a in s[:-1].split(",") if a.strip()])
    return out


@pytest.mark.parametrize("caddyfile", ["deploy/Caddyfile", "deploy/installer/Caddyfile"])
def test_127_0_0_1_is_served_by_the_localhost_site(caddyfile):
    """Caddy picks a site by the Host header: http://127.0.0.1:1111 matched none, an empty 200."""
    blocks = _sites(REPO / caddyfile)
    local = [b for b in blocks if "http://localhost" in b]
    assert len(local) == 1 and "http://127.0.0.1" in local[0], blocks


# --- platform-startup refuses a full-stack box ---------------------------------------------------

STARTUP_STUBS = r"""
$ErrorActionPreference = 'Continue'
$global:calls = New-Object System.Collections.Generic.List[string]
function global:docker {
  $a = @(foreach ($x in $args) { foreach ($y in @($x)) { [string]$y } })
  $global:calls.Add('docker ' + ($a -join ' '))
  $global:LASTEXITCODE = 0
  if ($a[0] -eq 'inspect' -and $a[2] -eq '{{json .Config.Labels}}') { $global:labels; return }
  if ($a[0] -eq 'compose' -and ($a -contains 'config')) { '{"services": {}}'; return }
}
function global:Invoke-WebRequest { param($Uri, $TimeoutSec, [switch]$UseBasicParsing) }
function global:Start-Sleep { param($Seconds, $Milliseconds) }
"""


@pytest.fixture(scope="module")
def startup(run_ps, shell, tmp_path_factory):
    out = {}
    for stack, labels in (("full", "C:\\r\\deploy\\docker-compose.yml"),
                          ("lean", "C:\\r\\deploy\\installer\\docker-compose.installer.yml")):
        root = tmp_path_factory.mktemp(f"startup-{stack}") / "repo"
        inst = root / "deploy" / "installer"
        inst.mkdir(parents=True)
        for f in ("platform-startup.ps1", "lib-runtime.ps1"):
            shutil.copy2(INSTALLER / f, inst / f)
        (root / "deploy" / ".env").write_text("PLATFORM_ENABLED_APPS=terminal-fun\n", encoding="utf-8")
        (root / "deploy" / "logs").mkdir()
        (root / "deploy" / "logs" / "startup.log").write_bytes(b"z" * (11 * 1024 * 1024))
        lab = json.dumps({"com.docker.compose.project.config_files": labels})
        prof = root.parent / "profile"
        (prof / "AppData" / "Local" / "Temp").mkdir(parents=True)
        body = STARTUP_STUBS + f"""
$env:USERPROFILE = {q(prof)}
$global:labels = {q(lab)}
& {q(inst / 'platform-startup.ps1')} -RuntimeMode desktop *>&1 | Out-Null
Write-Output "EXIT=$LASTEXITCODE"
Write-Output ("CALLS=" + ($global:calls -join ' | '))
"""
        r = run_ps(shell, body, timeout=180)
        assert r.returncode == 0, r.stdout + r.stderr
        log = root / "deploy" / "logs" / "startup.log"
        out[stack] = {"exit": int(r.stdout.split("EXIT=", 1)[1].split()[0]),
                      "calls": r.stdout.split("CALLS=", 1)[1].strip().split(" | "),
                      "log": log.read_text(encoding="utf-8-sig") if log.exists() else "",
                      "rotated": [p.name for p in (root / "deploy" / "logs").glob("startup-*.log")]}
    return out


def test_startup_does_nothing_on_a_full_stack_box(startup):
    s = startup["full"]
    assert s["exit"] == 2 and "runs the FULL stack" in s["log"], s
    assert not [c for c in s["calls"] if c.startswith("docker compose") and c.endswith(" up -d")], s["calls"]


def test_startup_brings_a_lean_box_up_as_before(startup):
    s = startup["lean"]
    assert s["exit"] == 0, s
    assert [c for c in s["calls"] if c.startswith("docker compose") and c.endswith(" up -d")], s["calls"]


def test_startup_rotates_its_log(startup):
    for s in startup.values():
        assert len(s["rotated"]) == 1, s["rotated"]                 # the 11 MB log moved aside
        assert len(s["log"]) < 1024 * 1024, len(s["log"])            # and this run wrote a fresh one


# --- the XTTS note ------------------------------------------------------------------------------

def test_no_one_says_xtts_re_downloads_under_the_service():
    """install-services sets TTS_HOME to the user's %LOCALAPPDATA%, which reuses the model already
    there; INSTRUCTIONS.md said Coqui XTTS 'has no cache env override' and re-downloads."""
    services = (REPO / "deploy" / "install-services.ps1").read_text(encoding="utf-8")
    assert '"TTS_HOME=$UserProfile\\AppData\\Local"' in services     # the setting the note is about
    instructions = (REPO / "deploy" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "no cache env override" not in instructions and "re-downloads once" not in instructions
    assert "TTS_HOME" in instructions
    assert "re-downloads XTTS" not in services
