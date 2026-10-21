"""The offline guard (tools/tests/offline_guard.py) that every suite's conftest takes.

Each test here reaches out on purpose, so each clears the guard's record (the `outbound` fixture)
afterwards, or the guard would fail it at teardown. Where a broken guard would let a connection
through, it goes nowhere: to 192.0.2.1, which is TEST-NET-1 (RFC 5737) and never routed, or to a
loopback port the test holds bound without listening on it, which the OS refuses and nothing
else can be listening on.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SAYS = "no network in a test"
TOOLS_TESTS = Path(__file__).resolve().parent


@pytest.fixture
def held_port():
    """A loopback port this test holds bound and is not listening on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    yield s.getsockname()[1]
    s.close()


def test_a_connection_is_refused_and_recorded(outbound):
    with pytest.raises(ConnectionRefusedError, match=SAYS):
        socket.create_connection(("192.0.2.1", 9), timeout=0.5)
    assert outbound == [("192.0.2.1", 9)]
    outbound.clear()


def test_loopback_is_refused_where_this_process_does_not_listen(outbound, held_port):
    """The broker, the gateway and every rail are loopback, so loopback is no exemption."""
    with pytest.raises(ConnectionRefusedError, match=SAYS):
        socket.create_connection(("127.0.0.1", held_port), timeout=0.5)
    with pytest.raises(ConnectionRefusedError, match=SAYS):
        socket.create_connection(("localhost", held_port), timeout=0.5)
    s = socket.socket()
    try:
        with pytest.raises(ConnectionRefusedError, match=SAYS):
            s.connect(("127.0.0.1", held_port))
        assert s.connect_ex(("127.0.0.1", held_port)) != 0
    finally:
        s.close()
    assert outbound == [("127.0.0.1", held_port), ("localhost", held_port),
                        ("127.0.0.1", held_port), ("127.0.0.1", held_port)]
    outbound.clear()


def test_a_server_the_test_starts_is_reached_until_it_closes(outbound):
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    try:
        socket.create_connection(("127.0.0.1", port), timeout=2).close()
        socket.create_connection(("localhost", port), timeout=2).close()
        assert outbound == []
    finally:
        srv.close()
    with pytest.raises(ConnectionRefusedError, match=SAYS):
        socket.create_connection(("127.0.0.1", port), timeout=0.5)
    assert outbound == [("127.0.0.1", port)]
    outbound.clear()


def test_urllib_meets_the_guard(outbound):
    # No proxy (urllib reads one from the registry on Windows): the connection is to the URL's
    # own host, and a guard that ever failed could not send this through a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with pytest.raises(urllib.error.URLError, match=SAYS):
        opener.open("http://192.0.2.1:9/", timeout=0.5)
    assert outbound == [("192.0.2.1", 9)]
    outbound.clear()


def test_an_event_loop_runs_and_its_connections_meet_the_guard(outbound, held_port):
    """asyncio on Windows connects through the proactor, never socket.connect, and builds its
    own loopback socketpair, which is this process's listener and so is let through."""
    assert asyncio.run(asyncio.sleep(0, result="ran")) == "ran"
    assert outbound == []

    async def dial():
        await asyncio.open_connection("127.0.0.1", held_port)

    with pytest.raises(ConnectionRefusedError, match=SAYS):
        asyncio.run(dial())
    assert outbound == [("127.0.0.1", held_port)]
    outbound.clear()


def test_a_host_name_is_not_resolved(outbound):
    with pytest.raises(socket.gaierror, match=SAYS):
        socket.getaddrinfo("broker.invalid", 11500)
    with pytest.raises(socket.gaierror, match=SAYS):
        socket.gethostbyname("broker.invalid")
    with pytest.raises(OSError, match=SAYS):
        socket.gethostbyaddr("192.0.2.1")
    assert outbound == [("broker.invalid", "dns"), ("broker.invalid", "dns"), ("192.0.2.1", "dns")]
    outbound.clear()


def test_a_literal_and_localhost_resolve_with_no_query(outbound):
    assert socket.getaddrinfo("10.0.0.5", 443)
    assert socket.getaddrinfo("localhost", 80)
    assert outbound == []


class TestExemptionLive:
    """A class whose name says Live is not watched: its connection meets the OS, not the guard."""

    def test_a_live_class_is_left_alone(self, request, held_port):
        # Refused by the OS, or on Windows timed out: it retries a refused loopback connect.
        with pytest.raises(OSError) as raised:
            socket.create_connection(("127.0.0.1", held_port), timeout=0.5)
        assert SAYS not in str(raised.value)
        with pytest.raises(KeyError):            # the guard keeps no record for it
            request.getfixturevalue("outbound")


class TestDeliveryIsWatched:
    """Live counts as a word: Delivery is not a live class, so its tests are watched."""

    def test_a_class_whose_name_merely_contains_live_is_watched(self, outbound):
        assert outbound == []


PLANTED = '''
import socket

import pytest


@pytest.fixture(scope="module")
def dials_while_built():
    try:
        socket.create_connection(("192.0.2.1", 9), timeout=0.5)
    except OSError:
        pass


def test_planted():
    try:                      # swallowed, as code built to survive a down broker does
        socket.create_connection(("192.0.2.1", 9), timeout=0.5)
    except OSError:
        pass


def test_module_fixture(dials_while_built):
    pass


def test_own_server():
    srv = socket.create_server(("127.0.0.1", 0))
    socket.create_connection(srv.getsockname()[:2], timeout=2).close()
    srv.close()


class TestPlantedLive:
    def test_exempt(self):
        held = socket.socket()
        held.bind(("127.0.0.1", 0))
        try:
            socket.create_connection(held.getsockname()[:2], timeout=0.5)
        except OSError:
            pass
        held.close()
'''

CONFTEST = '''
from offline_guard import outbound, pytest_runtest_setup, pytest_runtest_teardown  # noqa: F401
'''


def test_a_planted_call_errors_at_teardown_naming_the_test(tmp_path):
    """The whole point, end to end, in a pytest of its own: a call the code swallowed still
    fails its test, one made while a module fixture is built fails the test that built it, and
    a test's own server and a live class pass."""
    (tmp_path / "conftest.py").write_text(CONFTEST, encoding="utf-8")
    (tmp_path / "test_planted.py").write_text(textwrap.dedent(PLANTED), encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(TOOLS_TESTS))
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "--rootdir", str(tmp_path), str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env, cwd=tmp_path, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "4 passed, 2 errors" in out, out
    for name in ("test_planted", "test_module_fixture"):
        assert f"ERROR at teardown of {name} " in out, out
        assert (f"test_planted.py::{name} tried to reach the network, and was refused: "
                f"[('192.0.2.1', 9)]") in out, out
