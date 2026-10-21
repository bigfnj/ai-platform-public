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
import time
from contextlib import aclosing

import anyio
from fastapi import (Depends, FastAPI, File, Form, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect)
from openai import APIConnectionError

from ai_playground import (broker, config, corpora, db, demos, modelstate, nim,
                           ratelimit)
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


def _bench_request(req: object) -> tuple[int, int, list, str | None, int]:
    """(corpus, queryset, configs, reranker, rerank_depth) from a benchmark request, or a
    ValueError whose text is safe to show. No `k`: the engine never read it (see engine.run)."""
    if not isinstance(req, dict):
        raise ValueError("expected a JSON object")
    try:
        corpus_id = int(req.get("corpus") or 0)
        queryset_id = int(req.get("queryset") or 0)
        rerank_depth = int(req.get("rerank_depth") or 10)
    except (TypeError, ValueError):
        raise ValueError("corpus, queryset and rerank_depth must be numbers") from None
    configs = req.get("configs") or []
    if not isinstance(configs, list):
        raise ValueError("configs must be a list")
    return corpus_id, queryset_id, configs, (req.get("reranker") or None), rerank_depth


_READ_STEP = 64 * 1024


async def _read_capped(files: list[UploadFile], cap: int) -> list[bytes]:
    """Every file's bytes, or a 413 the moment their TOTAL passes `cap`. Read in steps, so an
    oversized upload is refused before it is copied into memory whole, and before anything is
    decoded, parsed or embedded. Starlette has already spooled the body to a temp file by now;
    this bounds what the rail then does with it."""
    out: list[bytes] = []
    total = 0
    for f in files:
        parts: list[bytes] = []
        while True:
            piece = await f.read(_READ_STEP)
            if not piece:
                break
            total += len(piece)
            if total > cap:
                size = (f"{cap / (1024 * 1024):g} MB" if cap >= 1024 * 1024
                        else f"{cap / 1024:g} KB")
                raise HTTPException(status_code=413, detail=f"upload too large (max {size})")
            parts.append(piece)
        out.append(b"".join(parts))
    return out


def _sources_payload(hits: list[dict]) -> list[dict]:
    return [{"n": i + 1, "source": h["source"], "score": round(h["score"], 3), "text": h["text"]}
            for i, h in enumerate(hits)]


def _context(hits: list[dict]) -> str:
    return "\n\n".join(f"[{i + 1}] ({h['source']}) {h['text']}" for i, h in enumerate(hits))


def _messages(question: str, hits: list[dict]) -> list[dict]:
    return [{"role": "system", "content": config.SYSTEM_PROMPT},
            {"role": "user", "content": f"Context:\n{_context(hits)}\n\nQuestion: {question}"}]


# Process-wide, NOT per-create_api(): the limiters have to outlive any app rebuild, or a caller
# able to trigger one would refill its own bucket. Configured (and argued) in config.py.
_NIM_PROBE_LIMIT = ratelimit.RateLimit(limit=config.NIM_PROBE_LIMIT,
                                       window_seconds=config.NIM_PROBE_WINDOW_SECONDS,
                                       name="NVIDIA NIM probe")
_NIM_CHAT_LIMIT = ratelimit.RateLimit(limit=config.NIM_CHAT_LIMIT,
                                      window_seconds=config.NIM_CHAT_WINDOW_SECONDS,
                                      name="NVIDIA NIM answers for you")
_NIM_GLOBAL_LIMIT = ratelimit.RateLimit(limit=config.NIM_GLOBAL_LIMIT,
                                        window_seconds=config.NIM_GLOBAL_WINDOW_SECONDS,
                                        name="NVIDIA NIM calls for this whole rail")
# The ceiling's only key. A constant, never anything from the request: the per-caller buckets
# are keyed on a header a direct-to-rail caller chooses, so a ceiling keyed the same way would
# be one more bucket per invented name.
_RAIL_WIDE = "*"


