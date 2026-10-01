"""Staged job execution with a live event stream.

A job is an ordered list of ``Step``s. ``run_workflow`` times each stage and emits
events a UI can subscribe to; on failure it stops the run with the failing stage
named.

There is no ``required_model`` on a ``Step``, and no ``ModelManager``: the platform
broker owns model residency, so a step just calls the broker and the broker
loads/evicts. ``rails/iep/src/iep_rail/jobs.py`` is the same shape, for the same
reason. ``StageResult.required_model`` deliberately survives the removal, see the
note on that field.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Callable


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
    # WIRE-VISIBLE, do not remove: `to_dict()` feeds the "stages" list in each job's
    # job.json (dashboard runner.py -> library.write_job_meta), so every bundle
    # already on disk carries this key. Nothing sets it any more (the broker owns
    # residency) and it is always None on new jobs, but dropping the field would
    # change an artifact existing jobs still have.
    required_model: str | None = None
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

    def progress(self, message: str) -> None:
        self.emit("stage_progress", stage=self._current_stage, message=message)


def run_workflow(steps: list[Step], ctx: JobContext, *,
                 emit_finished: bool = True) -> None:
    """Run steps in order. Raises ``JobFailed`` on the first failing stage.

    Pass ``emit_finished=False`` when a caller still has post-step work (e.g. the
    dashboard runner bundles the output) and wants to emit ``job_finished`` itself
    only once the job is truly done.

    ``emit_finished`` is keyword-only on purpose: this function used to take a
    ``manager`` third positional, and a stale caller passing one would otherwise
    have bound a truthy object to ``emit_finished`` and run on in silence.
    """
    ctx.emit("job_started")
    try:
        for step in steps:
            sr = StageResult(step.key, step.label,
                             status="running", started_at=time.time())
            ctx.stages.append(sr)
            ctx._current_stage = step.key
            ctx.emit("stage_started", stage=step.key, message=step.label)
            try:
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
                ctx.emit("job_failed", stage=step.key, message=str(e))
                raise JobFailed(f"stage {step.key!r} failed: {e}") from e
    finally:
        ctx._current_stage = None
    if emit_finished:
        ctx.emit("job_finished")
