"""What the Claude CLI backend's tests share: the fake subprocess, the fixtures, and every helper
more than one test_cli_backend*.py file uses. Not a test file.

The tests were one file, test_cli_backend.py, until 2026-10-19, when it neared publish's data
ceiling; they are split by concern now (test_cli_backend.py's docstring lists the files). Each
helper here moved verbatim from the section of that file it was written in. The fixtures are
imported BY NAME into each test file, which is how pytest finds them there: `group_kills` and
`_system_temp` are autouse, so every one of those files imports both.
"""
import asyncio
import base64
import json
import os
import pathlib
import sys
import tempfile

import pytest

from app import cli_backend
from app.cli_backend import ClaudeCliClient


# --- fake subprocess -------------------------------------------------------
#
# Shaped like an asyncio subprocess at the STREAM level (stdin writer, stdout/stderr readers),
# because that is the level the backend now reads at: it no longer calls communicate(), which
# buffered the whole of stdout before any cap could be checked.

class _FakeStream:
    """A StreamReader stand-in: `read(n)` hands out `data` in slices, then b"" for EOF.
    `hang` never reaches EOF; `endless` never runs out, and counts what it handed out."""

    def __init__(self, data: bytes = b"", *, hang: bool = False, endless: bool = False) -> None:
        self._data, self._pos, self._hang, self._endless = data, 0, hang, endless
        self.served = 0
        self.eof = False        # set when the process is killed: a dead writer's pipe ends

    async def read(self, n: int = -1) -> bytes:
        if self.eof:
            return b""
        if self._endless:
            await asyncio.sleep(0)          # yield, as a real pipe read would
            self.served += n
            return b"x" * n
        if self._pos < len(self._data):
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            self.served += len(chunk)
            return chunk
        if self._hang:
            await asyncio.sleep(5)
        return b""


class _FakeStdin:
    def __init__(self, proc: "_FakeProc") -> None:
        self._proc = proc

    def write(self, data: bytes) -> None:
        self._proc.stdin_seen = (self._proc.stdin_seen or b"") + data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._proc.stdin_closed = True


class _FakeProc:
    def __init__(self, out: bytes, err: bytes, code: int, *, hang: bool = False,
                 endless: bool = False) -> None:
        self._code = code
        # Running until waited for, like a real child: the backend's `finally` decides whether
        # to kill the tree from this, so a fake that started "exited" could never be killed.
        self.returncode = None
        self.pid = 4242
        self.killed = False
        self.stdin_seen: bytes | None = None
        self.stdin_closed = False
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStream(out, hang=hang, endless=endless)
        self.stderr = _FakeStream(err, hang=hang)

    def kill(self):
        self.killed = True
        self.returncode = -9
        self.stdout.eof = self.stderr.eof = True

    async def wait(self):
        if self.returncode is None:
            self.returncode = self._code
        return self.returncode


def _patch(monkeypatch, proc: _FakeProc, *, spawn_yields: bool = False,
           scribble: bool = False) -> dict:
    """Swap in a fake `create_subprocess_exec`, recording the argv/cwd/env it was called with.

    `taskkill` gets a trivially-finished stand-in: `_kill_tree` reaps the real tree through it
    on Windows, and that call would otherwise be captured as if it were the CLI invocation.
    `spawn_yields` makes that spawn suspend once first, as a real one does: a fake that never
    suspends cannot be cancelled, so it would hide what a cancellation does to the kill.
    `scribble`: the fake CLI writes into its TEMP as it starts (`_scribble`), as the real one
    does on every call.
    """
    seen: dict = {}

    async def fake(*argv, **kw):
        if argv and str(argv[0]).lower().startswith("taskkill"):
            if spawn_yields:
                await asyncio.sleep(0)
            seen["taskkill"] = list(argv)
            return _FakeProc(b"", b"", 0)
        seen["argv"] = list(argv)
        seen["cwd"] = kw.get("cwd")
        seen["env"] = kw.get("env")
        seen["kw"] = dict(kw)
        if scribble:
            seen.setdefault("temps", []).append(_scribble(kw["env"]))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return seen


_REAL_KILLPG = cli_backend._killpg


@pytest.fixture(autouse=True)
def group_kills(monkeypatch):
    """Every test here records POSIX group kills instead of sending them. The fake processes
    all claim pid 4242, and on Linux a real killpg(4242) would signal whatever group has that
    id. Tests that start REAL children take `real_group_kill` to put the real one back."""
    calls: list[int] = []
    monkeypatch.setattr(cli_backend, "_killpg", calls.append)
    return calls


@pytest.fixture
def real_group_kill(monkeypatch, group_kills):
    monkeypatch.setattr(cli_backend, "_killpg", _REAL_KILLPG)


