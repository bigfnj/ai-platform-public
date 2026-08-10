"""TeachTown Builder: upload a unit's worksheets -> qwen drafts an interactive
unit -> (optionally review/edit) -> build the interactive site + optional EN/ES
translation + audio -> self-contained bundle.

Params: {"name": str, "review": bool, "enrich": bool, "audio": bool}.
- review=true: stop after drafting; the draft unit.json is the deliverable to
  edit, then rebuild (the edit form re-submits with unit.json + the worksheets).
- If an uploaded file is named unit.json, it's used directly (the edit/rebuild
  and finalize path) and drafting is skipped.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from edu_media_core import pdf as core_pdf
from edu_media_core.jobs import JobContext, Step

from . import Workflow, register

_TT_DIR = Path(__file__).resolve().parents[4] / "teachtown"
_TT_HTML = _TT_DIR / "interactive-html"
if str(_TT_DIR) not in sys.path:
    sys.path.insert(0, str(_TT_DIR))
import builder as tt_builder  # noqa: E402
import enrich as tt_enrich  # noqa: E402

# Bring the interactive shell but not the sample units' data or heavy assets.
_IGNORE = shutil.ignore_patterns("enrichment.json", "tools", "__pycache__",
                                 "node_modules", "worksheets", "vendor", "data.json")


def _unit_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") or "unit"


def _derive_key(unit: dict, name: str) -> str:
    """Keep the key stable across edits: reuse the key baked into mission paths
    (<key>/<file>) so worksheet mappings survive a label change."""
    for m in unit.get("missions", []):
        p = m[6] if len(m) > 6 else ""
        if "/" in p:
            return p.split("/")[0]
    return _unit_key(name)


def _provided_unit(ctx: JobContext) -> Path | None:
    for f in ctx.state["input_files"]:
        if f.name.lower() == "unit.json":
            return f
    return None


def _draft(ctx: JobContext) -> None:
    input_root = ctx.state.get("input_dir")
    all_pdfs = [f for f in ctx.state["input_files"] if f.suffix.lower() == ".pdf"]
    # Only NON-lesson-plan PDFs become interactive worksheets (imaged in _build).
    # Lesson plans ("* Lesson Plan.pdf") are the vocabulary source and are not imaged.
    ctx.state["worksheet_files"] = [f for f in all_pdfs if not tt_builder.is_lesson_plan(f.name)]
    # Default the unit name to the uploaded master folder ("Great Expectations")
    # when the teacher didn't type one (job names auto-fill as teachtown_builder-<id>).
    typed = (ctx.state["params"].get("name") or "").strip()
    auto = ctx.state["name"].startswith("teachtown_builder-")
    name = (typed or (None if not auto else tt_builder.master_folder(all_pdfs, input_root))
            or ctx.state["name"] or "New Unit").strip()
    provided = _provided_unit(ctx)
    if provided:
        unit = json.loads(provided.read_text(encoding="utf-8"))
        key = _derive_key(unit, name)
        ctx.stages[-1].message = "using edited unit.json"
    else:
        key = _unit_key(name)
        unit = tt_builder.draft_unit(all_pdfs, key, name,
                                     input_root=input_root,
                                     guidance=(ctx.state["params"].get("guidance") or ""),
                                     progress=ctx.progress)
        n_vocab = sum(len(w["v"]) for w in unit["weekInfo"].values())
        ctx.stages[-1].message = f"{len(unit['missions'])} missions, {n_vocab} vocab words"
    ctx.state["unit_key"] = key
    ctx.state["unit"] = unit
    (ctx.state["output_dir"] / "unit.json").write_text(
        json.dumps(unit, ensure_ascii=False, indent=2), encoding="utf-8")


def _build(ctx: JobContext) -> None:
    out = ctx.state["output_dir"]
    key = ctx.state["unit_key"]
    shutil.copytree(_TT_HTML, out, dirs_exist_ok=True, ignore=_IGNORE)
    data = tt_builder.full_data(key, ctx.state["unit"])
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.state["data"] = data

    # Render the uploaded worksheets to JPGs (offline) + manifest keyed to how the
    # site references them: public/worksheets/intake/<key>/<filename>.
    manifest: dict[str, list[str]] = {}
    input_root = ctx.state.get("input_dir")
    for f in ctx.state["worksheet_files"]:
        # Same key the draft used for the mission ref, so missions resolve to their
        # worksheet and same-named files in different week folders don't collide.
        ref = tt_builder.rel_worksheet_name(f, input_root)
        h = hashlib.sha1(f"{key}/{ref}".encode("utf-8")).hexdigest()[:12]
        pages = core_pdf.render_pdf_pages(f, out / "public" / "worksheets" / "img" / h)
        manifest[f"public/worksheets/intake/{key}/{ref}"] = [
            f"public/worksheets/img/{h}/{p.name}" for p in pages]
    (out / "worksheets.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.stages[-1].message = f"unit '{key}' built, {len(manifest)} worksheet(s) imaged"


def _enrich(ctx: JobContext) -> None:
    ctx.state["enr"] = tt_enrich.translate_data(
        ctx.state["data"], ctx.state["unit_key"], progress=ctx.progress)
    ctx.stages[-1].message = f"{len(ctx.state['enr']['vocab'])} vocab translated"


def _audio(ctx: JobContext) -> None:
    tt_enrich.add_audio(ctx.state["data"], ctx.state["enr"],
                        ctx.state["output_dir"] / "public" / "audio",
                        ctx.state["unit_key"], progress=ctx.progress)
    ctx.stages[-1].message = "audio synthesized"


def _write_enr(ctx: JobContext) -> None:
    (ctx.state["output_dir"] / "enrichment.json").write_text(
        json.dumps(ctx.state["enr"], ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.stages[-1].message = "enrichment.json"


def _build_steps(ctx: JobContext) -> list[Step]:
    params = ctx.state.get("params", {})
    review = bool(params.get("review"))
    enrich = bool(params.get("enrich"))
    audio = bool(params.get("audio", enrich))
    editing = _provided_unit(ctx) is not None
    # Model work (drafting/translate/audio) goes through the broker (broker_media),
    # which owns residency, so no required_model on the steps.
    steps = [Step("draft", "Draft unit from worksheets", _draft)]
    if review and not editing:
        return steps  # stop for review; the draft unit.json is the deliverable
    steps.append(Step("build", "Build interactive unit", _build))
    if enrich:
        steps.append(Step("translate", "Translate to Spanish", _enrich))
        if audio:
            steps.append(Step("audio", "Generate audio", _audio))
        steps.append(Step("write", "Write enrichment", _write_enr))
    return steps


register(Workflow(
    key="teachtown_builder",
    label="TeachTown Builder",
    description="Upload a unit's worksheets → AI drafts an interactive lesson → "
                "review/edit → build (optionally with EN/ES translation + audio).",
    build=_build_steps,
))
