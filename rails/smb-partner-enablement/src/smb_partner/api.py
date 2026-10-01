"""SMB Partner Enablement rail — FastAPI.

Run (dev): uvicorn --factory smb_partner.api:create_api --port 8870

Routes (the gateway proxies these under /smb-partner-enablement/):
  GET  /api/health           liveness + which models are actually resident
  GET  /api/capabilities     model + voice capability, for the UI to render honestly
  GET  /api/collections      the SME corpus, per collection
  POST /api/ingest           re-ingest the seed knowledge base (admin)
  POST /api/upload           index an ad-hoc document (admin)
  POST /api/ask              grounded answer (+ optional voice payload)
  WS   /ws/ask               the same, streamed token-by-token
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from smb_partner import (
    broker,
    config,
    generate,
    ingest,
    modelstate,
    rag,
    scenarios,
    store,
    voice,
)

log = logging.getLogger("smb_partner.api")


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class TranscribeBody(BaseModel):
    # ~15 MB of base64 is a couple of minutes of opus; well past any single spoken question,
    # and bounded so a stuck recorder cannot post an unbounded body.
    audio_b64: str = Field(min_length=1, max_length=15_000_000)
    suffix: str | None = None
    language: str | None = None


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    collections: list[str] = []
    top_k: int = 0
    # A spoken turn gets the short, ear-shaped system prompt and a voice payload.
    speak: bool = False
    voice_backend: str | None = None


class UploadBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # 200k characters: ~8x the largest markdown file this rail's seed corpus ships, so a real
    # document never meets it, while the body stays finite — ingest embeds 32 chunks per broker
    # call, so an unbounded text is an unbounded embed fan-out. Deliberately the same number as
    # gemini-cx's UploadBody.text; the two routes are byte-identical and are kept in step.
    text: str = Field(min_length=1, max_length=200_000)
    source: str = "upload.md"


class ScenarioBody(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    # question id -> chosen option label. Unanswered questions are allowed; the package is
    # simply less specific, which beats blocking a partner who is in a hurry.
    answers: dict[str, str] = {}


def identity(x_platform_user: str | None = Header(default=None),
             x_platform_admin: str | None = Header(default=None)) -> dict[str, Any]:
    """Identity as set by the gateway. Fails closed when the header is absent: a request
    that did not come through the gateway is a sibling container, never an admin.

    Blank counts as absent-and-worse. Starlette hands `X-Platform-User:` through as "" rather
    than None, so `is None` alone admitted a caller asserting an empty identity, which then
    reached every identity-gated route as a named non-admin. Refused ahead of the STANDALONE
    hatch, so a blank header is rejected in dev as well. Kept identical to the gemini-cx twin.
    """
    if x_platform_user is not None and not x_platform_user.strip():
        raise HTTPException(status_code=401, detail="blank platform identity")
    if x_platform_user is None:
        if not config.STANDALONE:
            raise HTTPException(status_code=401, detail="no platform identity")
        return {"user": "standalone", "is_admin": True}
    return {"user": x_platform_user.strip(), "is_admin": x_platform_admin == "1"}


def ws_user(ws: WebSocket) -> str | None:
    """The identity behind a WebSocket handshake, or None to reject it.

    A websocket cannot carry a 401 body, so the caller closes the connection instead of
    raising. This is deliberately NOT redundant with the gateway authenticating the
    handshake: RC021 exists because the rail's own port is reachable by every sibling
    container on the compose network, and a guard that lives only in the gateway vanishes
    the moment the gateway is refactored. The HTTP routes here already fail closed; these
    sockets did not, and answered an un-gated caller in full.
    """
    user = ws.headers.get("x-platform-user")
    if user:
        return user
    return "standalone" if config.STANDALONE else None


def require_admin(who: dict = Depends(identity)) -> dict:
    if not who["is_admin"]:
        raise HTTPException(status_code=403, detail="admin only")
    return who


def _retrieve(question: str, collections: list[str], top_k: int) -> list[dict]:
    chunks, matrix = store.snapshot()
    if not chunks:
        return []
    return rag.rank(question, chunks, matrix, k=top_k,
                    collections=set(collections) if collections else None)


def _messages(question: str, hits: list[dict], *, spoken: bool) -> list[dict]:
    system = config.VOICE_SYSTEM_PROMPT if spoken else config.SYSTEM_PROMPT
    context = rag.build_context(hits) or "(no matching context was retrieved)"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


def _cites(hits: list[dict]) -> list[dict]:
    return [{"n": i, "source": h["source"], "collection": h["collection"],
             "title": h.get("title", ""), "score": round(h["score"], 4)}
            for i, h in enumerate(hits, start=1)]


async def _stream_with_heartbeat(ws: WebSocket, agen, *, heartbeat: float = 20.0):
    """Yield tokens from `agen`, sending a no-op {"type": "ping"} whenever the next token is
    more than `heartbeat` seconds away. The gap that matters is BEFORE the first token: a cold
    load of the answer model takes minutes, during which nothing flows between the `citations`
    frame and the first `token`, and a public WebSocket idle that long is cut at the Cloudflare
    edge (~100s) — sources shown, answer never arriving. The frontend ignores an unknown frame
    type, so the heartbeat needs no client change."""
    it = agen.__aiter__()
    while True:
        nxt = asyncio.ensure_future(it.__anext__())
        while True:
            done, _ = await asyncio.wait({nxt}, timeout=heartbeat)
            if nxt in done:
                break
            await ws.send_json({"type": "ping"})
        try:
            tok = nxt.result()
        except StopAsyncIteration:
            return
        yield tok


# The model slots this rail shows as header chips, in display order. Slot ids match the
# gateway's RAIL_MODEL_SLOTS and rail.json; "retrieval" has no panel counterpart because
# embedders are out of that panel's scope. Tag-tolerant matching and the four-state resolution
# live in modelstate.py, shared in shape (not by import) with every other rail.
MODEL_SLOTS: list[tuple[str, str, str]] = [
    ("reasoning", "LLM", config.RAG_MODEL),
    ("retrieval", "Retrieval", config.EMBED_MODEL),
]


def create_api() -> FastAPI:
    # docs/redoc/openapi off: they served the full route list and request schemas to a
    # caller with no identity, on a port every sibling container can reach. co-worker,
    # meeting-atlas and terminal-fun all close them; these two were imported without it.
    app = FastAPI(title="SMB Partner Enablement", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None)
    store.init()

    # Boot-ingest state, declared BEFORE startup fires so /api/health can read it on the very
    # first request — including in-process use where the startup event never runs at all.
    app.state.ingest_task = None
    app.state.ingest_done = False
    # Two fields rather than one, and the split IS the security boundary. `ingest_error` holds
    # the full "Type: message" for the operator (logs, in-process diagnosis); `ingest_error_type`
    # holds the exception CLASS alone, and that is the only half /api/health may emit. The
    # reasoning is on _ingest().
    app.state.ingest_error = ""
    app.state.ingest_error_type = ""

    @app.on_event("startup")
    async def _boot() -> None:
        # Ingest (and the opt-in warm) off the event loop: both are slow, blocking, and
        # non-fatal. The rail must answer /api/health while the corpus is still embedding.
        def _record_failure(kind: str, detail: str) -> None:
            """Record a boot-ingest failure in both halves: `detail` (full, server-side only)
            and `kind` (the exception class, the only half that reaches an un-gated caller)."""
            app.state.ingest_error = detail
            app.state.ingest_error_type = kind

        async def prepare() -> None:
            try:
                report = await asyncio.to_thread(ingest.ingest_seed)
                log.info("seed ingest: %s", report)
            except Exception as exc:  # noqa: BLE001 - boot must not die on ingest
                # The WHOLE exception, traceback included, goes to the log — which only an
                # operator reads. ingest_seed walks SEED_KB_DIR, so this message routinely
                # names an absolute host path and a corpus filename; see _ingest().
                log.warning("seed ingest failed: %r", exc, exc_info=True)
                _record_failure(type(exc).__name__, f"{type(exc).__name__}: {exc}")
            # Warm the pair this rail keeps resident, only when asked (config.WARM_ON_BOOT): a
            # warm holds both for 30 minutes on every start. Failure is fine either way, since
            # the first ask loads them.
            if config.WARM_ON_BOOT:
                for model in (config.EMBED_MODEL, config.RAG_MODEL):
                    try:
                        await asyncio.to_thread(broker.warm, model)
                    except Exception as exc:  # noqa: BLE001
                        log.info("warm %s skipped: %s", model, exc)
            # Set LAST and nowhere else. A flag flipped any earlier would record "nothing has
            # raised yet" rather than "the preparation ran" — this repo's own recurring bug.
            app.state.ingest_done = True

        def _ingest_finished(task: asyncio.Task) -> None:
            """RETRIEVE the task's outcome, then RELEASE the handle. `prepare()` guards each
            step with `except Exception`, but a cancellation at container stop, or anything
            wider raised between the guarded blocks, escapes it — and on a DISCARDED handle
            that only ever surfaces as an "exception was never retrieved" warning at
            interpreter exit, long after the rail has been answering healthy over a half-built
            corpus."""
            try:
                if task.cancelled():
                    _record_failure("CancelledError",
                                    "CancelledError: boot ingest did not complete")
                    log.warning("boot ingest was cancelled before it completed")
                    return
                exc = task.exception()
                if exc is not None:
                    _record_failure(type(exc).__name__, f"{type(exc).__name__}: {exc}")
                    log.error("boot ingest failed: %r", exc)
            finally:
                # CLEARED, not kept. The outcome has already been copied onto ingest_done /
                # ingest_error* above, so a finished handle tells /api/health nothing it cannot
                # read there — while pinning the Task plus `prepare()`'s closure over app,
                # config, store and broker for the life of the process. Dropping it here rather
                # than adding a shutdown handler is the deliberate choice: awaiting a
                # multi-minute seed ingest would delay container stop for no gain, and stop-time
                # cancellation is ALREADY observed — the loop cancels the pending task and this
                # same callback records it. In a `finally` so the cancelled branch's early
                # `return` releases it too. Kept in step with the gemini-cx twin.
                app.state.ingest_task = None

        # RETAINED on app.state while it RUNS: CPython holds only a WEAK reference to a running
        # task, so a dropped handle can be collected mid-flight, and the suspension points here
        # are exactly the long `to_thread` calls. That hazard ends when the task ends, which is
        # why the done-callback above lets it go. Kept in step with the gemini-cx twin.
        task = asyncio.create_task(prepare())
        task.add_done_callback(_ingest_finished)
        app.state.ingest_task = task

    def _ingest() -> dict[str, Any]:
        """The boot ingest as one of four literals, plus a `degraded` flag.

        PRECEDENCE. `ingest_done` is tested FIRST, ahead of the error. `prepare()` sets that
        flag as its very last statement, so it means "the preparation ran to the end" — which a
        recorded ingest error does not contradict. Testing the error first made `failed` STICKY:
        a seed ingest that failed while the two warms succeeded pinned this rail to `failed` for
        the life of the process, and tools/rail_smoke.py pins `state == "done"`, so one
        transient failure left the rail red with no route back short of a restart. A run that
        completed carrying a failure is `done, degraded` — the failure stays visible (and still
        turns `ok` off), but the state is one a later clean boot can leave behind. `failed` now
        means only what it says: the preparation did NOT complete — cancelled at container stop,
        or a raise wider than `prepare()`'s own guards. `not_started` stays distinct from `done`:
        "boot never scheduled it" is not "it finished".

        SECURITY. `error` carries the exception CLASS and never its message. /api/health is the
        only route on this rail with no `Depends(identity)`, so every sibling container on the
        compose network reads whatever is here, and the message out of the seed walk names an
        absolute host path and a corpus filename (FileNotFoundError, PermissionError and
        UnicodeDecodeError all do). The trade-off, stated plainly: an operator loses "which
        file" from the payload and reads it in the container log instead, while `failed` and
        `degraded` stay on the wire so the state machine still earns its keep.
        """
        if app.state.ingest_done:
            if app.state.ingest_error_type:
                return {"state": "done", "degraded": True,
                        "error": app.state.ingest_error_type}
            return {"state": "done"}
        if app.state.ingest_error_type:
            return {"state": "failed", "error": app.state.ingest_error_type}
        if app.state.ingest_task is not None:
            return {"state": "running"}
        return {"state": "not_started"}

    # The HTTP STATUS stays 200 in every ingest state, deliberately. deploy/docker-compose.yml
    # declares no `healthcheck:` for this service (and the gateway's depends_on is a plain list,
    # not `condition: service_healthy`), so nothing restarts on a non-200 today — but a first-boot
    # seed ingest (plus two model warms where WARM_ON_BOOT is on) is a multi-minute warm-up on an
    # 8 GB card, and the first healthcheck anyone points at this route would turn that warm-up
    # into a restart loop that never lets the ingest finish. So the state lives in the BODY:
    # `ingest` carries the detail and `ok` is derived from it rather than being the literal True
    # that could only lie.
    #
    # UN-GATED: this is the only route here without `Depends(identity)`, so every field below is
    # readable by any sibling container on the compose network. Keep everything it returns
    # non-identifying — see the security note on _ingest().
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        ingest_state = _ingest()
        # `ok` is False for a degraded run as well as an unfinished one: the preparation
        # finished, but the corpus behind the answers is not the corpus that was meant to be
        # there, and that is not a state to report as healthy.
        return {"ok": (ingest_state["state"] in ("done", "not_started")
                       and not ingest_state.get("degraded", False)),
                "ingest": ingest_state,
                "app": "smb-partner-enablement", **store.stats()}

    @app.get("/api/capabilities")
    async def capabilities(who: dict = Depends(identity)) -> dict[str, Any]:
        """What this rail can actually do right now — the UI renders from this rather than
        assuming, so a missing embedder or a disabled media worker is visible, not a crash."""
        def gather() -> dict[str, Any]:
            # Four-state chips (missing/cold/warming/loaded) under the shared envelope:
            # {"broker": "ok"|"unreachable", "models": [{slot,label,role,model,state}]}.
            #
            # This used to emit "broker_reachable" plus a boolean "resident" per model, so the
            # dot could only say on or off. That collapsed "not installed" and "installed but
            # cold" into one colour — the single distinction an operator acts on differently
            # (an `ollama pull` versus just asking a question). The keys are renamed rather
            # than added alongside: two spellings of the same status is how the drift started.
            out = modelstate.resolve(MODEL_SLOTS)
            return {
                **out,
                "voice": voice.describe(),
                "corpus": store.stats(),
                "user": who["user"],
                "is_admin": who["is_admin"],
            }

        return await asyncio.to_thread(gather)

    @app.get("/api/collections")
    async def list_collections(who: dict = Depends(identity)) -> dict[str, Any]:
        return {"collections": await asyncio.to_thread(store.collections)}

    @app.post("/api/ingest")
    async def reingest(force: bool = False, who: dict = Depends(require_admin)) -> dict[str, Any]:
        return await asyncio.to_thread(ingest.ingest_seed, force=force)

    @app.post("/api/upload")
    async def upload(body: UploadBody, who: dict = Depends(require_admin)) -> dict[str, Any]:
        try:
            count = await asyncio.to_thread(ingest.ingest_upload, body.name, body.text,
                                            source=body.source)
        except broker.BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"collection": body.name, "chunks": count}

    @app.post("/api/speak")
    async def speak_api(body: SpeakBody, who: dict = Depends(identity)) -> dict[str, Any]:
        """Synthesize arbitrary text through Kokoro (or browser fallback) — used by Read aloud."""
        return await asyncio.to_thread(voice.speak, body.text)

    @app.post("/api/transcribe")
    async def transcribe_api(body: TranscribeBody,
                             who: dict = Depends(identity)) -> dict[str, Any]:
        """Speech-to-text for a recorded utterance. The browser records with the microphone
        the user actually chose (Web Speech could not), and the audio never leaves this box."""
        try:
            return await asyncio.to_thread(
                voice.transcribe, body.audio_b64,
                suffix=body.suffix, language=body.language,
            )
        except voice.VoiceUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/ask")
    async def ask(body: AskBody, who: dict = Depends(identity)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            hits = _retrieve(body.question, body.collections, body.top_k)
            budget = config.VOICE_MAX_TOKENS if body.speak else config.MAX_TOKENS
            answer = broker.chat(
                config.RAG_MODEL,
                _messages(body.question, hits, spoken=body.speak),
                options={"num_predict": budget},
            )
            out: dict[str, Any] = {"answer": answer, "citations": _cites(hits),
                                   "grounded": bool(hits)}
            if body.speak:
                out["voice"] = voice.speak(answer, backend=body.voice_backend)
            return out

        try:
            return await asyncio.to_thread(run)
        except broker.BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # --- Scenario Builder ----------------------------------------------------

    @app.get("/api/scenarios")
    async def list_scenarios(who: dict = Depends(identity)) -> dict[str, Any]:
        """The scenarios, their diagnostic questions, and the generation stages the UI shows."""
        return {"scenarios": scenarios.public_view(), "stages": scenarios.STAGES}

    @app.post("/api/scenario/generate")
    async def scenario_generate(body: ScenarioBody,
                                who: dict = Depends(identity)) -> dict[str, Any]:
        """Buffered package generation. The WebSocket below is the better path — it reports each
        pass as it completes — but this exists for clients that cannot hold a socket open."""
        try:
            return await generate.generate_package(body.scenario_id, body.answers)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except broker.BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.websocket("/ws/scenario")
    async def ws_scenario(ws: WebSocket) -> None:
        """Streamed package generation, reporting the reasoning as it happens.

        Four event types go out. `stage` marks a pass starting and finishing; `analysis` carries
        the deterministic result of the first stage (open questions and the hard constraints that
        fired); `retrieval` names the sourced material a pass is standing on and how well each
        piece matched; `token` carries generation deltas.

        Generation is natively async, so events are awaited straight onto the socket — no worker
        thread and no queue bridge.
        """
        # Refuse BEFORE accept(), so nothing is ever established.
        if ws_user(ws) is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    body = ScenarioBody(**json.loads(raw))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    await ws.send_json({"type": "error", "detail": f"bad request: {exc}"})
                    continue

                async def emit(event: str, payload: dict[str, Any]) -> None:
                    await ws.send_json({"type": event, **payload})

                try:
                    package = await generate.generate_package(
                        body.scenario_id, body.answers, emit)
                    await ws.send_json({"type": "package", "package": package})
                except ValueError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001 — a frame always goes out; see below
                    # Per TURN rather than per socket, so an unexpected raise costs this package
                    # and leaves the loop able to serve the next one. NOTE the shipped frontend
                    # opens a NEW socket per build, so today this buys an error FRAME rather
                    # than a surviving session. Reached for real here: generate.py calls
                    # chat_stream WITHOUT _stream_with_heartbeat, so this path has no wrapper to
                    # lean on. Disconnects are NOT handled here: receive_text() is the only
                    # raiser of WebSocketDisconnect and it sits outside this try, so the outer
                    # clause takes it. send() raises RuntimeError, not WebSocketDisconnect.
                    log.exception("/ws/scenario failed mid-turn")
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 — never end the socket without a frame
            # Last resort, for the raises the per-turn clause cannot see: receive_text() on a
            # half-closed socket, or the bad-request send_json above failing.
            log.exception("/ws/scenario failed outside a turn")
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "detail": str(exc)})
        finally:
            # Closed explicitly, not merely returned from — the ws_rag shape. Starlette sends no
            # close when an endpoint coroutine simply ends, so falling off the end leaves the
            # browser holding a socket nobody is serving, waiting on a reply that cannot arrive.
            # It is also what lets the socket's END be observed as a disconnect instead of as
            # silence. Suppressed because the ordinary way out of the loop IS a disconnect, and
            # closing a closed socket raises.
            with contextlib.suppress(Exception):
                await ws.close()

    @app.websocket("/ws/ask")
    async def ws_ask(ws: WebSocket) -> None:
        """Streamed answers. The gateway's WS proxy has already authenticated the handshake
        and forwarded identity, so this endpoint trusts the connection it was handed."""
        # Refuse BEFORE accept(), so nothing is ever established.
        if ws_user(ws) is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    body = AskBody(**json.loads(raw))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    await ws.send_json({"type": "error", "detail": f"bad request: {exc}"})
                    continue
                try:
                    hits = await asyncio.to_thread(
                        _retrieve, body.question, body.collections, body.top_k)
                    await ws.send_json({"type": "citations", "citations": _cites(hits),
                                        "grounded": bool(hits)})
                    budget = config.VOICE_MAX_TOKENS if body.speak else config.MAX_TOKENS
                    parts: list[str] = []
                    async for tok in _stream_with_heartbeat(ws, broker.chat_stream(
                        config.RAG_MODEL,
                        _messages(body.question, hits, spoken=body.speak),
                        options={"num_predict": budget},
                    )):
                        parts.append(tok)
                        await ws.send_json({"type": "token", "token": tok})
                    answer = "".join(parts)
                    payload: dict[str, Any] = {"type": "done", "answer": answer}
                    if body.speak:
                        payload["voice"] = await asyncio.to_thread(
                            voice.speak, answer, backend=body.voice_backend)
                    await ws.send_json(payload)
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001 — a frame always goes out; see below
                    # Per TURN rather than per socket, so an unexpected raise costs this answer
                    # and leaves the loop able to serve the next one. NOTE the shipped frontend
                    # opens a NEW socket per ask and closes it on done or error, so today this
                    # buys an error FRAME rather than a surviving conversation; V-16's "the
                    # whole conversation, not the answer" overstated it. The frame is the point:
                    # without it the socket ends silently and the UI waits for a reply that
                    # cannot arrive. Disconnects are NOT handled here: receive_text() is the only
                    # thing that raises WebSocketDisconnect and it sits outside this try, so the
                    # outer clause takes it. send() raises RuntimeError, not WebSocketDisconnect.
                    # The send below is suppressed because the socket may be what broke.
                    log.exception("/ws/ask failed mid-turn")
                    with contextlib.suppress(Exception):
                        await ws.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 — never end the socket without a frame
            # Last resort, for the raises the per-turn clause cannot see: receive_text() on a
            # half-closed socket, or the bad-request send_json above failing.
            log.exception("/ws/ask failed outside a turn")
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "detail": str(exc)})
        finally:
            # Closed explicitly, not merely returned from — the ws_rag shape. Starlette sends no
            # close when an endpoint coroutine simply ends, so falling off the end leaves the
            # browser holding a socket nobody is serving, waiting on a reply that cannot arrive.
            # It is also what lets the socket's END be observed as a disconnect instead of as
            # silence. Suppressed because the ordinary way out of the loop IS a disconnect, and
            # closing a closed socket raises.
            with contextlib.suppress(Exception):
                await ws.close()

    return app
