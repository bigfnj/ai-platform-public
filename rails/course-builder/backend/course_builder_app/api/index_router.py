"""Index management: start, stream progress, drop."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import corpus as _corpus
from .. import jobs
from ..config import settings
from .identity import Identity, identity

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="indexer")


class IndexRequest(BaseModel):
    corpus_path: str
    resume: bool = False
    limit: int = 0


@router.post("/api/index/start")
async def start_index(req: IndexRequest, _: Identity = Depends(identity)):
    current = jobs.get(jobs.INDEX_JOB_ID)
    if current and current.status == jobs.JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="index job already running")

    job = jobs.start("index")

    async def run() -> None:
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                _pool,
                lambda: _corpus.run_index(
                    corpus_path=req.corpus_path,
                    db_path=settings.index_path,
                    embed_role=settings.embed_role,
                    resume=req.resume,
                    limit=req.limit,
                    progress=job.push,
                ),
            )
            job.finish(result)
        except Exception as exc:
            job.fail(str(exc))

    asyncio.create_task(run())
    return {"job_id": job.job_id, "status": "started"}


@router.get("/api/index/events")
async def index_events(_: Identity = Depends(identity)):
    job = jobs.get(jobs.INDEX_JOB_ID)
    if not job:
        raise HTTPException(status_code=404, detail="no index job started yet")

    async def generate():
        while True:
            try:
                event = await asyncio.wait_for(job.queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
                continue
            yield {"event": event["type"], "data": event.get("message", "")}
            if event["type"] in ("done", "error"):
                break

    return EventSourceResponse(generate())


@router.get("/api/index/status")
async def index_status(_: Identity = Depends(identity)):
    from .. import corpus as _corpus
    stats = _corpus.index_stats(settings.index_path)
    job = jobs.get(jobs.INDEX_JOB_ID)
    return {
        "index": stats,
        "job": {
            "status": job.status if job else None,
            "error": job.error if job else None,
        } if job else None,
    }


@router.delete("/api/index")
async def drop_index(_: Identity = Depends(identity)):
    import pathlib
    p = pathlib.Path(settings.index_path)
    if p.exists():
        p.unlink()
        # DuckDB WAL files
        for ext in (".wal", ".tmp"):
            px = p.with_suffix(ext)
            if px.exists():
                px.unlink()
    return {"dropped": True}
