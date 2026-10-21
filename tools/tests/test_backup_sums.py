"""The backups' SHA256SUMS.txt is what GNU `sha256sum -c` reads on any machine.

RESTORE.md promises `sha256sum -c SHA256SUMS.txt` works with no key and none of this code. Both
backup scripts wrote the file with Set-Content: CRLF line ends (and a BOM under 5.1), and
backup-voice also wrote BACKSLASH paths and uppercase hex. GNU sha256sum then failed every entry.
lib-runtime's Write-Sha256Sums writes LF, no BOM, forward slashes, lowercase; checked here under
both engines byte by byte and with the real GNU sha256sum, and backup-platform's own -Verify is
run against the new file, a file in the old format (old backups must still verify) and a tampered one.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "deploy" / "installer"
BACKUP = REPO / "deploy" / "backup-platform.ps1"
FILES = {"a.bin": b"alpha\n", "sub/b one.bin": b"beta", "sub/deeper/café.bin": b"\x00\x01\x02",
         "RESTORE.md": b"# not listed\n"}


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _sha256sum() -> str | None:
    found = shutil.which("sha256sum")
    if found or sys.platform != "win32":
        return found
    git = shutil.which("git")            # Git for Windows ships GNU coreutils under usr\bin
    for anc in (list(Path(git).resolve().parents)[:3] if git else []):
        cand = anc / "usr" / "bin" / "sha256sum.exe"
        if cand.is_file():
            return str(cand)
    return None


def make_tree(d: Path) -> Path:
    for rel, data in FILES.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return d


@pytest.fixture(scope="module")
def sums(run_ps, shell, tmp_path_factory):
    new = make_tree(tmp_path_factory.mktemp("new"))
    old = make_tree(tmp_path_factory.mktemp("old"))
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
$n = Write-Sha256Sums -Dir {q(new)} -Exclude @('SHA256SUMS.txt', 'RESTORE.md')
# The old writer, verbatim, for the control: GNU sha256sum must reject what it produced.
$dest = {q(old)}
Get-ChildItem $dest -Recurse -File | Where-Object {{ $_.Name -notin @('SHA256SUMS.txt', 'RESTORE.md') }} |
  Sort-Object FullName | ForEach-Object {{
    '{{0}}  {{1}}' -f (Get-Sha256Hex -Path $_.FullName), ($_.FullName.Substring($dest.Length + 1) -replace '\\\\', '/')
  }} | Set-Content (Join-Path $dest 'SHA256SUMS.txt') -Encoding utf8
"N=$n"
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    return {"new": new, "old": old, "count": r.stdout.strip().splitlines()[-1]}


def test_the_file_is_lf_without_a_bom_with_forward_slashes_and_right_hashes(sums):
    raw = (sums["new"] / "SHA256SUMS.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r" not in raw and raw.endswith(b"\n")
    lines = raw.decode("utf-8").splitlines()
    want = {rel: hashlib.sha256(data).hexdigest() for rel, data in FILES.items() if rel != "RESTORE.md"}
    got = {}
    for ln in lines:
        h, rel = ln.split("  ", 1)
        assert h == h.lower() and len(h) == 64 and "\\" not in rel, ln
        got[rel] = h
    assert got == want
    assert sums["count"] == f"N={len(want)}"


@pytest.mark.skipif(_sha256sum() is None, reason="needs GNU sha256sum (coreutils; Git for Windows ships one)")
def test_gnu_sha256sum_checks_the_new_file_and_rejected_the_old_one(sums):
    exe = _sha256sum()
    ok = subprocess.run([exe, "-c", "SHA256SUMS.txt"], cwd=sums["new"], capture_output=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ok.stdout.count(b": OK") == 3, ok.stdout
    # Control: the format the scripts used to write fails, so the check above can fail at all.
    bad = subprocess.run([exe, "-c", "SHA256SUMS.txt"], cwd=sums["old"], capture_output=True)
    assert bad.returncode != 0, bad.stdout + bad.stderr


@pytest.fixture(scope="module")
def verify(run_ps, shell, tmp_path_factory):
    if not BACKUP.is_file():                  # withheld from the public snapshot: said, not hidden
        pytest.skip("the backup script is withheld from this tree")
    base = tmp_path_factory.mktemp("verify")
    deploy = base / "repo" / "deploy"
    (deploy / "installer").mkdir(parents=True)
    shutil.copy2(BACKUP, deploy / BACKUP.name)
    shutil.copy2(INSTALLER / "lib-runtime.ps1", deploy / "installer" / "lib-runtime.ps1")
    out = {}
    body = "$ErrorActionPreference = 'Continue'\n"
    for name in ("new", "legacy", "tampered"):
        target = base / name
        d = make_tree(target / "2026-10-08")
        out[name] = target
        if name == "legacy":
            body += f"""
