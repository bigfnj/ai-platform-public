"""Every settings write in the broker survives a file filter holding the new file for a moment.

Each writer puts a temp file beside its target and renames it over the target, and on Windows
that rename raised ``PermissionError [WinError 5]`` now and then: an endpoint-security file filter
holds a freshly written file open briefly to scan it. Seen 2026-10-13 as a repoint
(test_cli_routing) and a token mint (test_tokens_store) failing under load and passing alone; in
production each is a 500 for a write that a moment later would have landed. `app/fsutil.py`
retries the replace, briefly, on Windows only.

No real filter is needed: `os.replace` is made to refuse the first calls, which is exactly what
the filter does. Patched on the `os` module itself, so a writer that went back to a bare
`Path.replace` (which calls `os.replace`) meets the refusal too, with no retry to absorb it.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import fsutil
from app.config import BrokerSettings, CliRolesFileError, SettingsWriteError

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture()
def waits(monkeypatch):
    """The backoff's sleeps, recorded instead of slept."""
    slept: list[float] = []
    monkeypatch.setattr(fsutil, "time", SimpleNamespace(sleep=slept.append))
    return slept


@pytest.fixture()
def held(monkeypatch):
    """Make `os.replace` onto a target refuse its first `times` calls, as a scanning filter
    does. Returns the list of every call made onto that target, refused ones included."""
    real = os.replace

    def install(target: Path, times: int, exc: type[OSError] = PermissionError) -> list[str]:
        calls: list[str] = []

        def flaky(src, dst, *a, **k):
            if Path(dst) == target:
                calls.append(str(src))
                if len(calls) <= times:
                    raise exc(13, "Access is denied", str(dst))
            return real(src, dst, *a, **k)

        monkeypatch.setattr(os, "replace", flaky)
        return calls
    return install


def _on(monkeypatch, platform: str) -> None:
    monkeypatch.setattr(fsutil, "sys", SimpleNamespace(platform=platform))


def _pair(tmp_path) -> tuple[Path, Path]:
    src, dst = tmp_path / "store.json.tmp", tmp_path / "store.json"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    return src, dst


# --- the helper ---------------------------------------------------------------------------

