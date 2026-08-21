"""Meeting Atlas backend config. Env prefix MEETING_ATLAS_.

This rail declares no model slots and never calls the broker, so it declares its own
BaseSettings rather than subclassing PlatformSettings — there is no broker_url to
inherit and no BROKER_AUTH_TOKEN to read. See rail.json's notes and RC005.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, tzinfo

from pydantic_settings import BaseSettings, SettingsConfigDict

try:  # stdlib on 3.9+, but keep the import survivable on a slim image
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

_log = logging.getLogger("meeting-atlas")


class MeetingAtlasSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEETING_ATLAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "meeting-atlas"
    host: str = "127.0.0.1"
    port: int = 8740

    # The Meetily recordings tree, bind-mounted read-only. One directory per meeting.
    recordings_dir: str = "/data/recordings"

    # The Meetily SQLite, bind-mounted read-only. OPTIONAL: it is the only place
    # Meetily keeps the real meeting title and its own generated summary, but a
    # summary.json sidecar can supply the title instead. Empty = not mounted.
    meetily_db: str = ""

    # Display timezone. Recording timestamps are UTC; everything a user reads is
    # local, and getting this wrong shifts every meeting by hours. Falls back to
    # UTC when the name is unknown rather than guessing.
    display_tz: str = "America/Los_Angeles"

    # Serve audio bytes through the gateway. Files can be large (a 27-minute
    # meeting is ~36 MB), and the gateway is the only path a browser has to them.
    serve_audio: bool = True

    # Re-index automatically when the newest folder mtime changes, at most this
    # often (seconds). 0 disables, leaving POST /api/reindex as the only trigger —
    # which is the intended production setup once a co-work task owns ingestion.
    autoreindex_seconds: int = 300

    def tz(self) -> tzinfo:
        """The display timezone, falling back to the SYSTEM LOCAL zone - not UTC.

        Windows ships no zoneinfo database, so ZoneInfo() raises there unless the
        tzdata package is installed (it is a declared dependency for exactly this
        reason). If it still fails, the machine's own local zone is far likelier to
        be right than UTC: defaulting to UTC would put every meeting hours off while
        looking entirely plausible. Logged, because a wrong clock that says nothing
        is the failure mode this rail is most likely to ship with.
        """
        name = (self.display_tz or "").strip()
        if name and ZoneInfo is not None:
            try:
                return ZoneInfo(name)
            except Exception as exc:  # noqa: BLE001 - must not break startup
                _log.warning("display_tz %r unusable (%s); falling back to the system "
                             "local zone. Install tzdata to fix this.", name, exc)
        local = datetime.now().astimezone().tzinfo
        return local or timezone.utc
