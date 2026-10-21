"""The one atomic replace every settings writer in the broker uses.

Each writer (roles.json, the Claude switch file, tokens.json, disabled.json) writes a temp file
beside its target and renames it over the target, so a concurrent hot-read never sees half a
file. On Windows that rename fails now and then with ``PermissionError [WinError 5]`` when nothing
is wrong: an endpoint-security file filter (antivirus, DLP, EDR; a managed workstation runs
several) opens the freshly written file to scan it and holds it for a moment, and ``os.replace``
cannot rename a file another process has open without delete sharing. Seen 2026-10-13 as a repoint
and a token mint failing under load, both passing alone; in production that was a 500 from
``PUT /v1/roles`` or ``POST /v1/tokens`` for a write that a moment later would have landed.

So the replace is retried, briefly, on Windows only. Anywhere else a ``PermissionError`` from a
rename is a real permission problem, and waiting would only delay the same answer.

A file held for longer than the retries is still an error, and every caller turns it into a
refusal that names the file and removes its temp file: ``config._write_settings_file`` (roles.json,
tokens.json, disabled.json) raises ``SettingsWriteError`` and ``set_cli_role`` raises
``CliRolesFileError``, each a 409 from its route. Until 2026-10-14 only the switch file did; the
other three let the ``PermissionError`` out as a 500 and left ``<name>.tmp`` behind.
"""

from __future__ import annotations

import os
import sys
import time

#: The waits between attempts: the first try, then up to five more, about 0.6 s in all before the
#: last error is raised. A scan holds a small JSON file for milliseconds, so this is generous, and
#: short enough that a held file which never comes free still fails while the caller is waiting.
_BACKOFF = (0.02, 0.04, 0.08, 0.16, 0.32)

#: None follows the platform (retry on Windows only). A test sets True or False to pin it.
RETRY_ON_PERMISSION_ERROR: bool | None = None


def _retrying() -> bool:
    if RETRY_ON_PERMISSION_ERROR is not None:
        return RETRY_ON_PERMISSION_ERROR
    return sys.platform == "win32"


def replace_with_retry(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
    """``os.replace(src, dst)``, retried on ``PermissionError`` on Windows (see the module).

    Only ``PermissionError`` is retried: a missing source or a full disk is not transient, and
    every caller turns an ``OSError`` into its own refusal (see the module). After the last
    attempt the last error is raised, unchanged. The waits block the calling thread, which for
    these writers is the event loop; they happen only while a file is held.
    """
    retrying = _retrying()
    for wait in (*_BACKOFF, None):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if wait is None or not retrying:
                raise
            time.sleep(wait)
