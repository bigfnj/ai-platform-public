"""The second uid: when the split engages, and what it does to a session HOME.

This is the module the 2026-09-10 audit findings turn on, and its whole value is that it
behaves correctly in THREE configurations, only one of which any given box can actually run:

  * un-split (today's compose)  — no capability. Games run as the backend's uid, as always.
  * split (the hardened compose) — CAP_SETUID+CAP_SETGID held; games are wrapped onto gameuser.
  * not Linux at all (this dev box) — no /proc, no grp module. Must behave like the first.

So the preconditions are injected rather than sampled. Testing this by looking at the real
/proc/self/status would mean the suite asserts whatever the machine happens to be, which for
a security control is the same as asserting nothing: the split path would go permanently
untested on every developer machine and on CI, and only ever "run" in production.

The failure direction is the thing to keep pinned. Every uncertain answer here has to fall to
the un-split path, because that path works. A sandbox module that raised, or that wrapped a
game in a setpriv that is not there, would turn a hardening measure into an outage.
"""
from __future__ import annotations

import ast
import os
import stat
from pathlib import Path

import pytest

from terminal_fun_app import sandbox

CAPS = sandbox.CAP_SETUID | sandbox.CAP_SETGID


@pytest.fixture()
def split_on(monkeypatch):
    """Every precondition satisfied: wrapper present, group resolves, capabilities held."""
    monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: p == sandbox.RUNAS)
    monkeypatch.setattr(sandbox, "share_gid", lambda: 10101)
    monkeypatch.setattr(sandbox, "cap_eff", lambda: CAPS)


@pytest.fixture()
def split_off(monkeypatch):
    """Today's container: the image is ready, the deployment granted nothing."""
    monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: p == sandbox.RUNAS)
    monkeypatch.setattr(sandbox, "share_gid", lambda: 10101)
    monkeypatch.setattr(sandbox, "cap_eff", lambda: 0)


# --- parse_cap_eff: the one bit of text handling ---------------------------------------------

class TestParseCapEff:
    STATUS = ("Name:\tpython3\n"
              "Uid:\t10001\t10001\t10001\t10001\n"
              "CapInh:\t0000000000000000\n"
              "CapPrm:\t00000000000000c0\n"
              "CapEff:\t00000000000000c0\n"
              "CapBnd:\t00000000000000c0\n"
              "Seccomp:\t2\n")

    def test_reads_the_effective_mask(self):
        assert sandbox.parse_cap_eff(self.STATUS) == 0xC0

    def test_a_full_root_mask(self):
        """An unconfined root container. Wide values must not overflow or be truncated — the
        mask is 64-bit and the two bits we care about sit low in it."""
        full = sandbox.parse_cap_eff("CapEff:\t000001ffffffffff\n")
        assert full == 0x000001FFFFFFFFFF
        assert full & CAPS == CAPS

    def test_absent_line_is_zero(self):
        assert sandbox.parse_cap_eff("Name:\tpython3\n") == 0

    def test_reads_capeff_and_none_of_the_other_four_cap_lines(self):
        """Every Cap* line has the same shape, and three of them mean something else. CapBnd
        is what the process could ever hold; CapPrm is what it may raise into effective; CapEff
        is what it holds now. Reading any of the others would report the split as available in
        a container where it is not, and the symptom would not be a security hole — it would be
        every game failing to launch, because setpriv(1) returns EPERM.

        Each line carries a distinct value so a wrong pick cannot coincidentally agree."""
        status = ("CapInh:\t0000000000000001\n"
                  "CapPrm:\t0000000000000002\n"
                  "CapEff:\t0000000000000004\n"
                  "CapBnd:\t0000000000000008\n"
                  "CapAmb:\t0000000000000010\n")
        assert sandbox.parse_cap_eff(status) == 0x4

    def test_a_non_hex_value_does_not_match_and_is_zero(self):
        """Unparseable must mean "no capabilities", not a traceback out of a game launch. The
        charset in the pattern is what enforces it — hence no try/except in the function."""
        assert sandbox.parse_cap_eff("CapEff:\tzzzz\n") == 0

    def test_a_capeff_line_anywhere_but_the_start_is_still_found(self):
        """re.M, not a startswith on line 1. /proc/self/status puts CapEff about 40 lines in."""
        assert sandbox.parse_cap_eff("a\nb\nCapEff:\t00000000000000c0\nc\n") == 0xC0

    def test_empty_input_is_zero(self):
        assert sandbox.parse_cap_eff("") == 0


