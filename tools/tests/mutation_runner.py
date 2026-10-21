"""The one runner every Python mutation harness in the repo shares.

Four properties, each of which every harness used to get wrong in its own way:

* a suite run is bounded and a timeout kills the whole process TREE: `run_bounded` (returns a
  SuiteRun) or `run_tree` (a drop-in for `subprocess.run` that raises TimeoutExpired, for a
  harness whose verdict logic is built around that);
* a mutated file is restored byte for byte, and PROVEN restored: `mutate_bytes` /
  `write_like` change a file in its own line endings, `restore_exact` writes the original
  bytes back and raises "TREE IS DIRTY" unless they round-trip;
* the command line is read before anything is touched: `parse_harness_args` answers `--list`
  by naming the cases and exiting, and refuses a flag the harness does not take. Most harnesses
  ignored argv, so `--list`, the read-only habit, REWROTE the tree and ran every suite;
* FIRED means pytest reported the NAMED test red: `named_failed` reads pytest's own summary
  lines (`FAILED <path>::<name>`) and matches the whole name. About thirty harnesses asked
  whether the name appeared anywhere in the output, which a traceback or a log line that merely
  mentions the test satisfies, and several matched it inside a FAILED line, where `test_x` is
  also found in `test_x_more`.

Lives in tools/tests because that directory ships with the public snapshot, so every harness
can import it there: the broker's `services/broker/tests/_suite_runner.py` re-exports it, the
rails' and the gateway's harnesses put this directory on sys.path. `mutation_runner.mjs` beside
it is the node harnesses' twin. NOT a pytest file (no `test_` prefix);
`test_mutation_runner.py` tests it and `mutate_mutation_runner.py` mutation-tests it.

Why it exists: every harness in this directory called `subprocess.run` with no timeout. A
mutation that makes a test wait for ever (an awaited event nobody sets, a loop whose exit was
the line removed) then hung the HARNESS, which looks exactly like a slow run: no verdict, no
output, and somebody waiting on it. A mutation that hangs the suite has broken something, but
it has not been shown to fail the named test, so the harnesses report it as its own verdict,
TIMEOUT, and never as FIRED.

Why not simply `subprocess.run(..., timeout=...)`: on Windows its timeout path kills the child
and then calls `communicate()` again to collect the output, and that read blocks until EVERY
process holding the pipe has exited. pytest children start grandchildren (a fake CLI, a
`taskkill`, a real `python -c` writer), and a grandchild inherits the std handles by default, so
the "timed out" harness went on waiting for it. Here the whole tree is killed, and the final
read is itself bounded.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from typing import NamedTuple

#: Seconds one suite run may take. The slowest suite a broker harness runs takes ~15 s on this
#: box, so 300 only ever trips on a hang. Overridable for a slower machine.
DEFAULT_TIMEOUT = float(os.environ.get("MUTATION_SUITE_TIMEOUT", "300"))

#: After the kill: how long the tree kill and the final read of the pipes may take. Bounded so
#: that something OUTSIDE the tree holding the pipe costs this long, not for ever.
REAP_TIMEOUT = 15.0

#: Opt-ins that let a test spend real money or quota (the Claude CLI's LiveTests; job-aid's keyed
#: job boards). A mutation harness runs its suite dozens of times, so one of these left set in
#: the developer's shell would multiply real calls by the mutation count. Every child this
#: module starts gets an environment WITHOUT them, whatever the caller passed.
LIVE_OPT_INS = ("CLI_LIVE", "JOB_AID_LIVE_TESTS")


def _without_live(env) -> dict:
    out = dict(os.environ if env is None else env)
    for name in LIVE_OPT_INS:
        out.pop(name, None)
    return out


def mutate_bytes(raw: bytes, old: str, new: str) -> tuple[int, bytes]:
    """Apply one mutation to a file's BYTES. Returns (occurrences of `old`, mutated bytes);
    the bytes are only changed when there is exactly one occurrence.

    Patterns are written with LF. A working copy may be CRLF (core.autocrlf), LF (written
    since checkout) or a mix, so a multi-line pattern is looked for in both spellings and only
    the matched span is replaced, in the spelling it was found in. Every other byte stays as
    it was. A text round trip (read_text/write_text) translates line endings on Windows, so a
    "restore" could rewrite an LF file as CRLF: the tree reads as changed and the next harness
    run mutates a file nobody meant to touch.
    """
    text = raw.decode("utf-8")
    variants = [(old, new)]
    if "\n" in old:
        variants.append((old.replace("\n", "\r\n"), new.replace("\n", "\r\n")))
    # Every occurrence by POSITION, in either spelling. An LF pattern that STARTS with "\n" also
    # matches the "\n" inside a CRLF line break, so plain counting found one CRLF occurrence
    # twice (once per spelling) and refused it as PATTERN x2. An LF-spelling match whose leading
    # "\n" is the second half of "\r\n" is that CRLF occurrence, already counted as such.
    hits: list[tuple[int, str, str]] = []
    for spelling, (o, n) in enumerate(variants):
        at = text.find(o)
        while at >= 0:
            inside_crlf = (spelling == 0 and o.startswith("\n") and at > 0
                           and text[at - 1] == "\r")
            if not inside_crlf:
                hits.append((at, o, n))
            at = text.find(o, at + 1)
    if len(hits) != 1:
        return len(hits), raw
    at, o, n = hits[0]
    # The one hit's own span. (With exactly one hit a replace-all would touch the same bytes:
    # an in-CRLF match of the LF spelling is always also a CRLF-spelling hit, so a second one
    # would have made the count 2. The harness proved that equivalence rather than a guard.)
    return 1, (text[:at] + n + text[at + len(o):]).encode("utf-8")


def restore_exact(path, original: bytes) -> None:
    """Write `original` back and PROVE the file now holds exactly those bytes. Raises rather
    than reporting a verdict over a tree that is not the one the harness found."""
    path.write_bytes(original)
    if path.read_bytes() != original:
        raise RuntimeError(f"{path} was not restored byte for byte -- TREE IS DIRTY")


def snapshot(paths) -> dict:
    """{path: bytes} for each path, for a harness that mutates a COPY and must prove the real
    files were never written."""
    return {p: p.read_bytes() for p in paths}


def assert_unchanged(snap: dict) -> None:
    """Raise "TREE IS DIRTY" naming every file whose bytes differ from `snap`."""
    changed = [str(p) for p, b in snap.items() if p.read_bytes() != b]
    if changed:
        raise RuntimeError(f"changed under the harness: {', '.join(changed)} -- TREE IS DIRTY")


def write_like(path, text: str, original: bytes) -> None:
    """Write `text` (LF line endings, as read_text hands them out) back in the line endings
    `original` used: CRLF if it had any, LF otherwise. For a harness that does its own text
    matching, so the MUTATED file differs from the original only where the mutation is, not
    in every line ending, which would change what an .sh, .yml or .ps1 file means. The
    restore is restore_exact's job, never this."""
    if b"\r\n" in original:
        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


