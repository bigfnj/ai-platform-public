"""SMB Partner Enablement rail — FastAPI.

Run (dev): uvicorn --factory smb_partner.api:create_api --port 8870

Routes (the gateway proxies these under /smb-partner-enablement/):
  GET  /api/health           liveness + the corpus counts and the boot ingest's state
  GET  /api/capabilities     model + voice capability, for the UI to render honestly
  GET  /api/collections      the SME corpus, per collection
  POST /api/ingest           re-ingest the seed knowledge base (admin)
  POST /api/upload           index an ad-hoc document (admin)
  POST /api/speak            read text aloud (Kokoro, or the browser's voice)
  POST /api/transcribe       speech to text for a recorded utterance
  POST /api/ask              grounded answer (+ optional voice payload)
  WS   /ws/ask               the same, streamed token-by-token
  GET  /api/scenarios        the Scenario Builder's scenarios, questions and stages
  WS   /ws/scenario          build a package, streamed (the only route that builds one)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import aclosing, asynccontextmanager
from typing import Any

import anyio
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from smb_partner.identity import Identity, identity, require_admin, ws_user
from smb_partner import (
    broker,
    config,
    generate,
    ingest,
    logsetup,
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


# The most chunks one answer may be grounded in. ~3x either rail's default TOP_K, and at
# rag.MAX_CHARS (1400) a 28k-character context, which is already past what the small answer
# models read well.
MAX_TOP_K = 20


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    collections: list[str] = []
    # 0 means the rail's TOP_K. Bounded on both sides: unvalidated, top_k=-1 sliced the ranking
    # to every chunk but one (measured: 299 citations, 423,796 prompt characters into one
    # answer call), and a large value did the same on purpose.
    top_k: int = Field(default=0, ge=0, le=MAX_TOP_K)
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


# Identity is the GENERATED identity.py every other rail carries (tools/rail_template.py),
# not a hand-written copy. The inline copy this replaced had drifted three ways from the
# contract: standalone resolved to the literal user "standalone" (RAIL_CONTRACT: None,
# never the string, because rails persist the caller), X-Platform-Admin counted only the
# exact "1" where the canonical helper reads 1/true/yes, and PLATFORM_STANDALONE was
# snapshotted into config at IMPORT time, so a test had to patch the module and a process
# could not see the variable change. `rail_template.py check` now holds it byte for byte.


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


#: Seconds of silence on /ws/ask before a no-op ping goes out (_stream_with_heartbeat). Read at
#: call time.
HEARTBEAT_S = 20.0


async def _stream_with_heartbeat(ws: WebSocket, agen, *, heartbeat: float | None = None):
    """Yield tokens from `agen`, sending a no-op {"type": "ping"} whenever the next token is
    more than `heartbeat` seconds away (HEARTBEAT_S when None). The gap that matters is BEFORE
    the first token: a cold load of the answer model takes minutes, and an owner's question on
    a role switched to the Claude CLI can wait up to the broker's BROKER_CLI_TIMEOUT for its
    first token or its local fallback, during which nothing flows between the `citations` frame
    and the first `token`, and a public WebSocket idle that long is cut at the Cloudflare edge
    (~100s) — sources shown, answer never arriving. The frontend ignores an unknown frame type,
    so the heartbeat needs no client change.

    LEFT EARLY, IT ENDS THE BROKER READ. A ping that raises (the client left) or a caller that
    stops reading (its own send raised; call this under `aclosing`) runs the `finally`: the
    read in flight is cancelled and awaited, then `agen` is closed, so its `async with` unwinds
    the broker request before this returns. Until 2026-10-14 the read was left running, so the
    rail kept reading /v1/chat/stream for nobody until the next frame: the broker's client-gone
    check never fired, a CLI call held its slot to its first token, and a CLI timeout started
    a local fallback nobody would read. openmaic's `_paced` has the same shape."""
    every = HEARTBEAT_S if heartbeat is None else heartbeat
    it = agen.__aiter__()
    nxt: asyncio.Future | None = None
    try:
        while True:
            nxt = asyncio.ensure_future(it.__anext__())
            while not (await asyncio.wait({nxt}, timeout=every))[0]:
                await ws.send_json({"type": "ping"})
            read, nxt = nxt, None
            try:
                tok = read.result()
            except StopAsyncIteration:
                return
            yield tok
    finally:
        await _end_read(nxt, it)


