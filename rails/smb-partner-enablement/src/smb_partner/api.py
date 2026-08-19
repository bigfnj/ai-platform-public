"""SMB Partner Enablement rail — FastAPI.

Run (dev): uvicorn --factory smb_partner.api:create_api --port 8870

Routes (the gateway proxies these under /smb-partner-enablement/):
  GET  /api/health           liveness + which models are actually resident
  GET  /api/capabilities     model + voice capability, for the UI to render honestly
  GET  /api/collections      the SME corpus, per collection
  POST /api/ingest           re-ingest the seed knowledge base (admin)
  POST /api/upload           index an ad-hoc document
  POST /api/ask              grounded answer (+ optional voice payload)
  WS   /ws/ask               the same, streamed token-by-token
"""
from __future__ import annotations

import asyncio
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
    text: str = Field(min_length=1)
    source: str = "upload.md"


class ScenarioBody(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=80)
    # question id -> chosen option label. Unanswered questions are allowed; the package is
    # simply less specific, which beats blocking a partner who is in a hurry.
    answers: dict[str, str] = {}


def identity(x_platform_user: str | None = Header(default=None),
             x_platform_admin: str | None = Header(default=None)) -> dict[str, Any]:
    """Identity as set by the gateway. Fails closed when the header is absent: a request
    that did not come through the gateway is a sibling container, never an admin."""
    if x_platform_user is None:
        if not config.STANDALONE:
            raise HTTPException(status_code=401, detail="no platform identity")
        return {"user": "standalone", "is_admin": True}
    return {"user": x_platform_user, "is_admin": x_platform_admin == "1"}


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


# The model slots this rail shows as header chips, in display order. Slot ids match the
# gateway's RAIL_MODEL_SLOTS and rail.json; "retrieval" has no panel counterpart because
# embedders are out of that panel's scope. Tag-tolerant matching and the four-state resolution
# live in modelstate.py, shared in shape (not by import) with every other rail.
MODEL_SLOTS: list[tuple[str, str, str]] = [
    ("reasoning", "LLM", config.RAG_MODEL),
    ("retrieval", "Retrieval", config.EMBED_MODEL),
]


def create_api() -> FastAPI:
    app = FastAPI(title="SMB Partner Enablement", version="0.1.0")
    store.init()

    @app.on_event("startup")
    async def _boot() -> None:
        # Ingest and warm off the event loop: both are slow, blocking, and non-fatal. The
        # rail must answer /api/health while the corpus is still embedding.
        async def prepare() -> None:
            try:
                report = await asyncio.to_thread(ingest.ingest_seed)
                log.info("seed ingest: %s", report)
            except Exception as exc:  # noqa: BLE001 - boot must not die on ingest
                log.warning("seed ingest failed: %s", exc)
            # Warm the pair this rail keeps resident. Failure is fine — the first ask loads them.
            for model in (config.EMBED_MODEL, config.RAG_MODEL):
                try:
                    await asyncio.to_thread(broker.warm, model)
                except Exception as exc:  # noqa: BLE001
                    log.info("warm %s skipped: %s", model, exc)

        asyncio.create_task(prepare())

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "app": "smb-partner-enablement", **store.stats()}

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
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/ask")
    async def ws_ask(ws: WebSocket) -> None:
        """Streamed answers. The gateway's WS proxy has already authenticated the handshake
        and forwarded identity, so this endpoint trusts the connection it was handed."""
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
                    async for tok in broker.chat_stream(
                        config.RAG_MODEL,
                        _messages(body.question, hits, spoken=body.speak),
                        options={"num_predict": budget},
                    ):
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
        except WebSocketDisconnect:
            return

    return app