$dest = {q(d)}
. {q(INSTALLER / 'lib-runtime.ps1')}
Get-ChildItem $dest -Recurse -File | Where-Object {{ $_.Name -notin @('SHA256SUMS.txt', 'RESTORE.md') }} |
  Sort-Object FullName | ForEach-Object {{
    '{{0}}  {{1}}' -f (Get-Sha256Hex -Path $_.FullName), ($_.FullName.Substring($dest.Length + 1) -replace '\\\\', '/')
  }} | Set-Content (Join-Path $dest 'SHA256SUMS.txt') -Encoding utf8
"""
        else:
            body += f"""
& {{ . {q(INSTALLER / 'lib-runtime.ps1')}; $null = Write-Sha256Sums -Dir {q(d)} -Exclude @('SHA256SUMS.txt', 'RESTORE.md') }}
"""
            if name == "tampered":
                body += f"[IO.File]::WriteAllBytes({q(d / 'a.bin')}, [byte[]](1,2,3))\n"
        body += f"""
Write-Output '<<<{name}>>>'
try {{ & {q(deploy / BACKUP.name)} -Verify -Target {q(target)} *>&1 | ForEach-Object {{ [string]$_ }} }}
catch {{ Write-Output "THROWN: $_" }}
"""
    r = run_ps(shell, body)
    assert r.returncode == 0, r.stdout + r.stderr
    res = {}
    for chunk in r.stdout.split("<<<")[1:]:
        name, _, text = chunk.partition(">>>")
        res[name] = text
    return res


def test_verify_accepts_the_new_file_and_an_old_backups_file(verify):
    for name in ("new", "legacy"):
        # three MATCH lines, the non-ASCII name among them: under 5.1 a BOM-less file read without
        # -Encoding UTF8 garbles that name and reports it MISSING
        assert "OK - 3 file(s) match SHA256SUMS.txt." in verify[name], verify[name]
        assert "MISSING" not in verify[name] and "THROWN" not in verify[name], verify[name]


def test_verify_still_fails_a_tampered_backup(verify):
    assert "MISMATCH" in verify["tampered"] and "THROWN: VERIFY FAILED" in verify["tampered"], verify["tampered"]


def test_no_script_writes_a_checksum_file_of_its_own():
    """Both backup scripts wrote SHA256SUMS.txt with Set-Content; Write-Sha256Sums is the one writer
    now. A Set-Content / Out-File / WriteAll naming the file anywhere else is that regression."""
    hits = []
    for path in sorted((REPO / "deploy").rglob("*.ps1")):
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            code = line.split(" #", 1)[0]
            if code.lstrip().startswith("#") or "SHA256SUMS" not in code:
                continue
            if any(w in code for w in ("Set-Content", "Out-File", "Add-Content", "WriteAll")):
                if not (path.name == "lib-runtime.ps1" and "Join-Path $root 'SHA256SUMS.txt'" in code):
                    hits.append(f"{path.relative_to(REPO)}:{n}: {code.strip()}")
    assert hits == [], hits
