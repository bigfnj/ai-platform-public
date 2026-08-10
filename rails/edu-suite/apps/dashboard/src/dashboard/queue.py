"""Single-worker, serialized job queue. Runs one job at a time (matching the
GPU's one-heavy-model limit), each in its own subprocess so all VRAM is
reclaimed by the OS between jobs.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from edu_media_core.jobs import Event

from .store import Store

# apps/dashboard/run_job.py — the per-job subprocess entrypoint.
_RUN_JOB = Path(__file__).resolve().parents[2] / "run_job.py"


class JobQueue:
    def __init__(self, store: Store):
        self.store = store
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="job-worker", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.next_queued()
            if job is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        job_id = job["id"]
        try:
            proc = subprocess.run(
                [sys.executable, str(_RUN_JOB), job_id, self.store.db_path],
                cwd=str(_RUN_JOB.parent),
            )
            # Reconcile: if the subprocess died without setting a terminal status.
            current = self.store.get_job(job_id)
            if current and current["status"] not in ("done", "failed"):
                msg = f"worker process exited unexpectedly (code {proc.returncode})"
                self.store.add_event(job_id, Event(kind="job_failed", ts=time.time(), message=msg))
                self.store.set_status(job_id, "failed", error=msg)
        except Exception as e:
            self.store.set_status(job_id, "failed", error=str(e))
