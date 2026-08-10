"""Staged job execution with a live event stream.

A job is an ordered list of ``Step``s; each declares the ``required_model`` it
needs. ``run_workflow`` ensures+validates that model before the step runs, times
each stage, and emits events a UI can subscribe to. On failure it unloads models
to reclaim VRAM and stops.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from .models import ModelManager


@dataclass
class Event:
    kind: str            # job_started | stage_started | stage_progress | stage_finished | model | job_finished | job_failed
    ts: float
    stage: str | None = None
    model: str | None = None
    status: str | None = None
    message: str = ""
    elapsed: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StageResult:
    key: str
    label: str
    required_model: str | None
    status: str = "pending"        # pending | running | done | failed
    started_at: float | None = None
    ended_at: float | None = None
    message: str = ""

    @property
    def elapsed(self) -> float | None:
        if self.started_at is not None and self.ended_at is not None:
            return self.ended_at - self.started_at
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["elapsed"] = self.elapsed
        return d


@dataclass
class Step:
    key: str
    label: str
    run: Callable[["JobContext"], None]
    required_model: str | None = None


class JobFailed(RuntimeError):
    """A stage raised; the job is aborted."""


class JobContext:
    """Shared state + event sink for one job run.

    ``state`` is a free dict steps use to pass data along (paths, extracted text,
    output files, etc.). ``emit`` receives every ``Event``.
    """

    def __init__(self, job_id: str,
                 emit: Callable[[Event], None] | None = None,
                 state: dict[str, Any] | None = None):
        self.job_id = job_id
        self._emit = emit or (lambda e: None)
        self.state: dict[str, Any] = state if state is not None else {}
        self.stages: list[StageResult] = []
        self._current_stage: str | None = None

    def emit(self, kind: str, **kw) -> None:
        self._emit(Event(kind=kind, ts=time.time(), **kw))

    def model_emit(self, status: str, name: str, message: str = "") -> None:
        """Adapter passed to ModelManager so model load/unload flows into the stream."""
        self.emit("model", model=name, status=status, message=message)

    def progress(self, message: str) -> None:
        self.emit("stage_progress", stage=self._current_stage, message=message)


def run_workflow(steps: list[Step], ctx: JobContext, manager: ModelManager,
                 emit_finished: bool = True) -> None:
    """Run steps in order. Wires ``manager`` to emit into ``ctx`` and enforces the
    declared required-model for each stage. Raises ``JobFailed`` on the first
    failing stage (after unloading models). Pass ``emit_finished=False`` when a
    caller still has post-step work (e.g. the dashboard runner bundles the output)
    and wants to emit ``job_finished`` itself only once the job is truly done."""
    manager.set_emit(ctx.model_emit)
    ctx.emit("job_started")
    try:
        for step in steps:
            sr = StageResult(step.key, step.label, step.required_model,
                             status="running", started_at=time.time())
            ctx.stages.append(sr)
            ctx._current_stage = step.key
            ctx.emit("stage_started", stage=step.key, model=step.required_model,
                     message=step.label)
            try:
                if step.required_model:
                    manager.ensure(step.required_model)
                    manager.validate(step.required_model)
                step.run(ctx)
                sr.status = "done"
                sr.ended_at = time.time()
                ctx.emit("stage_finished", stage=step.key, status="done",
                         elapsed=sr.elapsed, message=sr.message)
            except Exception as e:
                sr.status = "failed"
                sr.ended_at = time.time()
                sr.message = str(e)
                ctx.emit("stage_finished", stage=step.key, status="failed",
                         elapsed=sr.elapsed, message=str(e))
                try:
                    manager.unload_all()
                except Exception:
                    pass
                ctx.emit("job_failed", stage=step.key, message=str(e))
                raise JobFailed(f"stage {step.key!r} failed: {e}") from e
    finally:
        ctx._current_stage = None
    if emit_finished:
        ctx.emit("job_finished")
