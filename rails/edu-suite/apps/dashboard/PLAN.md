# edu-suite dashboard — plan

A local web dashboard for a non-technical special-education instructor. Pick a
**workflow**, **upload documents**, watch a **serialized job queue** with
**stage-level live status** (including model load/unload events + validation),
then **download a self-contained ZIP**. Outputs live in an ID-stamped per-job
**library** outside the repo, indexed in SQLite so it stays searchable at scale.

## Core abstractions (in `edu-media-core`)

- **`models.ModelManager`** — owns the GPU. `ensure(key)` unloads the current
  heavy model if different, loads the requested one, emitting
  `unloading/loading/loaded` events. `validate(key)` asserts the right model is
  resident (and only that one). Enforces one heavy model at a time on the 24GB
  4090. Handles: Ollama (via API + `/api/ps`), in-process XTTS/SDXL.
- **`jobs.JobContext` / `Step` / `run_workflow`** — a job is an ordered list of
  `Step`s; each declares a `required_model`. The runner ensures+validates that
  model before running the step, times each stage, streams events
  (`stage_started/progress/finished`, `model`, `job_finished/failed`), and on
  failure unloads models to reclaim VRAM.

## Dashboard app (`apps/dashboard`)

- FastAPI backend + a light browser UI (server-rendered + SSE for live status).
- **Job queue**: single background worker, serialized (matches the VRAM ceiling),
  state persisted in SQLite (`library.db`).
- **Workflow registry**: each workflow = a factory returning `Step`s for a job.
- Endpoints: `POST /jobs` (multi-file upload + create), `GET /jobs` (list/search),
  `GET /jobs/{id}` (detail), `GET /jobs/{id}/events` (SSE), `GET /jobs/{id}/download`
  (zip), `GET /` (UI).
- **Library** outside the repo (default `D:\edu-suite-library`):
  `<workflow>/<date>__<name-slug>__<shortid>/` with `input/`, `work/` (prunable),
  `output/`, `output.zip`, `job.json`.

## Workflows

- **Just Translate** — extract text → (qwen) translate → bundle. (Slice 1)
- **CVC Words** — (qwen) translate → (SDXL) images → (XTTS) audio → render → bundle. (Slice 2)
- **TeachTown Builder** — upload worksheets → (qwen) draft unit → review/edit → build interactive site, optional (qwen+XTTS) enrich → bundle. (Slice 3)

## Delivery slices

0. Spine, GPU-free: ModelManager + jobs primitives (stub-tested); dashboard
   skeleton (FastAPI, SQLite queue, library, a noop workflow); live-server smoke.
1. Just Translate end to end.
2. CVC Words (multi-model load/unload chain).
3. TeachTown Builder (draft from worksheets → review/edit → build; + enrich checkbox).
4. Library polish (search/filter/rename/delete, prune intermediates, re-download).
5. Hardening (stage-failure VRAM reclaim, queue guard, tests).

## Defaults

- Batch upload = one job → one zip (per-file items as sub-stages).
- Library at `D:\edu-suite-library` (override with `EDU_LIBRARY_DIR`).
- Instructor names each job (auto-default available).
