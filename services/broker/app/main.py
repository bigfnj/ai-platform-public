"""GPU / Model Broker — FastAPI app.

The single owner of the GPU for the platform. Ollama is the only TEXT backend; images, speech
and transcription run in short-lived worker subprocesses under their own interpreters, which
exit to reclaim VRAM.
Run: ``uvicorn app.main:app --app-dir services/broker --port 11500``
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import secrets
import sys
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.concurrency import run_in_threadpool

from app.broker import (
    Broker,
    ClientGone,
    ImageEmbedderNotAllowed,
    NoSubstituteError,
    NotAnImageModel,
    UnknownRoleError,
    UpstreamError,
)
from app.config import (
    TOKEN_SCOPES,
    BrokerSettings,
    CliRolesFileError,
    RolesFileError,
    SettingsWriteError,
)
from app.openai_compat import router as openai_compat_router
from app.streaming import ClosingStreamingResponse
from app.voice import VoiceRegistryError
from app.schemas import (
    TRANSCRIBE_MAX_B64,
    ChatRequest,
    CliRoleUpdate,
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


#: The deferred role audit's schedule (audit_when_ready): the first retry 2 s after boot, the
#: wait doubling to at most 60 s between tries, for at most 30 minutes in all.
_AUDIT_RETRY_FIRST = 2.0
_AUDIT_RETRY_CAP = 60.0
_AUDIT_RETRY_BUDGET = 30 * 60.0
#: Ceiling on one "does Ollama answer" probe. An Ollama that is up answers /api/version at once,
#: and a closed port refuses within about 2 s (Windows retries a refused loopback connect). This
#: bounds the third case, an address that drops packets, which would otherwise wait out the
#: client's generation timeout before the broker finished booting.
_OLLAMA_PROBE_TIMEOUT = 5.0


def _print_role_audit(lines: list[str]) -> None:
    for line in lines:
        print(f"[broker] ROLE WARNING: {line}", file=sys.stderr, flush=True)


async def _ollama_answers(broker: Broker) -> bool:
    try:
        await asyncio.wait_for(broker.ollama.version(), timeout=_OLLAMA_PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 - down, refusing or too slow: all of them "not yet"
        return False
    return True


async def audit_when_ready(broker: Broker, *,
                           sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                           clock: Callable[[], float] = time.monotonic) -> None:
    """The startup role audit, for a broker that booted before Ollama did.

    On a box where the broker is a service and Ollama an app started at logon, that is every
    boot. audit_roles() reads no model list then and returns nothing, and nothing is what a
    PASSING audit prints, so the map went unchecked with no sign of it. This retries until
    Ollama answers (2, 4, 8 ... 60 s apart, for at most 30 minutes) and then prints the
    warnings; or, if Ollama never answers, ONE line saying the audit did not run, so that
    silence can only ever mean a pass.

    `sleep` and `clock` are there for the tests: the schedule is half an hour long.
    """
    deadline = clock() + _AUDIT_RETRY_BUDGET
    delay = _AUDIT_RETRY_FIRST
    while (remaining := deadline - clock()) > 0:
        await sleep(min(delay, remaining))
        if await _ollama_answers(broker):
            print(f"[broker] Ollama answers at {broker.ollama.base_url}; running the deferred "
                  "role audit", file=sys.stderr, flush=True)
            try:
                lines = await broker.audit_roles()
            except Exception as exc:  # noqa: BLE001 - a background task must not die unheard
                lines = [f"role audit failed ({exc.__class__.__name__}: {exc})"]
            _print_role_audit(lines)
            return
        delay = min(delay * 2, _AUDIT_RETRY_CAP)
    print(f"[broker] ROLE WARNING: role audit skipped: Ollama never answered at "
          f"{broker.ollama.base_url} in {_AUDIT_RETRY_BUDGET / 60:g} minutes, so the role map "
          "was not checked. Restart the broker once Ollama is up.", file=sys.stderr, flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = BrokerSettings()
    broker = Broker(settings)
    app.state.settings = settings
    app.state.broker = broker
    # token id -> last-seen epoch. In memory on purpose; see require_token.
    app.state.token_seen = {}
    # The per-call CLI temp directories an earlier run could not remove (Broker.sweep_cli_temp),
    # before any call of this one starts. In a thread: a file filter can stall a delete.
    await asyncio.to_thread(broker.sweep_cli_temp)
    # The media and voice jobs' directories a broker that died mid-job left in the system temp
    # (Broker.sweep_media_temp), before any job of this run makes one there.
    await asyncio.to_thread(broker.sweep_media_temp)
    # Say it once, loudly, at startup. A role map that does not fit the card is otherwise
    # discovered one rail at a time, as red chips with no explanation attached. Ollama not up
    # yet is not a pass: the audit waits for it in the background (audit_when_ready).
    audit: asyncio.Task | None = None
    if await _ollama_answers(broker):
        _print_role_audit(await broker.audit_roles())
    else:
        print(f"[broker] Ollama does not answer at {broker.ollama.base_url} yet; the role audit "
              f"is deferred until it does (retrying for up to {_AUDIT_RETRY_BUDGET / 60:g} "
              "minutes)", file=sys.stderr, flush=True)
        audit = asyncio.create_task(audit_when_ready(broker), name="deferred-role-audit")
    try:
        yield
    finally:
        if audit is not None and not audit.done():
            audit.cancel()
            await asyncio.wait({audit})
        await broker.aclose()


#: Routes that CHANGE the platform rather than use it. An `inference`-scoped token is refused
#: here and allowed everywhere else. Matched on (method, path-prefix) because the split is
#: genuinely about mutation: PUT /v1/roles repoints a rail's model for every user at once, and
#: /v1/unload can evict the model somebody is mid-conversation with.
_FULL_SCOPE_ONLY: tuple[tuple[str, str], ...] = (
    ("PUT", "/v1/roles"),
    # Switches a role onto the owner's Claude subscription. An inference-scoped workstation
    # token must not be able to do that.
    ("PUT", "/v1/cli"),
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

    The credential's scope is left on `request.state.broker_scope` ("full" or "inference")
    for the routes that need more than allow-or-refuse: /v1/chat and /v1/chat/stream honour
    `requester` only from a full-scope credential (see `_trusted_requester`). An open broker, with nothing
    configured, records "full", the same answer it gives every full-scope route.

    /healthz is exempted FIRST and the token store is read LAST, only once the shared secret
    has not matched. Read first, a tokens.json the reader could not parse failed every request
    with a 500, the liveness probe included; and every rail's shared-secret call paid a parse
    of a file it never needed.

    The decision itself is `_authorise`, which `_TokenAndBodyGate` also makes BEFORE the body
    is read: FastAPI reads and parses a route's body before it solves this dependency, so on
    its own this refused an unauthenticated 200 MB POST only once the 200 MB was in memory.
    The gate refuses first; this still runs for every route, so the scope is left for it and
    the last-seen stamp written, and a credential revoked between the two is still refused.
    """
    if request.url.path == "/healthz":
        return
    try:
        scope, credential, match = _authorise(request.app.state.settings, request.method,
                                              request.url.path, request.headers)
    except _Refused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
    request.state.broker_scope = scope
    request.state.broker_credential = credential
    if match is not None:
        # Last-seen, in MEMORY only. It answers "is this workstation still using the token",
        # which is the whole point of the revoke workflow, without a disk write on every
        # request. Lost on restart, and the UI says so rather than implying a durable record.
        request.app.state.token_seen[match["id"]] = time.time()