# The header chips whose call site passes the viewer as `requester` (docs/RAIL_CONTRACT.md):
# `generation`, the RAG demo's answer on @ai-playground, from /api/rag/ask and /ws/rag's local
# branch, on that role only (broker._cli_requester). The NIM branch never reaches the broker,
# and `describe` (the Embedding Lab's draft on the shared @chat class role) names nobody.
CLI_SLOTS = ("generation",)

#: Seconds of silence on /ws/rag before a no-op ping goes out (_stream_with_heartbeat).
HEARTBEAT_S = 20.0


async def _stream_with_heartbeat(ws: WebSocket, agen, *, heartbeat: float | None = None):
    """Yield tokens from `agen`, sending a no-op {"type": "ping"} whenever the next token is
    more than `heartbeat` seconds away (HEARTBEAT_S when None, read at call time).

    The gap that matters is BEFORE the first token. A cold load of the generation model takes
    a while, and an owner's question on a role switched to the Claude CLI can be silent for up
    to the broker's BROKER_CLI_TIMEOUT before its first token or its local fallback. Nothing
    flows between the `sources` frame and the first `token` meanwhile, and a public WebSocket
    idle for ~100 s is cut at the Cloudflare edge: sources shown, answer never arriving. The
    RAG demo ignores a frame type it does not know (RagDemo's onFrame), so `ping` needs no
    client change. The twin of gemini-cx's and smb-partner-enablement's helper.

    LEFT EARLY, IT ENDS THE READ. A ping that raises (the client left) or a caller that stops
    reading (its own send raised; /ws/rag holds this under `aclosing`) runs the `finally`: the
    read in flight is cancelled and awaited, then `agen` is closed, so its `async with` unwinds
    the broker (or NVIDIA) request before this returns. Until 2026-10-14 the read was left
    running, so the rail kept reading /v1/chat/stream for nobody until the next frame: the
    broker's client-gone check never fired, a CLI call held its slot to its first token, and a
    CLI timeout started a local fallback nobody would read. openmaic's `_paced` has the same
    shape; rails do not import each other, so each carries its own."""
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


