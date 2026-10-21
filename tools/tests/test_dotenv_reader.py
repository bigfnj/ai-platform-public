"""deploy/.env read the way compose reads it (the Python readers).

Compose, the PowerShell scripts and the Python tools all read deploy/.env, and they disagreed:
`PLATFORM_DISABLED_APPS=servicenow  # comment` was `servicenow` to compose and the comment-
carrying string to rail_smoke, and with two lines compose took the last while the tools took the
first. So the rules are written down once, as cases measured against `docker compose config`
(tools/tests/dotenv_compose_cases.json), and every reader is held to the same table.
tools/deploy_snapshot.read_env is the Python reader; rail_smoke.declared() reads through it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_dotenv_deploy_snapshot", TOOLS / "deploy_snapshot.py")
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

TABLE = json.loads((Path(__file__).with_name("dotenv_compose_cases.json"))
                   .read_text(encoding="utf-8"))
CASES = TABLE["cases"]
# The rest of what compose measured (the PowerShell reader's table too): comment edges, quotes
# across lines, every escape, the ${} forms, `$$`, and the files compose refuses. read_env was held
# to the first table only and failed 22 of these; `pa$$word` read as `pa$`.
EXTRA = json.loads((Path(__file__).with_name("dotenv_compose_cases_extra.json"))
                   .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_read_env_gives_what_compose_gives(case, tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(case["env"].encode("utf-8"))
    assert ds.read_env(env, environ=TABLE["environ"]).get(case["key"]) == case["value"]


@pytest.mark.parametrize("case", EXTRA["cases"], ids=[c["name"] for c in EXTRA["cases"]])
def test_read_env_gives_what_compose_gives_in_the_extra_cases(case, tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(case["env"].encode("utf-8"))
    if case.get("error"):
        with pytest.raises(ds.DotEnvError, match=r"line \d+"):
            ds.read_env(env, environ=EXTRA["environ"])
        return
    assert ds.read_env(env, environ=EXTRA["environ"]).get(case["key"]) == case["value"]


def test_a_password_with_a_doubled_dollar_reads_as_compose_hands_it_over(tmp_path):
    """The audit's own example, in the shape a deploy\\.env holds it."""
    env = tmp_path / ".env"
    env.write_bytes(b"PLATFORM_ADMIN_PASSWORD=pa$$word\n")
    assert ds.read_env(env, environ={}) == {"PLATFORM_ADMIN_PASSWORD": "pa$word"}


def test_the_table_covers_the_rules_the_audit_named():
    """The defects the audit named must stay in the table, or the parity proves nothing
    about them."""
    names = {c["name"] for c in CASES}
    assert {"inline comment after spaces", "last line wins", "double-quoted keeps a hash",
            "export prefix", "crlf line ending", "utf-8 value", "indented key"} <= names


def test_a_utf8_bom_is_not_part_of_the_first_key(tmp_path):
    """Notepad and Set-Content -Encoding UTF8 on 5.1 write one."""
    env = tmp_path / ".env"
    env.write_bytes(b"\xef\xbb\xbfK=v\n")
    assert ds.read_env(env, environ={}) == {"K": "v"}


def _docker_compose() -> tuple[list[str] | None, str]:
    """(the argv that runs compose, or None, and why). docker's own compose first; else the
    other runtime's, podman here, whose `podman compose` hands the call to an external provider.
    Either counts only when it reports itself as Docker Compose, whose rules the table records:
    podman-compose, the other provider podman can use, reads .env by rules of its own."""
    why = []
    for cli in ("docker", "podman"):
        exe = shutil.which(cli)
        if exe is None:
            why.append(f"no {cli} CLI on PATH")
            continue
        try:
            v = subprocess.run([exe, "compose", "version"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            why.append(f"`{cli} compose version` did not run ({exc})")
            continue
        if v.returncode == 0 and "Docker Compose" in v.stdout:
            return [exe, "compose"], f"{cli} compose ({v.stdout.strip().splitlines()[-1]})"
        said = (v.stdout.strip() or v.stderr.strip() or f"exit {v.returncode}").splitlines()[-1]
        why.append(f"`{cli} compose` is not Docker Compose ({said})")
    return None, ("; ".join(why) + ". Install Docker Compose (docker's plugin, or docker-compose "
                  "as podman's provider) to re-measure the table here; the recorded measurement "
                  "still applies")


@pytest.fixture(scope="module")
def docker_compose() -> list[str]:
    argv, why = _docker_compose()
    if argv is None:
        pytest.skip(why)
    return argv


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_table_still_matches_live_compose(case, tmp_path, docker_compose):
    """The table is only as good as its last measurement, so wherever Docker Compose is
    installed, docker's or podman's, this repeats it (measured 2026-10-08 on a docker box: the
    whole table in about 7 s; `compose config` needs no running daemon or podman machine). It
    skipped on a podman box, which has no docker CLI. Through podman each case costs about
    1.4 s, measured 2026-10-14."""
    (tmp_path / "case.env").write_bytes(case["env"].encode("utf-8"))
    (tmp_path / "compose.yml").write_text(
        "services:\n  g:\n    image: alpine\n    environment:\n"
        f"      OUT: ${{{case['key']}-<unset>}}\n", encoding="utf-8")
    p = subprocess.run([*docker_compose, "--env-file", str(tmp_path / "case.env"), "-f",
                        str(tmp_path / "compose.yml"), "config", "--format", "json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       env={**os.environ, **TABLE["environ"]})
    assert p.returncode == 0, p.stderr
    got = json.loads(p.stdout)["services"]["g"]["environment"]["OUT"]
    assert (None if got == "<unset>" else got.replace("$$", "$")) == case["value"]
