"""WORKSTATION_START_DIR — the folder a preset session opens in.

Why it matters beyond taste: Claude Code asks whether it may trust the folder it opens,
and the default answer on that prompt is "No, exit". Land in the SSH user's home and one
Enter quits the session before the user has typed anything, which is indistinguishable
from a terminal that ignores keystrokes. So the dir has to reach the preset command, and
it has to reach the published VS Code launcher as a HOST path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from workstation_app.config import WorkstationSettings

WORK = "D:\\.ai-work"


def _settings(**over: object) -> WorkstationSettings:
    return WorkstationSettings(**over)  # type: ignore[arg-type]


def test_unset_start_dir_leaves_commands_untouched():
    """Default stays the old behaviour: run the bare program, land wherever the target's
    login shell lands. A deployment that never sets this must see no change at all."""
    s = _settings(start_dir="")
    assert s.preset_command("claude") == "claude"
    assert s.preset_command("codex") == "codex"


def test_start_dir_prefixes_every_preset_command():
    s = _settings(start_dir=WORK)
    assert s.preset_command("claude") == "cd 'D:\\.ai-work'; claude"
    assert s.preset_command("codex") == "cd 'D:\\.ai-work'; codex"


def test_interactive_shell_preset_is_never_wrapped():
    """An empty command means asyncssh runs NO command and the target opens a login
    shell. Prefixing it would silently downgrade that to a non-login shell, so the shell
    preset keeps returning None whether or not a start dir is configured."""
    assert _settings(start_dir=WORK).preset_command("shell") is None
    assert _settings(start_dir="").preset_command("shell") is None


def test_unknown_preset_still_resolves_to_none():
    assert _settings(start_dir=WORK).preset_command("no-such-preset") is None


def test_start_dir_is_stripped_before_use():
    assert _settings(start_dir=f"  {WORK}  ").preset_command("claude") == "cd 'D:\\.ai-work'; claude"


def test_a_quote_in_start_dir_is_refused_at_startup():
    """The dir is interpolated into a single-quoted shell word, so a quote inside it would
    close that word and run the remainder as a command. Refuse the setting rather than
    escape it per shell dialect — and refuse it at construction, so a bad value cannot sit
    in the config until the first session tries to use it."""
    with pytest.raises(ValidationError):
        _settings(start_dir="D:\\x'; calc.exe; echo '")


def test_published_vscode_folder_comes_from_start_dir():
    """The folder VS Code opens is resolved BY THE HOST. It used to be str(REPO_ROOT),
    this backend's own view of the tree, which is /app inside the container — so the
    launcher shipped a container path to a Windows RemoteApp."""
    app = _settings(start_dir=WORK).rdp_app("vscode")
    assert app is not None
    assert app["args"] == WORK
    assert not app["args"].startswith("/app")


def test_published_vscode_folder_is_empty_without_a_start_dir():
    """No configured folder must mean no folder directive, not a wrong one."""
    app = _settings(start_dir="").rdp_app("vscode")
    assert app is not None
    assert app["args"] == ""


def test_rdp_apps_json_override_still_wins():
    """The wholesale override is the escape hatch; start_dir must not reach back into it."""
    s = _settings(start_dir=WORK,
                  rdp_apps_json='[{"id":"np","label":"Notepad","icon":"N","alias":"notepad"}]')
    assert s.rdp_app("vscode") is None
    assert s.rdp_app("np") == {"id": "np", "label": "Notepad", "icon": "N", "alias": "notepad"}