def test_cap_eff_on_a_box_with_no_procfs_is_zero(monkeypatch):
    """Windows and macOS reach this; so does any container without /proc mounted."""
    def boom(*_a, **_kw):
        raise OSError("no /proc here")
    monkeypatch.setattr("builtins.open", boom)
    assert sandbox.cap_eff() == 0


# --- split_uid_active: all three preconditions ------------------------------------------------

class TestSplitUidActive:
    def test_active_when_everything_is_in_place(self, split_on):
        assert sandbox.split_uid_active() is True

    def test_inactive_without_the_capabilities(self, split_off):
        assert sandbox.split_uid_active() is False

    def test_setuid_alone_is_not_enough(self, monkeypatch, split_on):
        """--regid needs CAP_SETGID. Half the pair means setpriv fails at exec, which would
        kill the session rather than harden it."""
        monkeypatch.setattr(sandbox, "cap_eff", lambda: sandbox.CAP_SETUID)
        assert sandbox.split_uid_active() is False

    def test_setgid_alone_is_not_enough(self, monkeypatch, split_on):
        monkeypatch.setattr(sandbox, "cap_eff", lambda: sandbox.CAP_SETGID)
        assert sandbox.split_uid_active() is False

    def test_unrelated_capabilities_do_not_count(self, monkeypatch, split_on):
        monkeypatch.setattr(sandbox, "cap_eff", lambda: 1 << 21)  # CAP_SYS_ADMIN
        assert sandbox.split_uid_active() is False

    def test_extra_capabilities_alongside_the_pair_still_count(self, monkeypatch, split_on):
        monkeypatch.setattr(sandbox, "cap_eff", lambda: CAPS | (1 << 21))
        assert sandbox.split_uid_active() is True

    def test_inactive_when_the_wrapper_is_missing(self, monkeypatch, split_on):
        """An image built before this change, run under the new compose. Wrapping would exec
        a path that does not exist and every game would fail to start."""
        monkeypatch.setattr(sandbox.os.path, "isfile", lambda p: False)
        assert sandbox.split_uid_active() is False

    def test_inactive_when_the_shared_group_is_missing(self, monkeypatch, split_on):
        """setpriv --init-groups would still work, but the session HOME could not be handed
        to a group both uids are in, so the game could not write into its own HOME."""
        monkeypatch.setattr(sandbox, "share_gid", lambda: None)
        assert sandbox.split_uid_active() is False

    def test_gid_zero_is_a_real_group_not_a_falsy_miss(self, monkeypatch, split_on):
        """`if not gid` would read gid 0 as absent. Unlikely for funshare, and exactly the
        kind of thing that turns a security control off without a symptom."""
        monkeypatch.setattr(sandbox, "share_gid", lambda: 0)
        assert sandbox.split_uid_active() is True


def test_share_gid_is_none_where_there_is_no_group_database(monkeypatch):
    monkeypatch.setattr(sandbox, "SHARE_GROUP", "definitely-not-a-real-group-xyzzy")
    assert sandbox.share_gid() is None


# --- game_argv -------------------------------------------------------------------------------

