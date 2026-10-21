"""The Claude CLI backend: the text path's literal goldens, and a call with images (one
stream-json user message, the media type, the caps, the transcript, structured output).

Split out of test_cli_backend.py by concern on 2026-10-19 (its docstring says what the
backend's tests assert, and lists the files); the fakes and shared helpers are
cli_backend_fakes.py's.
"""
import builtins
import io
import json
import os
import pathlib
import tempfile
from unittest import mock

import pytest

from app import cli_backend
from app.cli_backend import (
    CliUnavailable,
    flatten_messages,
    sniff_image_type,
    vision_message,
)

# The fakes and helpers the test_cli_backend*.py files share. The fixtures come in by name,
# which is how pytest finds them here; group_kills and _system_temp are autouse.
from cli_backend_fakes import (  # noqa: F401
    REQUIRED_FLAGS,
    _FakeProc,
    _GIF87,
    _GIF89,
    _JPEG,
    _PNG,
    _PNG_B64,
    _SEEN,
    _SETTINGS,
    _WEBP,
    _b64,
    _blocks,
    _client,
    _img_q,
    _patch,
    _result,
    _system_temp,
    _transcript,
    group_kills,
)


# --- a call without images: exactly what it always was --------------------------------------
#
# Literal goldens, recorded from the last commit before images reached the CLI by
# running its own build_argv and chat() on these inputs. Not recomputed from the code under
# test, so any drift in the text path's argv or stdin fails here, by the byte.

_JSON_SYS = ("Reply with a single raw JSON value and nothing else. No prose before or after it, "
             "no markdown code fences, no explanation.")
_TEXT_HEAD = ["C:/fake/claude.exe", "-p", "--output-format", "json", "--tools", "",
              "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
              "--setting-sources", "", "--settings", _SETTINGS]


@pytest.mark.parametrize("model,system,want_json,schema,tail", [
    ("", "", False, None, []),
    ("", "be terse", False, None, ["--system-prompt", "be terse"]),
    ("", "be terse", True, None, ["--system-prompt", "be terse\n\n" + _JSON_SYS]),
    ("claude-haiku-5-5", "sys", True, {"type": "object"},
     ["--json-schema", '{"type": "object"}', "--model", "claude-haiku-5-5",
      "--system-prompt", "sys"]),
], ids=["plain", "system", "json", "schema-model"])
def test_a_call_without_images_builds_the_argv_it_always_did(model, system, want_json, schema,
                                                             tail):
    argv = _client(model=model).build_argv(system, want_json=want_json, schema=schema)
    assert argv == _TEXT_HEAD + tail


async def test_a_call_without_images_runs_the_argv_and_stdin_it_always_did(monkeypatch):
    proc = _FakeProc(_result('{"a": 1}'), b"", 0)
    seen = _patch(monkeypatch, proc)
    await _client(model="claude-haiku-5-5").chat(
        [{"role": "system", "content": "s1"}, {"role": "user", "content": "one"},
         {"role": "assistant", "content": "two"}, {"role": "user", "content": "thr\u00e9e"}],
        format="json")
    assert seen["argv"] == _TEXT_HEAD + ["--model", "claude-haiku-5-5",
                                         "--system-prompt", "s1\n\n" + _JSON_SYS]
    assert proc.stdin_seen == b"User: one\n\nAssistant: two\n\nUser: thr\xc3\xa9e"


async def test_an_empty_images_list_is_a_text_call(monkeypatch):
    """`images: []` is no image. It stays on the text path, argv and stdin as ever."""
    proc = _FakeProc(_result("ok"), b"", 0)
    seen = _patch(monkeypatch, proc)
    await _client().chat([{"role": "user", "content": "q", "images": []}])
    assert seen["argv"] == _TEXT_HEAD
    assert proc.stdin_seen == b"q"


# --- a call with images: one stream-json user message --------------------------------------

