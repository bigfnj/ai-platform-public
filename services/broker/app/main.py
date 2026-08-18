"""GPU / Model Broker — FastAPI app.

The single owner of the GPU for the platform. v0 is Ollama-only.
Run: ``uvicorn app.main:app --app-dir services/broker --port 11500``
"""

from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.broker import Broker
from app.config import BrokerSettings
from app.schemas import (
    ChatRequest,
    DisabledUpdate,
    EmbedImageRequest,
    EmbedRequest,
    CancelRequest,
    ImageRequest,
    LoadRequest,
    RoleUpdate,
    TtsBatchRequest,
    TranscribeRequest,
    TtsLightRequest,
    TtsRequest,
    UnloadRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = BrokerSettings()
    broker = Broker(settings)
    app.state.settings = settings
    app.state.broker = broker
    try:
        yield
    finally:
        await broker.aclose()


def require_token(request: Request) -> None:
    """Gate the control plane with a shared bearer token. Open when unset (dev / staged rollout);
    /healthz is always open (liveness). Applied app-wide so no /v1/* route is reachable untokened
    by a rogue container or LAN host once BROKER_AUTH_TOKEN is set."""
    token = request.app.state.settings.auth_token
    if not token or request.url.path == "/healthz":
        return
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else request.headers.get("x-broker-token", "")
    if not (supplied and secrets.compare_digest(supplied, token)):
        raise HTTPException(status_code=401, detail="invalid or missing broker token")


app = FastAPI(
    title="Platform GPU / Model Broker",
    version="0.0.1",
    summary="The only thing that touches the GPU. Ollama-only (v0).",
    lifespan=lifespan,
    dependencies=[Depends(require_token)],
)


def get_broker() -> Broker:
    return app.state.broker


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    broker = get_broker()
    reachable = True
    version = None
    try:
        version = await broker.ollama.version()
    except Exception:  # noqa: BLE001
        reachable = False
    status_code = 200 if reachable else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": "ok" if reachable else "degraded",
                 "ollama_reachable": reachable, "ollama_version": version},
    )


@app.get("/v1/status")
async def status() -> dict[str, Any]:
    return await get_broker().status()


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {"models": await get_broker().list_models()}


@app.get("/v1/roles")
async def roles() -> dict[str, Any]:
    """Every model role with its stored pattern + the concrete model it resolves to."""
    return {"roles": await get_broker().roles_view()}


@app.put("/v1/roles/{role}")
async def set_role(role: str, req: RoleUpdate) -> dict[str, Any]:
    """Repoint a role to a new model/glob (persisted to roles.json; hot on next resolve)."""
    try:
        get_broker().settings.set_role(role, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"roles": await get_broker().roles_view()}


@app.get("/v1/disabled")
async def disabled() -> dict[str, Any]:
    """The admin-disabled model names (availability control; models are still served if a role
    resolves to them). Powers the gateway model-pool + hides them from every rail's pickers."""
    return {"disabled": sorted(get_broker().settings.disabled())}


@app.put("/v1/disabled")
async def set_disabled(req: DisabledUpdate) -> dict[str, Any]:
    """Replace the full disabled-name set (persisted to disabled.json; hot on next read)."""
    get_broker().settings.set_disabled(req.names)
    return {"disabled": sorted(get_broker().settings.disabled())}


@app.get("/v1/ps")
async def ps() -> dict[str, Any]:
    return {"loaded": await get_broker().list_loaded()}


@app.post("/v1/load")
async def load(req: LoadRequest) -> dict[str, Any]:
    try:
        return await get_broker().load(req.model, keep_alive=req.keep_alive)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"load failed: {exc}") from exc


@app.post("/v1/unload")
async def unload(req: UnloadRequest) -> dict[str, Any]:
    try:
        return await get_broker().unload(req.model)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"unload failed: {exc}") from exc


@app.post("/v1/cancel")
async def cancel(req: CancelRequest) -> dict[str, Any]:
    return {"cancelled": get_broker().cancel_job(req.seq)}


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    try:
        return await get_broker().chat(
            req.model, messages, options=req.options, keep_alive=req.keep_alive,
            format=req.format,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"chat failed: {exc}") from exc


@app.post("/v1/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Token-streaming twin of /v1/chat. Returns NDJSON — one Ollama chunk per line
    ({"message":{"content":...},"done":false} ... {"done":true}). Purely additive:
    /v1/chat (buffered) is unchanged. Rails consume this DIRECTLY (not through the
    buffering gateway proxy) and relay tokens to the browser over the gateway's live
    WebSocket proxy. Errors after streaming starts are surfaced as a final line."""
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    broker = get_broker()

    async def _gen():
        try:
            async for chunk in broker.chat_stream(
                req.model, messages, options=req.options, keep_alive=req.keep_alive,
                format=req.format,
            ):
                yield json.dumps(chunk).encode() + b"\n"
        except Exception as exc:  # noqa: BLE001 — can't raise once streaming; emit an error line
            yield json.dumps({"error": f"chat_stream failed: {exc}", "done": True}).encode() + b"\n"

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@app.post("/v1/embed")
async def embed(req: EmbedRequest) -> dict[str, Any]:
    try:
        return await get_broker().embed(req.model, req.input)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"embed failed: {exc}") from exc


@app.post("/v1/embed_image")
async def embed_image(req: EmbedImageRequest) -> dict[str, Any]:
    try:
        return await get_broker().embed_image(req.images, model=req.model)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"embed_image failed: {exc}") from exc


@app.post("/v1/image")
async def image(req: ImageRequest) -> dict[str, Any]:
    try:
        return await get_broker().image(
            req.prompts, negative_prompt=req.negative_prompt, steps=req.steps,
            size=req.size, model=req.model,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"image failed: {exc}") from exc


@app.post("/v1/tts")
async def tts(req: TtsRequest) -> dict[str, Any]:
    segments = [s.model_dump() for s in req.segments]
    try:
        return await get_broker().tts(segments)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}") from exc


@app.post("/v1/tts_batch")
async def tts_batch(req: TtsBatchRequest) -> dict[str, Any]:
    items = [i.model_dump() for i in req.items]
    try:
        return await get_broker().tts_batch(items)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_batch failed: {exc}") from exc


@app.post("/v1/transcribe")
async def transcribe(req: TranscribeRequest) -> dict[str, Any]:
    try:
        return await get_broker().transcribe(
            req.audio_b64, suffix=req.suffix, language=req.language
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"transcribe failed: {exc}") from exc


@app.post("/v1/tts_light")
async def tts_light(req: TtsLightRequest) -> dict[str, Any]:
    try:
        return await get_broker().tts_light(
            req.text, voice=req.voice, lang_code=req.lang_code, speed=req.speed
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_light failed: {exc}") from exc
