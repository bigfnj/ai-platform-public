"""Platform API tier for the ai-playground rail.

The gateway proxies ``/ai-playground/api/*`` here (buffered) and ``/ai-playground/ws/*``
via its live WebSocket proxy. Every HTTP route lives under ``/api/*``; the streaming RAG
answer runs over ``/ws/rag`` so tokens reach the browser unbuffered.

Retrieval always runs locally through the broker (bge-m3); only generation flips between
the local model and NVIDIA NIM (the ``backend`` field on the WS request).
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import threading

from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect)

from ai_playground import broker, config, corpora, db, demos, nim
from ai_playground.api import deps
from ai_playground.api.deps import Identity
from ai_playground.bench import assets, describe, engine, pull, querysets, refresh, registry
from ai_playground.bench import providers as bench_providers


def _seed_in_background() -> None:
    """Ingest seed corpora (embed via broker) + load seed query sets off the startup path so
    create_api() stays fast. Query sets need no broker, so they load even if the GPU is cold."""
    def job() -> None:
        con = db.connect()
        try:
            with contextlib.suppress(Exception):
                querysets.ensure_seeds(con)
        finally:
            con.close()
        with contextlib.suppress(Exception):
            corpora.ensure_seeds()
    threading.Thread(target=job, daemon=True).start()


_MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _valid_model_id(mid: str) -> bool:
    """A registry id must be a plain slug — no path separators/traversal — since it becomes a
    directory name under MODELS_DIR (see assets.model_dir)."""
    return bool(mid) and _MODEL_ID_RE.fullmatch(mid) is not None


def _safe_rel_path(f: object) -> bool:
    """A fetch target must be a relative path inside the model dir: no absolute, drive, or ``..``."""
    if not isinstance(f, str) or not f:
        return False
    norm = f.replace("\\", "/")
    return not norm.startswith("/") and ":" not in f and ".." not in norm.split("/")


def _sources_payload(hits: list[dict]) -> list[dict]:
    return [{"n": i + 1, "source": h["source"], "score": round(h["score"], 3), "text": h["text"]}
            for i, h in enumerate(hits)]


def _context(hits: list[dict]) -> str:
    return "\n\n".join(f"[{i + 1}] ({h['source']}) {h['text']}" for i, h in enumerate(hits))


def _messages(question: str, hits: list[dict]) -> list[dict]:
    return [{"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{_context(hits)}\n\nQuestion: {question}"}]


def create_api() -> FastAPI:
    config.ensure_dirs()
    con = db.connect()
    try:
        db.init_db(con)
    finally:
        con.close()
    _seed_in_background()

    app = FastAPI(title="ai-playground", version="0.1.0",
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "broker": broker.up(), "nim": nim.available()}

    @app.get("/api/whoami")
    def whoami(ident: Identity = Depends(deps.identity)) -> dict:
        return {"user": ident.user, "is_admin": ident.is_admin}

    def _gen_info() -> dict:
        """The local generation model + GPU, resolved + vendor-labelled, so the RAG demo can show
        the actual NVIDIA model on the NVIDIA RTX 4090 (the showcase) rather than 'local GPU'."""
        try:
            model = broker.resolved_model(config.CHAT_MODEL)
        except Exception:  # noqa: BLE001
            model = config.CHAT_MODEL
        gpu = ""
        try:
            gpu = ((broker.status().get("gpu") or {}).get("gpu_name") or "").strip()
        except Exception:  # noqa: BLE001
            gpu = ""
        low = (model or "").lower()
        is_nvidia = "nemotron" in low or "nvidia" in low
        label = "NVIDIA Nemotron" if "nemotron" in low else (model or config.CHAT_MODEL)
        return {"role": config.CHAT_MODEL, "model": model or config.CHAT_MODEL,
                "label": label, "is_nvidia": is_nvidia, "gpu": gpu or "NVIDIA RTX 4090"}

    @app.get("/api/demos")
    def list_demos() -> dict:
        return {"demos": demos.DEMOS, "nim": nim.info(), "gen": _gen_info()}

    @app.post("/api/nim/probe")
    async def nim_probe() -> dict:
        """Real auth check for the 'Connect to NVIDIA NIM' toggle: a 1-token completion
        against the hosted endpoint. Raises 502 with the reason on a bad/absent key."""
        try:
            await nim.probe()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "endpoint": nim.BASE_URL, "chat_model": nim.CHAT_MODEL}

    # --- RAG demo -----------------------------------------------------------

    @app.get("/api/rag/corpora")
    def rag_corpora(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            return {"corpora": db.list_corpora(con, ident.user)}
        finally:
            con.close()

    @app.post("/api/rag/upload")
    async def rag_upload(ident: Identity = Depends(deps.identity),
                         name: str = Form(...),
                         files: list[UploadFile] = File(...)) -> dict:
        docs: list[tuple[str, str]] = []
        for f in files:
            raw = await f.read()
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="ignore")
            docs.append((f.filename or "upload.txt", text))
        try:
            return await asyncio.to_thread(corpora.ingest_upload, ident.user, name, docs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/rag/corpus/{corpus_id}")
    def rag_delete(corpus_id: int, ident: Identity = Depends(deps.identity)) -> dict:
        try:
            corpora.delete(corpus_id, ident.user, ident.is_admin)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"deleted": corpus_id}

    @app.post("/api/rag/ask")
    async def rag_ask(body: dict, ident: Identity = Depends(deps.identity)) -> dict:
        """Buffered (non-streaming) RAG answer — a fallback for non-WS clients. The demo
        drives /ws/rag for live token streaming."""
        question = (body.get("question") or "").strip()
        corpus_id = int(body.get("corpus") or 0)
        if not question:
            raise HTTPException(status_code=400, detail="empty question")
        try:
            hits = await asyncio.to_thread(corpora.retrieve, corpus_id, question, config.TOP_K, ident.user)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        answer = await asyncio.to_thread(
            broker.chat, config.CHAT_MODEL, _messages(question, hits),
            options={"num_predict": config.MAX_TOKENS})
        return {"answer": answer, "sources": _sources_payload(hits)}

    @app.websocket("/ws/rag")
    async def ws_rag(ws: WebSocket) -> None:
        """Live RAG: sends one `sources` frame, then `token` frames, then `done`. On any
        failure sends an `error` frame. `backend` picks local (broker) or nim generation."""
        await ws.accept()
        user = ws.headers.get("x-platform-user") or None
        try:
            req = await ws.receive_json()
        except Exception:  # noqa: BLE001 — client vanished / bad frame
            with contextlib.suppress(Exception):
                await ws.close()
            return
        question = (req.get("question") or "").strip()
        corpus_id = int(req.get("corpus") or 0)
        backend = (req.get("backend") or "local").lower()
        try:
            if not question:
                await ws.send_json({"type": "error", "message": "empty question"})
                return
            hits = await asyncio.to_thread(corpora.retrieve, corpus_id, question, config.TOP_K, user)
            await ws.send_json({"type": "sources", "sources": _sources_payload(hits)})
            msgs = _messages(question, hits)
            if backend == "nim":
                if not nim.available():
                    await ws.send_json({"type": "error", "message": "NVIDIA NIM key not configured"})
                    return
                gen = nim.chat_stream(msgs, max_tokens=config.MAX_TOKENS)
            else:
                gen = broker.chat_stream(config.CHAT_MODEL, msgs,
                                         options={"num_predict": config.MAX_TOKENS})
            async for tok in gen:
                await ws.send_json({"type": "token", "text": tok})
            await ws.send_json({"type": "done"})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001 — surface as an error frame, never crash the WS
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    # --- Embedding Lab demo -------------------------------------------------

    def _load_chunks(corpus_id: int, ident: Identity) -> tuple[str, list[dict]]:
        """Corpus name + its raw chunks (text+source), owner-checked. Vectors are ignored:
        the lab re-embeds with each candidate model, so the stored bge-m3 vectors don't apply."""
        con = db.connect()
        try:
            meta = db.get_corpus(con, corpus_id)
            if meta is None:
                raise ValueError("corpus not found")
            # Owner-scoped: a user corpus is readable only by its owner (shared corpora have a null
            # owner). A null identity (e.g. a direct-to-rail WS with no gateway header) is NOT the
            # owner of anything, so it can't read another tenant's uploaded documents.
            if (meta["kind"] == "user" and meta["owner"] is not None
                    and meta["owner"] != ident.user):
                raise PermissionError("not your corpus")
            chunks, _ = db.get_chunks(con, corpus_id)
            return meta["name"], chunks
        finally:
            con.close()

    @app.get("/api/bench/models")
    def bench_models(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            return {"models": registry.list_with_status(con),
                    "prompting": ["none", "bge-query", "model"]}
        finally:
            con.close()

    @app.post("/api/bench/models")
    def bench_add_model(body: dict, ident: Identity = Depends(deps.require_admin)) -> dict:
        """Add/replace a registry model. Minimal validation by provider; persisted to SQLite."""
        spec = dict(body or {})
        mid = (spec.get("id") or "").strip()
        prov = spec.get("provider")
        if not mid or prov not in ("broker", "onnx"):
            raise HTTPException(400, "need id and provider ('broker'|'onnx')")
        if not _valid_model_id(mid):
            raise HTTPException(400, "invalid id (letters/digits/._- only, no path separators)")
        if prov == "broker" and not spec.get("broker_model"):
            raise HTTPException(400, "broker model needs 'broker_model'")
        if prov == "onnx" and not (spec.get("hf_repo") and spec.get("onnx_file")):
            raise HTTPException(400, "onnx model needs 'hf_repo' and 'onnx_file'")
        # Path/repo fields feed the filesystem + HF fetch — reject traversal / absolute paths so a
        # spec can never write or read outside the model's own dir (defense-in-depth with model_dir).
        for f in list(spec.get("files") or []) + ([spec["onnx_file"]] if spec.get("onnx_file") else []):
            if not _safe_rel_path(f):
                raise HTTPException(400, f"unsafe file path '{f}'")
        if spec.get("hf_repo") and not re.fullmatch(r"[\w.-]+/[\w.-]+", str(spec["hf_repo"])):
            raise HTTPException(400, "invalid hf_repo (expected 'org/name')")
        spec["id"] = mid
        spec.setdefault("label", mid)
        spec.setdefault("mrl_dims", [])
        con = db.connect()
        try:
            db.upsert_bench_model(con, spec, ident.user)
        finally:
            con.close()
        return {"ok": True, "id": mid}

    @app.post("/api/bench/models/describe")
    async def bench_describe(body: dict, ident: Identity = Depends(deps.require_admin)) -> dict:
        """Draft a plain-English 'about' line for a model being added: match the name on Hugging
        Face, read its card, and condense with the broker LLM. Returned for the admin to approve."""
        name = (body.get("name") or body.get("id") or "").strip()
        if not name:
            raise HTTPException(400, "need a model name to describe")
        try:
            return await asyncio.to_thread(describe.describe, name, body.get("family"),
                                           body.get("hf_repo"), body.get("broker_model"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"could not draft a description: {exc}") from exc

    @app.delete("/api/bench/models/{model_id}")
    def bench_del_model(model_id: str, purge: bool = False,
                        ident: Identity = Depends(deps.require_admin)) -> dict:
        if not _valid_model_id(model_id):
            raise HTTPException(400, "invalid model id")
        con = db.connect()
        try:
            db.delete_bench_model(con, model_id)
        finally:
            con.close()
        if purge:
            with contextlib.suppress(Exception):
                assets.remove(model_id)
        return {"deleted": model_id}

    @app.post("/api/bench/models/{model_id}/fetch")
    async def bench_fetch_model(model_id: str,
                                ident: Identity = Depends(deps.require_admin)) -> dict:
        """Download an onnx model's int8 graph + tokenizer from Hugging Face into the volume."""
        if not _valid_model_id(model_id):
            raise HTTPException(400, "invalid model id")
        con = db.connect()
        try:
            spec = registry.get_spec(con, model_id)
        finally:
            con.close()
        if spec is None or spec.get("provider") != "onnx":
            raise HTTPException(400, "not an onnx model")
        try:
            return await asyncio.to_thread(assets.fetch, spec)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/bench/models/{model_id}/pull")
    async def bench_pull_model(model_id: str,
                               ident: Identity = Depends(deps.require_admin)) -> dict:
        """Best-effort pull of a broker (Ollama) model from the host daemon."""
        con = db.connect()
        try:
            spec = registry.get_spec(con, model_id)
        finally:
            con.close()
        if spec is None or spec.get("provider") != "broker":
            raise HTTPException(400, "not a broker model")
        try:
            return await asyncio.to_thread(pull.pull, spec["broker_model"])
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/bench/refresh")
    async def bench_refresh(ident: Identity = Depends(deps.require_admin)) -> dict:
        """Scan the registry's families for newer releases (read-only). Slow-ish (hits the Hub)."""
        con = db.connect()
        try:
            return await asyncio.to_thread(refresh.scan, con)
        finally:
            con.close()

    @app.post("/api/bench/refresh/repull-all")
    async def bench_repull_all(ident: Identity = Depends(deps.require_admin)) -> dict:
        """Re-pull every broker embedding model (idempotent). The scheduled 'model update' task
        the platform scheduler fires for this rail; also usable as a manual 'update all' button."""
        con = db.connect()
        try:
            return await asyncio.to_thread(refresh.repull_broker, con)
        finally:
            con.close()

    @app.post("/api/bench/refresh/adopt/{model_id}")
    async def bench_adopt(model_id: str, ident: Identity = Depends(deps.require_admin)) -> dict:
        """Fetch + register the newest version of an onnx model as a new entry (keeps the old)."""
        con = db.connect()
        try:
            return await asyncio.to_thread(refresh.adopt, con, model_id, ident.user)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            con.close()

    @app.get("/api/bench/corpora")
    def bench_corpora(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            return {"corpora": db.list_corpora(con, ident.user)}
        finally:
            con.close()

    @app.get("/api/bench/querysets")
    def bench_querysets(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            return {"querysets": db.list_querysets(con, ident.user)}
        finally:
            con.close()

    @app.post("/api/bench/querysets/upload")
    async def bench_upload_queryset(ident: Identity = Depends(deps.identity),
                                    name: str = Form(...),
                                    file: UploadFile = File(...)) -> dict:
        raw = await file.read()
        import json as _json
        try:
            data = _json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"not valid JSON: {exc}") from exc
        con = db.connect()
        try:
            return querysets.ingest(con, ident.user, name, data)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            con.close()

    @app.delete("/api/bench/querysets/{qsid}")
    def bench_del_queryset(qsid: int, ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            querysets.delete(con, qsid, ident.user, ident.is_admin)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        finally:
            con.close()
        return {"deleted": qsid}

    @app.get("/api/bench/runs")
    def bench_runs(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            return {"runs": db.list_runs(con, ident.user)}
        finally:
            con.close()

    @app.get("/api/bench/runs/{run_id}")
    def bench_run_detail(run_id: int, ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            run = db.get_run(con, run_id)
        finally:
            con.close()
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.post("/api/bench/run")
    async def bench_run(body: dict, ident: Identity = Depends(deps.identity)) -> dict:
        """Buffered benchmark run (fallback for non-WS clients). The demo drives /ws/bench."""
        corpus_id = int(body.get("corpus") or 0)
        queryset_id = int(body.get("queryset") or 0)
        configs = body.get("configs") or []
        k = int(body.get("k") or config.TOP_K)
        reranker = (body.get("reranker") or None)
        rerank_depth = int(body.get("rerank_depth") or 10)
        if not configs:
            raise HTTPException(400, "no model run-configs selected")
        try:
            cname, chunks = _load_chunks(corpus_id, ident)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        con = db.connect()
        try:
            qs = db.get_queryset(con, queryset_id)
            queries = db.get_queries(con, queryset_id)
            if not queries:
                raise HTTPException(400, "query set is empty")
            results = await asyncio.to_thread(engine.run, con, chunks, queries, configs, k,
                                              None, reranker, rerank_depth)
            db.add_run(con, owner=ident.user, corpus_name=cname,
                       queryset=(qs or {}).get("name", ""), k=k, results=results)
        finally:
            con.close()
        return {"corpus": cname, "queryset": (qs or {}).get("name", ""), "k": k,
                "n_docs": len(chunks), "results": results}

    @app.websocket("/ws/bench")
    async def ws_bench(ws: WebSocket) -> None:
        """Streamed benchmark: a `meta` frame, `progress` frames per config as they start/finish,
        then a `done` frame with the full results. Errors surface as an `error` frame."""
        await ws.accept()
        user = ws.headers.get("x-platform-user") or None
        try:
            req = await ws.receive_json()
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await ws.close()
            return
        corpus_id = int(req.get("corpus") or 0)
        queryset_id = int(req.get("queryset") or 0)
        configs = req.get("configs") or []
        k = int(req.get("k") or config.TOP_K)
        reranker = (req.get("reranker") or None)
        rerank_depth = int(req.get("rerank_depth") or 10)
        ident = Identity(user, False)
        try:
            if not configs:
                await ws.send_json({"type": "error", "message": "no models selected"})
                return
            cname, chunks = _load_chunks(corpus_id, ident)
            con = db.connect()
            try:
                qs = db.get_queryset(con, queryset_id)
                queries = db.get_queries(con, queryset_id)
            finally:
                con.close()
            if not queries:
                await ws.send_json({"type": "error", "message": "query set is empty"})
                return
            await ws.send_json({"type": "meta", "corpus": cname, "n_docs": len(chunks),
                                "queryset": (qs or {}).get("name", ""), "n_queries": len(queries),
                                "configs": len(configs)})
            loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue()

            def progress(cfg_id: str, phase: str) -> None:
                loop.call_soon_threadsafe(
                    q.put_nowait, {"type": "progress", "config": cfg_id, "phase": phase})

            async def worker() -> None:
                con2 = db.connect()
                try:
                    results = await asyncio.to_thread(
                        engine.run, con2, chunks, queries, configs, k, progress,
                        reranker, rerank_depth)
                    with contextlib.suppress(Exception):
                        db.add_run(con2, owner=user, corpus_name=cname,
                                   queryset=(qs or {}).get("name", ""), k=k, results=results)
                finally:
                    con2.close()
                loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "results": results})

            task = asyncio.create_task(worker())
            while True:
                frame = await q.get()
                await ws.send_json(frame)
                if frame["type"] in ("done", "error"):
                    break
            await task
        except (ValueError, PermissionError) as exc:
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": str(exc)})
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    return app
