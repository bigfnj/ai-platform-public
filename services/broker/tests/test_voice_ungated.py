"""Voice must stay OFF the GPU gate, and STT must stay multilingual.

These are STRUCTURAL tests on purpose. An end-to-end "does it return audio" test passes just
as happily when tts_light is routed through ``_run_media()`` — it would still return audio,
just after queueing behind chat and evicting the model the user is talking to. The property
that makes platform-wide voice shippable is not "it works", it is "it does not take the gate
and does not evict", so that is what these assert.

The same reasoning covers the whisper model id: a ``.en`` build returns text, so a round-trip
test goes green while every non-English utterance is quietly mangled.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from app.broker import Broker
from app.config import BrokerSettings

_SRC = Path(__file__).resolve().parents[1] / "app" / "broker.py"

# The two calls that would silently make voice unshippable if they appeared in these methods.
_GATE_CALL = "gate.hold"
_EVICT_CALL = "_evict_other_heavy"

VOICE_METHODS = ("tts_light", "transcribe")


def _method_source(name: str) -> str:
    """The method's EXECUTABLE code — docstring and comments stripped.

    Matching raw source is not good enough: tts_light's own docstring says "no gate.hold(), no
    _evict_other_heavy()" to document the property, and a naive substring check reads that as a
    violation. ast.unparse drops docstrings and comments, leaving only what actually runs, so
    prose about the gate can never be mistaken for use of it.
    """
    src = textwrap.dedent(inspect.getsource(getattr(Broker, name)))
    fn = ast.parse(src).body[0]
    body = list(getattr(fn, "body", []))
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring
    return "\n".join(ast.unparse(n) for n in body)


@pytest.mark.parametrize("name", VOICE_METHODS)
def test_voice_method_does_not_take_the_gate(name: str) -> None:
    """A mic press must not queue behind an in-flight chat completion."""
    src = _method_source(name)
    assert _GATE_CALL not in src, (
        f"Broker.{name}() references {_GATE_CALL}. Voice is called mid-conversation from any "
        f"rail; behind the gate, pressing the mic waits for chat to finish."
    )


@pytest.mark.parametrize("name", VOICE_METHODS)
def test_voice_method_does_not_evict(name: str) -> None:
    """Speaking must not drop the model the user is talking to."""
    src = _method_source(name)
    assert _EVICT_CALL not in src, (
        f"Broker.{name}() references {_EVICT_CALL}. Evicting per utterance unloads the resident "
        f"model and reloads it afterwards, which is worse than having no voice."
    )


@pytest.mark.parametrize("name", VOICE_METHODS)
def test_voice_method_calls_the_worker_directly(name: str) -> None:
    """The embed_image() precedent: run_media_job directly, never via _run_media()."""
    src = _method_source(name)
    assert "run_media_job" in src, f"Broker.{name}() should call media.run_media_job directly"
    assert "_run_media(" not in src, (
        f"Broker.{name}() goes through _run_media(), which holds the gate and evicts. Call "
        f"media.run_media_job directly."
    )


def test_run_media_still_gates_and_evicts() -> None:
    """Guards the premise of the tests above.

    If _run_media ever stopped gating, the assertions that voice avoids it would still pass but
    would no longer mean anything — the tests would be measuring the wrong thing and reporting
    green. So assert the gated path is still the gated path.
    """
    src = _method_source("_run_media")
    assert _GATE_CALL in src, "_run_media no longer holds the gate; the voice tests lost their point"
    assert _EVICT_CALL in src, "_run_media no longer evicts; the voice tests lost their point"


def test_whisper_default_is_multilingual() -> None:
    """A '.en' build is English-only and IGNORES the language parameter rather than erroring.

    Measured on the dev box: under ``small.en`` the Spanish sentence "La reunion con el cliente
    es el martes por la manana" came back as "La reu?a un con el cliente es el martes por la
    manana" with language reported as "en"; under ``small`` it came back verbatim. Nothing in
    the response says the parameter was dropped, which is why this needs a test rather than
    care.
    """
    model = BrokerSettings().whisper_model
    assert not model.endswith(".en"), (
        f"whisper_model={model!r} is an English-only build. It silently ignores `language` and "
        f"turns other languages into nonsense. Use the multilingual id (e.g. 'small')."
    )


def test_voice_ops_can_route_to_their_own_interpreter() -> None:
    """kokoro-onnx wants numpy>=2.0.2; the image stack's simple-lama-inpainting wants <2.0.0.

    They cannot share a venv, so the voice ops have to be able to point at a different
    interpreter without the image ops following them there.
    """
    s = BrokerSettings(media_python="C:/image/python.exe", media_python_voice="C:/voice/python.exe")
    assert s.media_python_for("kokoro_tts") == "C:/voice/python.exe"
    assert s.media_python_for("transcribe") == "C:/voice/python.exe"
    assert s.media_python_for("image") == "C:/image/python.exe"
    assert s.media_python_for("tts") == "C:/image/python.exe"  # XTTS stays with the torch venv


def test_voice_interpreter_falls_back_when_unset() -> None:
    """A deployment that only ever configured one interpreter must keep working untouched."""
    s = BrokerSettings(media_python="C:/only/python.exe", media_python_voice="")
    assert s.media_python_for("kokoro_tts") == "C:/only/python.exe"
    assert s.media_python_for("transcribe") == "C:/only/python.exe"


def test_tts_light_defaults_voice_and_language_together() -> None:
    """Voice ids are language-scoped by prefix, so resolving one without the other garbles audio.

    Asserted on the source rather than by calling it, because calling requires a media worker:
    both defaults must be read in the same method that builds the spec.
    """
    src = _method_source("tts_light")
    assert "kokoro_voice" in src and "kokoro_lang_code" in src, (
        "tts_light must default BOTH voice and lang_code from settings. Defaulting only one "
        "phonemises a Spanish voice as English, which produces noise rather than an accent."
    )


def test_speed_is_omitted_rather_than_nulled() -> None:
    """An unset optional should be absent from the spec, not present as null."""
    src = _method_source("tts_light")
    assert "if speed is not None:" in src, (
        "tts_light should only set spec['speed'] when a speed was given"
    )


def test_no_voice_role_remains() -> None:
    """Voice is a platform capability configured by BROKER_KOKORO_VOICE, not a per-rail @role.

    The removed @smb-partner-voice resolved to "kokoro", the only light TTS backend, so it could
    not select anything: a control that looked authoritative and steered nothing.
    """
    from app.config import DEFAULT_ROLES

    voiceish = [r for r in DEFAULT_ROLES if "voice" in r]
    assert not voiceish, (
        f"voice role(s) {voiceish} are back in DEFAULT_ROLES. Voice has one backend, so a role "
        f"cannot select anything; set BROKER_KOKORO_VOICE instead."
    )


def test_broker_module_parses_as_expected_shape() -> None:
    """Cheap guard that the methods under test still exist as async defs on Broker.

    inspect.getsource silently returns a decorator or a stub if these get renamed or wrapped,
    and every assertion above would then pass against the wrong text.
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Broker")
    names = {n.name for n in cls.body if isinstance(n, ast.AsyncFunctionDef)}
    for m in (*VOICE_METHODS, "_run_media"):
        assert m in names, f"Broker.{m} is no longer an async def; the source assertions are void"