class SuiteRun(NamedTuple):
    code: int
    out: str          # stdout + stderr, in that order, like the harnesses always built it
    timed_out: bool


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill `proc` AND its descendants. `proc.kill()` alone reaches the child only.

    If the child has already exited, taskkill /T can no longer find its orphans by parent PID;
    that case is what the bounded read after this call is for."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=REAP_TIMEOUT, check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass


def run_bounded(cmd: list[str], *, cwd, env=None, timeout: float | None = None,
                encoding: str = "utf-8", errors: str = "replace") -> SuiteRun:
    """`cmd` to completion or until `timeout` seconds, whichever comes first.

    Decoded as UTF-8 with replacement rather than in the console code page: a failure
    traceback quotes source that carries characters outside cp1252, and a decode error in the
    reader thread would read as the suite crashing.
    """
    limit = DEFAULT_TIMEOUT if timeout is None else timeout
    kw: dict = {}
    if sys.platform != "win32":
        # Its own process group, so killpg takes the grandchildren too. Windows needs no
        # equivalent: taskkill /T walks the tree by parent PID.
        kw["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=cwd, env=_without_live(env), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            encoding=encoding, errors=errors, **kw)
    try:
        out, err = proc.communicate(timeout=limit)
        return SuiteRun(proc.returncode, (out or "") + (err or ""), False)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            out, err = "", (f"(output unavailable: the pipe was still held {REAP_TIMEOUT:.0f} s "
                            "after the process tree was killed)")
        note = f"\n[TIMEOUT: the suite was still running after {limit:g} s and was killed]\n"
        return SuiteRun(-1, (out or "") + (err or "") + note, True)
    except BaseException:
        # Ctrl+C in the harness: without this the pytest child (and whatever it started) keeps
        # running against a tree the harness's `finally` is about to restore.
        _kill_tree(proc)
        raise


def run_tree(cmd, *, timeout: float | None = None, input=None, capture_output: bool = False,
             stdout=None, stderr=None, check: bool = False, **popen_kw
             ) -> subprocess.CompletedProcess:
    """`subprocess.run`, except that a timeout kills the whole process TREE before
    TimeoutExpired is raised, and the read after the kill is bounded.

    A drop-in for harnesses whose verdict logic already handles TimeoutExpired: their
    `subprocess.run(..., timeout=N)` becomes `run_tree(..., timeout=N)` and nothing else
    changes. `timeout=None` means DEFAULT_TIMEOUT, never "no limit": an unbounded run is the
    thing this module exists to rule out.
    """
    limit = DEFAULT_TIMEOUT if timeout is None else timeout
    if capture_output:
        stdout = stderr = subprocess.PIPE
    if input is not None:
        popen_kw["stdin"] = subprocess.PIPE
    if sys.platform != "win32":
        popen_kw.setdefault("start_new_session", True)
    popen_kw["env"] = _without_live(popen_kw.get("env"))
    # Text with no encoding named is decoded as UTF-8 with replacement, as run_bounded does. Popen
    # would use the console code page (cp1252 here), where 0x81, 0x8d, 0x8f, 0x90 and 0x9d have no
    # character: a child writing UTF-8 (PYTHONIOENCODING=utf-8, or a traceback quoting source)
    # raised UnicodeDecodeError in the reader thread, stdout came back None, and the harness died
    # on a TypeError instead of giving a verdict (one harness did exactly that).
    if (popen_kw.get("text") or popen_kw.get("universal_newlines")) and not popen_kw.get("encoding"):
        popen_kw["encoding"] = "utf-8"
        popen_kw.setdefault("errors", "replace")
    proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr, **popen_kw)
    try:
        out, err = proc.communicate(input=input, timeout=limit)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            out = err = None
        raise subprocess.TimeoutExpired(proc.args, limit, output=out, stderr=err) from None
    except BaseException:
        _kill_tree(proc)
        raise
    if check and proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, proc.args, out, err)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