_NO_TOKEN = "invalid or missing broker token"


class _Refused(Exception):
    """`_authorise`'s refusal: the status (401 or 403) and the detail every caller answers
    with. require_token raises it as the HTTPException it always was; the gate in front of
    routing answers it as the same JSON body."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _same(a: str, b: str) -> bool:
    """compare_digest over the UTF-8 bytes. On two str it raises TypeError for any character
    outside ASCII, and a header value can carry one (a server decodes headers as Latin-1): a
    bearer token of `\\xe9` was a 500 from the gate instead of a 401."""
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _authorise(settings: Any, method: str, path: str, headers: Any
               ) -> tuple[str, str, dict[str, Any] | None]:
    """The token gate's one decision, for every path but /healthz: ``(scope, credential,
    named row or None)``, or `_Refused` with the 401 or 403. Shared by `_TokenAndBodyGate`,
    which makes it before the body is read, and `require_token`, which makes it again for the
    route and keeps the scope; one function, so the two cannot disagree."""
    env_token = settings.auth_token
    # Keyed on the STORE EXISTING, not on it having rows: revoking the last named token must
    # mean "nobody is authorised", not "authorisation is off".
    if not env_token and not settings.tokens_configured():
        return "full", "open", None     # nothing configured at all: open, as before
    header = headers.get("authorization", "")
    supplied = (header[7:] if header.lower().startswith("bearer ")
                else headers.get("x-broker-token", ""))
    if not supplied:
        raise _Refused(401, _NO_TOKEN)

    if env_token and _same(supplied, env_token):
        return "full", "shared", None   # the shared secret is full scope by definition
    named = settings.tokens()          # hot-read, so a revoke lands on the next request
    # compare_digest per candidate rather than a dict lookup on the hash: the hash is not the
    # secret, but a short-circuiting compare over a handful of rows costs nothing to avoid.
    digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    match = next((r for r in named if _same(str(r.get("hash", "")), digest)), None)
    if match is None:
        raise _Refused(401, _NO_TOKEN)
    if _needs_full(method, path) and match.get("scope") != "full":
        raise _Refused(403, f"token {match.get('label')!r} is scoped 'inference' and cannot "
                            f"{method} {path}")
    # Exactly "full" or "inference": a row with any other scope never matched (tokens()).
    scope = "full" if match.get("scope") == "full" else "inference"
    return scope, str(match.get("label") or match.get("id") or ""), match


#: Credentials whose dropped `requester` has been logged once already (see _trusted_requester).
_REQUESTER_DROP_LOGGED: set[str] = set()


def _trusted_requester(request: Request, requester: str | None,
                       route: str = "/v1/chat") -> str | None:
    """`requester` as /v1/chat and /v1/chat/stream may use it: only from a FULL-scope
    credential, else None. `route` names the caller's route in the log line, nothing more.

    `requester` decides one thing, whether an owner's Claude subscription may answer, and the
    broker cannot check it: it trusts whoever sends it. A full-scope credential (the shared
    BROKER_AUTH_TOKEN the gateway and most rails hold, or a named `full` token) can already
    repoint every role,
    so trusting its `requester` adds nothing. An INFERENCE-scoped named token exists for limited
    trust (a LAN workstation), and taking its word for who is asking would let it name an owner
    and spend that owner's subscription on any role switched on. Its `requester` is dropped
    BEFORE cli_decision, so the call is background work (`no_requester`) and runs locally.

    Missing scope (a test that bypassed require_token) is treated as not full: fail closed.
    Logged once per credential, by label and never with the name it sent.
    """
    if requester is None:
        return None
    if getattr(request.state, "broker_scope", None) == "full":
        return requester
    who = str(getattr(request.state, "broker_credential", "") or "?")
    if who not in _REQUESTER_DROP_LOGGED:
        _REQUESTER_DROP_LOGGED.add(who)
        print(f"[broker] WARNING: {route} ignored `requester` from token {who!r}: only a "
              f"full-scope credential may name who is asking (logged once per token)",
              file=sys.stderr, flush=True)
    return None


app = FastAPI(
    title="Platform GPU / Model Broker",
    version="0.0.1",
    summary="The only thing that touches the GPU. Ollama for text; worker subprocesses for "
            "images, speech and transcription.",
    lifespan=lifespan,
    dependencies=[Depends(require_token)],
)


# --- the gate in front of routing: the token, then the body's size ---------------------------

_MIB = 1024 * 1024
#: The most body bytes any route not named in _BODY_ROOM reads in one request. That is the
#: control plane (a role pattern, a Claude switch, the disabled names, a token label, a load,
#: unload or cancel) and the routes whose one text field is bounded at 8,000 characters
#: (/v1/tts_light, /v1/voice/synthesize, /openai/v1/audio/speech: at most 48 KB even with every
#: character JSON-escaped), image prompts, and every route that takes no body at all.
_BODY_MAX = 1 * _MIB
#: The routes that carry images, batches or whole documents. 32 MiB holds a chat with several
#: photos (the vision rails downscale theirs to about 1600 px JPEG before sending), a tts or
#: tts_batch at its 500-item cap of 8,000-character texts with every character escaped (24 MB),
#: and a body as large as anything the gateway relays to a rail by default (26 MiB).
_BODY_INFERENCE = 32 * _MIB
_BODY_ROOM: dict[str, int] = {
    **dict.fromkeys(("/v1/chat", "/v1/chat/stream", "/v1/embed", "/v1/embed_image", "/v1/tts",
                     "/v1/tts_batch", "/openai/v1/chat/completions", "/openai/v1/embeddings"),
                    _BODY_INFERENCE),
    # The base64 bound TranscribeRequest holds, plus the ordinary allowance for the rest of the
    # JSON: the gateway's own room for dictation is the same sum.
    "/v1/transcribe": TRANSCRIBE_MAX_B64 + _BODY_MAX,
    # The largest file whose base64 fits that bound (what the route itself holds the upload to,
    # openai_compat._MAX_AUDIO_BYTES), plus the multipart framing and the form fields.
    "/openai/v1/audio/transcriptions": TRANSCRIBE_MAX_B64 // 4 * 3 + _BODY_MAX,
}


def _body_ceiling(path: str) -> int:
    return _BODY_ROOM.get(path.rstrip("/") or "/", _BODY_MAX)


def _declared_length(headers: Any) -> int | None:
    """The request's Content-Length, or None when it has none. The server has already refused a
    malformed one (h11 answers 400 before the app runs), so None there is only defensive."""
    raw = headers.get("content-length")
    if raw is None:
        return None
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 0 else None


def _too_large(path: str, ceiling: int) -> JSONResponse:
    message = (f"this request is larger than the {ceiling / _MIB:.1f} MiB the broker accepts on "
               f"{path}")
    if path.startswith("/openai/"):
        # The OpenAI surface's own envelope (openai_compat.error): an SDK reads error.message.
        return JSONResponse(status_code=413, content={"error": {
            "message": message, "type": "invalid_request_error", "param": None,
            "code": "request_too_large"}})
    return JSONResponse(status_code=413, content={"detail": message})


class _TokenAndBodyGate:
    """The token check and a body ceiling, both BEFORE a route reads a byte of the body.

    FastAPI reads and parses a route's body before it solves one dependency, and require_token
    is a dependency, so with BROKER_AUTH_TOKEN set an unauthenticated 200 MB POST to /v1/chat
    was read whole into memory and answered 422, not 401, and a multipart upload to
    /openai/v1/audio/transcriptions was spooled to temp before its 401. Nothing bounded a body
    at all: uvicorn sets no limit.

    THE TOKEN. When a credential is configured (the shared secret, or a token store), every path
    but /healthz is decided by `_authorise`, require_token's own decision, before the app runs:
    a missing or wrong credential is the 401 and an inference token on a full-scope route the
    403, with the same body the route would have given, and the body is never read. In the
    threadpool, as require_token runs: a token store is a file, and a stat must not stall the
    event loop. It covers what the dependency never did: FastAPI's /docs and /openapi.json, and
    a path no route serves (401 rather than 404, so the API is not mapped for a caller with no
    credential). On an OPEN broker it decides nothing: "open" is its answer, as before.

    THE BODY. A Content-Length over the route's ceiling (_body_ceiling) is a 413 before a byte
    is read. Any other body is counted as it arrives, and one chunk past the ceiling the route
    is told the client has gone, so it stops reading; whatever it answers to that is dropped and
    the client gets the 413 instead. The gateway's _PlatformBodyCeiling is the precedent, and
    says why the route is told rather than raised at.

    Pure ASGI, not BaseHTTPMiddleware: that one runs the app in a task group whose
    ExceptionGroup wrapping and buffering are both in the way of a streamed response.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[[], Awaitable[Any]],
                       send: Callable[[Any], Awaitable[None]]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        path = request.url.path
        if path != "/healthz":
            try:
                await run_in_threadpool(_authorise, scope["app"].state.settings,
                                        request.method, path, request.headers)
            except _Refused as exc:
                await JSONResponse(status_code=exc.status,
                                   content={"detail": exc.detail})(scope, receive, send)
                return
        ceiling = _body_ceiling(path)
        length = _declared_length(request.headers)
        if length is not None and length > ceiling:
            await _too_large(path, ceiling)(scope, receive, send)
            return
        taken = 0
        over = False
        answered = False

        async def counted() -> dict[str, Any]:
            nonlocal taken, over
            if over:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                taken += len(message.get("body", b""))
                if taken > ceiling:
                    over = True
                    return {"type": "http.disconnect"}
            return message

        async def gated(message: dict[str, Any]) -> None:
            nonlocal answered
            if over:
                return          # the route's answer to a body it never got
            answered = True
            await send(message)

        try:
            await self.app(scope, counted, gated)
        except Exception:
            if not over:
                raise
        if over and not answered:
            await _too_large(path, ceiling)(scope, receive, send)


