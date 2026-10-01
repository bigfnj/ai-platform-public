"""Workstation backend config.

Extends the shared PlatformSettings but overrides the env prefix to WORKSTATION_
so the SSH-target knobs read clearly (WORKSTATION_SSH_HOST, ...). This app never
touches the broker/GPU; broker_url is inherited but unused.
"""

from __future__ import annotations

import json

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict

from platform_core import PlatformSettings

# How a preset command is made to start in `start_dir`. `cd '<dir>'; <cmd>` parses in
# PowerShell *and* in every POSIX shell, so one form covers a Windows or a Linux SSH
# target. Single quotes are literal in both, which is why a start_dir containing one is
# refused at startup rather than escaped per dialect.
START_DIR_TEMPLATE = "cd '{dir}'; {cmd}"

# The rail presets. `command` empty => an interactive login shell; otherwise the
# program to launch inside the PTY. Override wholesale with WORKSTATION_PRESETS_JSON.
DEFAULT_PRESETS: list[dict[str, str]] = [
    {"id": "shell", "label": "Shell", "icon": "🖥", "command": ""},
    {"id": "claude", "label": "Claude Code", "icon": "🤖", "command": "claude"},
    {"id": "codex", "label": "Codex", "icon": "◆", "command": "codex"},
]

# Desktop applications published from the host as RDP RemoteApps ("seamless" apps:
# the app's own window on your local desktop, no remote desktop around it). `alias`
# must match the key registered by deploy/install-remoteapp.ps1 under TSAppAllowList.
# `args` is the RemoteApp command line — for VS Code, the folder to open. Left empty
# here and filled from `start_dir` in rdp_apps(), because that folder is opened by VS
# Code ON THE HOST: it used to be str(REPO_ROOT), this backend's own view of the tree,
# which resolves to /app inside the container — so the published launcher shipped
# `remoteapplicationcmdline:s:/app`, a container path handed to a Windows RemoteApp.
# Override wholesale with WORKSTATION_RDP_APPS_JSON.
DEFAULT_RDP_APPS: list[dict[str, str]] = [
    {
        "id": "vscode",
        "label": "VS Code",
        "icon": "📝",
        "alias": "vscode",
        "args": "",
    },
]


class WorkstationSettings(PlatformSettings):
    model_config = SettingsConfigDict(
        env_prefix="WORKSTATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "workstation"
    host: str = "127.0.0.1"
    port: int = 8720

    # SSH target = the workstation's own sshd. host.docker.internal reaches the
    # Docker host (Windows OpenSSH) from inside the container; point at WSL/Kryptos
    # by IP instead.
    ssh_host: str = "host.docker.internal"
    ssh_port: int = 22
    ssh_user: str = ""
    ssh_key_path: str = ""  # private key the backend authenticates with

    # Host-key verification. Provide a known_hosts file (recommended). The insecure
    # toggle disables the check for first-run bring-up ONLY — do not leave it on.
    known_hosts_path: str = ""
    insecure_skip_host_key_check: bool = False

    term_type: str = "xterm-256color"

    # Where a preset session starts. Empty => wherever the target's login shell lands,
    # which on Windows OpenSSH is the SSH user's home. Set it and every preset that has
    # a command is prefixed with a `cd`, so `claude` / `codex` open the tree you actually
    # work in. That is not cosmetic: Claude Code asks whether it may trust the folder it
    # opens, the home directory is a folder you should NOT blanket-trust, and the default
    # answer on that prompt is "No, exit" — so landing at home means one Enter quits the
    # session before you have typed anything.
    start_dir: str = ""

    # Session limits (P1.3). Seconds; 0 disables that check. Close a session after
    # idle_secs with no I/O (neither keystrokes nor output — so an actively working
    # session stays alive), and cap any session at max_secs regardless.
    idle_secs: int = 900
    max_secs: int = 8 * 3600

    # Session audit trail (P2.1). A daily-rotating log that keeps audit_retention_days
    # files and auto-deletes older ones. Metadata only (who / when / preset / exit).
    audit_enabled: bool = True
    audit_dir: str = "/audit"
    audit_retention_days: int = 30

    # --- RemoteApp (published desktop apps over RDP) -------------------------
    # rdp_host is the address the CLIENT dials, not one this backend connects to —
    # the browser never speaks RDP, it just downloads a launcher file. So it must be
    # reachable from wherever you open the .rdp: a LAN IP, a mesh-VPN name
    # (Tailscale/WARP), or 127.0.0.1 when a local `cloudflared access rdp` listener
    # is forwarding. Empty disables the feature (it stays off until deliberately set).
    rdp_host: str = ""
    rdp_port: int = 3389
    # Prefilled into the .rdp so the client doesn't ask which account. Not a
    # credential: RDP still authenticates normally.
    rdp_username: str = ""
    # Redirect the client's clipboard into the session. Drives/printers stay off.
    rdp_redirect_clipboard: bool = True
    # Optional JSON override of DEFAULT_RDP_APPS.
    rdp_apps_json: str = ""

    # Optional JSON override of DEFAULT_PRESETS.
    presets_json: str = ""

    @field_validator("start_dir")
    @classmethod
    def _start_dir_has_no_quote(cls, value: str) -> str:
        """The directory is interpolated into a single-quoted shell word. A quote inside
        it would close that word and let the remainder run as a command, so refuse it at
        startup — loudly, once — rather than escape it differently per shell dialect."""
        if "'" in value:
            raise ValueError("WORKSTATION_START_DIR must not contain a single quote")
        return value

    def rdp_enabled(self) -> bool:
        return bool(self.rdp_host.strip()) and bool(self.rdp_apps())

    def rdp_apps(self) -> list[dict[str, str]]:
        if self.rdp_apps_json.strip():
            return json.loads(self.rdp_apps_json)
        # Fill the folder VS Code opens from start_dir (a HOST path — see DEFAULT_RDP_APPS).
        # No start_dir configured => no folder directive, and VS Code opens empty.
        return [{**a, "args": a.get("args") or self.start_dir} for a in DEFAULT_RDP_APPS]

    def rdp_app(self, app_id: str) -> dict[str, str] | None:
        for a in self.rdp_apps():
            if a["id"] == app_id:
                return a
        return None

    def presets(self) -> list[dict[str, str]]:
        if self.presets_json.strip():
            return json.loads(self.presets_json)
        return DEFAULT_PRESETS

    def preset_ids(self) -> set[str]:
        return {p["id"] for p in self.presets()}

    def preset_command(self, preset_id: str) -> str | None:
        for p in self.presets():
            if p["id"] == preset_id:
                return self.with_start_dir((p.get("command") or "").strip() or None)
        return None

    def with_start_dir(self, command: str | None) -> str | None:
        """Prefix a preset command so it starts in `start_dir`.

        A None command means an interactive login shell — asyncssh runs no command at
        all — and there is nothing to prefix without turning it into a non-login shell,
        so those are left alone and land wherever the target's own shell lands.
        """
        start = self.start_dir.strip()
        if command is None or not start:
            return command
        return START_DIR_TEMPLATE.format(dir=start, cmd=command)