class TestGameArgv:
    ARGV = ["/usr/games/nethack", "-u", "Thrain"]

    def test_wraps_when_the_split_is_active(self, split_on):
        assert sandbox.game_argv(self.ARGV) == [sandbox.RUNAS, *self.ARGV]

    def test_passes_through_untouched_when_it_is_not(self, split_off):
        assert sandbox.game_argv(self.ARGV) == self.ARGV

    def test_returns_a_new_list_not_the_caller_s(self, split_off):
        """PtySession keeps its own argv; handing back the same object invites aliasing bugs
        across the relaunch loop, which re-derives argv on every settings change."""
        out = sandbox.game_argv(self.ARGV)
        assert out == self.ARGV and out is not self.ARGV

    def test_argument_order_is_preserved_exactly(self, split_on):
        """`-u <name>` is how saves.py keeps one player's NetHack save from colliding with
        another's. A wrapper that reordered or dropped an argument would silently merge two
        players' games."""
        assert sandbox.game_argv(self.ARGV)[1:] == self.ARGV


# --- make_session_home -----------------------------------------------------------------------

class TestMakeSessionHome:
    def test_creates_a_directory_with_the_prefix(self, split_off):
        home = sandbox.make_session_home("ft-")
        try:
            assert os.path.isdir(home) and os.path.basename(home).startswith("ft-")
        finally:
            os.rmdir(home)

    def test_un_split_leaves_mkdtemp_alone(self, monkeypatch, split_off):
        """No chown, no chmod: 0700 is correct when the game is this uid, and touching the
        mode anyway would widen a directory for no reason."""
        calls = []
        monkeypatch.setattr(sandbox.os, "chmod", lambda *a: calls.append(a))
        monkeypatch.setattr(sandbox.os, "chown", lambda *a: calls.append(a), raising=False)
        home = sandbox.make_session_home("ft-")
        try:
            assert calls == []
        finally:
            os.rmdir(home)

    def test_split_hands_the_home_to_the_shared_group(self, monkeypatch, split_on):
        chowned, chmodded = [], []
        monkeypatch.setattr(sandbox.os, "chown", lambda *a: chowned.append(a), raising=False)
        monkeypatch.setattr(sandbox.os, "chmod", lambda *a: chmodded.append(a))
        home = sandbox.make_session_home("ftsave-")
        try:
            assert chowned == [(home, -1, 10101)], "group-only chown, uid left at -1"
            assert chmodded == [(home, sandbox.SHARED_HOME_MODE)]
        finally:
            os.rmdir(home)

    def test_shared_mode_is_setgid_group_rwx_and_no_world_bits(self):
        """Spelled out rather than trusted to the constant. setgid is what keeps the game's
        subdirectories deletable by the backend; the absent world bits are what keep one
        player's session out of another's reach."""
        m = sandbox.SHARED_HOME_MODE
        assert m & stat.S_ISGID
        assert m & stat.S_IRWXG == stat.S_IRWXG
        assert m & stat.S_IRWXO == 0
        assert m & stat.S_IRWXU == stat.S_IRWXU

    def test_gid_zero_is_still_a_group_to_hand_the_home_to(self, monkeypatch, split_on):
        """`if gid:` would read gid 0 as "no shared group" and skip the chown, leaving a 0700
        HOME the game cannot enter — a broken session with nothing logged. Unlikely for
        funshare specifically, and exactly the kind of falsy-check bug that survives review."""
        monkeypatch.setattr(sandbox, "share_gid", lambda: 0)
        chowned = []
        monkeypatch.setattr(sandbox.os, "chown", lambda *a: chowned.append(a), raising=False)
        monkeypatch.setattr(sandbox.os, "chmod", lambda *a: None)
        home = sandbox.make_session_home("ft-")
        try:
            assert chowned == [(home, -1, 0)]
        finally:
            os.rmdir(home)

    def test_a_failed_chown_still_returns_a_usable_home(self, monkeypatch, split_on):
        """The best-effort contract. A container where the backend is not in funshare must
        still start games — as the backend's own uid, into a plain 0700 directory."""
        def refuse(*_a):
            raise PermissionError("not a member of that group")
        monkeypatch.setattr(sandbox.os, "chown", refuse, raising=False)
        home = sandbox.make_session_home("ft-")
        try:
            assert os.path.isdir(home)
        finally:
            os.rmdir(home)

    def test_two_sessions_never_share_a_home(self, split_off):
        a = sandbox.make_session_home("ft-")
        b = sandbox.make_session_home("ft-")
        try:
            assert a != b
        finally:
            os.rmdir(a)
            os.rmdir(b)