app.add_middleware(_TokenAndBodyGate)


app.include_router(openai_compat_router)


@app.exception_handler(httpx.TransportError)
async def ollama_unreachable(request: Request, exc: httpx.TransportError) -> JSONResponse:
    """An Ollama that cannot be reached is a 503 naming it, not a 500 and a traceback.

    Reached by the routes that call Ollama without catching for themselves (/v1/models,
    /v1/ps). A rail asking /v1/models at its own boot, on a box where the broker starts before
    Ollama, read the 500 as "the broker is broken" and filled the broker log with a chained
    ConnectError per request. Logged once per outage instead (OllamaClient.note_unreachable),
    and a transport failure from anywhere else is re-raised to the ordinary 500.
    """
    ollama = getattr(getattr(request.app.state, "broker", None), "ollama", None)
    sent = getattr(ollama, "sent", None)
    if sent is None or not sent(exc):
        raise exc
    if ollama.note_unreachable():
        print(f"[broker] Ollama is not reachable at {ollama.base_url} "
              f"({exc.__class__.__name__}); answering 503 until it is. Logged once per outage.",
              file=sys.stderr, flush=True)
    return JSONResponse(status_code=503,
                        content={"detail": f"Ollama is not reachable at {ollama.base_url}"})


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
    except UpstreamError as exc:
        # The remote is down, refusing us, or too slow: a 502 naming it, rather than the
        # unhandled 500 an UpstreamError used to become here.
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/upstreams")
async def upstreams() -> dict[str, Any]:
    """Registered remote brokers and whether each is answering. Never includes tokens."""
    return {"upstreams": await get_broker().upstreams_view()}


