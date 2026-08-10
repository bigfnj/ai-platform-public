"""Refresh the model families: detect newer releases and update in place (Option C).

Two kinds of finding, both best-effort and read-only until an admin clicks Update/Re-pull:
  * **update** — a newer version of a model we track (same publisher + name stem, higher version),
    e.g. ``arctic-embed-m-v1.5 -> v2.0``. Adopting it fetches an ONNX build of the newer repo (the
    repo itself, else an ``onnx-community``/``Xenova`` mirror) and registers it as a NEW entry, so
    the old one stays for head-to-head comparison.
  * **broker** — Ollama-served models are versioned by a mutable tag pointing at a content digest,
    not a name, so we detect updates by comparing the LOCAL manifest digest to the REMOTE one from
    the Ollama registry (anonymous token dance): ``up_to_date`` / ``update`` (re-pull to apply), or
    ``repull`` when the remote can't be reached (offer a plain idempotent re-pull as a fallback).

Version parsing only trusts a ``vN[.N]`` token (or a standalone ``N.N``), so a size like ``300m``
is never mistaken for a version.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request

from ai_playground import broker, config, db
from ai_playground.bench import assets, registry

_REGISTRY = "https://registry.ollama.ai"
_V = re.compile(r"v(\d+(?:\.\d+)*)", re.I)          # v1.5 / v2 / V2.0
_VDOT = re.compile(r"(?<![\d.])(\d+\.\d+)(?![\d.])")  # standalone 1.5


def _norm_digest(d: str) -> str:
    return (d or "").split(":")[-1].strip().lower()


def _remote_manifest_digest(tag: str) -> str | None:
    """Digest of an Ollama tag's remote manifest via the registry v2 API (anonymous token dance).
    None on any failure (offline / private / unknown) so the caller falls back to a plain re-pull."""
    name, _, ref = tag.partition(":")
    ref = ref or "latest"
    repo = name if "/" in name else f"library/{name}"
    url = f"{_REGISTRY}/v2/{repo}/manifests/{ref}"
    accept = ("application/vnd.docker.distribution.manifest.v2+json, "
              "application/vnd.oci.image.manifest.v1+json")

    def _fetch(bearer: str | None):
        h = {"Accept": accept}
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20)

    try:
        try:
            resp = _fetch(None)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            fields = dict(re.findall(r'(\w+)="([^"]*)"', exc.headers.get("WWW-Authenticate", "")))
            realm = fields.get("realm")
            if not realm:
                return None
            tok_url = f"{realm}?service={fields.get('service', '')}&scope={fields.get('scope', '')}"
            tok = json.load(urllib.request.urlopen(tok_url, timeout=20))
            resp = _fetch(tok.get("token") or tok.get("access_token"))
        header_digest = resp.headers.get("Docker-Content-Digest")
        if header_digest:
            return _norm_digest(header_digest)
        return hashlib.sha256(resp.read()).hexdigest()  # digest = sha256 of the manifest bytes
    except Exception:  # noqa: BLE001
        return None


def _local_digests() -> dict[str, str]:
    """name -> local manifest digest, straight from Ollama's /api/tags (the broker's /v1/models
    drops the digest). Direct-Ollama like pull.py, since this is a maintenance check, not a query."""
    try:
        resp = urllib.request.urlopen(config.OLLAMA_URL + "/api/tags", timeout=15)
        data = json.load(resp)
        return {m["name"]: _norm_digest(m.get("digest", ""))
                for m in data.get("models", []) if m.get("name")}
    except Exception:  # noqa: BLE001
        return {}


def _split(name: str) -> tuple[str, tuple[int, ...]]:
    """(stem, version tuple) for a repo/name. () version when it carries no version token."""
    base = name.split("/")[-1]
    for rx in (_V, _VDOT):
        ms = list(rx.finditer(base))
        if ms:
            last = ms[-1]
            ver = tuple(int(x) for x in last.group(1).split("."))
            stem = base[:last.start()].rstrip("-_.v ").lower()
            return stem, ver
    return base.rstrip("-_.v ").lower(), ()


def _api():
    from huggingface_hub import HfApi
    return HfApi()


def _author_models(api, author: str, cache: dict) -> list[str]:
    if author not in cache:
        try:
            cache[author] = [getattr(h, "id", None) for h in api.list_models(author=author, limit=100)]
        except Exception:  # noqa: BLE001
            cache[author] = []
    return [r for r in cache[author] if r]


def _newest_sibling(api, repo: str, cache: dict) -> tuple[str | None, tuple[int, ...]]:
    """The highest-versioned repo from the same author sharing this repo's name stem."""
    author = repo.split("/")[0] if "/" in repo else ""
    stem, ver = _split(repo)
    best_repo, best_ver = None, ver
    for rid in _author_models(api, author, cache):
        st2, v2 = _split(rid)
        if st2 == stem and v2 and v2 > best_ver:
            best_repo, best_ver = rid, v2
    return best_repo, best_ver