# --- the verdict: did pytest report the NAMED test red? -------------------------------------

#: A short-summary line: `FAILED <id>`, optionally ` - <message>`; `ERROR <id>` for a test whose
#: setup or teardown raised; `SUBFAILED[<msg>] (<k=v, ...>) <id>` (pytest 9) for a subtest that
#: failed, its description free text. Only at the start of a line and with ONE space after the
#: word, which is how pytest writes them and how no traceback, assertion message or captured log
#: record (`ERROR    name:file.py:12 ...`, padded) is written.
#:
#: The id is read in two parts. `test` is a path (no spaces) and its `::name` parts, each an
#: identifier, so it ends where the test's name does: at a parametrize id's `[`, at ` - `, or at
#: the end of the line. `rest` is everything after it. A parametrize id is free text that can
#: hold ` - `, `::` and brackets of its own (a test parametrized over summary lines quotes whole
#: ids), so no pattern cuts it out: an expected id is compared with the start of `rest`.
_SUMMARY = re.compile(r"^(?:FAILED|ERROR|SUBFAILED.*?) "
                      r"(?P<test>[^\s\[]+?(?:::\w+)+)(?P<rest>(?:\[| - ).*?)?\r?$", re.M)
#: A parametrize id for failed_ids' listing: up to the last `]` that ends the line or comes
#: before ` - `. Only the listing guesses; named_failed never relies on it.
_PARAM = re.compile(r"\[.*\](?= - |$)")


def _summaries(out: str) -> list[tuple[str, str]]:
    return [(m.group("test"), m.group("rest") or "") for m in _SUMMARY.finditer(out)]


def failed_ids(out: str) -> list[str]:
    """The node ids pytest's short test summary reports as failed or errored in `out`, in
    order. pytest writes that summary by default (`-r fE`), under `-q` and `-v` alike. An ERROR
    for a whole file (a collection error, no `::`) names no test and is not listed."""
    ids = []
    for test, rest in _summaries(out):
        param = _PARAM.match(rest)
        ids.append(test + (param.group(0) if param else ""))
    return ids


def _is(test: str, rest: str, expected: str) -> bool:
    name, bracket, param = expected.partition("[")
    got = test.replace("\\", "/").split("::")
    want = name.replace("\\", "/").split("::")
    if len(want) > len(got):
        return False
    got = got[-len(want):]
    if got[-1] != want[-1]:
        return False
    # A file part may sit under directories in the id (`tests/test_x.py`, or `tests\test_x.py`
    # on Windows), matched on a path boundary; a class part is matched whole.
    if not all(g == w or g.endswith("/" + w) for g, w in zip(got[:-1], want[:-1])):
        return False
    # A bare name is any parametrization of it; an id is that id, whole, then the line's end or
    # its message.
    return not bracket or rest == bracket + param or rest.startswith(bracket + param + " - ")


