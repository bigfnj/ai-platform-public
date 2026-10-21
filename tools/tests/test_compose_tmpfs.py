"""Every tmpfs in both compose files has a size, and workstation's and terminal-fun's hold theirs.

A tmpfs with no size= is bounded only by the memory of the VM the engine runs in (podman's machine,
or WSL's), and what is written there counts against that memory. The 2026-10-15 delta audit sized
three services' /tmp, each held by its own rail's compose test; workstation's and terminal-fun's
were left bare, terminal-fun's in the lean compose too (ops-e follow-up). Every tmpfs mount in
both files is held here, so the next one cannot arrive unsized either.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
FULL = REPO / "deploy" / "docker-compose.yml"
LEAN = REPO / "deploy" / "installer" / "docker-compose.installer.yml"
MIB = 1 << 20
_UNITS = {"": 1, "b": 1, "k": 1 << 10, "m": MIB, "g": 1 << 30}


def size_bytes(raw) -> int | None:
    """A tmpfs size in bytes: an int, or digits with an optional b/k/m/g suffix. None for anything
    else: no size, a percentage (of the VM's memory), and 0, which tmpfs takes as no limit at all."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        n = raw
    else:
        m = re.fullmatch(r"(\d+)([bkmg]?)", str(raw).strip().lower())
        if not m:
            return None
        n = int(m[1]) * _UNITS[m[2]]
    return n if n > 0 else None


def tmpfs_mounts(cfg: dict) -> list[tuple[str, str, int | None]]:
    """(service, target, size in bytes or None) for every tmpfs mount: the service-level `tmpfs:`
    short form (`/tmp:size=16m`, a string or a list) and a `volumes:` entry of `type: tmpfs` (its
    `tmpfs.size`)."""
    out = []
    for name, svc in (cfg.get("services") or {}).items():
        raw = svc.get("tmpfs") or []
        for t in ([raw] if isinstance(raw, str) else raw):
            target, _, opts = str(t).partition(":")
            sizes = [size_bytes(v) for k, _, v in (o.partition("=") for o in opts.split(",")) if k.strip() == "size"]
            out.append((name, target, sizes[-1] if sizes else None))
        for v in svc.get("volumes") or []:
            if isinstance(v, dict) and v.get("type") == "tmpfs":
                out.append((name, str(v.get("target", "")), size_bytes((v.get("tmpfs") or {}).get("size", ""))))
    return out


def mounts(path: Path) -> list[tuple[str, str, int | None]]:
    return tmpfs_mounts(yaml.safe_load(path.read_text(encoding="utf-8")))


def size_of(path: Path, service: str, target: str = "/tmp") -> int | None:
    got = [s for n, t, s in mounts(path) if (n, t) == (service, target)]
    assert len(got) == 1, (path.name, service, target, mounts(path))
    return got[0]


def test_the_reader_tells_a_size_from_none():
    """A witness for the checks below, which would pass on nothing if the reader saw no mounts or
    read every one as sized."""
    assert [size_bytes(v) for v in ("16m", "524288k", "268435456", "1G", 4096)] == \
        [16 * MIB, 512 * MIB, 256 * MIB, 1 << 30, 4096]
    assert [size_bytes(v) for v in ("", "0", 0, "50%", "big", None, True)] == [None] * 7
    cfg = {"services": {"a": {"tmpfs": "/tmp"}, "b": {"tmpfs": ["/tmp:mode=1777,size=8m", "/run"]},
                        "c": {"volumes": ["data:/data", {"type": "tmpfs", "target": "/scratch"},
                                          {"type": "tmpfs", "target": "/s2", "tmpfs": {"size": 1024}}]}}}
    assert tmpfs_mounts(cfg) == [("a", "/tmp", None), ("b", "/tmp", 8 * MIB), ("b", "/run", None),
                                 ("c", "/scratch", None), ("c", "/s2", 1024)]


def test_every_tmpfs_in_both_compose_files_has_a_size():
    found = {p.name: mounts(p) for p in (FULL, LEAN)}
    # At least workstation's, terminal-fun's and watch-builder's in the full stack (the public
    # snapshot de-wires the withheld rails, and their tmpfs with them), terminal-fun's in the lean
    # one. Fewer means the reader lost some.
    full = {(n, t) for n, t, _ in found[FULL.name]}
    assert {("workstation", "/tmp"), ("terminal-fun", "/tmp"), ("watch-builder", "/tmp/watch-builder")} <= full, full
    assert ("terminal-fun", "/tmp") in {(n, t) for n, t, _ in found[LEAN.name]}, found
    unsized = {f: [(n, t) for n, t, s in ms if s is None] for f, ms in found.items()}
    assert unsized == {FULL.name: [], LEAN.name: []}, unsized


def test_every_tmpfs_size_is_a_bound_well_under_the_vm():
    """At least 1 MiB, at most 1 GiB each: past that a ceiling is the VM's memory by another name."""
    wrong = [(p.name, n, t, s) for p in (FULL, LEAN) for n, t, s in mounts(p)
             if s is not None and not (MIB <= s <= 1 << 30)]
    assert wrong == [], wrong


def test_workstation_tmp_is_sized_for_what_it_writes():
    """The workstation PTY runs on the SSH target; this backend relays its bytes and writes its
    audit file to /audit, and its rootfs is read-only, so /tmp holds at most an incidental temp
    file. A few MiB is enough; more than 64 MiB would bound nothing a compromised backend wants."""
    size = size_of(FULL, "workstation")
    assert size is not None and 4 * MIB <= size <= 64 * MIB, size


def test_terminal_fun_tmp_is_sized_for_its_session_homes_on_both_stacks():
    """Every play session's HOME is a folder in /tmp (sandbox.make_session_home), Crawl's whole
    ~/.crawl is copied into it from the player's save, hollywood's tmux socket folder is there, and
    an escaped game shell can write anything there. At least 128 MiB for several players' sessions,
    at most 512 MiB, its share; the lean stack's is the full stack's."""
    full, lean = size_of(FULL, "terminal-fun"), size_of(LEAN, "terminal-fun")
    assert full is not None and 128 * MIB <= full <= 512 * MIB, full
    assert lean == full, (lean, full)