async def _end_read(pending: asyncio.Future | None, it) -> None:
    """Cancel a read still in flight, wait for it to end, then close the stream.

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


def _charge_nim(per_caller: ratelimit.RateLimit, user: str | None) -> None:
    """Charge one NVIDIA call to `user`'s bucket AND to the rail-wide ceiling, or raise 429.

    Every door that reaches NVIDIA goes through here, before its call. Per caller first, so a
    caller already over their own allowance never touches the shared ceiling; a call refused by
    the ceiling still costs the caller that attempt, which is the conservative direction (it can
    only under-spend, never over-spend).
    """
    per_caller.check(user)
    _NIM_GLOBAL_LIMIT.check(_RAIL_WIDE)


def create_api() -> FastAPI:
    config.ensure_dirs()
    con = db.connect()
    try:
        db.init_db(con)
    finally:
        con.close()
    _seed_in_background()

    # Identity is an APP-LEVEL dependency, not eighteen per-route ones, so a route added later
    # cannot arrive un-gated — which is how /api/health, /api/capabilities, /api/demos and
    # /api/nim/probe (a paid call on the platform's NVIDIA key) came to answer a caller with no
    # identity at all, on a port every sibling container on the compose network can reach.
    #
    # docs/redoc/openapi go off with it: FastAPI serves those through plain Starlette routes that
    # bypass app-level dependencies, so leaving them on would publish the whole route list and
    # request schemas to exactly the caller this dependency exists to refuse. Same shape as
    # the bouquet rail. WebSocket routes inherit the dependency too, but a socket cannot be handed a
    # 401 body, so each one ALSO refuses through deps.ws_user() before accept().
    app = FastAPI(title="ai-playground", version="0.1.0",
                  docs_url=None, redoc_url=None, openapi_url=None,
                  dependencies=[Depends(deps.identity)])

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "broker": broker.up(), "nim": nim.available()}

    @app.get("/api/whoami")
    def whoami(ident: Identity = Depends(deps.identity)) -> dict:
        return {"user": ident.user, "is_admin": ident.is_admin}

    @app.get("/api/capabilities")
    def capabilities(ident: Identity = Depends(deps.identity)) -> dict:
        """Four-state status for the header chips: the generation model (@ai-playground, the
        role the admin Rails panel repoints) and the retrieval embedder (@embed). Resolved live
        from the broker each call, so the chips reflect the current admin selection + residency.

        Opted into the Claude CLI (CLI_SLOTS): the generation chip reads Claude for exactly the
        viewers whose question would go there, judged on the same `ident.user` the RAG routes
        pass as `requester`."""
        return modelstate.resolve([
            ("generation", "LLM", config.CHAT_MODEL),
            # Slot id must be the manifest's ("embed"), not a second name for the same thing.
            # The label is free to read differently; the id is the contract RC007 checks.
            ("embed", "Retrieval", config.EMBED_MODEL),
            # The Embedding Lab's "describe this model" draft (bench/describe.py).
            ("describe", "Model notes", config.DESCRIBE_MODEL),
        ], requester=ident.user, cli_slots=CLI_SLOTS)

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
    async def nim_probe(ident: Identity = Depends(deps.identity)) -> dict:
        """Real auth check for the 'Connect to NVIDIA NIM' toggle: a 1-token completion
        against the hosted endpoint. Raises 502 with the reason on a bad/absent key.

        Rate-limited per caller AND against the rail-wide NVIDIA ceiling, because it spends
        money: the app-level identity dependency establishes that the caller has a NAME, which
        is a gate and not a budget, and a name is something a direct-to-rail caller can rotate.
        (It is not the only door that spends: /ws/rag with backend "nim" is the expensive one.)
        Charged BEFORE the upstream call, so a failing key (the case a caller is most likely to
        retry in a loop) is still metered -- limiting only successes would leave the
        expensive-and-broken path unbounded.
        """
        _charge_nim(_NIM_PROBE_LIMIT, ident.user)
        try:
            await nim.probe()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "endpoint": nim.BASE_URL, "chat_model": nim.CHAT_MODEL}

    @app.post("/api/maintenance/nim-check")
    async def maintenance_nim_check(ident: Identity = Depends(deps.require_admin)) -> dict:
        """Weekly proof that the configured NVIDIA cloud model still answers, fired by the
        platform scheduler (Admin > Schedule) with system-admin headers.

        NVIDIA has retired this rail's cloud default twice, and both times the key kept
        authenticating, so nothing failed until someone pressed the toggle (16 days) or ran
        the deep smoke check by hand (9 days). This puts the same 1-token probe on a schedule,
        and a failure lands in the run record as nim.ModelUnavailable's own sentence, which
        names the model and the env var to change.

        200 {ok, model, latency_s} when it answers; 502 when NVIDIA answered and refused (a
        retired or unentitled model, a bad key); 503 when NVIDIA did not answer at all. No key
        is a deployment that chose local-only generation, so it is a SKIP and a 200: a red row
        every week on such a box would teach the reader to ignore the row.

        Charged to the SAME limiters as /api/nim/probe, and before the upstream call, for the
        same reason that route gives: this spends money, and a second door with its own budget
        would double what any one caller (the scheduler's "system" included) can spend.
        """
        if not nim.available():
            return {"ok": True, "skipped": "no NVIDIA key configured", "model": nim.CHAT_MODEL}
        _charge_nim(_NIM_PROBE_LIMIT, ident.user)
        started = time.perf_counter()
        try:
            await nim.probe()
        except APIConnectionError as exc:   # includes APITimeoutError: no answer at all
            raise HTTPException(status_code=503,
                                detail=f"NVIDIA did not answer: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — ModelUnavailable's text is the point
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "model": nim.CHAT_MODEL,
                "latency_s": round(time.perf_counter() - started, 2)}

    # --- RAG demo -----------------------------------------------------------

    @app.get("/api/rag/corpora")
    def rag_corpora(ident: Identity = Depends(deps.identity)) -> dict:
        con = db.connect()
        try:
            rows = db.list_corpora(con, ident.user)
            # Report the default by SLUG. The demo used to take corpora[0], which made the
            # default a side effect of ingest order; naming it means a reordering can no longer
            # silently move the demo onto a corpus its sample questions aren't written for.
            slugs = {r["slug"] for r in rows}
            default = (config.DEFAULT_CORPUS_SLUG if config.DEFAULT_CORPUS_SLUG in slugs
                       else (rows[0]["slug"] if rows else None))
            return {"corpora": rows, "default": default}
        finally:
            con.close()

    @app.post("/api/rag/upload")
    async def rag_upload(ident: Identity = Depends(deps.identity),
                         name: str = Form(...),
                         files: list[UploadFile] = File(...)) -> dict:
        docs: list[tuple[str, str]] = []
        for f, raw in zip(files, await _read_capped(files, config.MAX_UPLOAD_BYTES)):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1", errors="ignore")
            docs.append((f.filename or "upload.txt", text))
        try:
            return await asyncio.to_thread(corpora.ingest_upload, ident.user, name, docs)
        except corpora.QuotaExceeded as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/rag/corpus/{corpus_id}")
    def rag_delete(corpus_id: int, ident: Identity = Depends(deps.identity)) -> dict:
        try:
            corpora.delete(corpus_id, ident.user, ident.is_admin)
        except (PermissionError, LookupError) as exc:
            # 404 for BOTH, matching every read path in this rail and the invariant db.py
            # states in prose: "a 403 would confirm the id exists, which is half of what was
            # leaking". Two halves had to change together. A 403 told a foreign corpus from
            # an absent one; and a silent no-op told an ABSENT one from a foreign one, by
            # answering 200 {"deleted": <id>}. Either alone still answers "does this id
            # exist", so corpora.delete now raises LookupError and both land here.
            raise HTTPException(status_code=404, detail="corpus not found") from exc
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
        # The viewer's own question, so the viewer is named (CLI_SLOTS); the broker keeps a
        # name only for an owner on an eligible role, and the facade only on this role.
        answer = await asyncio.to_thread(
            broker.chat, config.CHAT_MODEL, _messages(question, hits),
            options={"num_predict": config.MAX_TOKENS}, requester=ident.user)
        return {"answer": answer, "sources": _sources_payload(hits)}

    @app.websocket("/ws/rag")
    async def ws_rag(ws: WebSocket) -> None:
        """Live RAG: sends one `sources` frame, then `token` frames, then `done`. On any
        failure sends an `error` frame. `backend` picks local (broker) or nim generation.

        The nim branch is the rail's expensive NVIDIA door (up to MAX_TOKENS a question), so it
        is charged to the caller's answer budget and the rail-wide ceiling before any work."""
        # Refuse BEFORE accept(), so nothing is ever established. This socket used to read the
        # header purely to name an owner and answered whoever connected, gateway or not.
        who = deps.ws_user(ws)
        if who is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        user = who or None          # standalone yields "" — a null (shared-scope) owner
        try:
            req = await ws.receive_json()
        except Exception:  # noqa: BLE001 — client vanished / bad frame
            with contextlib.suppress(Exception):
                await ws.close()
            return
        try:
            # Parsed INSIDE the try: a frame that is not an object, or a corpus that is not a
            # number, used to raise between accept() and the try, so the socket died with no
            # error frame and the demo sat on "Retrieving..." with nothing to say why.
            if not isinstance(req, dict):
                await ws.send_json({"type": "error", "message": "expected a JSON object"})
                return
            question = str(req.get("question") or "").strip()
            backend = str(req.get("backend") or "local").lower()
            try:
                corpus_id = int(req.get("corpus") or 0)
            except (TypeError, ValueError):
                await ws.send_json({"type": "error", "message": "corpus must be a number"})
                return
            if not question:
                await ws.send_json({"type": "error", "message": "empty question"})
                return
            if backend == "nim":
                if not nim.available():
                    await ws.send_json({"type": "error", "message": "NVIDIA NIM key not configured"})
                    return
                # Charged BEFORE retrieval and before NVIDIA, so a refused question costs
                # neither the shared GPU's embed nor a cent: the refusal is the only work done.
                try:
                    _charge_nim(_NIM_CHAT_LIMIT, user)
                except HTTPException as exc:
                    await ws.send_json({"type": "error", "message": str(exc.detail)})
                    return
            hits = await asyncio.to_thread(corpora.retrieve, corpus_id, question, config.TOP_K, user)
            await ws.send_json({"type": "sources", "sources": _sources_payload(hits)})
            msgs = _messages(question, hits)
            # Who answers, as the broker's leading frames say (broker._served_by): relayed as a
            # `backend` frame before the next token, so the demo can say an answer is Claude's
            # rather than labelling it with the local model. Only the local branch fills it.
            served: list[dict] = []
            if backend == "nim":
                gen = nim.chat_stream(msgs, max_tokens=config.MAX_TOKENS)
            else:
                # The viewer's own question (CLI_SLOTS). `user` is None in standalone dev,
                # which sends no requester.
                gen = broker.chat_stream(config.CHAT_MODEL, msgs,
                                         options={"num_predict": config.MAX_TOKENS},
                                         requester=user, on_backend=served.append)
            # aclosing: a send that raises (the client left) leaves the loop, and the wrapper's
            # finally then ends the read here, not whenever the GC gets to it.
            async with aclosing(_stream_with_heartbeat(ws, gen)) as toks:
                async for tok in toks:
                    while served:
                        await ws.send_json({"type": "backend", **served.pop(0)})
                    await ws.send_json({"type": "token", "text": tok})
            while served:   # an answer with no token at all still says who gave it
                await ws.send_json({"type": "backend", **served.pop(0)})
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
        the platform scheduler fires for this rail; also usable as a manual 'update all' button.

        Any failed pull is a 502, so the Schedule tab records the run as an error with the
        counts and the first reason (the scheduler keeps a non-2xx detail; a 200 is "ok"
        whatever its body says, which is how every pull failing read as ok)."""
        con = db.connect()
        try:
            result = await asyncio.to_thread(refresh.repull_broker, con)
        except RuntimeError as exc:
            # Raised before any pull when @embed cannot be resolved (see repull_broker).
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc
        finally:
            con.close()
        if result["failed"]:
            raise HTTPException(status_code=502, detail={
                "count": result["count"], "failed": result["failed"],
                "first_error": result["first_error"],
                **({"skipped": result["skipped"]} if result.get("skipped") else {})})
        return result

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
        (raw,) = await _read_capped([file], config.MAX_QUERYSET_BYTES)
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
        except (PermissionError, LookupError) as exc:
            # 404 for both, same reasoning as rag_delete above. This one is the sharper of
            # the two, because querysets.delete is the sole any_owner=True caller, so it is
            # the only place that can tell a foreign id from an absent one at all.
            raise HTTPException(404, "queryset not found") from exc
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
            run = db.get_run(con, run_id, ident.user)
        finally:
            con.close()
        if run is None:
            # 404 covers "no such run" AND "not yours" on purpose — see db.get_queryset.
            raise HTTPException(404, "run not found")
        return run

    @app.post("/api/bench/run")
    async def bench_run(body: dict, ident: Identity = Depends(deps.identity)) -> dict:
        """Buffered benchmark run (fallback for non-WS clients). The demo drives /ws/bench."""
        try:
            corpus_id, queryset_id, configs, reranker, rerank_depth = _bench_request(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not configs:
            raise HTTPException(400, "no model run-configs selected")
        try:
            cname, chunks = _load_chunks(corpus_id, ident)
        except (ValueError, PermissionError) as exc:
            raise HTTPException(400, str(exc)) from exc
        con = db.connect()
        try:
            qs = db.get_queryset(con, queryset_id, ident.user)
            queries = db.get_queries(con, queryset_id, ident.user)
            if not queries:
                # Also the answer for another tenant's id: owner-scoped, it reads as empty.
                raise HTTPException(400, "query set is empty")
            results = await asyncio.to_thread(engine.run, con, chunks, queries, configs,
                                              reranker_id=reranker, rerank_depth=rerank_depth)
            db.add_run(con, owner=ident.user, corpus_name=cname,
                       queryset=(qs or {}).get("name", ""), k=None, results=results)
        finally:
            con.close()
        return {"corpus": cname, "queryset": (qs or {}).get("name", ""),
                "n_docs": len(chunks), "results": results}

    @app.websocket("/ws/bench")
    async def ws_bench(ws: WebSocket) -> None:
        """Streamed benchmark: a `meta` frame, `progress` frames per config as they start/finish,
        then a `done` frame with the full results. Errors surface as an `error` frame."""
        # Refuse BEFORE accept(), so nothing is ever established. This socket built an
        # Identity() straight out of the header with no rejection at all, so an un-gated caller
        # got a full benchmark run on the shared GPU.
        who = deps.ws_user(ws)
        if who is None:
            await ws.close(code=4401)
            return
        await ws.accept()
        user = who or None          # standalone yields "" — a null (shared-scope) owner
        try:
            req = await ws.receive_json()
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                await ws.close()
            return
        ident = Identity(user, False)
        # Set in the finally below, on EVERY exit, and read by engine.run between configs: the
        # one thing that actually stops the worker thread. See engine.run for why cancelling
        # the asyncio task never did.
        cancel = threading.Event()
        try:
            # Parsed inside the try for the reason ws_rag gives: a malformed frame used to
            # raise between accept() and here and leave the socket with no error frame.
            corpus_id, queryset_id, configs, reranker, rerank_depth = _bench_request(req)
            if not configs:
                await ws.send_json({"type": "error", "message": "no models selected"})
                return
            cname, chunks = _load_chunks(corpus_id, ident)
            con = db.connect()
            try:
                qs = db.get_queryset(con, queryset_id, user)
                queries = db.get_queries(con, queryset_id, user)
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

            def benchmark() -> list[dict]:
                """The whole run, connection included, inside the worker thread — so a cancelled
                worker cannot close a connection the thread is still executing on."""
                con2 = db.connect()
                try:
                    # Raises engine.Cancelled once the client is gone, so a run nobody will see
                    # is neither finished on the GPU nor written into the history.
                    results = engine.run(con2, chunks, queries, configs, progress=progress,
                                         reranker_id=reranker, rerank_depth=rerank_depth,
                                         cancel=cancel)
                    with contextlib.suppress(Exception):
                        db.add_run(con2, owner=user, corpus_name=cname,
                                   queryset=(qs or {}).get("name", ""), k=None, results=results)
                    return results
                finally:
                    con2.close()

            async def worker() -> None:
                # EVERY exit has to leave a frame on the queue. The reader below blocks on
                # q.get() with no timeout, so a worker that raises used to hang the socket open
                # forever — and engine.run raises readily: `rc["model"]` is a KeyError outside
                # its own per-config try, so one malformed run-config was enough.
                try:
                    results = await asyncio.to_thread(benchmark)
                    loop.call_soon_threadsafe(q.put_nowait, {"type": "done", "results": results})
                except Exception as exc:  # noqa: BLE001 — surface as an error frame, never hang
                    loop.call_soon_threadsafe(
                        q.put_nowait,
                        {"type": "error", "message": f"{type(exc).__name__}: {exc}"})

            task = asyncio.create_task(worker())
            try:
                while True:
                    frame = await q.get()
                    await ws.send_json(frame)
                    if frame["type"] in ("done", "error"):
                        break
                await task
            finally:
                # A client that closes the tab mid-run must not leave a benchmark orphaned: it
                # re-embeds the entire corpus once per model, on the GPU the whole platform
                # shares. Cancelling the task stops only the awaiter; what stops the THREAD is
                # the event, set by the outer finally on this same exit, which engine.run reads
                # before its next config. (The step in flight still finishes: a thread is not
                # interruptible.)
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
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
            # Every exit: a disconnect, an error frame, a finished run (harmless then). The one
            # signal engine.run polls between configs; see the reader's finally above.
            cancel.set()
            with contextlib.suppress(Exception):
                await ws.close()

    return app
