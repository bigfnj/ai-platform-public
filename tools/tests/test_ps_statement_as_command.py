"""No PowerShell script in the tree uses a statement keyword as a command.

`(try { ... } catch { ... })` reads like an expression and is not one: `try` is a STATEMENT, and
inside parentheses (a pipeline) PowerShell parses it as a command named `try`. The script loads
fine and throws "The term 'try' is not recognized" only when that line runs, which for
lib-runtime's stale-proxy reap was only in the watchdog, only after the podman machine went
down. The same holds for every statement keyword; `$( ... )`, a subexpression, takes statements
and is the form that works. So every tracked script is parsed (the real PowerShell parser, one
pwsh process for the lot) and any command whose name is a statement keyword fails this test.

foreach and where are not on the list: both are real aliases (ForEach-Object, Where-Object), so
`... | foreach { }` is a command and is correct.

The same pass checks a second trap that also only shows when the script runs: a script's OWN
param() defaults must not read where the script lives ($PSScriptRoot, $PSCommandPath,
$MyInvocation). Windows PowerShell 5.1 started with -File leaves all three empty while an advanced
script's defaults are evaluated (one [CmdletBinding()] or [Parameter()] makes it advanced), so
`(Split-Path $PSScriptRoot -Parent)` refuses to bind and the script never starts, and
`"$PSScriptRoot\\shots"` quietly becomes `\\shots` at the drive root. pwsh 7 fills them in, so a run
under pwsh proves nothing. Resolve such a default in the script body (`if (-not $X) { ... }`). A
function's param() defaults are evaluated when the function is called, and are fine.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
KEYWORDS = ("try", "catch", "finally", "if", "elseif", "else", "switch", "while", "for", "do", "until", "trap")
# The private tree carries 30 scripts; the public snapshot withholds seven of them (the backup
# scripts, the tray tool, three withheld rails' scripts). Either floor is far above what a scan
# that silently found nothing would report.
MIN_SCRIPTS = 25 if (REPO / "CLAUDE.md").is_file() else 20
PRUNE = {".git", "node_modules", "__pycache__", "dist", ".test-logs"}

SCAN = r"""
param([Parameter(Mandatory)][string]$ListFile, [Parameter(Mandatory)][string]$Keywords)
$kw = $Keywords -split ','
$out = foreach ($f in @(Get-Content -LiteralPath $ListFile -Encoding UTF8 | Where-Object { $_ })) {
  $tokens = $null; $errors = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$tokens, [ref]$errors)
  $hits = @($ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] }, $true) |
    Where-Object { $n = $_.GetCommandName(); $n -and ($kw -contains $n) } |
    ForEach-Object { [ordered]@{ line = $_.Extent.StartLineNumber; name = $_.GetCommandName()
                                 text = ($_.Extent.Text -split "`r?`n")[0] } })
  # The script's OWN param block only ($ast.ParamBlock), never a function's.
  $own = @(if ($ast.ParamBlock) {
    foreach ($p in @($ast.ParamBlock.Parameters | Where-Object { $_.DefaultValue })) {
      $p.DefaultValue.FindAll({ param($a) $a -is [System.Management.Automation.Language.VariableExpressionAst] -and
          (@('PSScriptRoot', 'PSCommandPath', 'MyInvocation') -contains $a.VariablePath.UserPath) }, $true) |
        ForEach-Object { [ordered]@{ line = $_.Extent.StartLineNumber; name = $_.VariablePath.UserPath
                                     text = ($p.Extent.Text -split "`r?`n")[0] } }
    }
  })
  [ordered]@{ file = $f; hits = $hits; own = $own
              errors = @($errors | ForEach-Object { "$($_.Extent.StartLineNumber): $($_.Message)" }) }
}
ConvertTo-Json -InputObject @($out) -Depth 5 -Compress
"""


def _scripts() -> list[Path]:
    """Every tracked .ps1/.psm1: `git ls-files` in a checkout. The public snapshot is also checked
    as a built directory that is not a checkout (see test_live_deselection.py), so a tree that is
    not its own git top level is walked instead. `-z`, read as UTF-8: without it git quotes a
    path holding a non-ASCII character, and the quoted form names no file, so that script was
    skipped in silence."""
    try:
        top = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=60)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() == REPO:
            ls = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "*.ps1", "*.psm1"],
                                capture_output=True, text=True, encoding="utf-8", timeout=60,
                                check=True)
            return [REPO / p for p in ls.stdout.split("\0") if p.strip()]
    except (OSError, subprocess.SubprocessError):
        pass
    out = []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".venv")]
        out += [Path(root) / f for f in files if f.endswith((".ps1", ".psm1"))]
    return sorted(out)


def test_a_tracked_script_with_a_non_ascii_name_is_listed(tmp_path, monkeypatch):
    """git quotes such a path unless given -z (core.quotepath, forced on here whatever this box's
    git config says), and the quoted form names no file, so the scan never read the script."""
    repo = tmp_path.resolve()
    script = repo / "scripts" / "réglage.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("$x = (try { 1 } catch { 2 })\n", encoding="utf-8")
    for key, value in (("GIT_CONFIG_COUNT", "1"), ("GIT_CONFIG_KEY_0", "core.quotepath"),
                       ("GIT_CONFIG_VALUE_0", "true")):
        monkeypatch.setenv(key, value)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(repo), "add", "--", "scripts"], check=True, timeout=60)
    monkeypatch.setattr(sys.modules[__name__], "REPO", repo)
    found = _scripts()
    assert found == [script] and script.is_file(), found


@pytest.fixture(scope="module")
def scan(tmp_path_factory):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("needs pwsh (PowerShell 7) to parse the scripts")
    d = tmp_path_factory.mktemp("kwscan")
    # The positive controls: each defect's own shape, and the form that is correct.
    controls = {
        "bad": (d / "control-paren-try.ps1", "$x = (try { 1 } catch { 2 })\n"),
        "good": (d / "control-subexpr-try.ps1", "$x = $(try { 1 } catch { 2 })\n"),
        "own_bad": (d / "control-own-path-default.ps1",
                    "[CmdletBinding()]\nparam([string]$Dir = (Join-Path $PSScriptRoot 'x'),\n"
                    "      [string]$Me = $PSCommandPath)\n$Dir\n"),
        "own_good": (d / "control-own-path-body.ps1",
                     "[CmdletBinding()]\nparam([string]$Dir = '')\n"
                     "if (-not $Dir) { $Dir = Join-Path $PSScriptRoot 'x' }\n"
                     "function f { [CmdletBinding()] param([string]$F = $PSScriptRoot) $F }\n"),
    }
    for path, text in controls.values():
        path.write_text(text, encoding="utf-8")
    scripts = _scripts()
    listing = d / "files.txt"
    listing.write_text("\n".join(str(p) for p in scripts + [p for p, _ in controls.values()])
                       + "\n", encoding="utf-8")
    runner = d / "scan.ps1"
    runner.write_text(SCAN, encoding="utf-8-sig")
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-File", str(runner),
                        "-ListFile", str(listing), "-Keywords", ",".join(KEYWORDS)],
                       capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    got = {Path(e["file"]): e for e in json.loads(r.stdout.strip().splitlines()[-1])}
    return scripts, got, {k: path for k, (path, _text) in controls.items()}


def test_the_scan_flags_a_parenthesised_try_and_not_a_subexpression(scan):
    _, got, c = scan
    assert [h["name"] for h in got[c["bad"]]["hits"]] == ["try"], got[c["bad"]]
    assert got[c["good"]]["hits"] == [], got[c["good"]]


def test_the_scan_flags_a_script_path_in_a_param_default_and_not_in_the_body(scan):
    _, got, c = scan
    assert [h["name"] for h in got[c["own_bad"]]["own"]] == ["PSScriptRoot", "PSCommandPath"], \
        got[c["own_bad"]]
    assert got[c["own_good"]]["own"] == [], got[c["own_good"]]


def test_the_scan_read_every_script_and_enough_of_them(scan):
    scripts, got, _ = scan
    names = {p.relative_to(REPO).as_posix() for p in scripts}
    assert len(scripts) >= MIN_SCRIPTS, f"only {len(scripts)} scripts found: {sorted(names)}"
    assert "deploy/installer/lib-runtime.ps1" in names, sorted(names)
    assert set(scripts) <= set(got), f"not scanned: {sorted(map(str, set(scripts) - set(got)))}"
    unparsed = {p.relative_to(REPO).as_posix(): got[p]["errors"] for p in scripts if got[p]["errors"]}
    assert not unparsed, f"scripts that do not parse, so nothing in them was checked: {unparsed}"


def test_no_script_uses_a_statement_keyword_as_a_command(scan):
    scripts, got, _ = scan
    hits = [f"{p.relative_to(REPO).as_posix()}:{h['line']}: `{h['name']}` runs as a command: {h['text']}"
            for p in scripts for h in got[p]["hits"]]
    assert not hits, ("a statement keyword parsed as a command (inside parentheses, use $( ... ) or a "
                      "function instead): " + "; ".join(hits))


def test_no_script_reads_its_own_path_in_a_param_default(scan):
    scripts, got, _ = scan
    hits = [f"{p.relative_to(REPO).as_posix()}:{h['line']}: ${h['name']}: {h['text']}"
            for p in scripts for h in got[p]["own"]]
    assert not hits, ("a script's param() default reads its own location, which Windows PowerShell "
                      "5.1 -File leaves empty there (resolve it in the body instead): "
                      + "; ".join(hits))
