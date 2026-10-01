"""Gemini Enterprise CX rail — FastAPI.

Run (dev): uvicorn --factory gemini_cx.api:create_api --port 8880

Routes (the gateway proxies these under /gemini-cx/):
  GET  /api/health        liveness, boot-ingest state, corpus stats, models, deck validation
  GET  /api/capabilities  what this rail can do right now, so the UI renders honestly
  GET  /api/questions     the curated question deck
  GET  /api/collections   the corpus, per collection
  POST /api/ingest        re-ingest the seed knowledge base (admin)
  POST /api/upload        index an ad-hoc document (admin)
  POST /api/speak         synthesize text via Kokoro (Read aloud); browser fallback
  POST /api/ask           grounded answer, buffered
  WS   /ws/ask            the same, streamed token-by-token

Streaming matters here rather than being a flourish: on an 8 GB card a 4B-class model emits an
800-token answer over roughly twenty seconds, and a spinner for twenty seconds reads as a hang.
The buffered POST is kept because it is trivially scriptable and because a WebSocket through a
corporate proxy is not guaranteed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from gemini_cx import broker, config, ingest, modelstate, questions, rag, store, voice

log = logging.getLogger("gemini_cx.api")


class AskBody(BaseModel):
    # Either free prose OR a deck question id. question_id wins when both are supplied,
    # because a deck click carries collection scoping that free prose cannot.
    question: str = Field(default="", max_length=4000)
    question_id: str = Field(default="", max_length=80)
    collections: list[str] = []
    top_k: int = 0


class UploadBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    # A document is legitimately far bigger than a question (4000) or a Read-aloud payload
    # (8000), so this bound is set from the corpus rather than from those: the largest markdown
    # file any rail on this platform actually ships is ~23 KB, and 200k characters is roughly
    # eight times that — no real upload will meet it. Finite is the point. Ingest chunks at
    # rag.MAX_CHARS (1400) and embeds 32 chunks per broker call, so an unbounded body is an
    # unbounded fan-out of embed requests driven by one caller; at this bound the worst case is
    # ~143 chunks, five embed calls. Kept in step with smb-partner-enablement's UploadBody.
    text: str = Field(min_length=1, max_length=200_000)
    source: str = "upload.md"


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


async def _stream_with_heartbeat(ws: WebSocket, agen: Any, *, heartbeat: float = 20.0) -> Any:
    """Yield tokens from `agen`, emitting a no-op {"type": "ping"} frame whenever the next
    token is more than `heartbeat` seconds away.

    The gap that matters is BEFORE the first token: a cold load of the answer model can take
    minutes, and for those minutes the rail sends the browser nothing between the `retrieval`
    frame and the first `token`. A public WebSocket idle that long is severed at the Cloudflare
    edge (~100s), which strands the UI at 'Retrieving and reasoning…' — sources shown, answer
    never arriving. A periodic frame keeps the hop alive through the load; both frontends
    ignore an unrecognised type, so `ping` needs no client change.
    """
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


def identity(x_platform_user: str | None = Header(default=None),
             x_platform_admin: str | None = Header(default=None)) -> dict[str, Any]:
    """Identity as set by the gateway. Fails closed when the header is absent: a request that
    did not come through the gateway is a sibling container, never an admin.

    Blank counts as absent-and-worse. Starlette hands `X-Platform-User:` through as "" rather
    than None, so `is None` alone admitted a caller asserting an empty identity, which then
    reached every identity-gated route as a named non-admin. Refused ahead of the STANDALONE
    hatch, so a blank header is rejected in dev as well.
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


def _resolve_ask(body: AskBody) -> tuple[str, list[str]]:
    """Turn a request into (question_text, collections). A deck id supplies its own scoping."""
    if body.question_id:
        q = questions.find(body.question_id)
        if q is None:
            raise HTTPException(status_code=404, detail=f"unknown question '{body.question_id}'")
        return q["text"], list(q.get("collections") or [])
    text = body.question.strip()
    if not text:
        raise HTTPException(status_code=422, detail="question or question_id is required")
    return text, list(body.collections or [])


def _retrieve(question: str, collections: list[str], top_k: int) -> list[dict]:
    chunks, matrix = store.snapshot()
    if not chunks:
        return []
    return rag.rank(question, chunks, matrix, k=top_k,
                    collections=set(collections) if collections else None)


def _messages(question: str, hits: list[dict]) -> list[dict]:
    context = rag.build_context(hits) or "(no matching context was retrieved)"
    return [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]