@pytest.fixture(autouse=True)
def _system_temp(monkeypatch, tmp_path_factory):
    """A client built with no workdir and no temp root (most of the fakes here) makes each call's
    temp directory in the system temp directory (cli_backend.call_temp_root). Here that is a
    directory of this run's own, never the developer's TEMP."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path_factory.getbasetemp() / "system-temp"))


def _scribble(env: dict) -> str:
    """What the real CLI leaves under its TEMP on every call (measured 2026-10-14 on claude
    2.1.296: `claude\\<workdir slug>\\<session id>\\scratchpad\\`, empty), plus a file in it, which
    the real one did not write but a later one may. Returns the TEMP it wrote into."""
    temp = env["TEMP"]
    scratch = os.path.join(temp, "claude", "C--work", "4d1c6b0e-session", "scratchpad")
    os.makedirs(scratch, exist_ok=True)
    with open(os.path.join(scratch, "note.txt"), "w", encoding="utf-8") as fh:
        fh.write("written by the call")
    return temp


@pytest.fixture
def broker_temp(monkeypatch, tmp_path):
    """The broker's own TEMP, TMP and TMPDIR, pointed at a directory of the test's: a child that
    inherited them would leave its writing there, and never in the developer's TEMP."""
    path = tmp_path / "broker-temp"
    path.mkdir()
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(path))
    return path


def _client(**kw) -> ClaudeCliClient:
    kw.setdefault("timeout", 30.0)
    return ClaudeCliClient("C:/fake/claude.exe", **kw)


def _result(text: str, **extra) -> bytes:
    body = {"type": "result", "subtype": "success", "is_error": False, "result": text}
    body.update(extra)
    return json.dumps(body).encode()


# --- what more than one test_cli_backend*.py file uses, each moved verbatim from the
# section it was written in --------------------------------------------------------------

REQUIRED_FLAGS = ("--tools", "--strict-mcp-config", "--disable-slash-commands",
                  "--no-session-persistence", "--setting-sources")


def _left_under(root) -> list[str]:
    """What is left under a temp root: nothing, or a root no call has made yet."""
    root = pathlib.Path(root)
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def _alive(pid: int) -> bool:
    """Is `pid` still running? A Linux zombie counts as dead: a killed orphan's parent in a
    container may never reap it, and its /proc entry stays until somebody does."""
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(handle, 0) == 0x102   # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False
    except OSError:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True


async def _cancel_again_and_again(task: asyncio.Task) -> None:
    """anyio's LEVEL-triggered cancellation, by hand: cancel `task` again on every pass of the
    loop until it is done, the way a cancelled anyio scope re-delivers to a task still inside it
    (Starlette's StreamingResponse under ASGI 2.3 when the client disconnects)."""
    while not task.done():
        task.cancel()
        await asyncio.sleep(0)


_SETTINGS = '{"claudeMdExcludes": ["**/.claude/CLAUDE.md", "**/.claude/rules/**"]}'


# The broker reads only an image's first bytes, so these are the magic numbers plus filler
# rather than decodable pictures; test_cli_backend.py's `_solid_png` makes a real one for
# the live test.

_PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"IHDR-and-the-rest"
_PNG_B64 = "iVBORw0KGgpJSERSLWFuZC10aGUtcmVzdA=="          # base64 of _PNG, written out
_JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"JFIF-and-the-rest"
_GIF87 = b"GIF87a" + b"the-rest"
_GIF89 = b"GIF89a" + b"the-rest"
_WEBP = b"RIFF" + (64).to_bytes(4, "little") + b"WEBPVP8 the-rest"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _img_q(*images: str, text: str = "what is this") -> list[dict]:
    return [{"role": "user", "content": text, "images": list(images)}]


def _transcript(text: str = "ok", *, result: bool = True, **extra) -> bytes:
    """A stream-json transcript shaped like the real one (measured 2026-10-09): an init record,
    a rate-limit event, the assistant message, then the result line carrying the verdict."""
    lines = [{"type": "system", "subtype": "init", "session_id": "s", "tools": []},
             {"type": "rate_limit_event"},
             {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}]
    if result:
        lines.append({"type": "result", "subtype": "success", "is_error": False,
                      "result": text, **extra})
    return ("\n".join(json.dumps(x) for x in lines) + "\n").encode()


def _blocks(stdin: bytes) -> list[dict]:
    lines = stdin.decode("ascii").split("\n")
    assert lines[-1] == "" and len(lines) == 2, "not exactly one newline-terminated line"
    line = json.loads(lines[0])
    assert line["type"] == "user" and line["message"]["role"] == "user"
    return line["message"]["content"]


def _system_prompt(argv: list[str]) -> str:
    return argv[argv.index("--system-prompt") + 1]


