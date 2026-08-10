"""Workstation backend config.

Extends the shared PlatformSettings but overrides the env prefix to WORKSTATION_
so the SSH-target knobs read clearly (WORKSTATION_SSH_HOST, ...). This app never
touches the broker/GPU; broker_url is inherited but unused.
"""

from __future__ import annotations

import json

from pydantic_settings import SettingsConfigDict

from platform_core import PlatformSettings

# The rail presets. `command` empty => an interactive login shell; otherwise the
# program to launch inside the PTY. Override wholesale with WORKSTATION_PRESETS_JSON.
DEFAULT_PRESETS: list[dict[str, str]] = [
    {"id": "shell", "label": "Shell", "icon": "🖥", "command": ""},
    {"id": "claude", "label": "Claude Code", "icon": "🤖", "command": "claude"},
    {"id": "codex", "label": "Codex", "icon": "◆", "command": "codex"},
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

    # Optional JSON override of DEFAULT_PRESETS.
    presets_json: str = ""

    def presets(self) -> list[dict[str, str]]:
        if self.presets_json.strip():
            return json.loads(self.presets_json)
        return DEFAULT_PRESETS

    def preset_ids(self) -> set[str]:
        return {p["id"] for p in self.presets()}

    def preset_command(self, preset_id: str) -> str | None:
        for p in self.presets():
            if p["id"] == preset_id:
                return (p.get("command") or "").strip() or None
        return None