@app.get("/v1/roles")
async def roles(inventory: bool = False, local_only: bool = False) -> dict[str, Any]:
    """Every model role with its stored pattern + the concrete model it resolves to.

    `?inventory=true` adds `upstreams` (every registered box with live health, `local` first,
    never a token) and `models` (each box's installed models, `local` included), read by the
    SAME probe that judged the delegated rows. That is the gateway's Rails tab in one call;
    asking /v1/upstreams and /v1/models?upstream=X as well dialled every remote twice.

    `?local_only=true` is the opposite: this card's roles only, no delegated rows, and no
    remote dialled at all, for a caller that only asks about local models (the Models tab)."""
    if inventory and local_only:
        raise HTTPException(status_code=400,
                            detail="inventory reads every box; local_only reads none. Pick one.")
    if local_only:
        return {"roles": await get_broker().roles_view_local()}
    if inventory:
        return await get_broker().roles_inventory()
    return {"roles": await get_broker().roles_view()}


@app.put("/v1/roles/{role}")
async def set_role(role: str, req: RoleUpdate) -> dict[str, Any]:
    """Repoint a role to a new model/glob (persisted to roles.json; hot on next resolve).

    400 for an unknown role or a bad pattern. 409 while roles.json cannot be read: the
    write would rewrite it from nothing and lose every mapping it holds (set_role); and 409
    when it cannot be written (SettingsWriteError), which was a 500."""
    try:
        get_broker().settings.set_role(role, req.model)
    except RolesFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SettingsWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"roles": await get_broker().roles_view()}