#: What a model might say about a rail's picture: rail content, kept out of every log.
_SEEN = "The worksheet shows a pupil's answers; please log in to see the rest of the quota"


# The transcripts below have the shape claude 2.1.296 writes (measured 2026-10-13 with
# `-p --output-format stream-json --verbose --include-partial-messages`, Haiku 5.5, a count to
# twelve): init, a rate-limit event, message_start, content_block_start, one content_block_delta
# per text_delta, the whole assistant message, content_block_stop, message_delta (the stop
# reason), message_stop, and the result line, whose text was exactly the deltas joined.

_Q = [{"role": "user", "content": "q"}]


class _ScriptedStream:
    """A stdout that hands out `steps` in order. Bytes are served (in `n`-byte slices); a number
    is a pause of that many seconds (a CLI writing slowly); an asyncio.Event is waited on (a CLI
    gone quiet until the test says otherwise). After the last step: EOF, or with `hold` nothing
    ever again (a CLI that hangs without closing stdout). A kill sets `eof`, which ends every
    wait, as a dead writer's pipe does. `parked` is set once a read reaches an Event: the reader
    has taken everything before it and asked for more, so a test waits on that, not a sleep."""

    def __init__(self, steps=(), *, hold: bool = False) -> None:
        self._steps, self._hold = list(steps), hold
        self.eof = False
        self.served = 0
        self.parked = asyncio.Event()

    async def read(self, n: int = -1) -> bytes:
        while self._steps and not self.eof:
            step = self._steps.pop(0)
            if isinstance(step, asyncio.Event):
                self.parked.set()
                while not (step.is_set() or self.eof):
                    await asyncio.sleep(0.01)
                continue
            if isinstance(step, (int, float)):
                await asyncio.sleep(step)
                continue
            chunk = step[:n] if n > 0 else step
            if len(chunk) < len(step):
                self._steps.insert(0, step[len(chunk):])
            self.served += len(chunk)
            return chunk
        while self._hold and not self.eof:
            await asyncio.sleep(0.01)
        return b""


def _streaming_proc(steps, *, code: int = 0, err: bytes = b"", hold: bool = False) -> _FakeProc:
    """A fake CLI whose stdout follows `steps`. `hold`: a CLI still running, so neither stdout
    nor stderr ever reaches EOF until it is killed (a real one holds both open while it runs)."""
    proc = _FakeProc(b"", err, code)
    proc.stdout = _ScriptedStream(steps, hold=hold)
    if hold:
        proc.stderr = _ScriptedStream([err] if err else [], hold=True)
    return proc


def _ev(event: dict) -> dict:
    return {"type": "stream_event", "event": event}


def _text_delta(text: str) -> dict:
    return _ev({"type": "content_block_delta", "index": 0,
                "delta": {"type": "text_delta", "text": text}})


_STREAM_USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 944,
                 "cache_read_input_tokens": 0, "output_tokens": 44}


def _partial(*texts: str, result=None, stop: str = "end_turn", tail: bool = True,
             **extra) -> list[dict]:
    """A partial-message transcript (see above) with one text delta per text. `result=False`
    leaves the result line out, a string replaces its text; `tail=False` stops after the deltas
    (a CLI still writing); `extra` goes onto the result line."""
    joined = "".join(texts)
    out = [{"type": "system", "subtype": "init", "session_id": "s"},
           {"type": "rate_limit_event"},
           _ev({"type": "message_start",
                "message": {"model": "claude-haiku-5-5", "role": "assistant", "content": []}}),
           _ev({"type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""}})]
    out += [_text_delta(t) for t in texts]
    if tail:
        out += [{"type": "assistant", "message": {"content": [{"type": "text", "text": joined}]}},
                _ev({"type": "content_block_stop", "index": 0}),
                _ev({"type": "message_delta", "delta": {"stop_reason": stop},
                     "usage": dict(_STREAM_USAGE)}),
                _ev({"type": "message_stop"})]
        if result is not False:
            out.append({"type": "result", "subtype": "success", "is_error": False,
                        "result": joined if result is None else result, "stop_reason": stop,
                        "usage": dict(_STREAM_USAGE), "duration_ms": 1567,
                        "total_cost_usd": 0.0003, "num_turns": 1, **extra})
    return out


def _lines(records) -> bytes:
    return ("\n".join(json.dumps(r) for r in records) + "\n").encode()


def _after_first_delta(records) -> int:
    return next(k for k, r in enumerate(records)
                if (r.get("event") or {}).get("type") == "content_block_delta") + 1


async def _frames(agen) -> list[dict]:
    return [f async for f in agen]


def _said(frames) -> list[str]:
    return [f["message"]["content"] for f in frames]
