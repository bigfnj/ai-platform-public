"""Workflow registry. A workflow builds the ordered Steps for a job from the
JobContext (whose ``state`` the queue pre-populates with input_files, work_dir,
output_dir, params, name).

Slice 0 ships only ``echo`` (GPU-free) to exercise the spine end to end.
Just-Translate / CVC / TeachTown land in later slices.
"""
from __future__ import annotations

import html
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from edu_media_core.jobs import JobContext, Step


@dataclass
class Workflow:
    key: str
    label: str
    description: str
    build: Callable[[JobContext], list[Step]]


REGISTRY: dict[str, Workflow] = {}


def register(wf: Workflow) -> None:
    REGISTRY[wf.key] = wf


def get(key: str) -> Workflow:
    if key not in REGISTRY:
        raise KeyError(f"unknown workflow {key!r}; known: {sorted(REGISTRY)}")
    return REGISTRY[key]


def all_workflows() -> list[Workflow]:
    return list(REGISTRY.values())


# --- echo (spine test, no models) -------------------------------------------

def _echo_ingest(ctx: JobContext) -> None:
    inputs: list[Path] = ctx.state["input_files"]
    out: Path = ctx.state["output_dir"]
    ctx.state["copied"] = []
    for i, f in enumerate(inputs, 1):
        ctx.progress(f"[{i}/{len(inputs)}] {f.name}")
        shutil.copy(f, out / f.name)
        ctx.state["copied"].append(f.name)
    ctx.stages[-1].message = f"{len(inputs)} file(s)"


def _echo_render(ctx: JobContext) -> None:
    out: Path = ctx.state["output_dir"]
    files = ctx.state.get("copied", [])
    items = "\n".join(f"<li>{html.escape(n)}</li>" for n in files)
    (out / "index.html").write_text(
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(ctx.state['name'])}</title>"
        f"<h1>{html.escape(ctx.state['name'])}</h1>"
        f"<p>Echo workflow — {len(files)} uploaded file(s):</p><ul>{items}</ul>",
        encoding="utf-8",
    )
    ctx.stages[-1].message = "index.html"


def _build_echo(ctx: JobContext) -> list[Step]:
    return [
        Step("ingest", "Read uploaded files", _echo_ingest),
        Step("render", "Build output page", _echo_render),
    ]


register(Workflow(
    key="echo",
    label="Echo (test)",
    description="Copies uploaded files into a bundle. No models — proves the pipeline.",
    build=_build_echo,
))


# --- register real workflows (imported for side effects) --------------------
from . import just_translate  # noqa: E402,F401
from . import cvc  # noqa: E402,F401
from . import teachtown_builder  # noqa: E402,F401
from . import iep_present_levels  # noqa: E402,F401