def _cites(hits: list[dict]) -> list[dict]:
    return [{"n": i, "source": h["source"], "collection": h["collection"],
             "title": h.get("title", ""), "score": round(h["score"], 4)}
            for i, h in enumerate(hits, start=1)]


# The model slots this rail shows as header chips, in display order. Tag-tolerant matching and
# the four-state resolution live in modelstate.py.
#
# Slot ids match the gateway's RAIL_MODEL_SLOTS and rail.json: the answer slot is "reasoning"
# (it was "llm", which the admin panel never called it). "retrieval" has no panel counterpart
# on purpose — embedders are out of that panel's scope. See conformance RC006/RC007.
MODEL_SLOTS: list[tuple[str, str, str]] = [
    ("reasoning", "LLM", config.RAG_MODEL),
    ("retrieval", "Retrieval", config.EMBED_MODEL),
]


def _models() -> dict[str, Any]:
    """Four-state status for this rail's model slots (missing/cold/warming/loaded)."""
    return modelstate.resolve(MODEL_SLOTS)


def create_api() -> FastAPI:
    # docs/redoc/openapi off: they served the full route list and request schemas to a
    # caller with no identity, on a port every sibling container can reach. co-worker,
    # meeting-atlas and terminal-fun all close them; these two were imported without it.
    app = FastAPI(title="Gemini Enterprise CX", version="0.1.0",
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
            # Opt-in (config.WARM_ON_BOOT): a warm holds both models for 30 minutes on every
            # start, and the first question loads them anyway.
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
                # `return` releases it too. Kept in step with the smb-partner-enablement twin.
                app.state.ingest_task = None

        # RETAINED on app.state while it RUNS: CPython holds only a WEAK reference to a running
        # task, so a dropped handle can be collected mid-flight, and the suspension points here
        # are exactly the long `to_thread` calls. That hazard ends when the task ends, which is
        # why the done-callback above lets it go.
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
        holds `status` at "degraded"), but the state is one a later clean boot can leave behind.
        `failed` now means only what it says: the preparation did NOT complete — cancelled at
        container stop, or a raise wider than `prepare()`'s own guards. `not_started` stays
        distinct from `done`: "boot never scheduled it" is not "it finished".

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
    # into a restart loop that never lets the ingest finish. So the state lives in the BODY's
    # `ingest` field, and `status` is derived from it rather than being the literal "ok" that
    # could only lie.
    #
    # UN-GATED: this is the only route here without `Depends(identity)`, so every field below is
    # readable by any sibling container on the compose network. Keep everything it returns
    # non-identifying — see the security note on _ingest().
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        deck_problems = questions.validate(config.SEED_KB_DIR)
        ingest_state = _ingest()
        # "degraded" covers BOTH shapes of trouble: a preparation that never finished
        # (state "failed") and one that finished carrying an ingest failure (state "done",
        # degraded). The second used to be reported as "failed" forever; see _ingest().
        degraded = ingest_state["state"] == "failed" or ingest_state.get("degraded", False)
        return {
            "status": ("starting" if ingest_state["state"] == "running"
                       else "degraded" if degraded else "ok"),
            "ingest": ingest_state,
            "corpus": store.stats(),
            "models": _models(),
            "deck": {"questions": len(questions.all_questions()),
                     "groups": len(questions.groups()),
                     "problems": deck_problems},
        }

    @app.get("/api/capabilities")
    def capabilities(_: dict = Depends(identity)) -> dict[str, Any]:
        """What the UI may offer. Retrieval needs a corpus; answering needs the broker."""
        stats = store.stats()
        models = _models()
        return {
            "retrieval": stats["chunks"] > 0,
            "answering": models.get("broker") == "ok",
            "streaming": True,
            "upload": True,
            "corpus": stats,
            "broker": models["broker"],
            "models": models["models"],
            "voice": voice.describe(),
        }

    @app.get("/api/questions")
    def question_deck(_: dict = Depends(identity)) -> dict[str, Any]:
        return {"groups": questions.groups(),
                "problems": questions.validate(config.SEED_KB_DIR)}

    @app.get("/api/collections")
    def collections(_: dict = Depends(identity)) -> dict[str, Any]:
        return {"collections": store.collections(), "corpus": store.stats()}

    @app.post("/api/ingest")
    async def reingest(force: bool = False, who: dict = Depends(require_admin)) -> dict[str, Any]:
        log.info("ingest requested by %s (force=%s)", who["user"], force)
        report = await asyncio.to_thread(ingest.ingest_seed, force=force)
        return {"report": report, "corpus": store.stats()}

    @app.post("/api/upload")
    async def upload(body: UploadBody, who: dict = Depends(require_admin)) -> dict[str, Any]:
        # Admin, not merely identified: ingest.ingest_upload -> store.replace_collection DELETEs
        # the named collection's chunks before inserting and rewrites its origin from 'seed' to
        # 'upload'. A non-admin posting name="gecx-overview" therefore repoints a curated
        # collection permanently — later answers are grounded in their text, citing a filename
        # they chose — and it survives both a restart and a plain re-ingest, because ingest_seed
        # only tracks origin == "seed" and skips an unchanged fingerprint. Only
        # /api/ingest?force=true undoes it. smb-partner-enablement gates the identical route the
        # same way.
        try:
            count = await asyncio.to_thread(
                ingest.ingest_upload, body.name, body.text, source=body.source)
        except broker.BrokerError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        log.info("upload %s by %s: %d chunks", body.name, who["user"], count)
        return {"collection": body.name, "chunks": count, "corpus": store.stats()}

    @app.post("/api/speak")
    async def speak_api(body: SpeakBody, who: dict = Depends(identity)) -> dict[str, Any]:
        """Synthesize arbitrary text through Kokoro (or browser fallback) — the Read aloud button.

        Separate from /api/ask on purpose: answers stream over the WebSocket, so the full text
        only exists on the client once the stream has finished. Synthesising server-side during
        the stream would either speak a fragment or force the answer to be buffered.
        """
        del who  # identity is enforced by the dependency; the payload is not user-scoped
        return await asyncio.to_thread(voice.speak, body.text)

    @app.post("/api/ask")
    async def ask(body: AskBody, who: dict = Depends(identity)) -> dict[str, Any]:
        question, scope = _resolve_ask(body)
        try:
            hits = await asyncio.to_thread(_retrieve, question, scope, body.top_k)
            answer = await asyncio.to_thread(
                broker.chat, config.RAG_MODEL, _messages(question, hits),
                options={"num_predict": config.MAX_TOKENS, "temperature": 0.2})
        except broker.BrokerError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"question": question, "answer": answer, "citations": _cites(hits),
                "collections": scope, "user": who["user"]}

    @app.websocket("/ws/ask")
    async def ask_stream(ws: WebSocket) -> None:
        """Streamed answer. Frame types: retrieval, token, done, error.

        The gateway authenticates the WS handshake itself (Starlette HTTP middleware does not
        run for websocket scope), so by the time we are here the connection is authorized.
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
                    body = AskBody(**json.loads(raw))
                    question, scope = _resolve_ask(body)
                except HTTPException as exc:
                    await ws.send_json({"type": "error", "error": exc.detail})
                    continue
                # TypeError as well, matching smb_partner/api.py:326 and :385. Without it a
                # JSON ARRAY body made AskBody(**[1, 2]) raise TypeError, which escaped to
                # the outer handler and ended the socket. That is V-16's own symptom with no
                # broker involved. Contained by the per-turn catch-all below since V-16, but
                # this is where it belongs: a bad request should get "bad request", not the
                # last-resort clause.
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    await ws.send_json({"type": "error", "error": f"bad request: {exc}"})
                    continue

                try:
                    hits = await asyncio.to_thread(_retrieve, question, scope, body.top_k)
                    await ws.send_json({"type": "retrieval", "question": question,
                                        "citations": _cites(hits)})
                    async for tok in _stream_with_heartbeat(ws, broker.chat_stream(
                            config.RAG_MODEL, _messages(question, hits),
                            options={"num_predict": config.MAX_TOKENS, "temperature": 0.2})):
                        await ws.send_json({"type": "token", "text": tok})
                    await ws.send_json({"type": "done"})
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "error": str(exc)})
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
                        await ws.send_json({"type": "error", "error": str(exc)})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 — never end the socket without a frame
            # Last resort, for the raises the per-turn clause cannot see: receive_text() on a
            # half-closed socket, or either bad-request send_json above failing. Nothing is left
            # to retry here, so this one reports and lets the socket go.
            log.exception("/ws/ask failed outside a turn")
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "error": str(exc)})
        finally:
            # Closed explicitly, not merely returned from — the ws_rag shape. Starlette sends no
            # close when an endpoint coroutine simply ends, so falling off the end leaves the
            # browser holding a socket nobody is serving, waiting on a reply that cannot arrive.
            # It is also what lets the socket's END be observed as a disconnect instead of as
            # silence, which is what test_a_json_array... asserts. Suppressed because the
            # ordinary way out of the loop IS a disconnect, and closing a closed socket raises.
            with contextlib.suppress(Exception):
                await ws.close()

    return app
