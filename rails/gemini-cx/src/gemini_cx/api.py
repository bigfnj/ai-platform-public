"""Gemini Enterprise CX rail — FastAPI.

Run (dev): uvicorn --factory gemini_cx.api:create_api --port 8880

Routes (the gateway proxies these under /gemini-cx/):
  GET  /api/health        liveness, corpus stats, resident models, deck validation
  GET  /api/capabilities  what this rail can do right now, so the UI renders honestly
  GET  /api/questions     the curated question deck
  GET  /api/collections   the corpus, per collection
  POST /api/ingest        re-ingest the seed knowledge base (admin)
  POST /api/upload        index an ad-hoc document
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
    text: str = Field(min_length=1)
    source: str = "upload.md"


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


def identity(x_platform_user: str | None = Header(default=None),
             x_platform_admin: str | None = Header(default=None)) -> dict[str, Any]:
    """Identity as set by the gateway. Fails closed when the header is absent: a request that
    did not come through the gateway is a sibling container, never an admin."""
    if x_platform_user is None:
        if not config.STANDALONE:
            raise HTTPException(status_code=401, detail="no platform identity")
        return {"user": "standalone", "is_admin": True}
    return {"user": x_platform_user, "is_admin": x_platform_admin == "1"}


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
MODEL_SLOTS: list[tuple[str, str, str]] = [
    ("llm", "LLM", config.RAG_MODEL),
    ("retrieval", "Retrieval", config.EMBED_MODEL),
]


def _models() -> dict[str, Any]:
    """Four-state status for this rail's model slots (missing/cold/warming/loaded)."""
    return modelstate.resolve(MODEL_SLOTS)


def create_api() -> FastAPI:
    app = FastAPI(title="Gemini Enterprise CX", version="0.1.0")
    store.init()

    @app.on_event("startup")
    async def _boot() -> None:
        # Ingest and warm off the event loop: both are slow, blocking, and non-fatal. The rail
        # must answer /api/health while the corpus is still embedding.
        async def prepare() -> None:
            try:
                report = await asyncio.to_thread(ingest.ingest_seed)
                log.info("seed ingest: %s", report)
            except Exception as exc:  # noqa: BLE001 - boot must not die on ingest
                log.warning("seed ingest failed: %s", exc)
            for model in (config.EMBED_MODEL, config.RAG_MODEL):
                try:
                    await asyncio.to_thread(broker.warm, model)
                except Exception as exc:  # noqa: BLE001
                    log.info("warm %s skipped: %s", model, exc)

        asyncio.create_task(prepare())

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        deck_problems = questions.validate(config.SEED_KB_DIR)
        return {
            "status": "ok",
            "corpus": store.stats(),
            "models": _models(),
            "deck": {"questions": len(questions.all_questions()),
                     "groups": len(questions.groups()),
                     "problems": deck_problems},
        }

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
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
    def question_deck() -> dict[str, Any]:
        return {"groups": questions.groups(),
                "problems": questions.validate(config.SEED_KB_DIR)}

    @app.get("/api/collections")
    def collections() -> dict[str, Any]:
        return {"collections": store.collections(), "corpus": store.stats()}

    @app.post("/api/ingest")
    async def reingest(force: bool = False, who: dict = Depends(require_admin)) -> dict[str, Any]:
        log.info("ingest requested by %s (force=%s)", who["user"], force)
        report = await asyncio.to_thread(ingest.ingest_seed, force=force)
        return {"report": report, "corpus": store.stats()}

    @app.post("/api/upload")
    async def upload(body: UploadBody, who: dict = Depends(identity)) -> dict[str, Any]:
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
                except (json.JSONDecodeError, ValueError) as exc:
                    await ws.send_json({"type": "error", "error": f"bad request: {exc}"})
                    continue

                try:
                    hits = await asyncio.to_thread(_retrieve, question, scope, body.top_k)
                    await ws.send_json({"type": "retrieval", "question": question,
                                        "citations": _cites(hits)})
                    async for tok in broker.chat_stream(
                            config.RAG_MODEL, _messages(question, hits),
                            options={"num_predict": config.MAX_TOKENS, "temperature": 0.2}):
                        await ws.send_json({"type": "token", "text": tok})
                    await ws.send_json({"type": "done"})
                except broker.BrokerError as exc:
                    await ws.send_json({"type": "error", "error": str(exc)})
        except WebSocketDisconnect:
            return

    return app
