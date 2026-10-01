"""OpenAI-compatible surface, so any OpenAI client can point at the broker.

    OpenAI(base_url="http://<host>:11500/openai/v1", api_key=BROKER_AUTH_TOKEN)

Mounted under its OWN prefix rather than alongside the platform dialect at /v1.
The two surfaces disagree about exactly one path -- `GET /v1/models` returns
`{"models":[...]}` to rails and must return `{"object":"list","data":[...]}` to an
OpenAI client -- and serving both from one path means one of them is wrong. A
half-compatible /v1 where chat works and models returns a foreign shape is the
worst outcome for a client author, because it fails after the client has already
decided the endpoint is OpenAI.

WHAT THIS ADDS over pointing the client straight at Ollama, which already speaks
this dialect on :11434:

  * the GPU gate -- requests queue FIFO instead of racing two 18 GB loads onto a
    24 GB card
  * one-heavy-model eviction
  * @role indirection, so a client can ask for `@chat` and the admin repoints it
    live without touching the client
  * the disable flag, enforced here for the first time (see
    `Broker.resolve_for_external`)
  * the broker's bearer token, so the card is not reachable by anything on the LAN

Requests are FORWARDED, not translated: see `OllamaClient.openai_chat`.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from app.broker import (
    Broker,
    ModelDisabledError,
    NoSubstituteError,
    UnknownRoleError,
)

router = APIRouter(prefix="/openai/v1", tags=["openai-compat"])

# --- limits and capability boundaries -------------------------------------
#
# Chat and embeddings are forwarded to Ollama, which owns their shapes. Audio and
# images are NOT: they run on the broker's own Kokoro / faster-whisper / SDXL
# workers, so this module translates, and every capability OpenAI names that those
# workers cannot actually deliver is REFUSED rather than approximated.
#
# The temptation is to accept `response_format: "mp3"` and return a wav anyway,
# since most players sniff the container. That is the wrong trade: a strict client
# decodes by the declared type, and silent format substitution produces a corrupt
# file with no error anywhere. A 400 naming what IS available is debuggable in one
# request.

# Kokoro returns a 24 kHz wav and the broker ships no encoder, so these are the
# formats that can be served truthfully.
_AUDIO_FORMATS = {"wav", "pcm"}
# OpenAI's own default is mp3. A client that omits the field gets wav, which is the
# one substitution made deliberately: refusing an unset field would fail every
# default-constructed request, and wav is self-describing enough to sniff.
_AUDIO_DEFAULT = "wav"

# The six OpenAI voice names map to whatever the operator configured
# (BROKER_KOKORO_VOICE) rather than erroring: a client hardcoding "alloy" should
# get speech, not a stack trace. Any other value is passed through, so real Kokoro
# ids ("af_heart", "ef_dora") keep working.
_OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer", "ash", "coral", "sage"}

# faster-whisper here returns text plus a language and a duration, and no per-segment
# timings, so the two subtitle formats cannot be synthesised honestly.
_TRANSCRIBE_FORMATS = {"json", "text", "verbose_json"}

# Each image holds the whole card (media evicts every heavy model). A request for
# ten is ten serialised generations behind one HTTP call, long past any client
# timeout, so the cap is here rather than discovered as a hang.
_MAX_IMAGES = 4

_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9]{1,7}$")


def error(
    status: int,
    message: str,
    *,
    type_: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> JSONResponse:
    """OpenAI's error envelope.

    FastAPI's default is `{"detail": ...}`, which every OpenAI SDK misreads: the
    python client looks for `error.message` and shows `None` when it is absent, so a
    clear refusal from here would reach the user as a blank error. The shape is part
    of the contract, not decoration.
    """
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": type_, "param": param, "code": code}},
    )


async def _body(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        parsed = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as exc:
        return error(400, f"invalid JSON body: {exc}")
    if not isinstance(parsed, dict):
        return error(400, "request body must be a JSON object")
    return parsed


def _broker(request: Request) -> Broker:
    return request.app.state.broker


@router.get("/models")
async def list_models(request: Request) -> Any:
    try:
        data = await _broker(request).openai_models()
    except Exception as exc:  # noqa: BLE001
        return error(502, f"could not list models: {exc}", type_="api_error")
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    if not body.get("model"):
        return error(400, "you must provide a model parameter", param="model")
    if not body.get("messages"):
        return error(400, "you must provide a messages parameter", param="messages")

    broker = _broker(request)
    stream = bool(body.get("stream"))

    # Resolution and the disable check happen INSIDE the broker call, which for the
    # streaming path means inside the generator -- so a refusal would otherwise be
    # raised after StreamingResponse had already committed 200 and the client would
    # see an empty stream instead of an error. Resolve once up front to get the
    # refusal out as a real status code, then hand the SAME installed-model snapshot
    # to the broker call so its own resolve adds no second round trip.
    #
    # The second resolve is NOT free without that: `OllamaClient.tags()` is an uncached
    # GET and `resolve_ref` reads it for every '@role', not only for a glob -- and
    # '@role' is the documented way to use this surface. The snapshot is per-request by
    # construction; `Broker.installed_snapshot` says why it must not outlive one.
    ref = str(body["model"])
    try:
        tags = await broker.installed_snapshot(ref)
        _model, fb = await broker.resolve_for_external(ref, tags=tags)
    except ModelDisabledError as exc:
        return error(403, str(exc), type_="invalid_request_error",
                     param="model", code="model_disabled")
    except UnknownRoleError as exc:
        return error(400, str(exc), type_="invalid_request_error",
                     param="model", code="unknown_role")
    except NoSubstituteError as exc:
        # 409, which openai-python maps to its documented ConflictError, so this is not an
        # invented code. The state IS conflicted: the role names a model that cannot run and
        # nothing installed shares its capability. An admin fixes it by pulling or re-enabling.
        return error(409, str(exc), type_="invalid_request_error",
                     param="model", code="no_substitute")
    except Exception as exc:  # noqa: BLE001
        return error(400, f"could not resolve model {body['model']!r}: {exc}", param="model")

    # Headers, not a body field. The OpenAI response body belongs to somebody else's spec and
    # the `model` field in it ALREADY names the substitute (the broker rewrites it before
    # forwarding and Ollama echoes it back), so the body tells the truth about WHAT ran; this
    # explains WHY. Emitted before the body, so a stream announces before its first token.
    fb_headers = fb.headers() if fb is not None else {}

    if not stream:
        try:
            return JSONResponse(await broker.openai_chat(body, tags=tags),
                                headers=fb_headers)
        except Exception as exc:  # noqa: BLE001
            return error(502, f"chat completion failed: {exc}", type_="api_error")

    async def relay():
        try:
            async for chunk in broker.openai_chat_stream(body, tags=tags):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            # The status line is long gone, so the only way to tell the client is a
            # final SSE frame in the shape its parser already handles.
            payload = json.dumps(
                {"error": {"message": f"chat completion failed: {exc}",
                           "type": "api_error", "param": None, "code": None}}
            )
            yield f"data: {payload}\n\n".encode()

    return StreamingResponse(
        relay(),
        media_type="text/event-stream",
        # A buffering proxy would defeat streaming; say so explicitly rather than
        # relying on the deployment not to have one. `fb_headers` is empty unless a
        # substitution happened, so the common response is byte-identical to before.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **fb_headers},
    )


@router.post("/audio/speech")
async def audio_speech(request: Request) -> Any:
    """OpenAI text-to-speech onto the broker's Kokoro worker.

    Returns raw audio BYTES, not JSON -- that is OpenAI's contract here, and a client
    writes the body straight to a file.

    Deliberately the Kokoro (`tts_light`) path and not XTTS: Kokoro is CPU/ONNX,
    takes no GPU gate and evicts nothing, so an external client voicing a paragraph
    cannot displace a rail's resident chat model. XTTS voice-cloning stays internal,
    where the caller knows it is buying a GPU-gated job and wants the per-segment
    timings that this response shape has nowhere to put.
    """
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    text = body.get("input")
    if not text or not isinstance(text, str):
        return error(400, "you must provide an input parameter", param="input")

    fmt = str(body.get("response_format") or _AUDIO_DEFAULT).lower()
    if fmt not in _AUDIO_FORMATS:
        return error(
            400,
            f"response_format {fmt!r} is not available from this server; "
            f"supported: {', '.join(sorted(_AUDIO_FORMATS))}",
            param="response_format", code="unsupported_format",
        )

    speed = body.get("speed")
    if speed is not None:
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return error(400, "speed must be a number", param="speed")
        # Narrower than OpenAI's 0.25-4.0 because Kokoro's own bound is 0.5-2.0.
        # Clamping silently would return audio at a speed the caller did not ask for.
        if not 0.5 <= speed <= 2.0:
            return error(400, "speed must be between 0.5 and 2.0 on this server",
                         param="speed")

    voice = body.get("voice")
    if isinstance(voice, str) and voice.lower() in _OPENAI_VOICES:
        voice = None          # fall through to the operator's configured default
    elif voice is not None and not isinstance(voice, str):
        return error(400, "voice must be a string", param="voice")

    try:
        result = await _broker(request).tts_light(
            text, voice=voice, lang_code=body.get("lang_code"), speed=speed)
    except Exception as exc:  # noqa: BLE001
        return error(502, f"speech synthesis failed: {exc}", type_="api_error")

    try:
        audio = base64.b64decode(result["audio_b64"])
    except (KeyError, binascii.Error, TypeError) as exc:
        return error(502, f"speech worker returned no usable audio: {exc}", type_="api_error")

    if fmt == "pcm":
        # OpenAI's "pcm" is headerless 16-bit LE samples. Kokoro hands back a RIFF
        # wav, so the 44-byte canonical header comes off rather than being passed on
        # as if it were sample data -- a client feeding this to an audio device would
        # otherwise hear the header as a click.
        audio = audio[44:] if audio[:4] == b"RIFF" else audio
        return Response(content=audio, media_type="audio/pcm")
    return Response(content=audio, media_type="audio/wav")


@router.post("/audio/transcriptions")
async def audio_transcriptions(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),
    language: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    prompt: str | None = Form(default=None),
    temperature: float | None = Form(default=None),
) -> Any:
    """OpenAI speech-to-text (multipart) onto the broker's faster-whisper worker.

    `model` is accepted and ignored: the whisper model is an operator setting
    (BROKER_WHISPER_MODEL), not a per-request choice, and every OpenAI client sends
    the literal "whisper-1". Rejecting it would fail every stock client for a value
    it cannot vary. `prompt` and `temperature` are likewise accepted and ignored --
    the worker exposes neither.
    """
    fmt = (response_format or "json").lower()
    if fmt not in _TRANSCRIBE_FORMATS:
        return error(
            400,
            f"response_format {fmt!r} is not available from this server "
            f"(no per-segment timings); supported: {', '.join(sorted(_TRANSCRIBE_FORMATS))}",
            param="response_format", code="unsupported_format",
        )

    raw = await file.read()
    if not raw:
        return error(400, "the uploaded file is empty", param="file")

    audio_b64 = base64.b64encode(raw).decode()
    # The broker caps the base64 at 15 MB. Catch it here so the caller learns the real
    # limit in file terms instead of a 422 about a field it never sent.
    if len(audio_b64) > 15_000_000:
        return error(413, f"audio file is too large ({len(raw) // 1024} KB); "
                          "the limit is about 11 MB of audio", param="file")

    # This reaches a filename in a LocalSystem subprocess. media_worker has the
    # authoritative check; anything not matching the safe pattern is dropped rather
    # than sanitised, because a repaired suffix is a guess about the container format.
    suffix = None
    name = file.filename or ""
    if "." in name:
        candidate = "." + name.rsplit(".", 1)[1]
        if _SUFFIX_RE.match(candidate):
            suffix = candidate

    try:
        result = await _broker(request).transcribe(
            audio_b64, suffix=suffix, language=language)
    except Exception as exc:  # noqa: BLE001
        return error(502, f"transcription failed: {exc}", type_="api_error")

    text = result.get("text", "")
    if fmt == "text":
        return PlainTextResponse(text)
    if fmt == "verbose_json":
        return {
            "task": "transcribe",
            "language": result.get("language"),
            "duration": result.get("duration"),
            "text": text,
            # Present and empty, not omitted: the key is part of the shape, and a
            # client that iterates it should get nothing rather than a KeyError.
            "segments": [],
        }
    return {"text": text}


@router.post("/images/generations")
async def images_generations(request: Request) -> Any:
    """OpenAI image generation onto the broker's SDXL / FLUX worker.

    Only `b64_json` is offered. OpenAI's default is `url`, and this server has
    nowhere to host one -- returning a data: URI in a field a client will hand to an
    <img src> or a downloader is the kind of half-truth that fails later and
    somewhere else.
    """
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    prompt = body.get("prompt")
    if not prompt or not isinstance(prompt, str):
        return error(400, "you must provide a prompt parameter", param="prompt")

    fmt = str(body.get("response_format") or "b64_json").lower()
    if fmt != "b64_json":
        return error(400, "response_format must be 'b64_json' on this server "
                          "(no URL hosting is configured)",
                     param="response_format", code="unsupported_format")

    try:
        n = int(body.get("n") or 1)
    except (TypeError, ValueError):
        return error(400, "n must be an integer", param="n")
    if not 1 <= n <= _MAX_IMAGES:
        return error(400, f"n must be between 1 and {_MAX_IMAGES} on this server", param="n")

    size = body.get("size") or "512x512"
    if size == "auto":
        size = "512x512"
    match = re.fullmatch(r"(\d+)x(\d+)", str(size))
    if not match:
        return error(400, f"size {size!r} is not valid; use WIDTHxHEIGHT", param="size")
    width, height = int(match.group(1)), int(match.group(2))
    if width != height:
        return error(400, f"size {size!r} is not available; this server generates "
                          "square images only", param="size")
    if not 128 <= width <= 1536:
        return error(400, f"size {size!r} is out of range; between 128x128 and "
                          "1536x1536", param="size")

    # OpenAI's `model` is optional and its values ("dall-e-3", "gpt-image-1") mean
    # nothing here, so an unrecognised one falls back to the broker default rather
    # than failing. A media @role or a real backend name is honoured.
    model = body.get("model")
    known = model if isinstance(model, str) and (
        model.startswith("@") or model in {"sdxl-turbo", "flux-schnell"}) else "sdxl-turbo"

    try:
        result = await _broker(request).image(
            [prompt] * n, negative_prompt=body.get("negative_prompt"),
            size=width, model=known)
    except UnknownRoleError as exc:
        # _resolve_media raises this now. Without the arm, `{"model": "@nosuchrole"}` answered
        # 502 here while /v1/image answered 400 -- the same 400-vs-502 split this session set
        # out to remove, recreated one surface over.
        return error(400, str(exc), type_="invalid_request_error",
                     param="model", code="unknown_role")
    except Exception as exc:  # noqa: BLE001
        return error(502, f"image generation failed: {exc}", type_="api_error")

    images = [img for img in (result.get("images") or []) if img]
    if not images:
        errs = "; ".join(str(e) for e in (result.get("errors") or [])) or "no images returned"
        return error(502, f"image generation produced nothing: {errs}", type_="api_error")
    return {"created": int(time.time()), "data": [{"b64_json": img} for img in images]}


@router.post("/embeddings")
async def embeddings(request: Request) -> Any:
    body = await _body(request)
    if isinstance(body, JSONResponse):
        return body
    if not body.get("model"):
        return error(400, "you must provide a model parameter", param="model")
    if body.get("input") in (None, "", []):
        return error(400, "you must provide an input parameter", param="input")
    try:
        return await _broker(request).openai_embeddings(body)
    except ModelDisabledError as exc:
        return error(403, str(exc), param="model", code="model_disabled")
    except UnknownRoleError as exc:
        return error(400, str(exc), type_="invalid_request_error",
                     param="model", code="unknown_role")
    except NoSubstituteError as exc:
        return error(409, str(exc), type_="invalid_request_error",
                     param="model", code="no_substitute")
    except Exception as exc:  # noqa: BLE001
        return error(502, f"embeddings failed: {exc}", type_="api_error")
