"""ONNX model asset management.

An onnx-provider model is a set of files (the quantized graph, any external ``*_data`` blob,
and ``tokenizer.json``) pulled from a Hugging Face repo into the models volume
(``/srv/var/models/<id>/``). Fetching is self-service and idempotent; presence and footprint
back the "available? / fetch" state in the registry and UI. Nothing here needs the GPU.
"""
from __future__ import annotations

import os
from pathlib import Path

from ai_playground import config


def model_dir(model_id: str) -> Path:
    """The on-disk dir for a model's assets, confined to MODELS_DIR. Rejects any id that escapes
    the root (e.g. ``..`` or an absolute path) — otherwise a crafted id would let ``remove`` /
    ``fetch`` touch arbitrary paths (a purge of ``..`` would rmtree the whole data volume)."""
    base = config.MODELS_DIR.resolve()
    p = (base / model_id).resolve()
    if p == base or not p.is_relative_to(base):
        raise ValueError(f"invalid model id '{model_id}'")
    return p


def _required_files(spec: dict) -> list[str]:
    files = list(spec.get("files") or [])
    if spec.get("onnx_file") and spec["onnx_file"] not in files:
        files.append(spec["onnx_file"])
    if "tokenizer.json" not in files:
        files.append("tokenizer.json")
    return files


def present(spec: dict) -> bool:
    d = model_dir(spec["id"])
    return all((d / f).exists() and (d / f).stat().st_size > 0 for f in _required_files(spec))


def footprint_mb(spec: dict) -> float:
    d = model_dir(spec["id"])
    total = 0
    if d.exists():
        for root, _, files in os.walk(d):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
    return round(total / 1e6, 1)


def fetch(spec: dict) -> dict:
    """Download every required file for an onnx model into its dir. Returns a summary.

    Raises RuntimeError with a readable reason on any failure (gated repo, no network, …)."""
    from huggingface_hub import hf_hub_download  # lazy: only the fetch path needs it

    repo = spec.get("hf_repo")
    if not repo:
        raise RuntimeError(f"model '{spec['id']}' has no hf_repo to fetch from")
    dst = model_dir(spec["id"])
    dst.mkdir(parents=True, exist_ok=True)
    got: list[dict] = []
    try:
        for f in _required_files(spec):
            path = hf_hub_download(repo, f, local_dir=str(dst))
            got.append({"file": f, "mb": round(os.path.getsize(path) / 1e6, 1)})
    except Exception as exc:  # noqa: BLE001 — surface a clean message to the API/UI
        raise RuntimeError(f"fetch of {repo} failed: {type(exc).__name__}: {exc}") from exc
    return {"id": spec["id"], "repo": repo, "files": got, "footprint_mb": footprint_mb(spec)}


def remove(model_id: str) -> None:
    import shutil
    d = model_dir(model_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
