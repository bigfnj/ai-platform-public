"""GPU / Model Broker — FastAPI app.

The single owner of the GPU for the platform. Ollama is the only TEXT backend; images, speech
and transcription run in short-lived worker subprocesses under their own interpreters, which
exit to reclaim VRAM.
Run: ``uvicorn app.main:app --app-dir services/broker --port 11500``
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.broker import Broker, NoSubstituteError, UnknownRoleError
from app.config import TOKEN_SCOPES, BrokerSettings
from app.openai_compat import router as openai_compat_router
from app.schemas import (
    ChatRequest,
    DisabledUpdate,
    EmbedImageRequest,
    EmbedRequest,
    CancelRequest,
    ImageRequest,
    LoadRequest,
    RoleUpdate,
    TokenCreate,
    TranscribeRequest,
    TtsBatchRequest,
    TtsLightRequest,
    TtsRequest,
    UnloadRequest,
    VoiceRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = BrokerSettings()
    broker = Broker(settings)
    app.state.settings = settings
    app.state.broker = broker
    # token id -> last-seen epoch. In memory on purpose; see require_token.
    app.state.token_seen = {}
    # Say it once, loudly, at startup. A role map that does not fit the card is otherwise
    # discovered one rail at a time, as red chips with no explanation attached.
    for line in await broker.audit_roles():
        print(f"[broker] ROLE WARNING: {line}", file=sys.stderr, flush=True)
    try:
        yield
    finally:
        await broker.aclose()


#: Routes that CHANGE the platform rather than use it. An `inference`-scoped token is refused
#: here and allowed everywhere else. Matched on (method, path-prefix) because the split is
#: genuinely about mutation: PUT /v1/roles repoints a rail's model for every user at once, and
#: /v1/unload can evict the model somebody is mid-conversation with.
_FULL_SCOPE_ONLY: tuple[tuple[str, str], ...] = (
    ("PUT", "/v1/roles"),
    ("PUT", "/v1/disabled"),
    ("POST", "/v1/load"),
    ("POST", "/v1/unload"),
    ("POST", "/v1/cancel"),
    ("GET", "/v1/tokens"),
    ("POST", "/v1/tokens"),
    ("DELETE", "/v1/tokens"),
)


def _needs_full(method: str, path: str) -> bool:
    return any(method == m and path.startswith(pre) for m, pre in _FULL_SCOPE_ONLY)


def require_token(request: Request) -> None:
    """Gate the control plane. Open when NOTHING is configured (dev / staged rollout);
    /healthz is always open (liveness). Applied app-wide so no /v1/* or /openai/v1/* route is
    reachable untokened by a rogue container or LAN host.

    Two kinds of credential are accepted, and the difference is operational:

      BROKER_AUTH_TOKEN   one shared secret, full scope. Every rail sends it, and the GATEWAY
                          needs it to reach /v1/tokens -- so it is deliberately not listed or
                          revocable through the admin UI. Revoking it there would lock the
                          gateway out of its own token manager on the first click.
      a NAMED token       per-host, labelled, revocable on its own, and scoped. Created in
                          Admin > Broker. Stored as a sha256 hash; the plaintext exists only in
                          the response that minted it.

    The bearer form is what an OpenAI client already sends as its api_key, so the compat
    surface needs no separate auth path: `OpenAI(api_key=<either kind>)` just works.
    """
    settings = request.app.state.settings
    env_token = settings.auth_token
    named = settings.tokens()          # hot-read, so a revoke lands on the next request
    if request.url.path == "/healthz":
        return
    # Keyed on the STORE EXISTING, not on it having rows: revoking the last named token must
    # mean "nobody is authorised", not "authorisation is off".
    if not env_token and not settings.tokens_configured():
        return                          # nothing configured at all: open, as before
    header = request.headers.get("authorization", "")
    supplied = (header[7:] if header.lower().startswith("bearer ")
                else request.headers.get("x-broker-token", ""))
    if not supplied:
        raise HTTPException(status_code=401, detail="invalid or missing broker token")

    if env_token and secrets.compare_digest(supplied, env_token):
        return                          # the shared secret is full scope by definition
    # compare_digest per candidate rather than a dict lookup on the hash: the hash is not the
    # secret, but a short-circuiting compare over a handful of rows costs nothing to avoid.
    digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    match = next((r for r in named
                  if secrets.compare_digest(str(r.get("hash", "")), digest)), None)
    if match is None:
        raise HTTPException(status_code=401, detail="invalid or missing broker token")
    if _needs_full(request.method, request.url.path) and match.get("scope") != "full":
        raise HTTPException(
            status_code=403,
            detail=f"token {match.get('label')!r} is scoped 'inference' and cannot "
                   f"{request.method} {request.url.path}")
    # Last-seen, in MEMORY only. It answers "is this workstation still using the token", which
    # is the whole point of the revoke workflow, without a disk write on every request. Lost on
    # restart, and the UI says so rather than implying a durable record.
    request.app.state.token_seen[match["id"]] = time.time()


app = FastAPI(
    title="Platform GPU / Model Broker",
    version="0.0.1",
    summary="The only thing that touches the GPU. Ollama for text; worker subprocesses for "
            "images, speech and transcription.",
    lifespan=lifespan,
    dependencies=[Depends(require_token)],
)


app.include_router(openai_compat_router)


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
async def models(upstream: str | None = None) -> dict[str, Any]:
    """Installed models. `?upstream=<name>` asks a registered remote broker for ITS inventory
    instead — the admin picker needs that to offer a sensible model for a delegated role, and
    this box's list is exactly the wrong one to show there."""
    try:
        return {"models": await get_broker().models_view(upstream)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/upstreams")
async def upstreams() -> dict[str, Any]:
    """Registered remote brokers and whether each is answering. Never includes tokens."""
    return {"upstreams": await get_broker().upstreams_view()}


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


@app.get("/v1/tokens")
async def list_tokens(request: Request) -> dict[str, Any]:
    """Named tokens, WITHOUT their hashes. `last_seen` is in-memory and resets on a restart.

    The shared BROKER_AUTH_TOKEN is reported as present-or-not and is never listed as a row:
    it cannot be revoked here, because the gateway authenticates with it in order to reach this
    endpoint at all.
    """
    seen = request.app.state.token_seen
    rows = [{k: v for k, v in r.items() if k != "hash"} | {"last_seen": seen.get(r["id"])}
            for r in get_broker().settings.tokens()]
    return {"tokens": sorted(rows, key=lambda r: r.get("label", "")),
            "scopes": list(TOKEN_SCOPES),
            "shared_token_in_use": bool(get_broker().settings.auth_token)}


@app.post("/v1/tokens")
async def create_token(req: TokenCreate) -> dict[str, Any]:
    """Mint a token and return the plaintext ONCE. It is stored only as a sha256 hash, so this
    response is the only chance to copy it -- which the admin UI states before generating."""
    try:
        plain, row = get_broker().settings.add_token(req.label, req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"token": plain, **{k: v for k, v in row.items() if k != "hash"}}


@app.delete("/v1/tokens/{token_id}")
async def delete_token(token_id: str) -> dict[str, Any]:
    """Revoke by id. Effective on the NEXT request -- tokens() is hot-read, like roles."""
    if not get_broker().settings.revoke_token(token_id):
        raise HTTPException(status_code=404, detail=f"no such token '{token_id}'")
    return {"revoked": token_id}


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
    # A caller naming a role that does not exist, or a role nothing can substitute
    # for, is a 4xx the admin can act on. Folding them into the generic 502 tells a
    # rail 'the backend broke' about a configuration fault it could name precisely.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoSubstituteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"load failed: {exc}") from exc


@app.post("/v1/unload")
async def unload(req: UnloadRequest) -> dict[str, Any]:
    try:
        return await get_broker().unload(req.model)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
            format=req.format, think=req.think,
        )
    # A caller naming a role that does not exist, or a role nothing can substitute
    # for, is a 4xx the admin can act on. Folding them into the generic 502 tells a
    # rail 'the backend broke' about a configuration fault it could name precisely.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NoSubstituteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
                format=req.format, think=req.think,
            ):
                yield json.dumps(chunk).encode() + b"\n"
        except Exception as exc:  # noqa: BLE001 — can't raise once streaming; emit an error line
            yield json.dumps({"error": f"chat_stream failed: {exc}", "done": True}).encode() + b"\n"

    return StreamingResponse(_gen(), media_type="application/x-ndjson")


@app.post("/v1/embed")
async def embed(req: EmbedRequest) -> dict[str, Any]:
    try:
        return await get_broker().embed(req.model, req.input)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"embed failed: {exc}") from exc


@app.post("/v1/embed_image")
async def embed_image(req: EmbedImageRequest) -> dict[str, Any]:
    try:
        return await get_broker().embed_image(req.images, model=req.model)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"embed_image failed: {exc}") from exc


@app.post("/v1/image")
async def image(req: ImageRequest) -> dict[str, Any]:
    try:
        return await get_broker().image(
            req.prompts, negative_prompt=req.negative_prompt, steps=req.steps,
            size=req.size, model=req.model,
        )
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"image failed: {exc}") from exc


@app.post("/v1/tts")
async def tts(req: TtsRequest) -> dict[str, Any]:
    segments = [s.model_dump() for s in req.segments]
    try:
        return await get_broker().tts(segments)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}") from exc


@app.post("/v1/tts_batch")
async def tts_batch(req: TtsBatchRequest) -> dict[str, Any]:
    items = [i.model_dump() for i in req.items]
    try:
        return await get_broker().tts_batch(items)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_batch failed: {exc}") from exc


@app.post("/v1/tts_light")
async def tts_light(req: TtsLightRequest) -> dict[str, Any]:
    """Kokoro-82M read-aloud. Deliberately NOT gated on the GPU and evicts nothing, so it
    can be called from any rail mid-conversation without displacing the resident model."""
    try:
        return await get_broker().tts_light(
            req.text, voice=req.voice, lang_code=req.lang_code, speed=req.speed
        )
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_light failed: {exc}") from exc


@app.post("/v1/transcribe")
async def transcribe(req: TranscribeRequest) -> dict[str, Any]:
    """Speech-to-text. Like /v1/tts_light this skips the GPU gate and evicts nothing — the
    caller has just stopped talking and is waiting on the result."""
    try:
        return await get_broker().transcribe(
            req.audio_b64, suffix=req.suffix, language=req.language
        )
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"transcribe failed: {exc}") from exc


@app.get("/v1/voice/catalog")
async def voice_catalog() -> dict[str, Any]:
    """EVERY registered voice for the ai-voice rail, each flagged runnable or not.

    Deliberately NOT filtered to configured engines: filtering made a registered-but-unwired
    voice vanish from the rail with no explanation. See Broker.voice_catalog.
    """
    return get_broker().voice_catalog()


@app.post("/v1/voice/synthesize")
async def voice_synthesize(req: VoiceRequest) -> dict[str, Any]:
    try:
        return await get_broker().voice_synthesize(req.voice_id, req.text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely. No NoSubstituteError arm: it is raised only under
    # `substitute=True`, which this path does not set -- see `Broker.embed`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"voice synth failed: {exc}") from exc
