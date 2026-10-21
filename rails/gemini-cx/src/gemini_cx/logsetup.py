"""Make this rail's own log lines reach the container log.

GENERATED — do not edit in place. This file is emitted by tools/rail_template.py and every
copy must match byte for byte; `rail_template.py check` fails otherwise. Change the template,
then `rail_template.py sync` to roll it out.

It is duplicated rather than imported, for identity.py's reason: a rail that has to import the
platform to boot is not a component you can lift out.

WHY IT EXISTS. uvicorn configures only its own loggers (uvicorn, uvicorn.error, uvicorn.access).
The root logger keeps no handler and stays at WARNING, so a record from a module logger such as
logging.getLogger("<package>.api") at INFO reaches nothing and is dropped, while WARNING and
above appear only through Python's bare last-resort handler. Measured on 2026-10-07 by applying
uvicorn 0.51.0's own LOGGING_CONFIG: INFO dropped, WARNING printed. Every `log.info(...)` in a
rail that configures no logging was writing to nowhere, with nothing to say so.

configure() attaches ONE stderr handler to the rail's top-level logger (this package's name),
so every module logger under it is covered, and sets that logger's level from LOG_LEVEL
(default INFO; a name such as DEBUG or WARNING, or a number). Call it from the app factory.
It is idempotent: a second call (a factory built twice in tests, a reload) re-reads the level
and never adds a second handler. It changes WHERE records go, never what is logged.

Propagation is left ON, so pytest's caplog and any root configuration still see every record.
The cost: a process that also gives the ROOT logger a handler prints each line twice. uvicorn's
default configuration gives root none, which is the case this exists for.
"""
from __future__ import annotations

import logging
import os
import sys

# The package this file sits in, e.g. "gemini_cx" for gemini_cx/logsetup.py.
PACKAGE = __name__.rpartition(".")[0] or __name__
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _StderrHandler(logging.Handler):
    """Writes to sys.stderr as it is at EMIT time, not as it was when the handler was built, so
    a redirected stderr (a test's capture, a supervisor's pipe) is followed rather than missed."""

    platform_rail_stderr = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            sys.stderr.write(self.format(record) + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 - logging must never take the request down with it
            self.handleError(record)


def _level(raw: str) -> int | None:
    """LOG_LEVEL as a logging level, or None when it is not one."""
    if not raw:
        return logging.INFO
    if raw.isdigit():
        return int(raw)
    value = logging.getLevelName(raw.upper())
    return value if isinstance(value, int) else None


def configure() -> logging.Logger:
    """Route this rail's log records to stderr at LOG_LEVEL. Safe to call more than once."""
    logger = logging.getLogger(PACKAGE)
    if not any(getattr(h, "platform_rail_stderr", False) for h in logger.handlers):
        handler = _StderrHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
    raw = os.environ.get("LOG_LEVEL", "").strip()
    level = _level(raw)
    logger.setLevel(logging.INFO if level is None else level)
    if level is None:
        # Said, not swallowed: a typo here would otherwise look like "the rail logs nothing".
        logger.warning("LOG_LEVEL=%r is not a logging level; using INFO", raw)
    return logger