# --- pty_session's wiring, read rather than executed -----------------------------------------

class TestPtySessionUsesTheSandbox:
    """pty_session cannot be IMPORTED on this box — fcntl/termios/pty are Linux-only, which is
    why main.py imports it lazily inside one route — so the two calls that carry the uid split
    into a real game launch are unreachable by an ordinary test here. They are also the two
    easiest lines in the change to lose in a merge, and losing either is silent: games keep
    working, just as the wrong uid, which is the state this whole change exists to leave.

    So they are asserted against the parsed AST. Source-reading rather than executing is the
    same trade tools/rail_conformance.py makes throughout, and the AST rather than a substring
    for the reason RC005 gives: a name in a comment is not a call, and matching text would let
    the reassuring comment above the code outlive the code.
    """

    SRC = (Path(__file__).resolve().parents[1]
           / "terminal_fun_app" / "pty_session.py").read_text(encoding="utf-8")
    TREE = ast.parse(SRC)

    def _calls(self, func_name: str) -> list[ast.Call]:
        fn = next(n for n in ast.walk(self.TREE)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == func_name)
        return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    def _names(self, func_name: str) -> set[str]:
        return {c.func.id for c in self._calls(func_name) if isinstance(c.func, ast.Name)}

    def test_it_imports_both_helpers_from_sandbox(self):
        imported = {a.name for n in ast.walk(self.TREE)
                    if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("sandbox")
                    for a in n.names}
        assert {"game_argv", "make_session_home"} <= imported

    def test_start_wraps_the_argv_through_game_argv(self):
        """Without this the game is exec'd directly and inherits the backend's uid — the
        pre-split behaviour, restored silently on a container that grants the capability."""
        assert "game_argv" in self._names("start")

    def test_start_builds_its_ephemeral_home_through_make_session_home(self):
        """The non-saveable games' HOME. mkdtemp's 0700 is unusable by the game uid."""
        assert "make_session_home" in self._names("start")

    def test_start_does_not_call_mkdtemp_directly(self):
        """Both HOME creation sites have to agree. A stray mkdtemp here would break every
        non-saveable toy under the split while saved games kept working — the hardest shape
        of this bug to notice, because the games people test with are the ones that resume."""
        attrs = {c.func.attr for c in self._calls("start") if isinstance(c.func, ast.Attribute)}
        assert "mkdtemp" not in attrs

    def test_the_module_never_reaches_for_tempfile_at_all(self):
        assert not any(isinstance(n, ast.Import) and any(a.name == "tempfile" for a in n.names)
                       for n in ast.walk(self.TREE))


class TestMainUsesTheSandbox:
    """The OTHER session-HOME site. main.py owns the HOME for saveable games (nethack, crawl)
    so it can seed the save before launch and capture it after; PtySession owns the ephemeral
    one for everything else. Both have to go through make_session_home or the split breaks for
    exactly half the catalogue — and the half that keeps working is the half people reach for
    when they check, because a toy that starts looks fine and a roguelike that will not resume
    reads as a save bug.

    main.py IS importable here, but the websocket handler it lives in is not reachable without
    a PTY, so this is read the same way pty_session's is.
    """

    TREE = ast.parse((Path(__file__).resolve().parents[1]
                      / "terminal_fun_app" / "main.py").read_text(encoding="utf-8"))

    def test_it_imports_make_session_home_from_sandbox(self):
        imported = {a.name for n in ast.walk(self.TREE)
                    if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("sandbox")
                    for a in n.names}
        assert "make_session_home" in imported

    def test_the_saveable_home_goes_through_make_session_home(self):
        calls = {c.func.id for c in ast.walk(self.TREE)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert "make_session_home" in calls

    def test_no_bare_mkdtemp_survives_anywhere_in_main(self):
        attrs = {c.func.attr for c in ast.walk(self.TREE)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
        assert "mkdtemp" not in attrs