@app.put("/v1/cli/roles/{role}")
async def set_cli_role(role: str, req: CliRoleUpdate) -> dict[str, Any]:
    """Switch ONE role's Claude CLI eligibility on or off, choose the Claude model its calls
    use, or both (BROKER_CLI_ROLES_FILE; hot).

    `{"enabled": true|false}`, `{"model": "<id>"}` (an id from BROKER_CLI_MODELS, or null to
    clear the role's own, so its calls use BROKER_CLI_MODEL, else the CLI's own default), or
    both. Which ROLES may use the CLI, and with which model, is all this changes. Who may
    (BROKER_CLI_OWNERS) and whether the backend runs at all (BROKER_CLI_ENABLED) stay
    environment settings that no route can reach: the owner list is the licence boundary, and a
    request is not the place to move it.

    400 for a role this broker does not know or a model BROKER_CLI_MODELS does not allow. 409
    when roles.json cannot be read and the switch is ON or a model is chosen (switching a role
    OFF still works then, carrying every other key forward), or the switch file is not
    configured, is invalid (refused rather than overwritten), or cannot be written: a state of
    the box to fix.
    The reply is the same `cli` block roles_inventory carries, read after the write."""
    broker = get_broker()
    # Left out of the body, the model stays as it is; null clears it.
    change = {"model": req.model} if "model" in req.model_fields_set else {}
    try:
        broker.settings.set_cli_role(role, req.enabled, **change)
    except CliRolesFileError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"cli": broker.cli_summary()}


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
    response is the only chance to copy it -- which the admin UI states before generating.

    REFUSED (409) as the FIRST token on a broker with no BROKER_AUTH_TOKEN. The store existing
    is what turns token checks on (see require_token), so that mint used to switch enforcement
    on for clients that hold no token at all: the gateway and every rail got 401 on their next
    call, and the console that made the mint could no longer reach this route to undo it. The
    way back was deleting tokens.json on the broker host. With the shared secret set, every
    existing client already holds a token the broker accepts, so minting is safe.
    """
    settings = get_broker().settings
    if not settings.auth_token and not settings.tokens_configured():
        raise HTTPException(status_code=409, detail=(
            "refused: this broker has no BROKER_AUTH_TOKEN, and creating its first named token "
            "turns token checks on, so the gateway, this console and every rail (which hold no "
            "token) would get 401 on their next call. Set BROKER_AUTH_TOKEN for the broker and "
            "the gateway, restart both, then create named tokens."))
    try:
        plain, row = settings.add_token(req.label, req.scope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SettingsWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"token": plain, **{k: v for k, v in row.items() if k != "hash"}}


@app.delete("/v1/tokens/{token_id}")
async def delete_token(token_id: str) -> dict[str, Any]:
    """Revoke by id. Effective on the NEXT request -- tokens() is hot-read, like roles. 409 when
    tokens.json cannot be written: the token is NOT revoked, and the refusal says so."""
    try:
        found = get_broker().settings.revoke_token(token_id)
    except SettingsWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=404, detail=f"no such token '{token_id}'")
    return {"revoked": token_id}


@app.get("/v1/disabled")
async def disabled() -> dict[str, Any]:
    """The admin-disabled model names (availability control; models are still served if a role
    resolves to them). Powers the gateway model-pool + hides them from every rail's pickers."""
    return {"disabled": sorted(get_broker().settings.disabled())}


