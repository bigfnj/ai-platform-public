"""No test outside a live class reaches the network: the guard every suite's conftest installs.

Measured 2026-10-15 by recording every connection and lookup each suite made: tests in several
suites reached the broker on localhost:11500, one with a real POST /v1/chat (a GPU generation
over synthetic data whenever the box's broker was up), and others looked up compose service
names, while every suite still passed, because a broker or a container that does not answer was
tolerated. A test that reaches the owner's running system spends real GPU time, or a real Claude
CLI call if it names the owner, and its result hangs on what that system said. The first suite
fixed carried this guard in its own conftest; it lives here so each suite imports one copy.

While a test runs (from the start of its setup, which is where module and session fixtures are
built, to the end of its teardown):

* A CONNECTION is refused unless it goes to a socket this process is listening on: a loopback
  address (or the wildcard) on a port one of its own open listening sockets holds. That is a stub
  server a test started for itself, and the socketpair every asyncio event loop makes on Windows
  (and so every TestClient). The broker, the gateway, Ollama and the rails are loopback too, and
  are refused, because this process does not listen on their ports. Checked at
  socket.create_connection, socket.socket.connect / connect_ex (which every client opens its
  sockets through, ssl and urllib3 included), and on Windows the asyncio proactor's connect,
  which an httpx.AsyncClient goes through without calling socket.connect at all.
* A HOST NAME LOOKUP is refused: socket.getaddrinfo, gethostbyname and gethostbyname_ex for a
  name, and gethostbyaddr / getnameinfo for an address that is not loopback (a PTR query). An IP
  literal resolves without a query and is let through, and so is "localhost", which the OS
  answers itself; a connection to either is then judged by the rule above.
* The test that tried ERRORs at its teardown, naming what it tried, even when the code under
  test swallowed the refusal, as code built to survive a down broker usually does.

A class whose name says Live (LiveTests, LiveFetchTests, TestChromiumEngineLive) is left alone:
those are the opt-in tests that reach real services on purpose, deselected or skipped by default
and run by run-tests.ps1 -IncludeLive. "Live" as a word: Delivery and Alive are not live.

Out of reach, by construction: a child process (a PowerShell script a test runs, a `python -c`),
which has its own sockets; a thread that outlives its test and connects after the test's
teardown; and code that reaches the network without a Python socket call (ctypes, a C library).

A suite's conftest takes the guard by importing its two hooks and its fixture:

    from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown

(tools/tests is put on sys.path first; rails find it the way their mutation harnesses find
mutation_runner.py). The hooks are conftest hooks, so they guard that directory's tests and no
other. `outbound` is the guard's record for the current test, for the guard's own tests: one that
reaches out on purpose clears it afterwards, or the guard would fail it at teardown.

Stdlib and pytest only, so a suite whose other imports may be broken can still take it.
"""
from __future__ import annotations

import errno
import ipaddress
import re
import socket
import sys
import weakref

import pytest

_SAYS = "no network in a test (tools/tests/offline_guard.py)"
_LIVE = re.compile(r"Live(?![a-z])")
_LOCAL_NAMES = frozenset({"localhost", "localhost."})
_OUTBOUND = pytest.StashKey[list]()


class _Window:
    """The current test's record while it runs; None outside a guarded test."""
    reached: list | None = None


_WINDOW = _Window()

# Every listening socket this process opens, recorded for as long as it stays open. Recorded
# from the moment this module is imported (a conftest's import, before any test module), so a
# server a session fixture starts is known when a test connects to it.
_LISTENERS: weakref.WeakSet = weakref.WeakSet()


def _host(host) -> str:
    return host.decode("ascii", "replace") if isinstance(host, (bytes, bytearray)) else str(host)