def _onnx_files(api, repo: str) -> tuple[str | None, list[str] | None]:
    """(onnx_file, files-to-fetch) if the repo ships an ONNX build, preferring a quantized one."""
    try:
        files = api.list_repo_files(repo)
    except Exception:  # noqa: BLE001
        return None, None
    onnx = [f for f in files if f.endswith(".onnx")]
    if not onnx:
        return None, None
    pick = next((f for f in onnx if "quant" in f.lower() or "int8" in f.lower()), onnx[0])
    need = [pick]
    if pick + "_data" in files:
        need.append(pick + "_data")
    if "tokenizer.json" in files:
        need.append("tokenizer.json")
    return pick, need


def scan(con) -> dict:
    """Read-only survey. Returns {'updates': [...onnx...], 'broker': [...]}—no downloads."""
    specs = registry.all_specs(con)
    api = _api()
    cache: dict = {}
    updates = []
    for s in specs:
        if s.get("provider") != "onnx" or not s.get("hf_repo"):
            continue
        stem, ver = _split(s["hf_repo"])
        best_repo, best_ver = _newest_sibling(api, s["hf_repo"], cache)
        newer = best_repo is not None and best_ver > ver
        updates.append({
            "id": s["id"], "label": s["label"], "family": s.get("family"), "provider": "onnx",
            "current": ".".join(map(str, ver)) or "—",
            "latest": ".".join(map(str, best_ver)) if newer else (".".join(map(str, ver)) or "—"),
            "status": "newer" if newer else "up_to_date",
            "latest_repo": best_repo if newer else None,
        })
    # broker: compare local manifest digest to the remote registry digest to detect a same-tag update.
    local_digests = _local_digests()  # name -> digest (from Ollama /api/tags)
    broker_out = []
    for s in specs:
        if s.get("provider") != "broker" or not s.get("broker_model"):
            continue
        tag = s["broker_model"]
        base = tag.split(":")[0]
        local_digest = (local_digests.get(tag) or local_digests.get(f"{base}:latest")
                        or next((v for k, v in local_digests.items() if k.split(":")[0] == base), None))
        lm = local_digest is not None
        remote_digest = _remote_manifest_digest(tag) if local_digest else None
        if local_digest and remote_digest:
            status = "up_to_date" if _norm_digest(local_digest) == remote_digest else "update"
        else:
            status = "repull"   # couldn't determine → offer a plain idempotent re-pull
        broker_out.append({
            "id": s["id"], "label": s["label"], "family": s.get("family"), "provider": "broker",
            "current": tag, "status": status, "installed": lm,
        })
    return {"updates": updates, "broker": broker_out}


def repull_broker(con) -> dict:
    """Re-pull every broker (Ollama) model in the registry. Idempotent — Ollama only downloads a
    tag whose remote digest changed, so this is safe to run on a schedule. This is the action the
    platform scheduler fires for the ai-playground 'model update' task."""
    from ai_playground.bench import pull  # local: avoid import cycle at module load
    results = []
    for s in registry.all_specs(con):
        if s.get("provider") != "broker" or not s.get("broker_model"):
            continue
        tag = s["broker_model"]
        try:
            r = pull.pull(tag)
            results.append({"id": s["id"], "model": tag, "ok": True, "status": r.get("status")})
        except Exception as exc:  # noqa: BLE001 — one bad pull must not stop the rest
            results.append({"id": s["id"], "model": tag, "ok": False, "error": str(exc)[:160]})
    return {"repulled": results, "count": len(results)}


def adopt(con, model_id: str, owner) -> dict:
    """Fetch + register the newest version of an onnx model as a NEW entry (keeps the old one)."""
    spec = registry.get_spec(con, model_id)
    if spec is None or spec.get("provider") != "onnx" or not spec.get("hf_repo"):
        raise ValueError("not an onnx model with a repo")
    api = _api()
    stem, ver = _split(spec["hf_repo"])
    best_repo, best_ver = _newest_sibling(api, spec["hf_repo"], {})
    if not best_repo or best_ver <= ver:
        raise ValueError("no newer version found")
    onnx_file, files = _onnx_files(api, best_repo)
    if not onnx_file:  # newer official repo has no ONNX — try common mirrors
        vs = ".".join(map(str, best_ver))
        for mirror in (f"onnx-community/{stem}-v{vs}-ONNX", f"Xenova/{stem}-v{vs}"):
            onnx_file, files = _onnx_files(api, mirror)
            if onnx_file:
                best_repo = mirror
                break
    if not onnx_file:
        raise ValueError(f"found v{'.'.join(map(str, best_ver))} but no ONNX build to fetch — add it manually")
    vs = ".".join(map(str, best_ver))
    new_id = re.sub(r"[^a-z0-9.-]+", "-", f"{stem}-v{vs}").strip("-")
    new = dict(spec)
    new.update({"id": new_id, "hf_repo": best_repo, "onnx_file": onnx_file, "files": files,
                "label": re.sub(r"v[\d.]+", f"v{vs}", spec["label"]) if re.search(r"v[\d.]+", spec["label"])
                         else f"{spec['label']} v{vs}"})
    db.upsert_bench_model(con, new, owner)
    summary = assets.fetch(new)
    return {"id": new_id, "label": new["label"], "hf_repo": best_repo, "footprint_mb": summary.get("footprint_mb")}
