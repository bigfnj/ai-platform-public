"""A media job leaves nothing on disk, however it ends, and its big JSON stays off the event loop.

The worker made its own temp files in the system temp (`broker-stt-*`, a transcribe's clip of up
to 11 MB, and a tts job's TemporaryDirectory) and removed them in its own `finally`, which never
runs for a worker the broker kills on a timeout or a cancel: each such job left one behind. Now
the broker's per-job directory holds a `scratch` directory the spec names, the worker makes its
temp files there, and the broker removes the whole directory when the job ends.

And `run_media_job` wrote the spec (a transcribe's is up to 15 MB of base64) and read the result
(an image batch's is several MB) in the event loop, stalling every other route while each went to
disk. Both happen in a thread now.

The worker here is the REAL media_worker.py, run by this interpreter, with stand-ins for the two
libraries it would load (faster_whisper, edu_media_core.tts) on the spec's `media_core_src`.

A job's directory that a broker which died mid-job left is removed at the next start, a voice
job's (`broker-voice-*`, voice.run_voice_job) as well as a media job's, each against its own
timeout; and a voice job removes its own directory as a media job does, in tries and never
raising (the end of this file).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from app import media

_FAKE_WHISPER = '''
import os, time
from types import SimpleNamespace


class WhisperModel:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, clip, **k):
        with open(os.environ["MEDIA_TEST_READY"], "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\\n{clip}")
        if os.environ.get("MEDIA_TEST_HANG"):
            time.sleep(120)
        return [SimpleNamespace(text=" heard")], SimpleNamespace(language="en", duration=1.0)
'''

_FAKE_TTS = '''
import os, time

SAMPLE_RATE = 24000


def generate_timed_audio(segments, out_path):
    out_path.write_bytes(b"RIFF-not-really")
    with open(os.environ["MEDIA_TEST_READY"], "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\\n{out_path}")
    if os.environ.get("MEDIA_TEST_HANG"):
        time.sleep(120)
    return []
'''


def _alive(pid: int) -> bool:
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
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_leftover(pid: int | None) -> None:
    if pid is None or not _alive(pid):
        return
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        import signal
        os.kill(pid, signal.SIGKILL)


@pytest.fixture
def worker_env(tmp_path, monkeypatch):
    """A system temp of the test's own, for the broker (its job directory) and the worker (its
    inherited TEMP), and the two stand-in libraries. Returns (system temp, libs, ready file)."""
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    for key in ("TEMP", "TMP", "TMPDIR"):
        monkeypatch.setenv(key, str(system_temp))
    libs = tmp_path / "libs"
    (libs / "edu_media_core").mkdir(parents=True)
    (libs / "faster_whisper.py").write_text(_FAKE_WHISPER, encoding="utf-8")
    (libs / "edu_media_core" / "__init__.py").write_text("", encoding="utf-8")
    (libs / "edu_media_core" / "tts.py").write_text(_FAKE_TTS, encoding="utf-8")
    ready = tmp_path / "ready.txt"
    monkeypatch.setenv("MEDIA_TEST_READY", str(ready))
    return system_temp, libs, ready


def _spec(op: str, libs: Path, tmp_path: Path) -> dict:
    if op == "transcribe":
        return {"op": "transcribe", "media_core_src": str(libs), "suffix": ".webm",
                "audio_b64": base64.b64encode(b"\x1aE\xdf\xa3" + b"x" * 4096).decode()}
    return {"op": "tts", "media_core_src": str(libs), "voices_dir": str(tmp_path),
            "segments": []}


async def _until(check, limit: float) -> bool:
    deadline = time.monotonic() + limit
    while not check():
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.05)
    return True


@pytest.mark.parametrize("ending", ["cancelled", "timed-out"])
@pytest.mark.parametrize("op", ["transcribe", "tts"])
def test_a_killed_worker_leaves_nothing_in_the_system_temp(worker_env, tmp_path, monkeypatch,
                                                           op, ending):
    """The worker made its temp directory and is in the middle of the job (the stand-in library
    says so, then hangs) when the broker kills it: a /v1/cancel, or the job's timeout. Its own
    `finally` never runs. Before, its `broker-stt-*` (or tts TemporaryDirectory) stayed in the
    system temp, one per job; now it is in the job's directory, removed with it."""
    system_temp, libs, ready = worker_env
    monkeypatch.setenv("MEDIA_TEST_HANG", "1")
    spec = _spec(op, libs, tmp_path)
    pid = None

    async def scenario():
        nonlocal pid
        task = asyncio.ensure_future(media.run_media_job(
            # The worker is in its library within about 0.5 s here; the timeout is eight times that.
            python_exe=sys.executable, spec=spec, timeout=60 if ending == "cancelled" else 4))
        assert await _until(lambda: task.done() or ready.exists(), 30), "the worker never began"
        assert not task.done(), f"the job ended before the kill: {task}"
        await asyncio.sleep(0.1)
        pid = int(ready.read_text(encoding="utf-8").splitlines()[0])
        if ending == "cancelled":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(media.MediaError, match="timed out"):
                await task

    try:
        asyncio.run(scenario())
        left = sorted(p.name for p in system_temp.iterdir())
        assert left == [], f"the job left {left} in the system temp"
    finally:
        _kill_leftover(pid)


