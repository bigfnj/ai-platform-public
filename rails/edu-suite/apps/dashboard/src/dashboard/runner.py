"""Execute one job (in-process). Invoked inside a per-job subprocess by the queue
so that all GPU memory is reclaimed by the OS when the process exits — the only
reliable way to hand a clean GPU from a torch job (XTTS/SDXL) to an Ollama job.
"""
from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path

from edu_media_core.jobs import Event, JobContext, JobFailed, run_workflow
from edu_media_core.models import ModelManager

from . import library, workflows
from .store import Store


def _preflight(ctx: JobContext, steps) -> None:
    """Fail fast with a clear message if a required backend is missing."""
    needs = {s.required_model for s in steps if s.required_model}
    if "qwen" in needs:
        import requests
        model = os.getenv("EDU_LLM_MODEL", "mistral-small3*:24b")
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        try:
            tags = requests.get(f"{host}/api/tags", timeout=5).json().get("models", [])
        except Exception:
            msg = f"Ollama is not reachable at {host}. Start Ollama and try again."
            ctx.emit("job_failed", message=msg)
            raise JobFailed(msg)
        names = [m.get("name", "") for m in tags]
        # A wildcard (e.g. "mistral-small3*:24b") is resolved by the broker, which fails
        # loudly there if nothing matches — so don't substring-check a glob here.
        is_glob = any(c in model for c in "*?[")
        if not is_glob and not any(model in n or n in model for n in names):
            msg = f"Ollama model {model!r} is not installed. Run:  ollama pull {model}"
            ctx.emit("job_failed", message=msg)
            raise JobFailed(msg)


def execute_job(store: Store, job: dict) -> None:
    job_id = job["id"]
    job_dir = Path(job["dir"])
    input_dir = job_dir / "input"
    store.set_status(job_id, "running")
    state = {
        "name": job["name"],
        "workflow": job["workflow"],
        # rglob (not glob) so uploaded folder structure — e.g. a unit's Week N
        # subfolders — is seen; TeachTown Builder derives week/unit from it.
        "input_files": sorted(p for p in input_dir.rglob("*") if p.is_file()),
        "input_dir": input_dir,
        "work_dir": job_dir / "work",
        "output_dir": job_dir / "output",
        "params": json.loads(job.get("params") or "{}"),
    }
    ctx = JobContext(job_id, emit=lambda e: store.add_event(job_id, e), state=state)
    # All workflows route model work through the platform broker now, so the runner
    # holds no local model handles; the broker owns GPU residency across all apps.
    manager = ModelManager()
    try:
        steps = workflows.get(job["workflow"]).build(ctx)
        _preflight(ctx, steps)
        # Don't let run_workflow announce "job_finished" — it isn't finished until we've
        # packaged the bundle below and flipped the status. We emit it ourselves at the end.
        run_workflow(steps, ctx, manager, emit_finished=False)
        ctx.emit("stage_progress", message="packaging the download…")
        # Name the bundle's root HTML after the job (same base as the download zip), so a
        # folder of bundles isn't all "index.html". teachtown_builder is exempt: its index.html
        # is a served app shell that self-loads via fetch and defaults to index.html.
        if job["workflow"] != "teachtown_builder":
            idx = job_dir / "output" / "index.html"
            if idx.exists():
                base = library.bundle_basename(job["name"], job.get("created_at"))
                idx.rename(idx.with_name(f"{base}.html"))
        zip_path = library.bundle_zip(job_dir)
        library.write_job_meta(job_dir, {
            "id": job_id, "name": job["name"], "workflow": job["workflow"],
            "params": state["params"],
            "inputs": [p.relative_to(input_dir).as_posix() for p in state["input_files"]],
            "outputs": [p.name for p in sorted((job_dir / "output").rglob("*")) if p.is_file()],
            "bundle": zip_path.name,
            "stages": [s.to_dict() for s in ctx.stages],
        })
        store.set_status(job_id, "done")
        ctx.emit("job_finished")  # only now — bundle written + status flipped
    except JobFailed as e:
        # JobFailed already carries the failing stage (run_workflow / _preflight).
        store.set_status(job_id, "failed", error=str(e))
    except Exception as e:
        # Unexpected error outside a stage (bundling, meta, etc.): name where it
        # stopped and the exception type so there's a place to start troubleshooting.
        stage = next((s.label for s in reversed(ctx.stages)
                      if s.status in ("running", "failed")), None)
        msg = f"{stage + ': ' if stage else ''}{type(e).__name__}: {e}"
        store.add_event(job_id, Event(kind="job_failed", ts=time.time(), message=msg))
        store.set_status(job_id, "failed", error=msg)
        traceback.print_exc()
    finally:
        try:
            manager.unload_all()
        except Exception:
            pass