def _ip(host):
    try:
        ip = ipaddress.ip_address(_host(host).strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip


def _resolvable(host) -> bool:
    """A lookup that needs no query: no host, an IP literal, or localhost."""
    return host is None or _host(host).lower() in _LOCAL_NAMES or _ip(host) is not None


def _own_addresses() -> tuple[set, set]:
    """(the ports, the non-IP addresses such as an AF_UNIX path) of this process's open
    listening sockets."""
    ports, others = set(), set()
    for s in list(_LISTENERS):
        try:
            if s.fileno() == -1:
                continue
            name = s.getsockname()
        except OSError:
            continue
        if isinstance(name, tuple):
            ports.add(name[1])
        else:
            others.add(name)
    return ports, others


def _permitted(address) -> bool:
    """Whether `address` is a socket this process listens on."""
    ports, others = _own_addresses()
    if not isinstance(address, tuple):
        return address in others
    if len(address) < 2:
        return False
    host = _host(address[0])
    if host.lower() not in _LOCAL_NAMES:
        ip = _ip(host)
        if ip is None or not (ip.is_loopback or ip.is_unspecified):
            return False
    return address[1] in ports


def _what(address):
    return tuple(address) if isinstance(address, (tuple, list)) else address


def _refuse_connection(address) -> bool:
    """Record and say so when the current test may not connect to `address`."""
    reached = _WINDOW.reached
    if reached is None or _permitted(address):
        return False
    reached.append(_what(address))
    return True


def _refuse_lookup(name) -> bool:
    reached = _WINDOW.reached
    if reached is None:
        return False
    reached.append((_host(name), "dns"))
    return True


_real = {
    "create_connection": socket.create_connection,
    "getaddrinfo": socket.getaddrinfo,
    "gethostbyname": socket.gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
    "gethostbyaddr": socket.gethostbyaddr,
    "getnameinfo": socket.getnameinfo,
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
    "listen": socket.socket.listen,
}


def _create_connection(address, *args, **kwargs):
    if _refuse_connection(address):
        raise ConnectionRefusedError(f"{_SAYS}: {address!r}")
    return _real["create_connection"](address, *args, **kwargs)


def _connect(self, address):
    if _refuse_connection(address):
        raise ConnectionRefusedError(f"{_SAYS}: {address!r}")
    return _real["connect"](self, address)


def _connect_ex(self, address):
    if _refuse_connection(address):
        return errno.ECONNREFUSED
    return _real["connect_ex"](self, address)


def _listen(self, *args):
    _real["listen"](self, *args)
    _LISTENERS.add(self)


def _getaddrinfo(host, *args, **kwargs):
    if not _resolvable(host) and _refuse_lookup(host):
        raise socket.gaierror(socket.EAI_NONAME, f"{_SAYS}: {_host(host)!r}")
    return _real["getaddrinfo"](host, *args, **kwargs)


def _gethostbyname(name):
    if not _resolvable(name) and _refuse_lookup(name):
        raise socket.gaierror(socket.EAI_NONAME, f"{_SAYS}: {_host(name)!r}")
    return _real["gethostbyname"](name)


def _gethostbyname_ex(name):
    if not _resolvable(name) and _refuse_lookup(name):
        raise socket.gaierror(socket.EAI_NONAME, f"{_SAYS}: {_host(name)!r}")
    return _real["gethostbyname_ex"](name)


def _local(host) -> bool:
    ip = _ip(host)
    return _host(host).lower() in _LOCAL_NAMES or (ip is not None and ip.is_loopback)


def _gethostbyaddr(host):
    if not _local(host) and _refuse_lookup(host):
        raise socket.herror(1, f"{_SAYS}: {_host(host)!r}")
    return _real["gethostbyaddr"](host)


def _getnameinfo(sockaddr, flags):
    if not _local(sockaddr[0]) and _refuse_lookup(sockaddr[0]):
        raise socket.gaierror(socket.EAI_NONAME, f"{_SAYS}: {_host(sockaddr[0])!r}")
    return _real["getnameinfo"](sockaddr, flags)


socket.create_connection = _create_connection
socket.getaddrinfo = _getaddrinfo
socket.gethostbyname = _gethostbyname
socket.gethostbyname_ex = _gethostbyname_ex
socket.gethostbyaddr = _gethostbyaddr
socket.getnameinfo = _getnameinfo
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.socket.listen = _listen

if sys.platform == "win32":
    # The proactor connects with ConnectEx on the raw handle: socket.connect is never called.
    from asyncio import windows_events as _windows_events

    _real["proactor_connect"] = _windows_events.IocpProactor.connect

    def _proactor_connect(self, conn, address):
        if _refuse_connection(address):
            raise ConnectionRefusedError(f"{_SAYS}: {address!r}")
        return _real["proactor_connect"](self, conn, address)

    _windows_events.IocpProactor.connect = _proactor_connect


def _exempt(item) -> bool:
    cls = getattr(item, "cls", None)
    return cls is not None and bool(_LIVE.search(cls.__name__))


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_setup(item):
    if not _exempt(item):
        reached: list = []
        item.stash[_OUTBOUND] = reached
        _WINDOW.reached = reached
    return (yield)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    try:
        result = yield
    finally:
        _WINDOW.reached = None
    reached = item.stash.get(_OUTBOUND, None)
    if reached:
        pytest.fail(f"{item.nodeid} tried to reach the network, and was refused: {reached} "
                    "(patch the client for the test)", pytrace=False)
    return result


@pytest.fixture
def outbound(request) -> list:
    """What this test has tried to reach so far: the guard's own record, for its tests."""
    return request.node.stash[_OUTBOUND]
