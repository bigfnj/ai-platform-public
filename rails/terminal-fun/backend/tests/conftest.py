"""Shared fixtures for the terminal-fun suite."""
from __future__ import annotations

import errno
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def full_disk(monkeypatch):
    """`full_disk(where, nth=2)`: a disk that fills partway through a copy, as a full /tmp does.

    From the `nth` file copied to a path `where(path)` picks, each copy writes two bytes and
    raises ENOSPC. Returns the list of those destinations, so a test can witness that the disk
    filled partway and not before the first copy. Every file copy saves.py makes reaches one of
    two patches: shutil.copyfile (copytree and copy2, once copy2's Windows shortcut, CopyFile2,
    one API call that no Python patch sees, is taken away; on Linux copy2 has none), or
    shutil.copyfileobj into a file object that names its path (_place_file's descriptor). A
    copyfileobj inside a patched copyfile is that copy's own and is not counted twice.
    """
    def arm(where, nth: int = 2) -> list[Path]:
        if getattr(shutil, "_winapi", None) is not None:
            monkeypatch.delattr(shutil._winapi, "CopyFile2", raising=False)
        real, real_obj = shutil.copyfile, shutil.copyfileobj
        hits: list[Path] = []
        inside: list[bool] = []

        def full(dst: Path) -> bool:
            hits.append(dst)
            return len(hits) >= nth

        def copyfile(src, dst, *a, **k):
            if not where(Path(dst)) or not full(Path(dst)):
                inside.append(True)
                try:
                    return real(src, dst, *a, **k)
                finally:
                    inside.pop()
            Path(dst).write_bytes(Path(src).read_bytes()[:2])
            raise OSError(errno.ENOSPC, "No space left on device", str(dst))

        def copyfileobj(fsrc, fdst, *a, **k):
            name = getattr(fdst, "name", None)
            if inside or not isinstance(name, str) or not where(Path(name)) or not full(Path(name)):
                return real_obj(fsrc, fdst, *a, **k)
            fdst.write(fsrc.read()[:2])
            raise OSError(errno.ENOSPC, "No space left on device", name)

        monkeypatch.setattr(shutil, "copyfile", copyfile)
        monkeypatch.setattr(shutil, "copyfileobj", copyfileobj)
        return hits
    return arm


# An address on terminal_fun_net, as the gateway holds it there. Any address works; what matters
# is that the TestClient's peer and the stubbed lookup agree, the way the real ones do.
GATEWAY = "10.89.0.2"


@pytest.fixture()
def gateway(monkeypatch):
    """The gateway as this backend sees it: `settings.gateway_host` resolves to `.addrs`.

    main.gateway_peers is the one object the middleware holds, so it is patched in place (and put
    back afterwards) rather than replaced. `.calls` records every lookup, `.now` is the clock, and
    `.addrs` may be set to an OSError to make the next lookup fail.
    """
    from terminal_fun_app import main

    state = SimpleNamespace(address=GATEWAY, addrs=[GATEWAY], calls=[], now=1000.0,
                            peers=main.gateway_peers)

    def resolve(host: str) -> list[str]:
        state.calls.append(host)
        if isinstance(state.addrs, BaseException):
            raise state.addrs
        return list(state.addrs)

    peers = main.gateway_peers
    monkeypatch.setattr(peers, "resolve", resolve)
    monkeypatch.setattr(peers, "_clock", lambda: state.now)
    monkeypatch.setattr(peers, "addresses", frozenset())
    monkeypatch.setattr(peers, "local", ())
    monkeypatch.setattr(peers, "_resolved_at", None)
    # A fresh refusal log per test, so one test's line cannot be held back by another's.
    monkeypatch.setattr(main, "_refused", main._RefusalLog(clock=lambda: state.now))
    # This container's own addresses: none. The backend reads them by looking its own hostname
    # up, which a test did on the box (2026-10-15) and the offline guard below refuses. The tests
    # of the own-address rule set theirs (test_gateway_peer.py).
    monkeypatch.setattr(main, "_own_addresses", lambda: frozenset())
    return state


# No test outside a live class reaches the network: tools/tests/offline_guard.py, which every
# suite's conftest takes. tools/tests is appended, so nothing there shadows a module of this suite.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.append(str(next(p for p in Path(__file__).resolve().parents
                         if (p / "tools" / "tests" / "offline_guard.py").is_file())
                    / "tools" / "tests"))
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: E402,F401
