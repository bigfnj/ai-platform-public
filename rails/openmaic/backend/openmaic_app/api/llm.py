"""An OpenAI-compatible facade over the Ollama-native broker.

WHY THIS EXISTS. OpenMAIC talks to model providers in the OpenAI shape: POST
/v1/chat/completions, sampling at the top level, SSE deltas at choices[0].delta.content. The
platform broker speaks Ollama: POST /v1/chat (no /completions), sampling nested under
``options`` with ``num_predict``, NDJSON frames with the text at message.content. Nothing
bridges them, so this does.

Pointing OpenMAIC straight at Ollama would also work and would need none of this. It is the
wrong answer on this platform: the broker owns the single-heavy-model VRAM policy, and
generating one course is a burst of dozens of LLM calls. Bypassing the gate means those calls
evict whatever another rail had resident, over and over, on a card with room for one heavy
model. Going through the broker also keeps ``@role`` indirection working, which is what makes
Admin -> Rails authoritative for this rail rather than decorative.

AUTHENTICATION IS DIFFERENT HERE, DELIBERATELY. Every other route on this rail is gated by
X-Platform-User, because every other caller is a browser coming through the gateway. This
caller is not: it is the openmaic-app container calling a sibling on the compose network, and
it has no platform identity to present. So the shim is a MOUNTED SUB-APPLICATION with its own
dependency rather than a router under the parent's gate — a mount is not a route, so the
parent's app-level dependency would never run for it anyway, and inheriting a gate by accident
is how a hole gets left open. It accepts either credential: a platform identity (a human
poking at it through the gateway) or the shared service token.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from contextlib import aclosing
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import broker
from ..config import settings
from .identity import standalone

_log = logging.getLogger("openmaic.llm")

# Shared service token for container-to-container calls. Compose passes the same value to this
# rail and to openmaic-app. Empty => the token check cannot pass, and only a platform identity
# (or PLATFORM_STANDALONE) gets in; it never silently degrades to "open".
_SHIM_TOKEN = os.environ.get("OPENMAIC_SHIM_TOKEN", "").strip()


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    return authorization[7:].strip() if authorization[:7].lower() == "bearer " else ""


def llm_caller(
    authorization: str | None = Header(default=None),
    x_platform_user: str | None = Header(default=None),
) -> str:
    """Fail closed. Returns a label for logging; raises 401 when neither credential is present."""
    if x_platform_user:
        return x_platform_user
    supplied = _bearer(authorization)
    if _SHIM_TOKEN and supplied and secrets.compare_digest(supplied, _SHIM_TOKEN):
        return "openmaic-app"
    if standalone():
        return "standalone"
    raise HTTPException(status_code=401,
                        detail="unauthenticated (no platform identity or service token)")


shim = FastAPI(title="OpenMAIC LLM shim", docs_url=None, redoc_url=None, openapi_url=None,
               dependencies=[Depends(llm_caller)])


# --- request translation ----------------------------------------------------------------------

def _options(body: dict) -> dict:
    """OpenAI top-level sampling -> Ollama ``options``.

    Only keys the caller actually sent are forwarded: Ollama applies the model's own defaults
    for anything absent, and materialising our own defaults here would silently override the
    Modelfile for every request.
    """
    opts: dict[str, Any] = {}
    # max_completion_tokens is the current spelling; max_tokens is the one OpenMAIC still sends.
    for src in ("max_completion_tokens", "max_tokens"):
        if isinstance(body.get(src), int):
            opts["num_predict"] = body[src]
            break
    for src, dst in (("temperature", "temperature"), ("top_p", "top_p"),
                     ("frequency_penalty", "frequency_penalty"),
                     ("presence_penalty", "presence_penalty"), ("seed", "seed")):
        if body.get(src) is not None:
            opts[dst] = body[src]
    stop = body.get("stop")
    if isinstance(stop, str):
        opts["stop"] = [stop]
    elif isinstance(stop, list) and stop:
        opts["stop"] = stop
    return opts


def _data_url_payload(url: str) -> str | None:
    """The base64 payload of a `data:` URL, which is how OpenAI clients inline an image.

    A remote http(s) image URL returns None: Ollama wants bytes, not a link, and silently
    answering from the prompt alone would be the same failure this function exists to stop.
    """
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    _, _, payload = url.partition(",")
    return payload or None


def _messages(body: dict) -> list[dict]:
    """Normalise OpenAI message content into the Ollama shape.

    OpenAI allows content to be a list of typed parts. Ollama wants a string plus a separate
    ``images`` list, and an unhandled list arrives at the model as the repr of a Python list —
    which looks like a working call and produces quietly terrible output.

    Images are extracted rather than discarded. Dropping them is the SAME class of bug one part
    type over: upload a scanned page, ask for a class about it, and the model answers from the
    surrounding prompt alone — confidently, with a 200, and no way to tell from the response that
    it never saw the picture.
    """
    out: list[dict] = []
    for m in body.get("messages") or []:
        content, images = m.get("content"), []
        if isinstance(content, list):
            text_parts = []
            for p in content:
                if not isinstance(p, dict):
                    continue
                if p.get("type") == "text":
                    text_parts.append(p.get("text", ""))
                elif p.get("type") == "image_url":
                    payload = _data_url_payload((p.get("image_url") or {}).get("url", ""))
                    if payload:
                        images.append(payload)
            content = "".join(text_parts)
        msg: dict[str, Any] = {"role": m.get("role") or "user", "content": content or ""}
        if images:
            msg["images"] = images
        out.append(msg)
    return out


def _response_format(body: dict) -> str | dict | None:
    """OpenAI ``response_format`` -> Ollama ``format``.

    Both spellings are handled: ``json_object`` is the older one OpenMAIC sends, ``json_schema``
    is current, and the broker takes a dict schema straight through. Matching only the first
    means a structured-output request degrades to free text with a 200 — the model was never put
    in JSON mode, so the caller's parse fails on prose.

    The defensive isinstance is not paranoia: a client sending a bare string here used to raise
    AttributeError inside the route and escape the OpenAI error envelope as a bare 500.
    """
    rf = body.get("response_format")
    if not isinstance(rf, dict):
        return None
    kind = rf.get("type")
    if kind == "json_object":
        return "json"
    if kind == "json_schema":
        schema = (rf.get("json_schema") or {}).get("schema")
        return schema if isinstance(schema, dict) else "json"
    return None


def _model(body: dict) -> str:
    """The model to ask for. An ``@role`` passes through untouched for the broker to expand."""
    return str(body.get("model") or settings.llm_model)


def _usage(frame: dict) -> dict:
    """Ollama's token counters -> the OpenAI ``usage`` block."""
    prompt = int(frame.get("prompt_eval_count") or 0)
    completion = int(frame.get("eval_count") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion}


