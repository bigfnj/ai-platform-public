"""lib-runtime's Read-DotEnvFile reads deploy\\.env the way docker compose does, on both engines.

Every case is a measured compose answer: dotenv_compose_cases.json (shared with the Python readers,
tools/tests/test_dotenv_reader.py) and dotenv_compose_cases_extra.json (the rest of what the
PowerShell reader implements: comment edges, quotes across lines, escapes, the ${} forms, and the
files compose refuses). Values come back base64-encoded, so the console code page cannot touch them.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
INSTALLER = HERE.parents[1] / "deploy" / "installer"
FILES = ("dotenv_compose_cases.json", "dotenv_compose_cases_extra.json")


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _cases():
    out = []
    for f in FILES:
        doc = json.loads((HERE / f).read_text(encoding="utf-8"))
        for c in doc["cases"]:
            out.append((f, doc["environ"], c))
    return out


CASES = _cases()
IDS = [f"{f.split('_')[-1].split('.')[0]}:{c['name']}" for f, _, c in CASES]


@pytest.fixture(scope="module")
def answers(run_ps, shell, tmp_path_factory):
    d = tmp_path_factory.mktemp("dotenv")
    body = f"""
$ErrorActionPreference = 'Stop'
. {q(INSTALLER / 'lib-runtime.ps1')}
Get-ChildItem Env: | Where-Object {{ $_.Name -like 'A55_*' }} | ForEach-Object {{ Remove-Item "Env:$($_.Name)" }}
$all = @()
"""
    environ_set = set()
    for i, (_, environ, c) in enumerate(CASES):
        f = d / f"{i}.env"
        f.write_bytes(c["env"].encode("utf-8"))
        for k, v in environ.items():
            if (k, v) not in environ_set:
                body += f"$env:{k} = {q(v)}\n"
                environ_set.add((k, v))
        body += f"""
try {{
  $v = Get-DotEnvValue -Path {q(f)} -Key {q(c['key'])}
  if ($null -eq $v) {{ $all += @{{ s = 'unset' }} }}
  else {{ $all += @{{ s = 'value'; b = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($v)) }} }}
}} catch {{ $all += @{{ s = 'error'; m = "$_" }} }}
"""
    body += "ConvertTo-Json -InputObject $all -Depth 3 -Compress\n"
    r = run_ps(shell, body, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(got) == len(CASES), (len(got), len(CASES))
    return got


@pytest.mark.parametrize("idx", range(len(CASES)), ids=IDS)
def test_the_reader_gives_composes_answer(answers, idx):
    _, _, case = CASES[idx]
    got = answers[idx]
    if case.get("error"):
        assert got["s"] == "error", (case, got)
        assert "line" in got["m"], got                       # the message names where
        return
    assert got["s"] != "error", (case, got)
    if case["value"] is None:
        assert got["s"] == "unset", (case, got)
    else:
        assert got["s"] == "value", (case, got)
        assert base64.b64decode(got["b"]).decode("utf-8") == case["value"], case


_OWN_PARSER = re.compile(r"""(?:-match|-Pattern)\s+['"]\^\\s\*[A-Z][A-Z0-9_]*\\s\*=|StartsWith\("\$\w+="\)""")


def test_no_deploy_script_parses_env_lines_itself():
    """Every value a deploy script takes from deploy\\.env goes through Read-DotEnvFile. Each one-off
    parser that stood here disagreed with compose somewhere (the first line where compose takes the
    last, a kept comment, kept quotes, ANSI on 5.1), which is the defect pinned here. A
    `^\\s*NAME\\s*=` pattern or a StartsWith("$Key=") against the file is that regression coming
    back."""
    hits = []
    for path in sorted((HERE.parents[1] / "deploy").rglob("*.ps1")):
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.lstrip().startswith("#") and _OWN_PARSER.search(line):
                hits.append(f"{path.relative_to(HERE.parents[1])}:{n}: {line.strip()}")
    assert hits == [], hits


def test_the_case_files_cover_the_rules_the_audit_named():
    """The audit named four: trailing comments, quotes, last line wins, UTF-8. Each must have
    a case, so a file trimmed later cannot quietly stop asking."""
    names = " | ".join(c["name"] for _, _, c in CASES)
    for needle in ("inline comment after spaces", "double-quoted keeps a hash", "single-quoted keeps a hash",
                   "last line wins", "utf-8 value", "a BOM before the first key",
                   "an unquoted password with a dollar loses the tail"):
        assert needle in names, needle