@app.put("/v1/disabled")
async def set_disabled(req: DisabledUpdate) -> dict[str, Any]:
    """Replace the full disabled-name set (persisted to disabled.json; hot on next read). 409
    when disabled.json cannot be written (SettingsWriteError), which was a 500."""
    try:
        get_broker().settings.set_disabled(req.names)
    except SettingsWriteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    # `substitute=True`, which this path does not set -- see `Broker.unload`. Turn that on
    # here and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"unload failed: {exc}") from exc


@app.post("/v1/cancel")
async def cancel(req: CancelRequest) -> dict[str, Any]:
    return {"cancelled": get_broker().cancel_job(req.seq)}


def _warn_unknown_fields(route: str, req: ChatRequest) -> None:
    """Name every top-level field this route is about to ignore.

    The broker's chat dialect is Ollama's: sampling goes under `options` (`num_predict`,
    `temperature`, ...). A rail written against the OpenAI shape sends those at the top level,
    where nothing reads them, and the call still answers 200 with plausible output, so nothing
    else would ever say the setting was lost. Field NAMES only, repr'd and capped: a value can
    be prompt text or a credential, and a name is caller-controlled text going into a log.
    """
    extra = sorted(req.model_extra or {})
    if not extra:
        return
    names = ", ".join(repr(k[:40]) for k in extra[:20]) + (" ..." if len(extra) > 20 else "")
    print(f"[broker] WARNING: {route} ignored unknown field(s) {names} (model={req.model!r}). "
          "Sampling settings belong inside \"options\" (max_tokens -> options.num_predict, "
          "temperature -> options.temperature); at the top level they never reach the model.",
          file=sys.stderr, flush=True)


