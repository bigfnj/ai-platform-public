"""RemoteApp launcher generation.

The .rdp is a newline-delimited list of `key:type:value` directives handed to the
client's RDP client, so the two things worth pinning are that the RemoteApp-mode
directives are all present (a missing one silently degrades to a full desktop, or the
client refuses outright) and that nothing can smuggle extra directives in through a
value.
"""

from __future__ import annotations

import pytest

from workstation_app.config import WorkstationSettings
from workstation_app.main import _build_rdp, _rdp_line


def _settings(**over: object) -> WorkstationSettings:
    base: dict[str, object] = {"rdp_host": "192.168.1.11", "rdp_username": "Admin"}
    base.update(over)
    return WorkstationSettings(**base)  # type: ignore[arg-type]


def _parse(rdp: str) -> dict[str, str]:
    out = {}
    for line in rdp.splitlines():
        if not line:
            continue
        key, _kind, value = line.split(":", 2)
        out[key] = value
    return out


def test_disabled_without_a_host():
    assert _settings(rdp_host="").rdp_enabled() is False
    assert _settings().rdp_enabled() is True


def test_unknown_app_id_is_not_resolved():
    # The route 404s on None rather than building a file from request input.
    assert _settings().rdp_app("../../etc/passwd") is None
    assert _settings().rdp_app("vscode") is not None


def test_remoteapp_mode_directives_present():
    s = _settings()
    fields = _parse(_build_rdp(s, s.rdp_app("vscode")))
    assert fields["remoteapplicationmode"] == "1"
    assert fields["remoteapplicationprogram"] == "||vscode"
    # Older clients read `alternate shell`; both carry the alias.
    assert fields["alternate shell"] == "||vscode"
    # Without this a non-RDS host (any Windows Pro box) is refused by the client.
    assert fields["disableremoteappcapscheck"] == "1"
    assert fields["full address"] == "192.168.1.11:3389"
    assert fields["username"] == "Admin"


def test_client_filesystem_is_not_redirected():
    s = _settings()
    fields = _parse(_build_rdp(s, s.rdp_app("vscode")))
    assert fields["drivestoredirect"] == ""
    assert fields["redirectprinters"] == "0"
    assert fields["redirectcomports"] == "0"
    assert fields["redirectsmartcards"] == "0"
    # Clipboard is the one redirection an editor actually needs, and it's togglable.
    assert fields["redirectclipboard"] == "1"
    off = _settings(rdp_redirect_clipboard=False)
    assert _parse(_build_rdp(off, off.rdp_app("vscode")))["redirectclipboard"] == "0"


def test_no_username_line_when_unset():
    s = _settings(rdp_username="")
    assert "username" not in _parse(_build_rdp(s, s.rdp_app("vscode")))


@pytest.mark.parametrize("hostile", ["a\r\nredirectclipboard:i:0", "a\nnew:i:1"])
def test_newlines_cannot_inject_directives(hostile: str):
    # A newline in any interpolated value would append a directive of the attacker's
    # choosing; they're flattened to spaces instead.
    assert "\n" not in _rdp_line("k", "s", hostile)
    assert "\r" not in _rdp_line("k", "s", hostile)
    s = _settings()
    app = dict(s.rdp_app("vscode"), args=hostile)
    fields = _parse(_build_rdp(s, app))
    assert fields["redirectclipboard"] == "1"
    assert "new" not in fields


def test_custom_app_list_via_json():
    s = _settings(rdp_apps_json='[{"id":"np","label":"Notepad","icon":"🗒","alias":"notepad"}]')
    assert s.rdp_app("vscode") is None
    fields = _parse(_build_rdp(s, s.rdp_app("np")))
    assert fields["remoteapplicationprogram"] == "||notepad"
    assert fields["remoteapplicationname"] == "Notepad"
    # No args configured => the directive is still emitted, empty.
    assert fields["remoteapplicationcmdline"] == ""