# --- routes -------------------------------------------------------------------------------------

@shim.get("/v1/models")
def list_models() -> dict:
    """Installed models plus the platform's ``@role`` aliases.

    The roles are listed first and on purpose: picking ``@openmaic`` in OpenMAIC's model
    selector is what keeps Admin -> Rails in charge of which model this rail actually uses.
    Choosing a concrete name pins it and that panel stops mattering.
    """
    created = int(time.time())
    data: list[dict] = []
    try:
        for r in broker.roles():
            role = r.get("role")
            if role:
                data.append({"id": "@" + str(role), "object": "model", "created": created,
                             "owned_by": "platform-broker", "resolved": r.get("resolved")})
        for m in broker.models():
            name, disabled = m.get("name"), m.get("disabled")
            if name and not disabled:
                data.append({"id": name, "object": "model", "created": created,
                             "owned_by": "ollama"})
    except broker.BrokerError as exc:
        _log.warning("model list unavailable: %s", exc)
    return {"object": "list", "data": data}


@shim.post("/v1/embeddings")
async def embeddings(request: Request) -> dict:
    body = await request.json()
    raw = body.get("input")
    inputs = [raw] if isinstance(raw, str) else [str(x) for x in (raw or [])]
    model = str(body.get("model") or settings.embed_model)
    try:
        vectors = await broker.embed(model, inputs)
    except broker.BrokerError as exc:
        raise HTTPException(status_code=502, detail="broker embed failed: " + str(exc)) from exc
    return {
        "object": "list",
        "model": model,
        "data": [{"object": "embedding", "index": i, "embedding": v}
                 for i, v in enumerate(vectors)],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


def _finish_reason(frame: dict) -> str:
    """Ollama's ``done_reason`` -> the OpenAI ``finish_reason``.

    Hardcoding "stop" was the bug this replaces: a reply cut off at ``num_predict`` reports
    ``done_reason: "length"``, and calling that a clean stop tells the client a truncated slide
    body is a finished one. Same class of lie as swallowing a mid-stream error.
    """
    return "length" if frame.get("done_reason") == "length" else "stop"


@shim.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001 - any malformed body is the same 400
        raise HTTPException(status_code=400, detail="request body is not valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    model, messages = _model(body), _messages(body)
    if not messages:
        # The broker enforces min_length=1 and 422s, which this shim would relabel as
        # "broker chat failed" — blaming the GPU layer for a malformed client request.
        raise HTTPException(status_code=400, detail="messages must not be empty")
    options, keep_alive = _options(body), settings.llm_keep_alive
    fmt = _response_format(body)
    # A thinking model asked for structured output spends its whole budget reasoning and returns
    # EMPTY content — measured at a ~33% empty-JSON rate and ~8x latency on another rail here. So
    # thinking is turned OFF whenever a format is requested, and left at the model's own default
    # otherwise. This matters because the reasoning slot is admin-repointable and roles.json
    # already ships a thinking model: the first admin to use the panel this rail exists to honour
    # would otherwise get <think> preambles as course text.
    think = False if fmt is not None else None

    if not body.get("stream"):
        try:
            resp = await broker.chat(model, messages, options=options, fmt=fmt, think=think,
                                     keep_alive=keep_alive)
        except broker.BrokerError as exc:
            raise HTTPException(status_code=502,
                                detail="broker chat failed: " + str(exc)) from exc
        content = (resp.get("message") or {}).get("content", "") or ""
        return {
            "id": "chatcmpl-" + uuid.uuid4().hex, "object": "chat.completion",
            "created": int(time.time()), "model": resp.get("model") or model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": _finish_reason(resp)}],
            "usage": _usage(resp),
        }

    want_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    return StreamingResponse(
        _sse(model, messages, options, keep_alive, fmt=fmt, think=think,
             want_usage=want_usage),
        media_type="text/event-stream",
        # SSE through a reverse proxy buffers into uselessness without these; the whole point
        # of streaming is that slides appear as they are written.
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


def _err_event(message: str) -> str:
    """An OpenAI-shaped error delivered inside an already-200 SSE stream."""
    return "data: " + json.dumps({"error": {"message": message, "type": "broker_error"}}) + "\n\n"


async def _sse(model: str, messages: list[dict], options: dict, keep_alive: str, *,
               fmt: str | dict | None = None, think: bool | None = None,
               want_usage: bool = False) -> AsyncIterator[str]:
    """Broker NDJSON -> OpenAI SSE chunks.

    The broker reports a mid-stream failure as a final frame carrying ``error`` with HTTP 200
    long since sent, so every frame is inspected rather than just the status code. A caller
    that trusted the status alone would render a truncated lecture as a finished one.

    The final ``done`` frame is kept rather than discarded at the break: it is the only place the
    real ``done_reason`` and the token counters appear, and both used to be thrown away one line
    before they were needed.
    """
    cid, created = "chatcmpl-" + uuid.uuid4().hex, int(time.time())
    # Report the model the broker actually ran, not the @role that was asked for. Otherwise the
    # same request answers "@openmaic" when streamed and "mistral-small3.2:24b" when not.
    state = {"model": model, "done": {}}

    def chunk(delta: dict, finish: str | None = None, usage: dict | None = None) -> str:
        payload: dict[str, Any] = {
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": state["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            payload["usage"] = usage
        return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    yield chunk({"role": "assistant", "content": ""})
    stream = broker.chat_stream(model, messages, options=options, fmt=fmt, think=think,
                                keep_alive=keep_alive)
    try:
        # aclosing() so the generator's `async with` blocks unwind HERE, at the break, rather
        # than whenever the GC gets to the suspended generator. Without it the broker socket and
        # its pool outlive the response, and a course generation is dozens of these back to back.
        async with aclosing(stream):
            async for frame in stream:
                if frame.get("error"):
                    _log.error("broker stream error mid-response: %s", frame["error"])
                    yield _err_event(str(frame["error"]))
                    yield "data: [DONE]\n\n"
                    return
                if frame.get("model"):
                    state["model"] = frame["model"]
                piece = (frame.get("message") or {}).get("content") or ""
                if piece:
                    yield chunk({"content": piece})
                if frame.get("done"):
                    state["done"] = frame
                    break
    except broker.BrokerError as exc:
        _log.error("broker stream failed: %s", exc)
        yield _err_event(str(exc))
        yield "data: [DONE]\n\n"
        return
    done = state["done"]
    yield chunk({}, finish=_finish_reason(done),
                usage=_usage(done) if want_usage else None)
    yield "data: [DONE]\n\n"


@shim.exception_handler(HTTPException)
async def _openai_error(_request: Request, exc: HTTPException) -> JSONResponse:
    """Answer in the OpenAI error envelope; an OpenAI client parses that and shows the reason
    instead of a bare 'fetch failed'."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"message": str(exc.detail), "type": "invalid_request_error",
                           "code": exc.status_code}},
    )