def test_a_finished_job_makes_its_temp_files_in_its_own_directory(worker_env, tmp_path):
    """The clip the worker transcribes is inside the broker's job directory (its scratch), and the
    job, having answered, leaves nothing."""
    system_temp, libs, ready = worker_env
    result = asyncio.run(media.run_media_job(
        python_exe=sys.executable, spec=_spec("transcribe", libs, tmp_path), timeout=60))
    assert result["text"] == "heard"
    clip = Path(ready.read_text(encoding="utf-8").splitlines()[1])
    job_dir = clip.parents[2]
    assert job_dir.parent == system_temp and job_dir.name.startswith("broker-media-"), clip
    assert clip.parent.parent.name == "scratch" and clip.parent.name.startswith("broker-stt-")
    assert list(system_temp.iterdir()) == []


def test_a_worker_run_by_hand_keeps_the_system_temp():
    """A spec with no scratch directory (a worker run by hand), or one that names a directory
    that is not there, makes its temp files where it always did."""
    import importlib.util
    name = "_broker_media_worker"       # test_transcribe_suffix loads it under the same name
    media_worker = sys.modules.get(name)
    if media_worker is None:
        found = importlib.util.spec_from_file_location(name, media.WORKER)
        media_worker = importlib.util.module_from_spec(found)
        sys.modules[name] = media_worker
        found.loader.exec_module(media_worker)
    assert media_worker._scratch({}) is None
    assert media_worker._scratch({"scratch_dir": "C:/no/such/dir/anywhere"}) is None