@app.post("/v1/chat")
async def chat(req: ChatRequest, request: Request) -> dict[str, Any]:
    _warn_unknown_fields("/v1/chat", req)
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    try:
        return await get_broker().chat(
            req.model, messages, options=req.options, keep_alive=req.keep_alive,
            format=req.format, think=req.think,
            requester=_trusted_requester(request, req.requester),
            # The CLI path asks this before it starts a call that waited for a slot, and
            # before a local fallback: a rail that already gave up must not cost either.
            client_gone=request.is_disconnected,
        )
    except ClientGone as exc:
        # Nobody is listening; 499 is nginx's "client closed request", for the access log.
        return JSONResponse(status_code=499, content={"detail": str(exc)})
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
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    """Token-streaming twin of /v1/chat. Returns NDJSON — one Ollama chunk per line
    ({"message":{"content":...},"done":false} ... {"done":true}). Purely additive:
    /v1/chat (buffered) is unchanged. Rails call this directly, and relay tokens to the browser
    over the gateway's live WebSocket proxy. (The gateway's HTTP proxy once buffered a whole
    response; it has relayed one as it arrives since 2026-10-13.) Errors after streaming starts
    are surfaced as a final line.

    `requester` counts here exactly as on /v1/chat (only from a full-scope credential), and
    the connection's own `is_disconnected` goes with it: an owner's streamed call on an
    eligible role can be answered by the Claude CLI (Broker.chat_stream). A caller who hung
    up is written nothing more, not even an error line."""
    _warn_unknown_fields("/v1/chat/stream", req)
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    broker = get_broker()
    # Decided now, while the request is in hand; the generator runs after this returns.
    who = _trusted_requester(request, req.requester, route="/v1/chat/stream")
    gone = request.is_disconnected

    async def _gen():
        try:
            # aclosing: when the response closes this generator (a cancellation or a failed
            # write that ended the stream in send(), ClosingStreamingResponse), closing it
            # closes the broker's at once, and with it the CLI's, before the request ends.
            async with contextlib.aclosing(broker.chat_stream(
                req.model, messages, options=req.options, keep_alive=req.keep_alive,
                format=req.format, think=req.think, requester=who, client_gone=gone,
            )) as frames:
                async for chunk in frames:
                    yield json.dumps(chunk).encode() + b"\n"
        except ClientGone:
            # Nobody is reading. An error line would be the broker reporting a failure of its
            # own for a call its caller ended.
            return
        except Exception as exc:  # noqa: BLE001 — can't raise once streaming; emit an error line
            yield json.dumps({"error": f"chat_stream failed: {exc}", "done": True}).encode() + b"\n"

    return ClosingStreamingResponse(_gen(), media_type="application/x-ndjson")


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
async def embed_image(req: EmbedImageRequest, request: Request) -> dict[str, Any]:
    try:
        return await get_broker().embed_image(req.images, model=req.model,
                                              client_gone=request.is_disconnected)
    except ClientGone as exc:
        return _hung_up(exc)
    except ImageEmbedderNotAllowed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"embed_image failed: {exc}") from exc


def _hung_up(exc: ClientGone) -> JSONResponse:
    """A gated job whose caller hung up, stopped and the GPU gate free again (Broker._run_media,
    voice_synthesize), or a light-media job's, its slot back (embed_image, transcribe). Nobody is
    listening; 499 for the access log, as /v1/chat answers."""
    return JSONResponse(status_code=499, content={"detail": str(exc)})


