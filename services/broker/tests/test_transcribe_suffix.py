"""The dictation clip's filename suffix is attacker-controlled, and it used to reach a path.

WHY THIS EXISTS. `media_worker._transcribe` built its temp clip as `Path(tmp) / f"clip{suffix}"`
and the next statement was `clip.write_bytes(base64.b64decode(audio_b64))`. Both halves come
straight from the request body, `suffix` had no constraint at any of the five hops between the
browser and the write, and a value of "/../../evil.dll" resolves OUTSIDE tmp -- measured on this
box, landing in %TEMP%, for both the "/" and "\\" separator forms.

What made it serious rather than untidy is who runs it. `POST /api/platform/transcribe` requires
only `require_user` (read-aloud and dictation are the two capabilities every rail gets for free,
by design), and `deploy/install-services.ps1` registers `platform-broker` with no ObjectName, so
it runs as LocalSystem and the media worker is its direct subprocess. Any logged-in user, any
rail container, or anything holding the shared broker token could therefore write chosen bytes to
a chosen path as SYSTEM.

Two layers, and the order matters. The sanitiser is asserted directly, AND `do_transcribe` is
run end to end against a stubbed `faster_whisper` so the assertion lands on the path the worker
actually wrote.

The functional half was added after an audit mutation-tested the original file and found it
toothless: its only guard on the authoritative site was a source-text check, which passes if the
sanitiser call is moved somewhere nothing invokes, or kept while `suffix` is reassigned on the
next line. Either restores the vulnerability with the suite green. This file previously claimed a
functional test was impossible because faster-whisper is not installed in the repo venv; that was
wrong -- the import sits INSIDE do_transcribe, so a stub in sys.modules is enough -- and being
wrong about it is what left the fix unguarded.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

BROKER = Path(__file__).resolve().parents[1]


def _load_media_worker():
    """Load app/media_worker.py by path.

    It is a script the broker spawns (`python media_worker.py in.json out.json`), not part of an
    importable package, and its heavy dependencies (torch, faster-whisper, diffusers) are all
    imported INSIDE the job functions. So the module imports fine on the repo venv, which is what
    makes this test runnable in the default suite.
    """
    name = "_broker_media_worker"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, BROKER / "app" / "media_worker.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mw():
    return _load_media_worker()


# Each of these, interpolated into `clip{suffix}`, either escapes the temp directory or turns the
# clip into something other than an audio file. The last two are the ones a validator written as
# "reject if it contains .." would let through.
@pytest.mark.parametrize("hostile", [
    "/../../evil.dll",
    "\\..\\..\\evil.dll",
    "/../evil.ps1",
    "../evil.txt",
    ".webm/../../evil.dll",
    "/absolute.dll",
    "C:/Windows/Temp/evil.dll",
    ".we/bm",
    ".we\\bm",
    "\x00.webm",
    ".webm\x00.dll",
    "",
    ".",
    "..",
    ".toolongextension",
    ".we bm",
])
def test_hostile_suffixes_fall_back_to_the_default(mw, hostile):
    got = mw.safe_clip_suffix(hostile)
    assert got == ".webm", f"{hostile!r} was accepted as {got!r}"


def test_hostile_suffixes_cannot_escape_the_temp_directory(mw):
    """The property that actually matters, asserted on a real path join rather than a regex."""
    tmp = Path(tempfile.mkdtemp(prefix="broker-stt-test-"))
    for hostile in ("/../../evil.dll", "\\..\\..\\evil.dll", "../evil.txt", "C:/Windows/evil.dll"):
        clip = tmp / f"clip{mw.safe_clip_suffix(hostile)}"
        assert clip.resolve().parent == tmp.resolve(), f"{hostile!r} escaped to {clip.resolve()}"


@pytest.mark.parametrize("ok", [".webm", ".ogg", ".mp3", ".m4a", ".wav", ".WEBM", ".mp4", ".oga"])
def test_the_real_container_hints_still_pass(mw, ok):
    """Chrome sends .webm, Firefox .ogg, Safari .mp4. Breaking these breaks dictation, so the
    fix has to be narrow rather than merely safe."""
    assert mw.safe_clip_suffix(ok) == ok


def test_none_and_non_strings_take_the_default(mw):
    for raw in (None, 0, [], {}, False):
        assert mw.safe_clip_suffix(raw) == ".webm"


def test_the_request_schema_rejects_traversal_at_the_edge(mw):
    """Defence in depth: the worker substitutes a safe default, while the HTTP boundary refuses
    the request outright so the caller is told rather than silently corrected."""
    sys.path.insert(0, str(BROKER))
    try:
        from app.schemas import TranscribeRequest
    finally:
        sys.path.pop(0)
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TranscribeRequest(audio_b64="AAAA", suffix="/../../evil.dll")
    with pytest.raises(ValidationError):
        TranscribeRequest(audio_b64="AAAA", suffix="../evil.txt")
    # The legitimate shapes, and the absent case, must still validate.
    assert TranscribeRequest(audio_b64="AAAA", suffix=".webm").suffix == ".webm"
    assert TranscribeRequest(audio_b64="AAAA").suffix is None


def test_the_write_site_uses_the_sanitiser(mw):
    """A source-text check, kept only as a cheap tripwire for the CALL disappearing.

    It is deliberately NOT the guard. It is order-blind: an audit mutation-tested this very
    assertion and found two ways to pass it while restoring the vulnerability — move the
    sanitiser call into a function nothing invokes and give the write site
    `spec.get("suffix")` again, or keep the call and reassign `suffix` on the NEXT line. Both
    read as "the substring is present". The functional test below is what actually holds, and
    this docstring exists so nobody mistakes this one for coverage.
    """
    src = (BROKER / "app" / "media_worker.py").read_text(encoding="utf-8")
    assert 'safe_clip_suffix(spec.get("suffix"))' in src


def _stub_whisper(monkeypatch, seen: list):
    """Put a fake `faster_whisper` in sys.modules so `do_transcribe` runs to completion.

    `from faster_whisper import WhisperModel` sits INSIDE do_transcribe, which is what makes
    this possible. The earlier version of this file claimed a functional test was impossible
    because faster-whisper is not installed in the repo venv; that was wrong, and the wrongness
    cost the fix its only real guard. The stub records the path it is handed, which is the
    assertion target: the bug was never about transcription, it was about where the bytes land.
    """
    import sys
    import types

    class _Seg:
        text = ""

    class _Info:
        language = "en"
        duration = 0.0

    class _WhisperModel:
        def __init__(self, *a, **kw) -> None:
            pass

        def transcribe(self, path, **kw):
            seen.append(path)
            return ([_Seg()], _Info())

    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = _WhisperModel        # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)


@pytest.mark.parametrize("hostile", [
    "/../../evil.dll",
    "\\..\\..\\evil.dll",
    "../evil.txt",
    ".webm/../../evil.dll",
    "C:/Windows/Temp/evil.dll",
])
def test_a_hostile_suffix_cannot_place_the_clip_outside_its_temp_dir(mw, monkeypatch, hostile):
    """The property the fix exists for, asserted on the REAL write.

    do_transcribe runs end to end here: it base64-decodes caller bytes, writes them, hands the
    path to the (stubbed) model, and deletes the directory. The assertion is on the path the
    worker actually wrote, so any future refactor that recomputes the suffix after sanitising
    it, or drops the sanitiser and keeps the substring, fails here.
    """
    seen: list = []
    _stub_whisper(monkeypatch, seen)

    out = mw.do_transcribe({"audio_b64": "QUFBQQ==", "suffix": hostile})

    assert out["language"] == "en"
    assert len(seen) == 1, "the worker never reached the model, so nothing was written"
    written = Path(seen[0])
    assert written.name == "clip.webm", f"hostile suffix survived into the filename: {written.name}"
    assert written.parent.name.startswith("broker-stt-"), (
        f"the clip was written outside a broker temp dir: {written}"
    )
    assert not written.exists(), "the temp directory should be removed on the way out"


def test_a_legitimate_suffix_reaches_the_filename(mw, monkeypatch):
    """The other half, so the test above cannot pass by the worker refusing everything."""
    seen: list = []
    _stub_whisper(monkeypatch, seen)
    mw.do_transcribe({"audio_b64": "QUFBQQ==", "suffix": ".ogg"})
    assert Path(seen[0]).name == "clip.ogg"


def test_the_bytes_written_are_the_caller_bytes(mw, monkeypatch):
    """Pins that this is a real exercise of the write path rather than a mocked no-op: if the
    decode or the write were skipped, the file the model is handed would not hold the payload."""
    seen: list = []
    observed: dict = {}
    _stub_whisper(monkeypatch, seen)

    real_open = Path.write_bytes

    def spy(self, data):
        observed["path"] = Path(self)
        observed["data"] = data
        return real_open(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy)
    mw.do_transcribe({"audio_b64": "QUFBQQ==", "suffix": ".webm"})

    assert observed["data"] == b"AAAA"
    assert observed["path"].name == "clip.webm"
