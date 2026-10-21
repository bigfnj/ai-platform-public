"""deploy\\.env writers leave the file byte-identical when nothing changes, and never re-encode it.

lib-runtime's Set-EnvValue read the file with a bare Get-Content, which Windows PowerShell 5.1
decodes as ANSI, and wrote it back as UTF-8: every lean-box logon (platform-startup sets
WINDOWS_HOST) re-encoded every non-ASCII byte once more. The audit measured 5,614 -> 46,425 bytes
in 11 calls, and a value holding an accented character mangled after one. activate-model-roles.ps1
did the same and also stacked a fresh header comment on every run. Driven here under BOTH engines
(5.1 is the one that broke), against a copy of the real lean template.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
NON_ASCII = "C:\\Users\\Zo\u00eb\\donn\u00e9es \u20ac"           # a path a lean box could really have
HEADER = "# --- model roles (activate-model-roles.ps1) ---"
# Every `NAME: ${NAME:-@role}` in the full compose file, which is where activate-model-roles reads
# its block from. Read here rather than named: the public snapshot withholds some rails, and with
# them their compose services, so a hard-coded rail's key (SERVICENOW_EMBED_MODEL, EDU_LLM_MODEL)
# failed there although the script was right.
_ROLE_VAR = re.compile(r"^\s+([A-Z][A-Z0-9_]*):\s*\$\{([A-Z][A-Z0-9_]*):-(@[a-z0-9][a-z0-9-]*)\}", re.M)
ROLE_VARS = {m.group(1): m.group(3)
             for m in _ROLE_VAR.finditer((REPO / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
             if m.group(1) == m.group(2)}
assert ROLE_VARS, "deploy/docker-compose.yml declares no ${NAME:-@role} variable, so these tests check nothing"
SEED_VAR = next(iter(ROLE_VARS))              # the first one, whichever rails this tree holds


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


@pytest.fixture(scope="module")
def runs(run_ps, shell, tmp_path_factory):
    base = tmp_path_factory.mktemp("envw")
    template = (INSTALLER / "env.lean.example").read_bytes()
    assert any(b > 127 for b in template), "the template must carry non-ASCII text for this test to mean anything"
    crlf = base / "crlf.env"
    crlf.write_bytes(template.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    lf = base / "lf.env"
    lf.write_bytes(template.replace(b"\r\n", b"\n"))
    bom = base / "bom.env"
    bom.write_bytes(b"\xef\xbb\xbfFIRST=1\r\n")
    # activate-model-roles.ps1, run for real with -EnvOnly from a scratch deploy\ folder.
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    shutil.copy2(REPO / "deploy" / "activate-model-roles.ps1", deploy / "activate-model-roles.ps1")
    shutil.copy2(REPO / "deploy" / "docker-compose.yml", deploy / "docker-compose.yml")   # the role vars' source
    shutil.copy2(INSTALLER / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    (deploy / ".env").write_bytes(("OWNER_NOTE=caf\u00e9 \u20ac\r\n" + SEED_VAR + "=mistral-small3:24b\r\n"
                                   "# reads \u2192 this line stays\r\n").encode("utf-8"))
    body = f"""
$ErrorActionPreference = 'Stop'
$LogFile = Join-Path {q(base)} 'log.txt'; $TempBase = {q(base)}; function Write-Log($m) {{ }}
. {q(INSTALLER / 'lib-runtime.ps1')}
function Hex($p) {{ [BitConverter]::ToString([IO.File]::ReadAllBytes($p)) }}
$out = [ordered]@{{}}
foreach ($f in {q(crlf)}, {q(lf)}) {{
  Set-EnvValue -Path $f -Key 'CO_WORKER_INBOX_WIN' -Value {q(NON_ASCII)}
  $snaps = @()
  for ($i = 0; $i -lt 10; $i++) {{ Set-EnvValue -Path $f -Key 'WINDOWS_HOST' -Value '192.168.127.254'; $snaps += Hex $f }}
  $out[[IO.Path]::GetFileName($f)] = @{{ distinct = @($snaps | Select-Object -Unique).Count; length = (Get-Item $f).Length }}
}}
Set-EnvValue -Path {q(bom)} -Key 'FIRST' -Value '1'
$act = @()
for ($i = 0; $i -lt 10; $i++) {{
  & {q(deploy / 'activate-model-roles.ps1')} -EnvOnly | Out-Null
  $act += Hex {q(deploy / '.env')}
}}
$out['activate'] = @{{ distinct = @($act | Select-Object -Unique).Count }}
$out | ConvertTo-Json -Depth 3 -Compress
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    got["files"] = {p.name: p.read_bytes() for p in (crlf, lf, bom)}
    got["template"] = template
    got["activate_env"] = (deploy / ".env").read_bytes()
    return got


def test_ten_set_env_value_calls_leave_the_file_byte_identical(runs):
    for name in ("crlf.env", "lf.env"):
        assert runs[name]["distinct"] == 1, (name, runs[name])     # all ten snapshots the same bytes


def test_non_ascii_survives_set_env_value(runs):
    text = runs["files"]["crlf.env"].decode("utf-8")               # strict: mojibake that is not UTF-8 raises
    assert f"CO_WORKER_INBOX_WIN={NON_ASCII}" in text.splitlines()
    for line in runs["template"].decode("utf-8").splitlines():
        if any(ord(c) > 127 for c in line):                         # every non-ASCII comment, untouched
            assert line.rstrip("\r") in text.splitlines(), line
    assert "\u00c3" not in text                                     # the mark of UTF-8 read as ANSI
    grew = len(runs["files"]["crlf.env"]) - len(runs["template"])
    assert grew < 200, grew                                         # two keys, not a re-encoded file


def test_the_files_own_line_ending_is_kept_and_a_bom_is_dropped(runs):
    assert b"\r\n" not in runs["files"]["lf.env"]
    assert runs["files"]["crlf.env"].count(b"\r\n") == runs["files"]["crlf.env"].count(b"\n")
    assert runs["files"]["bom.env"] == b"FIRST=1\r\n"


def test_the_role_block_is_every_role_variable_the_stack_declares(runs):
    """The hand-kept map stopped at 14 (no co-worker, smb, gemini-cx, openmaic, servicenow, no
    embedder) while the script claimed roles were live platform-wide. It is read from compose now."""
    lines = runs["activate_env"].decode("utf-8").splitlines()
    block = lines[lines.index(HEADER) + 1:]
    got = dict(ln.split("=", 1) for ln in block)
    assert got == ROLE_VARS, (sorted(set(ROLE_VARS) ^ set(got)))


def test_ten_role_activations_leave_deploy_env_byte_identical(runs):
    assert runs["activate"]["distinct"] == 1, runs["activate"]
    lines = runs["activate_env"].decode("utf-8").splitlines()
    assert lines.count(HEADER) == 1, lines                         # one header, not one per run
    assert "OWNER_NOTE=caf\u00e9 \u20ac" in lines and "# reads \u2192 this line stays" in lines
    assert f"{SEED_VAR}={ROLE_VARS[SEED_VAR]}" in lines and f"{SEED_VAR}=mistral-small3:24b" not in lines