def test_a_cancelled_job_removes_its_directory_only_once_its_writing_thread_ends(
        tmp_path, monkeypatch):
    """A cancellation ends the job's wait for the thread writing its spec, not the thread: the
    removal waits for the thread, which holds the file it would delete (on Windows a file open in
    another thread cannot be deleted), and the job still ends cancelled, with nothing left."""
    import threading as th
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    events: list[str] = []
    writing = th.Event()
    real_write, real_remove = media._write_json, media._remove_job_dir

    def slow_write(path, data):
        writing.set()
        time.sleep(0.5)
        real_write(path, data)
        events.append("written")

    def remove(path):
        events.append("removed")
        return real_remove(path)

    monkeypatch.setattr(media, "_write_json", slow_write)
    monkeypatch.setattr(media, "_remove_job_dir", remove)

    async def scenario():
        task = asyncio.ensure_future(media.run_media_job(
            python_exe=sys.executable, spec={"op": "image"}, timeout=30))
        assert await _until(writing.is_set, 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert events == ["written", "removed"], events
    assert list(tmp_path.iterdir()) == []


def test_a_refused_removal_is_tried_again(tmp_path, monkeypatch, capsys):
    """A just-killed worker, or a file filter, can hold a file for a moment: the delete is tried
    again, and a second try that succeeds leaves no log line."""
    job = tmp_path / "broker-media-x"
    (job / "scratch").mkdir(parents=True)
    calls: list[str] = []
    real = media.shutil.rmtree

    def held_once(path, *a, **k):
        calls.append(str(path))
        if len(calls) == 1:
            raise PermissionError(13, "The process cannot access the file")
        return real(path, *a, **k)

    monkeypatch.setattr(media.shutil, "rmtree", held_once)
    monkeypatch.setattr(media, "_REMOVE_PAUSE", 0.01)
    assert media._remove_job_dir(job) is True
    assert len(calls) == 2 and not job.exists()
    assert capsys.readouterr().err == ""


def test_a_removal_refused_every_time_is_logged_once(tmp_path, monkeypatch, capsys):
    job = tmp_path / "broker-media-y"
    job.mkdir()
    calls: list[str] = []

    def always_held(path, *a, **k):
        calls.append(str(path))
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(media.shutil, "rmtree", always_held)
    monkeypatch.setattr(media, "_REMOVE_PAUSE", 0.01)
    assert media._remove_job_dir(job) is False
    assert len(calls) == media._REMOVE_TRIES == 5
    err = capsys.readouterr().err
    assert err.count("could not be removed after 5 tries") == 1 and "broker-media-y" in err


class _AnsweringProc:
    """A worker that answers at once: it writes `result` to the out path it was handed."""

    def __init__(self, out_path: str, result: dict) -> None:
        self._out, self._result = out_path, result
        self.returncode = None

    async def communicate(self):
        Path(self._out).write_text(json.dumps(self._result), encoding="utf-8")
        self.returncode = 0
        return b"", b""

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


#: How long the disk takes over the spec and over the result in the two tests below: a file
#: filter scanning a 15 MB file as it is written or opened can hold it far longer than this.
DISK_STALL_S = 0.3


@pytest.fixture
def answering(monkeypatch, tmp_path):
    """create_subprocess_exec swapped for an `_AnsweringProc` carrying a 6 MB image result, the
    system temp the test's own, and a disk that takes DISK_STALL_S over in.json and over
    out.json. Returns the threads that wrote in.json and read out.json, and the result."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    result = {"images": [base64.b64encode(os.urandom(4_500_000)).decode()], "errors": []}

    async def fake_exec(*argv, **_kw):
        return _AnsweringProc(argv[3], result)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    seen: dict[str, str] = {}
    real_write, real_read = Path.write_text, Path.read_text

    def write_text(self, *a, **k):
        if self.name == "in.json":
            seen["write"] = threading.current_thread().name
            time.sleep(DISK_STALL_S)
        return real_write(self, *a, **k)

    def read_text(self, *a, **k):
        if self.name == "out.json":
            seen["read"] = threading.current_thread().name
            time.sleep(DISK_STALL_S)
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "read_text", read_text)
    return seen, result


def test_the_spec_and_the_result_go_to_disk_off_the_event_loop(answering):
    """In a thread, both: the event loop's own thread never writes the spec or reads the result."""
    seen, result = answering
    loop_thread = threading.current_thread().name
    spec = {"op": "transcribe", "audio_b64": "A" * 15_000_000, "suffix": ".webm"}
    out = asyncio.run(media.run_media_job(python_exe=sys.executable, spec=spec, timeout=30))
    assert out == result
    assert seen["write"] != loop_thread and seen["read"] != loop_thread, seen


def test_a_15_mb_transcribe_does_not_stall_a_concurrent_request(answering):
    """A ticker beside the job, as /healthz would be: the longest gap between its ticks is the
    longest anything else waited while the job wrote its 15 MB spec and read its 6 MB result over
    a disk that takes 0.3 s for each. In the loop that was at least 0.6 s; in a thread, what is
    left is the JSON encoder and decoder, which hold the GIL (19-26 ms measured here, against
    60-76 ms for the whole job in the loop on a disk that does not stall)."""
    spec = {"op": "transcribe", "audio_b64": "A" * 15_000_000, "suffix": ".webm"}

    async def scenario():
        loop = asyncio.get_running_loop()
        worst = 0.0
        running = True

        async def ticker():
            nonlocal worst
            while running:
                before = loop.time()
                await asyncio.sleep(0.001)
                worst = max(worst, loop.time() - before)

        tick = asyncio.ensure_future(ticker())
        await asyncio.sleep(0.05)
        await media.run_media_job(python_exe=sys.executable, spec=spec, timeout=30)
        running = False
        await tick
        return worst

    worst = asyncio.run(scenario())
    assert worst < LAG_BOUND_S, f"a concurrent request waited {worst * 1000:.0f} ms"


#: Half the disk's stall on ONE file; six times the worst gap measured with the I/O in a thread.
LAG_BOUND_S = 0.15


# --- what a broker that died mid-job left: swept at the next start -------------------------------

def _aged(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    for p in (path, *path.rglob("*")):
        os.utime(p, (then, then))


def _job_dir(system_temp: Path) -> Path:
    """A job's directory as run_media_job makes it (tempfile's own name), with what a transcribe
    leaves there when the broker dies before its cleanup: the spec and the worker's clip."""
    job = Path(tempfile.mkdtemp(prefix="broker-media-", dir=system_temp))
    clip = job / "scratch" / "broker-stt-abcdefgh"
    clip.mkdir(parents=True)
    (job / "in.json").write_text('{"op": "transcribe", "audio_b64": "GkXfow=="}', encoding="utf-8")
    (clip / "clip.webm").write_bytes(b"\x1aE\xdf\xa3")
    return job


@pytest.fixture
def media_temp(tmp_path, monkeypatch):
    """A system temp of the test's own, and the settings for a broker that uses it."""
    from app.config import BrokerSettings
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    for key in ("BROKER_MEDIA_TIMEOUT", "BROKER_VOICE_TIMEOUT", "BROKER_CLI_TEMP_ROOT",
                "BROKER_CLI_WORKDIR"):
        monkeypatch.delenv(key, raising=False)

    def settings(**kw):
        return BrokerSettings(roles_file=str(tmp_path / "roles.json"),
                              disabled_file=str(tmp_path / "disabled.json"),
                              tokens_file=str(tmp_path / "tokens.json"),
                              upstreams_file=str(tmp_path / "upstreams.json"), auth_token="", **kw)
    return system_temp, settings


def test_the_broker_removes_a_dead_brokers_job_directory_at_startup_and_leaves_a_young_one(
        media_temp, monkeypatch):
    """A broker stopped mid-job (an NSSM stop, the watchdog at shutdown, a crash) never runs the
    job's cleanup, and a file filter can hold a file past its tries, so the directory, a
    transcribe's whole recording in its spec, stayed in the service account's TEMP for good. The
    real lifespan removes one untouched for two hours before it serves, and leaves one ten minutes
    old (another broker's job in flight in the same TEMP, for all it knows), anything not named
    as a job's directory is, and a file with a job directory's name."""
    system_temp, settings = media_temp
    stale, young = _job_dir(system_temp), _job_dir(system_temp)
    keep = [young.name, "broker-media-notes-of-mine", "someone-elses-folder", "broker-media-abcd1234"]
    (system_temp / keep[1]).mkdir()
    (system_temp / keep[2]).mkdir()
    (system_temp / keep[3]).write_text("a file with a job directory's name", encoding="utf-8")
    for p in (stale, *(system_temp / k for k in keep[1:])):
        _aged(p, 2 * 3600)
    _aged(young, 600)
    assert _boot(monkeypatch, settings(media_timeout=1200.0), system_temp) == sorted(keep), \
        "the startup left the dead broker's job directory"


def _boot(monkeypatch, settings, system_temp: Path) -> list[str]:
    """Run the real lifespan's startup with `settings` (Ollama answering, the role audit finding
    nothing) and return what is left in the system temp once it serves."""
    from app import main
    from app.broker import Broker
    monkeypatch.setattr(main, "BrokerSettings", lambda: settings)

    async def answers(_broker):
        return True

    async def no_findings(_self):
        return []

    monkeypatch.setattr(main, "_ollama_answers", answers)
    monkeypatch.setattr(Broker, "audit_roles", no_findings)
    for name in ("settings", "broker", "token_seen"):
        monkeypatch.setattr(main.app.state, name, None, raising=False)

    async def boot() -> list[str]:
        async with main.lifespan(main.app):
            return sorted(p.name for p in system_temp.iterdir())

    return asyncio.run(boot())


@pytest.mark.parametrize("timeout,kept", [(600.0, False), (3600.0, True)],
                         ids=["an-hour", "three-timeouts"])
def test_the_media_sweep_waits_an_hour_or_three_media_timeouts_if_longer(
        media_temp, capsys, timeout, kept):
    """A job directory last written two hours ago: stale beside a 600 s BROKER_MEDIA_TIMEOUT (the
    hour is the floor), but beside an hour-long timeout it can be a job whose worker has written
    nothing since its first minute, so it waits for three hours."""
    from app.broker import Broker
    system_temp, settings = media_temp
    job = _job_dir(system_temp)
    _aged(job, 2 * 3600)
    assert Broker(settings(media_timeout=timeout)).sweep_media_temp() == \
        ((0, 0) if kept else (1, 0))
    assert job.exists() is kept
    log = capsys.readouterr().err
    assert ("removed 1 stale media job" in log and str(system_temp) in log) is not kept, log


def test_a_media_sweep_that_cannot_remove_a_directory_says_so_and_the_broker_starts(
        media_temp, monkeypatch, capsys):
    from app.broker import Broker
    system_temp, settings = media_temp
    job = _job_dir(system_temp)
    _aged(job, 2 * 3600)

    def refuse(path, *a, **k):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(media.shutil, "rmtree", refuse)
    assert Broker(settings()).sweep_media_temp() == (0, 1)
    assert job.exists()
    assert "1 would not go" in capsys.readouterr().err


# --- a voice job's directory, swept by the same rule against BROKER_VOICE_TIMEOUT ----------------

def _voice_job_dir(system_temp: Path) -> Path:
    """A voice job's directory as run_voice_job makes it (tempfile's own name under the prefix it
    hands mkdtemp), with what it holds while the engine runs: the text being read aloud."""
    job = Path(tempfile.mkdtemp(prefix="broker-voice-", dir=system_temp))
    (job / "text.txt").write_text("the text being read aloud", encoding="utf-8")
    return job


def test_the_broker_removes_a_dead_brokers_voice_job_directory_at_startup_and_leaves_a_young_one(
        media_temp, monkeypatch):
    """A voice job's directory holds the text being read aloud, and only the job's own `finally`
    removed it, so a broker stopped mid-job left it in the service account's TEMP for good. The
    real lifespan removes one untouched for two hours and leaves one ten minutes old, names that
    only look like a voice job's (too long, too short, a capital tempfile never draws, the prefix
    inside another name), and a file with a voice job directory's name."""
    system_temp, settings = media_temp
    stale, young = _voice_job_dir(system_temp), _voice_job_dir(system_temp)
    # Distinct case-insensitively too: NTFS would take the capital name for the file's.
    lookalikes = ["broker-voice-notes-of-mine", "broker-voice-abc1234", "broker-voice-ABCDEFGH",
                  "my-broker-voice-abcd1234"]
    for name in lookalikes:
        (system_temp / name).mkdir()
    (system_temp / "broker-voice-abcd1234").write_text("a file with a voice job directory's name",
                                                        encoding="utf-8")
    keep = [young.name, *lookalikes, "broker-voice-abcd1234"]
    for p in (stale, *(system_temp / k for k in keep[1:])):
        _aged(p, 2 * 3600)
    _aged(young, 600)
    assert _boot(monkeypatch, settings(voice_timeout=1200.0), system_temp) == sorted(keep), \
        "the startup left the dead broker's voice job directory"


@pytest.mark.parametrize("age,voice_timeout,media_timeout,kept", [
    (45 * 60, 600.0, 3600.0, True),
    (2 * 3600, 600.0, 3600.0, False),
    (2 * 3600, 3600.0, 600.0, True),
], ids=["an-hour-floor", "past-the-hour", "three-voice-timeouts"])
def test_the_voice_sweep_waits_an_hour_or_three_voice_timeouts_if_longer(
        media_temp, capsys, age, voice_timeout, media_timeout, kept):
    """Beside a 600 s BROKER_VOICE_TIMEOUT, a voice job's directory last written 45 minutes ago is
    past three timeouts and still kept (the hour is the floor), and one two hours old goes. Beside
    an hour-long timeout one two hours old can be a job whose engine has written nothing since it
    began, so it waits for three hours. BROKER_MEDIA_TIMEOUT, set the other way each time, has no
    say."""
    from app.broker import Broker
    system_temp, settings = media_temp
    job = _voice_job_dir(system_temp)
    _aged(job, age)
    broker = Broker(settings(voice_timeout=voice_timeout, media_timeout=media_timeout))
    assert broker.sweep_media_temp() == ((0, 0) if kept else (1, 0))
    assert job.exists() is kept
    log = capsys.readouterr().err
    assert ("removed 1 stale voice job" in log and str(system_temp) in log) is not kept, log


class _FakeEngine:
    """The voice engine run_voice_job spawns, as the asyncio process it gets back. `ending` is
    "answers" (it writes the wav and exits 0), "fails" (exit 1, no wav) or "hangs" (until it is
    killed). `seen` gets the job's directory and the text the engine was handed, as it starts."""

    def __init__(self, argv: list[str], ending: str, seen: dict) -> None:
        self.argv, self.ending, self.seen, self.returncode = argv, ending, seen, None

    async def communicate(self):
        text = Path(self.argv[self.argv.index("--text") + 1].removeprefix("@"))
        out = Path(self.argv[self.argv.index("--out") + 1])
        self.seen["dir"], self.seen["text"] = out.parent, text.read_text(encoding="utf-8")
        assert text.parent == out.parent
        if self.ending == "hangs":
            await asyncio.sleep(3600)
        if self.ending == "answers":
            out.write_bytes(b"RIFF-not-really")
        self.returncode = 0 if self.ending == "answers" else 1
        return b"", b"the engine's stderr"

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.fixture
def voice_job(media_temp, tmp_path, monkeypatch):
    """`(run, seen)`: `run(ending, timeout)` is run_voice_job's coroutine with its engine a
    `_FakeEngine` of that ending, in the system temp of `media_temp`; `seen` is the engine's."""
    from app import voice
    entry = tmp_path / "cli.py"
    entry.write_text("", encoding="utf-8")
    seen: dict = {}

    def run(ending: str = "answers", timeout: float = 30.0):
        async def fake_exec(*argv, **_kw):
            return _FakeEngine([str(a) for a in argv], ending, seen)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        return voice.run_voice_job(python_exe=sys.executable, entry=str(entry), cwd=None,
                                   voice_id="v", text="read me aloud", timeout=timeout)

    return run, seen


def test_a_voice_job_makes_its_directory_under_the_name_the_sweep_matches(media_temp, voice_job):
    """The sweep matches only the names run_voice_job makes, so the two must agree: the job's
    directory, where the engine is handed the text and told to write the wav, is in the system
    temp under a name `voice.JOB_DIR_NAME` matches whole, and is gone once the job answers."""
    from app import voice
    system_temp, _settings = media_temp
    run, seen = voice_job
    assert asyncio.run(run()) == b"RIFF-not-really" and seen["text"] == "read me aloud"
    job_dir = seen["dir"]
    assert job_dir.parent == system_temp, job_dir
    assert voice.JOB_DIR_NAME.fullmatch(job_dir.name), job_dir.name
    assert list(system_temp.iterdir()) == []


def test_a_sweep_of_one_kind_that_fails_leaves_the_other_swept_and_the_broker_starts(
        media_temp, monkeypatch, capsys):
    """The media sweep raising (nothing it calls is expected to: this is the boot's guard) is
    logged, and the voice jobs' directories are still swept."""
    from app.broker import Broker
    system_temp, settings = media_temp
    media_job, voice_job = _job_dir(system_temp), _voice_job_dir(system_temp)
    for p in (media_job, voice_job):
        _aged(p, 2 * 3600)
    real = media.sweep_job_dirs

    def media_fails(root, *, older_than, name=None):
        if name is None or "media" in name.pattern:
            raise RuntimeError("the media sweep fell over")
        return real(root, older_than=older_than, name=name)

    monkeypatch.setattr(media, "sweep_job_dirs", media_fails)
    assert Broker(settings()).sweep_media_temp() == (1, 0)
    assert media_job.exists() and not voice_job.exists()
    err = capsys.readouterr().err
    assert "media temp sweep of" in err and "RuntimeError: the media sweep fell over" in err, err
    assert "removed 1 stale voice job" in err, err


# --- a voice job removes its directory as a media job does: in tries, never raising -------------

def _hold_the_text(monkeypatch, times: int | None) -> list[str]:
    """os.unlink refuses a voice job's text.txt, as a just-killed engine (its venv's python.exe is
    a launcher, and the interpreter it started dies a little after it) or a file filter holding it
    does: the first `times` times, or every time (None). The old cleanup's Path.unlink and the
    removal's shutil.rmtree both delete through os.unlink. Returns the refusals, as they happen."""
    real = os.unlink
    refused: list[str] = []

    def unlink(path, *a, **k):
        name = os.fspath(path)
        if name.endswith("text.txt") and (times is None or len(refused) < times):
            refused.append(name)
            raise PermissionError(13, "The process cannot access the file because it is being "
                                      "used by another process", name)
        return real(path, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(media, "_REMOVE_PAUSE", 0.01)
    return refused


def test_a_voice_job_whose_text_is_held_a_moment_still_removes_its_directory(
        media_temp, voice_job, monkeypatch, capsys):
    """The removal is tried again, so the job answers and its directory is gone. The cleanup's
    unlink used to raise the first refusal out of the job in place of the wav."""
    system_temp, _settings = media_temp
    run, _seen = voice_job
    refused = _hold_the_text(monkeypatch, times=2)
    assert asyncio.run(run()) == b"RIFF-not-really"
    assert len(refused) == 2 and list(system_temp.iterdir()) == []
    assert "could not be removed" not in capsys.readouterr().err


@pytest.mark.parametrize("ending", ["fails", "times-out", "cancelled"])
def test_a_voice_job_s_directory_refused_every_time_is_logged_and_the_job_keeps_its_own_error(
        media_temp, voice_job, monkeypatch, capsys, ending):
    """A text held past every try: the give-up line names the directory once, the directory is
    left for the next start's sweep, and the job ends as it would have, a VoiceError or a
    cancellation. The cleanup's unlink used to raise its PermissionError in their place, so a
    timeout or a cancel reached the caller as a permission fault."""
    from app import voice
    run, seen = voice_job
    refused = _hold_the_text(monkeypatch, times=None)

    async def scenario():
        if ending == "fails":
            with pytest.raises(voice.VoiceError, match="voice engine failed"):
                await run("fails")
        elif ending == "times-out":
            with pytest.raises(voice.VoiceError, match="timed out"):
                await run("hangs", timeout=0.2)
        else:
            task = asyncio.ensure_future(run("hangs"))
            assert await _until(lambda: "dir" in seen, 10), "the engine never started"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    job_dir = seen["dir"]
    assert len(refused) == media._REMOVE_TRIES and job_dir.exists()
    err = capsys.readouterr().err
    assert err.count(f"[broker] job directory {job_dir} could not be removed after "
                     f"{media._REMOVE_TRIES} tries") == 1, err


def test_a_voice_job_cancelled_again_while_it_removes_its_directory_ends_once_it_is_gone(
        media_temp, voice_job, monkeypatch):
    """A cancellation can arrive twice (anyio delivers a disconnect's at every await; a server
    shutting down cancels a handler already cancelled). The job still ends only once its directory
    is gone, as a media job does (settle): a removal left running in its thread would end the job
    with the text still on disk."""
    run, seen = voice_job
    refused = _hold_the_text(monkeypatch, times=1)
    monkeypatch.setattr(media, "_REMOVE_PAUSE", 0.5)       # the window the second cancel lands in

    async def scenario() -> bool:
        task = asyncio.ensure_future(run("hangs"))
        assert await _until(lambda: "dir" in seen, 10), "the engine never started"
        task.cancel()
        assert await _until(lambda: bool(refused), 10), "the removal never began"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return seen["dir"].exists()

    assert asyncio.run(scenario()) is False, "the job ended with its directory still on disk"