@app.post("/v1/image")
async def image(req: ImageRequest, request: Request) -> dict[str, Any]:
    try:
        return await get_broker().image(
            req.prompts, negative_prompt=req.negative_prompt, steps=req.steps,
            size=req.size, model=req.model, client_gone=request.is_disconnected,
        )
    except ClientGone as exc:
        return _hung_up(exc)
    # A caller naming a role that does not exist is a 4xx the admin can act on; folding it
    # into the generic 502 tells a rail 'the backend broke' about a configuration fault it
    # could name precisely (`Broker._resolve_media` raises it). No NoSubstituteError arm:
    # `Broker.image` resolves through `_resolve_media`, which expands an @role by the role map
    # and never substitutes, so nothing on this path raises it. Give that path a substitute
    # and the 409 must come back in the same edit, or it degrades to a silent 502.
    except UnknownRoleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A model that draws no images (`@chat`, "dall-e-3") is the caller's mistake, refused
    # before the worker starts; the worker used to draw SDXL for it.
    except NotAnImageModel as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"image failed: {exc}") from exc


@app.post("/v1/tts")
async def tts(req: TtsRequest, request: Request) -> dict[str, Any]:
    segments = [s.model_dump() for s in req.segments]
    try:
        return await get_broker().tts(segments, client_gone=request.is_disconnected)
    except ClientGone as exc:
        return _hung_up(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts failed: {exc}") from exc


@app.post("/v1/tts_batch")
async def tts_batch(req: TtsBatchRequest, request: Request) -> dict[str, Any]:
    items = [i.model_dump() for i in req.items]
    try:
        return await get_broker().tts_batch(items, client_gone=request.is_disconnected)
    except ClientGone as exc:
        return _hung_up(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_batch failed: {exc}") from exc


@app.post("/v1/tts_light")
async def tts_light(req: TtsLightRequest, request: Request) -> dict[str, Any]:
    """Kokoro-82M read-aloud. Deliberately NOT gated on the GPU and evicts nothing, so it
    can be called from any rail mid-conversation without displacing the resident model.

    A caller who hangs up stops the job: the connection's own `is_disconnected` goes with it
    (Broker.tts_light), as on /v1/chat. The gateway lets go of its request when the browser
    stops a read."""
    try:
        return await get_broker().tts_light(
            req.text, voice=req.voice, lang_code=req.lang_code, speed=req.speed,
            client_gone=request.is_disconnected,
        )
    except ClientGone as exc:
        # Nobody is listening; 499 for the access log, as /v1/chat answers.
        return JSONResponse(status_code=499, content={"detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"tts_light failed: {exc}") from exc


@app.post("/v1/transcribe")
async def transcribe(req: TranscribeRequest, request: Request) -> dict[str, Any]:
    """Speech-to-text. Like /v1/tts_light this skips the GPU gate and evicts nothing — the
    caller has just stopped talking and is waiting on the result. A caller who hangs up stops
    the job (Broker.transcribe asks the connection), as on /v1/tts_light."""
    try:
        return await get_broker().transcribe(
            req.audio_b64, suffix=req.suffix, language=req.language,
            client_gone=request.is_disconnected,
        )
    except ClientGone as exc:
        return _hung_up(exc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"transcribe failed: {exc}") from exc


@app.get("/v1/voice/catalog")
async def voice_catalog() -> dict[str, Any]:
    """EVERY registered voice for the ai-voice rail, each flagged runnable or not.

    Deliberately NOT filtered to configured engines: filtering made a registered-but-unwired
    voice vanish from the rail with no explanation. See Broker.voice_catalog.

    A registry that cannot be read is a 503 naming the file, not a 500: the service is up and
    misconfigured, and the rail shows the reason instead of a stack trace's worth of nothing.
    """
    try:
        return get_broker().voice_catalog()
    except VoiceRegistryError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/v1/voice/synthesize")
async def voice_synthesize(req: VoiceRequest, request: Request) -> dict[str, Any]:
    try:
        return await get_broker().voice_synthesize(req.voice_id, req.text,
                                                   client_gone=request.is_disconnected)
    except ClientGone as exc:
        return _hung_up(exc)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VoiceRegistryError as exc:
        # The catalog's status for the same fault (a 503 naming the file), so the rail reads one
        # answer to "the registry cannot be read" whichever of the two routes met it. It fell to
        # the 502 below, which says the synthesis itself failed.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"voice synth failed: {exc}") from exc