async def _end_read(pending: asyncio.Future | None, it: Any) -> None:
    """Cancel a broker read still in flight, wait for it to end, then close the stream.

    Shielded: anyio delivers a cancellation again at every await, so an unshielded wait could
    return with the read still running, and `aclose()` would then meet a generator that is
    still executing."""
    with anyio.CancelScope(shield=True):
        if pending is not None:
            pending.cancel()
            await asyncio.wait({pending})
            if not pending.cancelled():
                pending.exception()     # retrieved, so asyncio never logs it as unretrieved
        aclose = getattr(it, "aclose", None)
        if aclose is not None:
            await aclose()


# The model slots this rail shows as header chips, in display order. Slot ids match the
# gateway's RAIL_MODEL_SLOTS and rail.json; "retrieval" has no panel counterpart because
# embedders are out of that panel's scope. Tag-tolerant matching and the four-state resolution
# live in modelstate.py, shared in shape (not by import) with every other rail.
MODEL_SLOTS: list[tuple[str, str, str]] = [
    ("reasoning", "LLM", config.RAG_MODEL),
    ("retrieval", "Retrieval", config.EMBED_MODEL),
]

# The chips whose call sites pass the viewer as `requester` (docs/RAIL_CONTRACT.md):
# `reasoning`, the answer on @smb-partner-rag, from /api/ask, /ws/ask and /ws/scenario, on that
# role only (broker._cli_requester). Every scenario pass carries it, a prefilled one too
# (generate._pass), and the boot warm names nobody.
CLI_SLOTS = ("reasoning",)