async def test_an_image_call_sends_one_stream_json_user_message(monkeypatch):
    """The wire format measured on claude 2.1.293: `--input-format stream-json --output-format
    stream-json --verbose`, and ONE user line on stdin holding the image as a base64 block and
    the text after it. Every isolation flag of the text path, in the text path's order."""
    proc = _FakeProc(_transcript("a red square"), b"", 0)
    seen = _patch(monkeypatch, proc)
    out = await _client(model="claude-haiku-5-5").chat(
        [{"role": "system", "content": "be terse"}] + _img_q(_PNG_B64))
    assert seen["argv"] == [
        "C:/fake/claude.exe", "-p", "--input-format", "stream-json", "--output-format",
        "stream-json", "--verbose", "--tools", "", "--strict-mcp-config",
        "--disable-slash-commands", "--no-session-persistence", "--setting-sources", "",
        "--settings", _SETTINGS, "--model", "claude-haiku-5-5", "--system-prompt", "be terse"]
    assert proc.stdin_seen == (
        b'{"type": "user", "message": {"role": "user", "content": [{"type": "image", '
        b'"source": {"type": "base64", "media_type": "image/png", "data": '
        b'"iVBORw0KGgpJSERSLWFuZC10aGUtcmVzdA=="}}, {"type": "text", "text": '
        b'"what is this"}]}}\n')
    assert proc.stdin_closed
    assert out["message"]["content"] == "a red square"


@pytest.mark.parametrize("flag", REQUIRED_FLAGS)
def test_an_image_argv_carries_each_isolation_flag(flag):
    argv = _client().build_argv("", want_json=False, stream_json=True)
    assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert json.loads(argv[argv.index("--settings") + 1])["claudeMdExcludes"]
    assert "json" not in argv, "the text path's --output-format json leaked into the image argv"


async def test_an_image_call_runs_in_the_same_workdir_with_the_same_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BROKER_AUTH_TOKEN", "full-scope")
    seen = _patch(monkeypatch, _FakeProc(_transcript(), b"", 0))
    client = _client(workdir=str(tmp_path), oauth_token="tok-1")
    await client.chat(_img_q(_PNG_B64))
    assert seen["cwd"] == str(tmp_path)
    assert seen["env"] == client.build_env(seen["env"]["TEMP"])
    assert "BROKER_AUTH_TOKEN" not in seen["env"]
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-1"


def test_images_come_before_their_turns_text_and_the_text_is_the_text_paths():
    """Each turn's images just before its words (Claude reads an image best ahead of the text
    about it), and the text blocks joined by a blank line ARE the text path's prompt."""
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "first", "images": [_b64(_PNG)]},
            {"role": "assistant", "content": "an answer"},
            {"role": "user", "content": "and these?", "images": [_b64(_JPEG), _b64(_GIF89)]}]
    system, stdin = vision_message(msgs)
    blocks = _blocks(stdin)
    assert [b["type"] if b["type"] == "text" else b["source"]["media_type"] for b in blocks] == [
        "image/png", "text", "text", "image/jpeg", "image/gif", "text"]
    stripped = [{k: v for k, v in m.items() if k != "images"} for m in msgs]
    assert (system, "\n\n".join(b["text"] for b in blocks if b["type"] == "text")) == (
        flatten_messages(stripped))


def test_an_image_alone_is_a_question():
    """A blank text block is left out (the API refuses an empty one); the image still goes."""
    _system, stdin = vision_message(_img_q(_b64(_PNG), text="   "))
    assert [b["type"] for b in _blocks(stdin)] == ["image"]


def test_an_image_on_a_system_turn_is_refused():
    """`--system-prompt` is text only, and moving the picture into the user turn would change
    what was asked. Refused, so the local model answers the question as it was put."""
    with pytest.raises(CliUnavailable) as exc:
        vision_message([{"role": "system", "content": "s", "images": [_b64(_PNG)]},
                        {"role": "user", "content": "q"}])
    assert exc.value.reason == "vision_unsupported"
    assert "system" in exc.value.detail


@pytest.mark.parametrize("images", [_PNG_B64, {"a": 1}], ids=["a-string", "a-dict"])
def test_images_that_are_not_a_list_are_refused(images):
    """The string is a WHOLE valid PNG, so it is refused for being a bare string and for
    nothing else: taken as a one-image list, it would be sent. (It was 'iVBORw0K', six bytes,
    which the sniffer refused anyway, so a string-to-list rewrap passed.)"""
    with pytest.raises(CliUnavailable) as exc:
        vision_message([{"role": "user", "content": "q", "images": images}])
    assert exc.value.reason == "vision_unsupported"
    assert "not a list" in exc.value.detail