def test_a_replace_held_twice_lands_on_the_third_try(tmp_path, monkeypatch, held, waits):
    _on(monkeypatch, "win32")
    src, dst = _pair(tmp_path)
    calls = held(dst, times=2)
    fsutil.replace_with_retry(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()
    assert len(calls) == 3
    assert waits == [0.02, 0.04], "the backoff does not start short and double"


def test_off_windows_the_first_permission_error_is_raised(tmp_path, monkeypatch, held, waits):
    """Anywhere else a PermissionError from a rename is a real permission problem: waiting would
    only delay the same answer, so it is raised at once and the target is left as it was."""
    _on(monkeypatch, "linux")
    src, dst = _pair(tmp_path)
    calls = held(dst, times=1)
    with pytest.raises(PermissionError):
        fsutil.replace_with_retry(src, dst)
    assert len(calls) == 1 and waits == []
    assert dst.read_text(encoding="utf-8") == "old"


def test_the_retries_are_bounded_and_the_last_error_is_raised(tmp_path, monkeypatch, held, waits):
    """A file that never comes free still fails, inside a second, while the caller waits."""
    _on(monkeypatch, "win32")
    src, dst = _pair(tmp_path)
    calls = held(dst, times=1000)
    with pytest.raises(PermissionError):
        fsutil.replace_with_retry(src, dst)
    assert len(calls) == 6, f"{len(calls)} attempts, expected the first and five retries"
    assert waits == [0.02, 0.04, 0.08, 0.16, 0.32] and sum(waits) < 1.0
    assert dst.read_text(encoding="utf-8") == "old"


def test_only_a_permission_error_is_retried(tmp_path, monkeypatch, held, waits):
    """A missing temp file or a full disk is not transient, and every caller turns an OSError
    into its own refusal: retrying those would only make the refusal slower."""
    _on(monkeypatch, "win32")
    src, dst = _pair(tmp_path)
    calls = held(dst, times=1, exc=FileNotFoundError)
    with pytest.raises(FileNotFoundError):
        fsutil.replace_with_retry(src, dst)
    assert len(calls) == 1 and waits == []


def test_the_override_pins_the_retry_either_way(tmp_path, monkeypatch, held, waits):
    _on(monkeypatch, "win32")
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", False)
    src, dst = _pair(tmp_path)
    held(dst, times=1)
    with pytest.raises(PermissionError):
        fsutil.replace_with_retry(src, dst)
    _on(monkeypatch, "linux")
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", True)
    calls = held(dst, times=1)
    fsutil.replace_with_retry(src, dst)
    assert len(calls) == 2 and dst.read_text(encoding="utf-8") == "new"


# --- every writer uses it -------------------------------------------------------------------

def _roles(tmp_path):
    s = BrokerSettings(roles_file=str(tmp_path / "roles.json"),
                       upstreams_file=str(tmp_path / "upstreams.json"))
    return (tmp_path / "roles.json", lambda: s.set_role("chat", "qwen3:8b"),
            lambda: s.roles()["chat"] == "qwen3:8b")


def _cli_switch(tmp_path):
    p = tmp_path / "cli-roles.json"
    s = BrokerSettings(roles_file=str(tmp_path / "roles.json"), cli_roles="chat,edu",
                       cli_roles_file=str(p))
    return (p, lambda: s.set_cli_role("chat", False),
            lambda: json.loads(p.read_text(encoding="utf-8"))["chat"] is False)


def _tokens(tmp_path):
    s = BrokerSettings(tokens_file=str(tmp_path / "tokens.json"))
    return (tmp_path / "tokens.json", lambda: s.add_token("studio", "inference"),
            lambda: [r["label"] for r in s.tokens()] == ["studio"])


def _disabled(tmp_path):
    s = BrokerSettings(disabled_file=str(tmp_path / "disabled.json"))
    return (tmp_path / "disabled.json", lambda: s.set_disabled(["gemma3:4b"]),
            lambda: s.disabled() == {"gemma3:4b"})


@pytest.mark.parametrize("writer", [_roles, _cli_switch, _tokens, _disabled],
                         ids=["roles", "cli-switch", "tokens", "disabled"])
def test_every_settings_writer_survives_a_held_file(tmp_path, monkeypatch, held, waits, writer):
    """The repoint behind PUT /v1/roles, the Claude switch, a token mint, and the disabled set:
    each lands although its first two replaces are refused. Before, the first refusal was a
    PermissionError out of the route (a 500), or for the switch a 409 saying the file could not
    be written."""
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", True)
    target, write, landed = writer(tmp_path)
    calls = held(target, times=2)
    try:
        write()
    except (PermissionError, CliRolesFileError, SettingsWriteError) as exc:
        pytest.fail(f"a held file failed the write instead of being retried: {exc!r}")
    assert landed(), "the write did not land"
    assert len(calls) == 3 and waits == [0.02, 0.04]


# --- a file held past the retries is a refusal, and leaves no temp file ----------------------
#
# The retries are bounded (test_the_retries_are_bounded_and_the_last_error_is_raised), so a file
# held for longer still fails. set_role, set_disabled, add_token and revoke_token let that
# PermissionError out: a 500 from PUT /v1/roles, PUT /v1/disabled, POST and DELETE /v1/tokens,
# and `<name>.tmp` left beside the file. Now each is a SettingsWriteError naming the file (the
# routes' 409) and the temp file is gone; the Claude switch's CliRolesFileError removes its own.

def _revoke(tmp_path):
    s = BrokerSettings(tokens_file=str(tmp_path / "tokens.json"))
    _plain, row = s.add_token("studio", "inference")          # minted before the file is held
    return (tmp_path / "tokens.json", lambda: s.revoke_token(row["id"]),
            lambda: [r["label"] for r in s.tokens()] == [])


#: writer -> (its setup, the refusal it raises).
REFUSALS = {"roles": (_roles, SettingsWriteError), "tokens": (_tokens, SettingsWriteError),
            "revoke": (_revoke, SettingsWriteError), "disabled": (_disabled, SettingsWriteError),
            "cli-switch": (_cli_switch, CliRolesFileError)}


@pytest.mark.parametrize("writer", sorted(REFUSALS))
def test_a_file_held_past_the_retries_is_a_refusal_naming_it_and_leaves_no_temp_file(
        tmp_path, monkeypatch, held, waits, writer):
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", True)
    setup, refusal = REFUSALS[writer]
    target, write, _landed = setup(tmp_path)
    before = target.read_bytes() if target.exists() else None
    held(target, times=1000)
    with pytest.raises(refusal) as exc:
        write()
    assert "could not be written" in str(exc.value)
    if refusal is SettingsWriteError:
        assert target.name in str(exc.value) and str(tmp_path) not in str(exc.value), \
            "the refusal names the file and never its path"
    assert sorted(p.name for p in tmp_path.glob("*.tmp")) == [], "the temp file was left behind"
    assert (target.read_bytes() if target.exists() else None) == before, "the file changed"


def test_a_failed_mint_authenticates_nothing(tmp_path, monkeypatch, held, waits):
    """add_token appends its row to the list tokens() returned and then writes. Refused, the row
    must not be in what the next request reads, which a list shared with the cache would be."""
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", True)
    s = BrokerSettings(tokens_file=str(tmp_path / "tokens.json"))
    s.add_token("seed", "inference")
    held(tmp_path / "tokens.json", times=1000)
    with pytest.raises(SettingsWriteError):
        s.add_token("doomed", "full")
    assert [r["label"] for r in s.tokens()] == ["seed"]


@pytest.fixture()
def gate_app(tmp_path, monkeypatch):
    """The real app on a broker whose settings files live in tmp_path, the shared secret set."""
    from app.main import app
    settings = BrokerSettings(roles_file=str(tmp_path / "roles.json"),
                              disabled_file=str(tmp_path / "disabled.json"),
                              tokens_file=str(tmp_path / "tokens.json"),
                              upstreams_file=str(tmp_path / "upstreams.json"),
                              auth_token="secret")
    monkeypatch.setattr(app.state, "settings", settings, raising=False)
    monkeypatch.setattr(app.state, "broker", SimpleNamespace(settings=settings), raising=False)
    monkeypatch.setattr(app.state, "token_seen", {}, raising=False)
    monkeypatch.setattr(fsutil, "RETRY_ON_PERMISSION_ERROR", True)
    return TestClient(app, raise_server_exceptions=False,
                      headers={"authorization": "Bearer secret"}), settings


@pytest.mark.parametrize("route", ["PUT /v1/roles", "PUT /v1/disabled", "POST /v1/tokens",
                                   "DELETE /v1/tokens"])
def test_each_settings_route_answers_a_held_file_with_a_409(tmp_path, gate_app, held, waits,
                                                            route):
    client, settings = gate_app
    token_id = settings.add_token("studio", "inference")[1]["id"]   # for the revoke
    target, call = {
        "PUT /v1/roles": ("roles.json",
                          lambda: client.put("/v1/roles/chat", json={"model": "qwen3:8b"})),
        "PUT /v1/disabled": ("disabled.json",
                             lambda: client.put("/v1/disabled", json={"names": ["qwen3:8b"]})),
        "POST /v1/tokens": ("tokens.json",
                            lambda: client.post("/v1/tokens", json={"label": "laptop"})),
        "DELETE /v1/tokens": ("tokens.json", lambda: client.delete(f"/v1/tokens/{token_id}")),
    }[route]
    held(tmp_path / target, times=1000)
    r = call()
    assert r.status_code == 409, r.text
    assert target in r.json()["detail"] and "could not be written" in r.json()["detail"]
    assert sorted(p.name for p in tmp_path.glob("*.tmp")) == []
    assert [t["label"] for t in settings.tokens()] == ["studio"], "a token was minted or revoked"


def test_no_writer_in_the_broker_renames_a_file_any_other_way():
    """The parametrized test above covers the writers there are. This keeps the next one from
    calling `os.replace`, `os.rename` or `Path.replace/rename` directly and meeting the filter
    with no retry. A one-argument `.replace(x)` is a Path rename: str.replace takes two."""
    found = []
    for py in sorted(APP.glob("*.py")):
        if py.name == "fsutil.py":
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"), filename=str(py))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("replace", "rename")):
                continue
            on_os = isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
            if on_os or (len(node.args) == 1 and not node.keywords):
                found.append(f"{py.name}:{node.lineno}")
    assert found == [], f"rename outside app/fsutil.py's replace_with_retry: {found}"
