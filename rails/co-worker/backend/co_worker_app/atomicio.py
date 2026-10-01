"""JSON writes that survive the inbox being a 9p bind mount.

The inbox is a Windows host directory shared into the container. Under Podman's
Hyper-V machine that arrives as a **9p** mount, and 9p does not support
rename-over-an-existing-file: `os.replace(tmp, target)` raises EPERM once
`target` exists. The first write of a file succeeds and every later one fails,
which is why triage appeared to work and then silently 503'd forever after.

So: try the atomic path first (it is correct on ext4/NTFS-native volumes and on
named volumes), and degrade only as far as necessary. The unlink+rename fallback
has a sub-millisecond window where the file is absent — acceptable for a triage
sidecar and a derived brief, both of which are recoverable, and far better than
not being able to write at all.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

_log = logging.getLogger("co-worker.atomicio")


def write_json(path: Path, data: Any, *, indent: int = 2, sort_keys: bool = False) -> None:
    """Write `data` as JSON to `path`, as atomically as the filesystem allows."""
    d = path.parent
    d.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=f".{path.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, sort_keys=sort_keys)

        try:
            os.replace(tmp, str(path))
            return
        except OSError as first:
            # 9p (and some SMB/virtiofs configurations) reject rename-over-existing.
            if not path.exists():
                raise
            try:
                path.unlink()
                os.replace(tmp, str(path))
                _log.debug("write_json: used unlink+rename fallback for %s", path.name)
                return
            except OSError:
                # Last resort: rewrite in place. Torn-write risk is real but a
                # failed write here means the feature is simply unusable.
                _log.warning(
                    "write_json: %s does not support rename; writing %s in place (%s)",
                    d, path.name, first,
                )
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=indent, sort_keys=sort_keys)
                return
    finally:
        Path(tmp).unlink(missing_ok=True)