async def test_chat_sends_the_images_of_every_user_turn(monkeypatch):
    """Through chat(): images on an EARLIER user turn arrive, each ahead of its own turn's words,
    when the last message carries none. Deciding the path by the last message alone would
    send this call down the text path, which refuses it."""
    proc = _FakeProc(_transcript("two dishes"), b"", 0)
    seen = _patch(monkeypatch, proc)
    out = await _client().chat([
        {"role": "user", "content": "q1", "images": [_PNG_B64]},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2", "images": [_b64(_JPEG)]},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "q3"}])
    assert seen["argv"][seen["argv"].index("--input-format") + 1] == "stream-json"
    assert [b["text"] if b["type"] == "text" else b["source"]["media_type"]
            for b in _blocks(proc.stdin_seen)] == [
        "image/png", "User: q1", "Assistant: a1", "image/jpeg", "User: q2", "Assistant: a2",
        "User: q3"]
    assert out["message"]["content"] == "two dishes"


@pytest.mark.parametrize("role", ["assistant", "Assistant", "ASSISTANT", "model", "tool", "bot"])
async def test_chat_refuses_an_image_on_an_assistant_turn(monkeypatch, role):
    """The stdin is ONE user message, so an image on any turn that is not the user's would arrive
    as the asker's own, and dropping it would leave the model answering about a picture it never
    saw. The role is a free string on the wire, so the rule is "exactly user", not "not
    assistant" (a missing or empty role is read as user, as the text path reads it). Refused
    like a system-turn image, before anything is started."""
    seen = _patch(monkeypatch, _FakeProc(_transcript(), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat([{"role": "user", "content": "q1"},
                              {"role": role, "content": "a1", "images": [_PNG_B64]},
                              {"role": "user", "content": "q2"}])
    assert exc.value.reason == "vision_unsupported"
    assert "the picture would arrive as the asker's own" in exc.value.detail
    assert "argv" not in seen


# --- the media type: sniffed, never declared ------------------------------------------------

@pytest.mark.parametrize("raw,media_type", [
    (_PNG, "image/png"), (_JPEG, "image/jpeg"), (_GIF87, "image/gif"), (_GIF89, "image/gif"),
    (_WEBP, "image/webp"),
], ids=["png", "jpeg", "gif87a", "gif89a", "webp"])
def test_the_media_type_is_sniffed_from_the_bytes(raw, media_type):
    assert sniff_image_type(raw) == media_type
    [block, _text] = _blocks(vision_message(_img_q(_b64(raw)))[1])
    assert block["source"] == {"type": "base64", "media_type": media_type, "data": _b64(raw)}


@pytest.mark.parametrize("raw", [
    b"BM" + b"\x00" * 30,                                   # BMP
    b"%PDF-1.7\n" + b"x" * 20,                              # PDF
    b"II*\x00" + b"x" * 20,                                 # TIFF
    b"RIFF" + (64).to_bytes(4, "little") + b"WAVEfmt ",     # RIFF, but audio
    b"just some text, not an image",
    b"",
], ids=["bmp", "pdf", "tiff", "riff-wave", "text", "empty"])
def test_a_format_claude_does_not_read_is_refused(raw):
    """Claude reads JPEG, PNG, GIF and WebP. Anything else falls back to the local model,
    rather than reaching the API under a media type it is not."""
    assert sniff_image_type(raw) is None
    with pytest.raises(CliUnavailable) as exc:
        vision_message(_img_q(_b64(raw)))
    assert exc.value.reason == "vision_unsupported"


@pytest.mark.parametrize("raw,media_type", [
    (bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]), "image/png"),
    (bytes([0xFF, 0xD8, 0xFF]) + b"\x00" * 64, "image/jpeg"),
], ids=["png-signature-alone", "jpeg-header-over-zeros"])
def test_only_the_header_is_checked(raw, media_type):
    """The magic bytes say which type to announce; nothing checks that the rest decodes. A
    bare PNG signature and a JPEG header over zeros are SENT, and are the API's to reject (see
    sniff_image_type for what then happens, and what is not measured). Pinned so the README's
    account of what is checked stays true of the code."""
    [block, _text] = _blocks(vision_message(_img_q(_b64(raw)))[1])
    assert block["source"] == {"type": "base64", "media_type": media_type, "data": _b64(raw)}


def test_a_data_url_is_refused_and_its_declared_type_never_sent():
    """The one way a caller could name a media type. Ollama's decoder refuses a data URL too,
    so the local answer is what it always was; the CLI never sees `image/jpeg` on a PNG."""
    with pytest.raises(CliUnavailable) as exc:
        vision_message(_img_q("data:image/jpeg;base64," + _b64(_PNG)))
    assert exc.value.reason == "vision_unsupported"


@pytest.mark.parametrize("b64", [
    _PNG_B64[:8] + "!" + _PNG_B64[8:],          # a character outside the alphabet
    _PNG_B64.rstrip("="),                       # the padding missing
    _PNG_B64[:8] + " " + _PNG_B64[8:],          # a space, which Ollama's decoder refuses too
], ids=["bad-character", "no-padding", "space"])
def test_invalid_base64_is_refused_not_repaired(b64):
    """A repaired image is a different image. A decoder that skipped what it did not expect
    would hand the first and the last over as a perfectly good PNG, which is exactly why a
    lenient decoder is wrong here."""
    with pytest.raises(CliUnavailable) as exc:
        vision_message(_img_q(b64))
    assert exc.value.reason == "vision_unsupported"


def test_line_breaks_in_the_base64_are_dropped_as_ollama_drops_them():
    """MIME-style line breaks are the one thing Ollama's own decoder (Go's standard base64)
    skips. Tolerated the same way, and what is sent is the canonical encoding, no breaks."""
    wrapped = "\r\n".join(_PNG_B64[i:i + 4] for i in range(0, len(_PNG_B64), 4))
    [block, _text] = _blocks(vision_message(_img_q(wrapped))[1])
    assert block["source"]["data"] == _PNG_B64


@pytest.mark.parametrize("image", [b"raw bytes", 42, None], ids=["bytes", "int", "none"])
def test_an_image_that_is_not_a_string_is_refused(image):
    with pytest.raises(CliUnavailable) as exc:
        vision_message([{"role": "user", "content": "q", "images": [_b64(_PNG), image]}])
    assert exc.value.reason == "vision_unsupported"


# --- caps: Claude's own limits or below ------------------------------------------------------

def test_the_caps_are_claudes_own_or_below():
    """The Vision guide (read 2026-10-09): 5 MB base64 per image on Bedrock and Google Cloud
    (10 MB direct), a stricter 2000 px rule for every image past 20 in a request, and a 32 MB
    request. Raising any of these past that turns a local answer into an API error and a
    wasted CLI call, or worse, a silent downscale nobody asked for."""
    assert cli_backend._IMAGE_MAX_B64 <= 5_000_000
    assert cli_backend._IMAGE_MAX_COUNT <= 20
    assert cli_backend._IMAGES_MAX_TOTAL_B64 <= 32_000_000 - 8_000_000


def test_an_image_over_the_per_image_cap_is_refused(monkeypatch):
    big = _b64(_PNG + b"x" * 300)
    monkeypatch.setattr(cli_backend, "_IMAGE_MAX_B64", len(big))
    vision_message(_img_q(big))                              # at the cap: sent
    monkeypatch.setattr(cli_backend, "_IMAGE_MAX_B64", len(big) - 1)
    with pytest.raises(CliUnavailable) as exc:
        vision_message(_img_q(big))
    assert exc.value.reason == "vision_unsupported" and "cap" in exc.value.detail


def test_more_images_than_the_cap_are_refused_counted_across_the_call():
    eleven = [{"role": "user", "content": "a", "images": [_b64(_PNG)] * 11},
              {"role": "assistant", "content": "b"}]
    vision_message(eleven + [{"role": "user", "content": "c", "images": [_b64(_PNG)] * 9}])
    with pytest.raises(CliUnavailable) as exc:
        vision_message(eleven + [{"role": "user", "content": "c", "images": [_b64(_PNG)] * 10}])
    assert exc.value.reason == "vision_unsupported" and "21 images" in exc.value.detail


def test_images_over_the_total_cap_are_refused(monkeypatch):
    one = _b64(_PNG)
    monkeypatch.setattr(cli_backend, "_IMAGES_MAX_TOTAL_B64", 3 * len(one))
    vision_message(_img_q(one, one, one))
    with pytest.raises(CliUnavailable) as exc:
        vision_message(_img_q(one, one, one, one))
    assert exc.value.reason == "vision_unsupported"


async def test_a_refused_image_never_starts_the_cli(monkeypatch):
    seen = _patch(monkeypatch, _FakeProc(_transcript(), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_b64(b"not an image")))
    assert exc.value.reason == "vision_unsupported"
    assert "argv" not in seen


# --- reading the transcript ------------------------------------------------------------------

async def test_an_image_call_reads_the_result_line_and_its_usage(monkeypatch):
    _patch(monkeypatch, _FakeProc(_transcript(
        "Red", usage={"input_tokens": 2, "cache_creation_input_tokens": 2351,
                      "cache_read_input_tokens": 535, "output_tokens": 4},
        duration_ms=1636, total_cost_usd=0.0006, num_turns=1,
        modelUsage={"claude-haiku-5-5": {"inputTokens": 1180}}), b"", 0))
    out = await _client().chat(_img_q(_PNG_B64))
    assert out["message"] == {"role": "assistant", "content": "Red"}
    assert out["cli"] == {"backend": "claude", "input_tokens": 2888, "output_tokens": 4,
                          "duration_ms": 1636, "total_cost_usd": 0.0006, "num_turns": 1}
    assert "prompt_eval_count" not in out


async def test_an_is_error_result_line_falls_back_classified(monkeypatch):
    """Exit 0 with `is_error: true` on the result line: the in-band refusal, as on the text
    path. Delivering its sentence as the answer would hand a rail an error message as content."""
    _patch(monkeypatch, _FakeProc(_transcript("Claude usage limit reached", is_error=True),
                                  b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "rate_limited"


async def test_a_transcript_without_a_result_line_falls_back(monkeypatch):
    """The CLI died, or the protocol moved, after the assistant line. Its text is NOT the
    answer: a turn that never finished is refused rather than pieced together."""
    _patch(monkeypatch, _FakeProc(_transcript("half an answer", result=False), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "bad_stream"


async def test_an_empty_transcript_falls_back(monkeypatch):
    _patch(monkeypatch, _FakeProc(b"", b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "empty"


async def test_a_line_that_is_not_json_does_not_hide_the_result(monkeypatch):
    lines = _transcript("Blue").split(b"\n")
    _patch(monkeypatch, _FakeProc(b"\n".join(lines[:2] + [b"{not json", b"plain words"]
                                             + lines[2:]), b"", 0))
    out = await _client().chat(_img_q(_PNG_B64))
    assert out["message"]["content"] == "Blue"


async def test_a_failed_image_call_reports_the_result_sentence(monkeypatch):
    """Exit 1 with empty stderr and the reason in the transcript's result line, the shape a bad
    token takes on the text path. The init record that opens the transcript says nothing."""
    _patch(monkeypatch, _FakeProc(_transcript(
        "Failed to authenticate. API Error: 401 Invalid bearer token", is_error=True), b"", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "not_signed_in"
    assert exc.value.detail.startswith("Failed to authenticate")


def _cut(stdout: bytes) -> bytes:
    """The transcript with its last line cut off mid-write, as a kill leaves it."""
    return stdout.rstrip(b"\n")[:-40]


@pytest.mark.parametrize("stdout,lines", [
    (_transcript(_SEEN, result=False), 3),
    (_cut(_transcript(_SEEN, result=False)), 3),
    (_transcript(_SEEN, result=False) + b"Error: the usage limit was reached\n", 4),
    (b'{"type": "system", "subtype": "init", "cwd": "C:/svc/work", "session_id": "s-77"}\n', 1),
], ids=["assistant-line-last", "assistant-line-cut", "a-plain-line-last", "init-only"])
async def test_a_failed_image_call_without_an_error_sentence_records_no_transcript_line(
        monkeypatch, stdout, lines):
    """Exit 1, stderr empty and no is_error result line: the CLI killed from outside (taskkill
    /F) after the assistant line and before the result line, or dead right after init. The
    detail reaches the CLI FALLBACK log line and cli.fallbacks.last on /v1/status, so it is a
    fixed sentence with the exit code and the line count, never a line of the transcript: the
    assistant line is the model's description of the picture, and this fixture's init record
    names a workdir and a session. The reason is `failed`, not one guessed from the model's
    words."""
    _patch(monkeypatch, _FakeProc(stdout, b"", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "failed"
    assert exc.value.detail == (f"the CLI exited with code 1 and printed no error sentence; "
                                f"its transcript ({lines} lines) is not recorded")
    for leaked in ("worksheet", "pupil", "usage limit", "C:/svc/work", "s-77"):
        assert leaked not in str(exc.value)


async def test_a_failed_image_call_reports_its_stderr(monkeypatch):
    """With no is_error result line, stderr is the CLI's own words, as on the text path."""
    _patch(monkeypatch, _FakeProc(_transcript(_SEEN, result=False),
                                  b"Error: Claude usage limit reached\n", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "rate_limited"
    assert exc.value.detail == "Error: Claude usage limit reached"


async def test_a_nonzero_exit_after_a_success_result_line_is_refused_and_not_quoted(monkeypatch):
    """Exit 1 although the transcript ends in a result line with an answer and no is_error. The
    exit code wins: the call falls back. And the answer, the model's account of the picture,
    is not the failure's detail either."""
    _patch(monkeypatch, _FakeProc(_transcript(_SEEN), b"", 1))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64))
    assert exc.value.reason == "failed"
    assert "worksheet" not in str(exc.value) and "(4 lines)" in exc.value.detail


async def test_a_nonzero_exit_of_a_text_call_is_refused_whatever_its_result_says(monkeypatch):
    """The text path's twin: a result object with an answer and is_error false, on exit 1."""
    _patch(monkeypatch, _FakeProc(_result("an answer"), b"", 1))
    with pytest.raises(CliUnavailable):
        await _client().chat([{"role": "user", "content": "q"}])


# --- structured output and accounting on an image call ---------------------------------------

async def test_an_image_call_with_a_schema_uses_the_cli_s_json_schema(monkeypatch):
    """Measured 2026-10-09: `--json-schema` works with stream-json input, and the result line
    carries the JSON as text (and as `structured_output`). So the text path's rule holds: the
    CLI enforces the shape, the instruction is not added."""
    seen = _patch(monkeypatch, _FakeProc(_transcript('{"colour":"Blue"}'), b"", 0))
    schema = {"type": "object", "properties": {"colour": {"type": "string"}}}
    out = await _client().chat([{"role": "system", "content": "You name colours."}]
                               + _img_q(_PNG_B64), format=schema)
    argv = seen["argv"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == schema
    assert argv[argv.index("--system-prompt") + 1] == "You name colours."
    assert json.loads(out["message"]["content"]) == {"colour": "Blue"}


async def test_bare_json_mode_on_an_image_call_is_instructed_and_validated(monkeypatch):
    seen = _patch(monkeypatch, _FakeProc(_transcript("It is purple."), b"", 0))
    with pytest.raises(CliUnavailable) as exc:
        await _client().chat(_img_q(_PNG_B64), format="json")
    assert exc.value.reason == "bad_json"
    assert seen["argv"][seen["argv"].index("--system-prompt") + 1] == _JSON_SYS
    assert "--json-schema" not in seen["argv"]


async def test_an_image_call_reports_the_options_it_could_not_honour(monkeypatch):
    _patch(monkeypatch, _FakeProc(_transcript(), b"", 0))
    out = await _client().chat(_img_q(_PNG_B64), options={"temperature": 0.2}, think=False)
    assert out["cli"]["dropped"] == ["temperature", "think"]


async def test_an_image_is_never_written_to_disk(monkeypatch, tmp_path):
    """Images reach the CLI on stdin and nowhere else: a student's worksheet or a family photo
    left in a temp directory outlives the call. Every way Python writes a file is made to fail
    for the length of the call, and the workdir must still be empty after."""
    seen = _patch(monkeypatch, _FakeProc(_transcript("ok"), b"", 0))

    def refuse(*_a, **_k):
        raise AssertionError("the image call touched the filesystem")

    patches = [mock.patch.object(builtins, "open", refuse), mock.patch.object(io, "open", refuse),
               mock.patch.object(os, "open", refuse),
               mock.patch.object(pathlib.Path, "write_bytes", refuse),
               mock.patch.object(pathlib.Path, "write_text", refuse),
               mock.patch.object(pathlib.Path, "open", refuse),
               mock.patch.object(tempfile, "mkstemp", refuse),
               mock.patch.object(tempfile, "NamedTemporaryFile", refuse),
               mock.patch.object(tempfile, "TemporaryFile", refuse)]
    for p in patches:
        p.start()
    try:
        out = await _client(workdir=str(tmp_path)).chat(_img_q(_PNG_B64))
    finally:
        for p in reversed(patches):
            p.stop()
    assert out["message"]["content"] == "ok"
    assert list(tmp_path.iterdir()) == []
    assert not any(_PNG_B64 in a for a in seen["argv"]), "the image went on the command line"
