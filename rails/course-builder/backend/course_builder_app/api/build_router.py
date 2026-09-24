"""Build routes: start a build job, stream progress, download result."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import builder as _builder
from .. import jobs
from ..config import settings
from .identity import Identity, identity

router = APIRouter()
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="builder")


class BuildRequest(BaseModel):
    prompt: str
    blueprint: str = ""
    k: int = 12
    raw: bool = False
    title: str = ""
    # Override the default condense role for this run (e.g. "@chat" for local-only).
    condense_role: str = ""


@router.post("/api/build/start")
async def start_build(req: BuildRequest, _: Identity = Depends(identity)):
    if not req.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt is required")

    current = jobs.get(jobs.BUILD_JOB_ID)
    if current and current.status == jobs.JobStatus.RUNNING:
        raise HTTPException(status_code=409, detail="build job already running")

    job = jobs.start("build")
    condense_role = req.condense_role.strip() or settings.condense_role

    async def run() -> None:
        loop = asyncio.get_event_loop()
        try:
            md = await loop.run_in_executor(
                _pool,
                lambda: _builder.run_build(
                    db_path=settings.index_path,
                    embed_role=settings.embed_role,
                    condense_role=condense_role,
                    prompt=req.prompt,
                    blueprint_csv=settings.blueprint_csv,
                    blueprint=req.blueprint,
                    k=req.k,
                    raw=req.raw,
                    title=req.title,
                    progress=job.push,
                ),
            )
            job.finish(md)
        except Exception as exc:
            job.fail(str(exc))

    asyncio.create_task(run())
    return {"job_id": job.job_id, "status": "started"}


@router.get("/api/build/events")
async def build_events(_: Identity = Depends(identity)):
    job = jobs.get(jobs.BUILD_JOB_ID)
    if not job:
        raise HTTPException(status_code=404, detail="no build job started yet")

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


@router.get("/api/build/result")
async def build_result(_: Identity = Depends(identity)):
    job = jobs.get(jobs.BUILD_JOB_ID)
    if not job or job.status != jobs.JobStatus.DONE or not job.result:
        raise HTTPException(status_code=404, detail="no completed build result")
    return Response(
        content=job.result.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="course-source.md"'},
    )