def create_api() -> FastAPI:
    # First, so the seed-ingest and warm lines below reach the container log at all. Under
    # uvicorn an INFO line from an smb_partner.* logger otherwise reaches no handler.
    logsetup.configure()
    # A LIFESPAN, not @app.on_event("startup"): on_event is deprecated on the pinned FastAPI
    # (0.139) and warned on every app this suite builds. The boot ingest and the opt-in warm
    # hang off it, so it is not something to drop: removed, the rail would boot without ever
    # ingesting. Same body, same task handling, run on entry; nothing runs on exit, because
    # stop-time cancellation of a pending ingest is recorded by _ingest_finished.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Ingest (and the opt-in warm) off the event loop: both are slow, blocking, and
        # non-fatal. The rail must answer /api/health while the corpus is still embedding.
        def _record_failure(kind: str, detail: str) -> None:
            """Record a boot-ingest failure in both halves: `detail` (full, server-side only)
            and `kind` (the exception class, the only half /api/health reports)."""
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
        yield

    # docs/redoc/openapi off: they served the full route list and request schemas to a
    # caller with no identity, on a port every sibling container can reach. co-worker,
    # meeting-atlas and terminal-fun all close them; these two were imported without it.
    app = FastAPI(title="SMB Partner Enablement", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    store.init()

    # Boot-ingest state, declared BEFORE the lifespan runs so /api/health can read it on the
    # very first request — including in-process use where the lifespan never runs at all.
    app.state.ingest_task = None
    app.state.ingest_done = False
    # Two fields rather than one, and the split IS the security boundary. `ingest_error` holds
    # the full "Type: message" for the operator (logs, in-process diagnosis); `ingest_error_type`
    # holds the exception CLASS alone, and that is the only half /api/health may emit. The
    # reasoning is on _ingest().
    app.state.ingest_error = ""
    app.state.ingest_error_type = ""

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

        SECURITY. `error` carries the exception CLASS and never its message. /api/health is
        gated like every other route now, but any signed-in user still reads it, and the
        message out of the seed walk names an absolute host path and a corpus filename
        (FileNotFoundError, PermissionError and UnicodeDecodeError all do). The trade-off, stated plainly: an operator loses "which
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
    # GATED, like every other route. It was the one route with no `Depends(identity)`, kept
    # open "for the container probe", and there is no such probe: no HEALTHCHECK in the
    # Dockerfile, no `healthcheck:` in either compose file, and tools/rail_smoke.py sends
    # identity headers. Open, it handed any sibling container the corpus shape (measured:
    # 1051 chunks / 9 collections), and the test that kept it open asserted three keys that
    # ARE corpus contents under a sentence saying it "says nothing about the corpus". The
    # gemini-cx twin's open health was also its most expensive read; that half did not apply
    # here, the disclosure did.
    @app.get("/api/health")
    async def health(_: Identity = Depends(identity)) -> dict[str, Any]:
        ingest_state = _ingest()
        # `ok` is False for a degraded run as well as an unfinished one: the preparation
        # finished, but the corpus behind the answers is not the corpus that was meant to be
        # there, and that is not a state to report as healthy.
        return {"ok": (ingest_state["state"] in ("done", "not_started")
                       and not ingest_state.get("degraded", False)),
                "ingest": ingest_state,
                "app": "smb-partner-enablement", **store.stats()}

    @app.get("/api/capabilities")
    async def capabilities(who: Identity = Depends(identity)) -> dict[str, Any]:
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
            #
            # Opted into the Claude CLI (CLI_SLOTS): the answer chip reads Claude for exactly
            # the viewers whose question would go there, judged on the same `who.user` the
            # answer routes pass as `requester`.
            out = modelstate.resolve(MODEL_SLOTS, requester=who.user, cli_slots=CLI_SLOTS)
            return {
                **out,
                "voice": voice.describe(),
                "corpus": store.stats(),
                "user": who.user,
                "is_admin": who.is_admin,
            }

        return await asyncio.to_thread(gather)

    @app.get("/api/collections")
    async def list_collections(who: Identity = Depends(identity)) -> dict[str, Any]:
        return {"collections": await asyncio.to_thread(store.collections)}

    @app.post("/api/ingest")
    async def reingest(force: bool = False,
                       who: Identity = Depends(require_admin)) -> dict[str, Any]:
        return await asyncio.to_thread(ingest.ingest_seed, force=force)

    @app.post("/api/upload")
    async def upload(body: UploadBody, who: Identity = Depends(require_admin)) -> dict[str, Any]:
        try:
            count = await asyncio.to_thread(ingest.ingest_upload, body.name, body.text,
                                            source=body.source)
        except broker.BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"collection": body.name, "chunks": count}

    @app.post("/api/speak")
    async def speak_api(body: SpeakBody, who: Identity = Depends(identity)) -> dict[str, Any]:
        """Synthesize arbitrary text through Kokoro (or browser fallback) — used by Read aloud."""
        return await asyncio.to_thread(voice.speak, body.text)

    @app.post("/api/transcribe")
    async def transcribe_api(body: TranscribeBody,
                             who: Identity = Depends(identity)) -> dict[str, Any]:
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
    async def ask(body: AskBody, who: Identity = Depends(identity)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            hits = _retrieve(body.question, body.collections, body.top_k)
            budget = config.VOICE_MAX_TOKENS if body.speak else config.MAX_TOKENS
            answer = broker.chat(
                config.RAG_MODEL,
                _messages(body.question, hits, spoken=body.speak),
                options={"num_predict": budget},
                requester=who.user,
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
    async def list_scenarios(who: Identity = Depends(identity)) -> dict[str, Any]:
        """The scenarios, their diagnostic questions, and the generation stages the UI shows."""
        return {"scenarios": scenarios.public_view(), "stages": scenarios.STAGES}

    @app.websocket("/ws/scenario")
    async def ws_scenario(ws: WebSocket) -> None:
        """Streamed package generation, reporting the reasoning as it happens. The only route
        that builds a package: a buffered POST /api/scenario/generate ran all four passes inside
        one request behind the gateway's 600 s read timeout, which a pass waiting on the Claude
        CLI could outlast, and nothing but tests called it, so it was retired on 2026-10-14. This
        socket pings while a pass is silent (generate._with_heartbeat).

        Four event types go out. `stage` marks a pass starting and finishing; `analysis` carries
        the deterministic result of the first stage (open questions and the hard constraints that
        fired); `retrieval` names the sourced material a pass is standing on and how well each
        piece matched; `token` carries generation deltas.

        Generation is natively async, so events are awaited straight onto the socket — no worker
        thread and no queue bridge.
        """
        # Refuse BEFORE accept(), so nothing is ever established. Standalone dev yields "",
        # which names nobody as `requester`.
        user = ws_user(ws)
        if user is None:
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
                        body.scenario_id, body.answers, emit, requester=user or None)
                    await ws.send_json({"type": "package", "package": package})
                except WebSocketDisconnect:
                    # The client left mid-build: an event's send raised it (Starlette turns the
                    # server's OSError into WebSocketDisconnect, code 1006). Not a failure to
                    # log with a traceback; the outer clause ends the socket.
                    raise
                except ValueError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001 — a frame always goes out; see below
                    # Per TURN rather than per socket, so an unexpected raise costs this package
                    # and leaves the loop able to serve the next one. NOTE the shipped frontend
                    # opens a NEW socket per build, so today this buys an error FRAME rather
                    # than a surviving session. generate.py reaches chat_stream through its own
                    # wrapper (generate._with_heartbeat), and a pass's own failure is recorded
                    # in the package rather than raised, so what lands here is the unexpected.
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
        # Refuse BEFORE accept(), so nothing is ever established. Standalone dev yields "",
        # which names nobody as `requester`.
        user = ws_user(ws)
        if user is None:
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
                    # aclosing: a token send that raises leaves the loop, and the wrapper's
                    # finally then ends the broker read here, not whenever the GC gets to it.
                    async with aclosing(_stream_with_heartbeat(ws, broker.chat_stream(
                        config.RAG_MODEL,
                        _messages(body.question, hits, spoken=body.speak),
                        options={"num_predict": budget},
                        requester=user or None,
                    ))) as toks:
                        async for tok in toks:
                            parts.append(tok)
                            await ws.send_json({"type": "token", "token": tok})
                    answer = "".join(parts)
                    payload: dict[str, Any] = {"type": "done", "answer": answer}
                    if body.speak:
                        payload["voice"] = await asyncio.to_thread(
                            voice.speak, answer, backend=body.voice_backend)
                    await ws.send_json(payload)
                except WebSocketDisconnect:
                    # The client left mid-answer: a send raised it (Starlette turns the
                    # server's OSError into WebSocketDisconnect, code 1006). Not a failure to
                    # log with a traceback; the outer clause ends the socket.
                    raise
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "detail": str(exc)})
                except Exception as exc:  # noqa: BLE001 — a frame always goes out; see below
                    # Per TURN rather than per socket, so an unexpected raise costs this answer
                    # and leaves the loop able to serve the next one. NOTE the shipped frontend
                    # opens a NEW socket per ask and closes it on done or error, so today this
                    # buys an error FRAME rather than a surviving conversation; V-16's "the
                    # whole conversation, not the answer" overstated it. The frame is the point:
                    # without it the socket ends silently and the UI waits for a reply that
                    # cannot arrive. The send below is suppressed because the socket may be what
                    # broke.
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
