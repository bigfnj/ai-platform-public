"""In-memory async job state for long-running index and build operations."""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    job_id: str
    kind: str  # "index" | "build"
    status: JobStatus = JobStatus.RUNNING
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=512))
    result: Any = None
    error: str = ""

    def push(self, msg: str) -> None:
        try:
            self.queue.put_nowait({"type": "progress", "message": msg})
        except asyncio.QueueFull:
            pass

    def finish(self, result: Any = None) -> None:
        self.status = JobStatus.DONE
        self.result = result
        try:
            self.queue.put_nowait({"type": "done", "result": result})
        except asyncio.QueueFull:
            pass

    def fail(self, error: str) -> None:
        self.status = JobStatus.ERROR
        self.error = error
        try:
            self.queue.put_nowait({"type": "error", "message": error})
        except asyncio.QueueFull:
            pass


_jobs: dict[str, Job] = {}

# Stable IDs so the UI can always find the current index/build job.
INDEX_JOB_ID = "index-current"
BUILD_JOB_ID = "build-current"


def start(kind: str) -> Job:
    job_id = INDEX_JOB_ID if kind == "index" else BUILD_JOB_ID
    job = Job(job_id=job_id, kind=kind)
    _jobs[job_id] = job
    return job


def get(job_id: str) -> Job | None:
    return _jobs.get(job_id)