def named_failed(out: str, expected: str) -> bool:
    """Whether pytest reported the test `expected` failed (or errored) in `out`: one of
    `failed_ids` IS that test. `expected` is written as the harness tables write it: a bare
    function name (`test_x`), with its parametrize id (`test_x[case]`), with its class
    (`TestY::test_x`) or with its file (`test_file.py::test_x`). The whole name must match:
    `test_x` is neither `test_x_more` nor `other_test_x`, and a bare name, with no id of its
    own, is any parametrization of it.

    The verdict every Python harness gives FIRED by. A harness that asked `expected in out`
    counted a traceback, an assertion message or a log line naming the test, which a run that
    failed something else entirely can print."""
    return any(_is(test, rest, expected) for test, rest in _summaries(out))


# --- the command line -----------------------------------------------------------------------

class HarnessArgs(NamedTuple):
    listing: bool               # --list was given (returned only when the harness lists itself)
    only: tuple[str, ...]       # the label filter's terms; empty unless the harness takes one
    flags: frozenset[str]       # which of the harness's own flags were given


_HELP = ("--list names every case (and each control, marked) and runs nothing. Without it the "
         "harness REWRITES files in this tree, runs its suites, and restores them.")


def _label(case) -> str:
    """A case's label: the case itself (a str), its first field (the tables' tuples), its
    "label" key, or its .label attribute."""
    if isinstance(case, str):
        return case
    if isinstance(case, (tuple, list)):
        return case[0]
    if isinstance(case, dict):
        return case["label"]
    return case.label


def parse_harness_args(cases, controls=(), *, flags=(), filter: bool = False,
                       ignore_case: bool = False, argv=None) -> HarnessArgs:
    """Read a mutation harness's command line, before it reads, writes or runs anything.

    `--list` prints `case<TAB>label` for each of `cases` and `control<TAB>label` for each of
    `controls` (a case is a label or a record whose label `_label` finds), then a count, and
    exits 0. With `filter=True` the positional arguments are the harness's label filter, each a
    substring of a label (case-insensitive with `ignore_case`, as the harness's own filter is),
    and `--list` names only what they select. `flags` are the harness's own switches (such as
    `--whole-file`), returned in `.flags`. Anything else is refused with exit 2 and a usage
    line: an option the harness does not take, and a positional argument to a harness with no
    filter. Both used to be ignored, so a mistyped flag ran every mutation.

    `cases=None` is for a harness that prints its own listing (where each case lives): `--list`
    is then validated like the rest and returned in `.listing`, and the harness lists and exits.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    prog = os.path.basename(sys.argv[0]) or "harness"
    usage = (f"usage: {prog} [--list]" + "".join(f" [{f}]" for f in flags)
             + (" [LABEL-FILTER ...]" if filter else ""))

    def utf8(stream):
        # Labels quote source, and a cp1252 pipe cannot take every character in it. Only on the
        # paths that print and exit, so a harness's own run keeps the streams it always had.
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):     # a stand-in stream a caller swapped in
            pass
        return stream

    def refuse(why: str):
        print(f"{prog}: {why}\n{usage}", file=utf8(sys.stderr))
        raise SystemExit(2)

    listing, only, given = False, [], set()
    for a in argv:
        if a in ("-h", "--help"):
            print(f"{usage}\n{_HELP}", file=utf8(sys.stdout))
            raise SystemExit(0)
        if a == "--list":
            listing = True
        elif a in flags:
            given.add(a)
        elif a.startswith("-") and a != "-":
            refuse(f"unknown option {a!r}")
        elif filter:
            only.append(a)
        else:
            refuse(f"takes no label filter, so {a!r} would have been ignored and every case run")
    if listing and cases is not None:
        utf8(sys.stdout)
        fold = (lambda s: s.lower()) if ignore_case else (lambda s: s)
        picked = [_label(c) for c in cases]
        picked_controls = [_label(c) for c in controls]
        if only:
            keep = lambda label: any(fold(o) in fold(label) for o in only)  # noqa: E731
            picked = [x for x in picked if keep(x)]
            picked_controls = [x for x in picked_controls if keep(x)]
        for label in picked:
            print(f"case\t{label}")
        for label in picked_controls:
            print(f"control\t{label}")
        print(f"{len(picked)} mutations" + (f", {len(picked_controls)} controls" if controls else "")
              + "; listed, nothing run")
        raise SystemExit(0)
    if listing:
        utf8(sys.stdout)                        # the harness prints its own listing next
    return HarnessArgs(listing, tuple(only), frozenset(given))
