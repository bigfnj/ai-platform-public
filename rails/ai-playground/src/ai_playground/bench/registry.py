"""The model registry — the catalog of embedders the lab can bench.

A model spec is a plain dict declaring its provider (``broker`` | ``onnx``), how to reach it
(``broker_model`` or ``hf_repo`` + files), its prompting templates, native dim and any
Matryoshka dims. Seed models are built in below; users/admins add more at runtime (persisted
in SQLite), so "bench a new model as it ships" is one registry entry — no code change.

Adding a model:
  * **broker**: ``ollama pull <tag>`` on the broker box (or the UI's pull button), then a spec
    with ``provider: "broker"`` and ``broker_model: "<tag>"``.
  * **onnx**: a spec with ``provider: "onnx"``, an ``hf_repo`` and the file list; the UI's Fetch
    button (or ``assets.fetch``) pulls the int8 graph + tokenizer into the models volume.
"""
from __future__ import annotations

from ai_playground import broker, db
from ai_playground.bench import assets
from ai_playground.bench.providers import BrokerEmbedder, OnnxEmbedder, RerankOnnx

# The BGE v1.5 retrieval instruction, used by arctic's trained prompt too.
_BGE_PREFIX = "Represent this sentence for searching relevant passages: {text}"

SEED_MODELS: list[dict] = [
    # ---- broker / Ollama (GPU-served through the platform broker) --------------------------
    {
        "id": "bge-m3", "label": "BGE-M3 · platform @embed", "provider": "broker",
        "broker_model": "bge-m3", "family": "BAAI", "params": "568M",
        "native_dim": 1024, "mrl_dims": [], "ctx": 8192,
        "query_template": "", "doc_template": "",
        "about": "A multilingual all-rounder that can read whole pages at once. The dependable default.",
        "notes": "The platform's default retrieval embedder. Multilingual, 8192-token context.",
    },
    {
        "id": "embeddinggemma", "label": "EmbeddingGemma-300m · broker/GPU", "provider": "broker",
        "broker_model": "embeddinggemma", "family": "Google", "params": "300M",
        "native_dim": 768, "mrl_dims": [768, 512, 256, 128], "ctx": 2048,
        "query_template": "task: search result | query: {text}",
        "doc_template": "title: none | text: {text}",
        "about": "Google's model built for phones and laptops: near top-tier quality at a fraction of the size.",
        "notes": "On-device SOTA under 500M. Same weights as the ONNX build, GPU-served here.",
    },
    {
        "id": "qwen3-embedding-0.6b", "label": "Qwen3-Embedding-0.6B · broker/GPU",
        "provider": "broker", "broker_model": "qwen3-embedding:0.6b", "family": "Alibaba",
        "params": "600M", "native_dim": 1024, "mrl_dims": [], "ctx": 32768,
        "query_template": "Instruct: Given a search query, retrieve relevant passages\nQuery: {text}",
        "doc_template": "",
        "about": "You can tell it what you're searching for, and it punches well above its size on quality.",
        "notes": "Instruction-tuned; top MTEB for its size. 32k context.",
    },
    # ---- onnx / CPU (int8 graph beside the exe, fetched from Hugging Face) ------------------
    {
        "id": "bge-small-en-v1.5", "label": "bge-small-en-v1.5 · ONNX int8 CPU",
        "provider": "onnx", "hf_repo": "Xenova/bge-small-en-v1.5",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
        "pooling": "cls", "family": "BAAI", "params": "33M",
        "native_dim": 384, "mrl_dims": [], "ctx": 512,
        "query_template": "", "doc_template": "",
        "about": "The featherweight we already ship in offline apps. Near-instant, with a tiny footprint.",
        "notes": "Today's recall.py / desktopPet embedder. WordPiece, CLS-pool, no server.",
    },
    {
        "id": "bge-base-en-v1.5", "label": "bge-base-en-v1.5 · ONNX int8 CPU",
        "provider": "onnx", "hf_repo": "Xenova/bge-base-en-v1.5",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
        "pooling": "cls", "family": "BAAI", "params": "109M",
        "native_dim": 768, "mrl_dims": [], "ctx": 512,
        "query_template": "", "doc_template": "",
        "about": "bge-small's bigger sibling: a step up in accuracy for a modest increase in size.",
        "notes": "Drop-in bigger sibling of bge-small — identical WordPiece vocab, only the dim changes.",
    },
    {
        "id": "arctic-embed-m-v1.5", "label": "snowflake-arctic-embed-m-v1.5 · ONNX int8 CPU",
        "provider": "onnx", "hf_repo": "Snowflake/snowflake-arctic-embed-m-v1.5",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
        "pooling": "cls", "family": "Snowflake", "params": "109M",
        "native_dim": 768, "mrl_dims": [768, 256], "ctx": 512,
        "query_template": _BGE_PREFIX, "doc_template": "",
        "about": "Snowflake's search specialist, tuned hard for retrieval, and it shrinks to save space.",
        "notes": "WordPiece drop-in + Matryoshka (truncate to 256). Trained to use the query prefix.",
    },
    {
        "id": "embeddinggemma-onnx", "label": "EmbeddingGemma-300m · ONNX int8 CPU",
        "provider": "onnx", "hf_repo": "onnx-community/embeddinggemma-300m-ONNX",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "onnx/model_quantized.onnx_data", "tokenizer.json"],
        "pooling": "graph", "pad_len": 512, "family": "Google", "params": "300M",
        "native_dim": 768, "mrl_dims": [768, 512, 256, 128], "ctx": 2048,
        "query_template": "task: search result | query: {text}",
        "doc_template": "title: none | text: {text}",
        "about": "The same Google model, packaged to run on the CPU right next to an app, fully offline.",
        "notes": "The beside-the-exe path: SentencePiece + baked sentence_embedding head. Padded to 512.",
    },
    # ---- rerankers / CPU cross-encoders (second-stage relevance judges) ---------------------
    # A reranker isn't an embedder: it reads (query, doc) together and scores the match. In the
    # Lab it runs as an optional two-stage step (embed → top-N by cosine → rerank those N), so
    # `kind: "reranker"` keeps it out of the embedder columns and in its own selector.
    {
        "id": "bge-reranker-base", "label": "bge-reranker-base · ONNX int8 CPU", "kind": "reranker",
        "provider": "onnx", "hf_repo": "Xenova/bge-reranker-base",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
        "max_len": 512, "family": "BAAI", "params": "278M",
        "native_dim": None, "mrl_dims": [], "ctx": 512,
        "query_template": "", "doc_template": "",
        "about": "A second-pass judge: it reads the question and a candidate together and scores how well they match, sharpening the top results a first-pass embedder returns.",
        "notes": "Cross-encoder reranker. Two-stage: embed → top-N by cosine → rerank those N by this model's joint relevance score. CPU int8, no server.",
    },
    {
        "id": "ms-marco-minilm-l6", "label": "ms-marco-MiniLM-L6 · ONNX int8 CPU", "kind": "reranker",
        "provider": "onnx", "hf_repo": "Xenova/ms-marco-MiniLM-L-6-v2",
        "onnx_file": "onnx/model_quantized.onnx",
        "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
        "max_len": 512, "family": "Microsoft", "params": "23M",
        "native_dim": None, "mrl_dims": [], "ctx": 512,
        "query_template": "", "doc_template": "",
        "about": "A tiny, fast reranker trained on web-search relevance — a lightweight way to sharpen the top results when bge-reranker is heavier than you need.",
        "notes": "Cross-encoder reranker (MS MARCO). Much smaller than bge-reranker; the fast/cheap end of two-stage retrieval.",
    },
]

# Fields safe to expose to the frontend (everything here is non-secret).
_PUBLIC = ("id", "label", "provider", "kind", "family", "params", "native_dim", "mrl_dims", "ctx",
           "about", "notes", "hf_repo", "broker_model", "query_template", "doc_template")


def _public(spec: dict) -> dict:
    d = {k: spec.get(k) for k in _PUBLIC}
    d["kind"] = spec.get("kind") or "embedder"
    d["has_query_prompt"] = bool(spec.get("query_template"))
    return d


def all_specs(con) -> list[dict]:
    """Seed models overlaid with user-added ones (a user id shadows a seed of the same id)."""
    merged = {m["id"]: m for m in SEED_MODELS}
    for m in db.list_bench_models(con):
        merged[m["id"]] = m
    return list(merged.values())


def get_spec(con, model_id: str) -> dict | None:
    return next((m for m in all_specs(con) if m["id"] == model_id), None)


def _broker_names() -> set[str]:
    try:
        return {m.get("name", "") for m in broker.models()}
    except Exception:  # noqa: BLE001 — broker down: report everything unavailable, don't crash
        return set()


def _broker_available(spec: dict, names: set[str]) -> bool:
    want = (spec.get("broker_model") or "").lstrip("@")
    if not want:
        return False
    # tolerate a missing ':latest' tag (ollama lists 'x:latest', specs often say 'x')
    return any(n == want or n == f"{want}:latest" or n.split(":")[0] == want.split(":")[0]
               for n in names)


def list_with_status(con) -> list[dict]:
    """Public specs annotated with availability + footprint for the UI registry panel. A broker
    model an admin has Disabled (broker disabled.json, surfaced on /v1/models) is treated as
    unavailable here too, so the Embedding Lab honours the same platform-wide availability flag."""
    try:
        bm = broker.models()
    except Exception:  # noqa: BLE001
        bm = []
    names = {m.get("name", "") for m in bm}
    disabled = {m.get("name", "") for m in bm if m.get("disabled")}
    out = []
    for spec in all_specs(con):
        pub = _public(spec)
        if spec["provider"] == "onnx":
            pub["available"] = assets.present(spec)
            pub["footprint_mb"] = assets.footprint_mb(spec)
        else:
            is_disabled = _broker_available(spec, disabled)   # reuse the matcher against disabled
            pub["available"] = _broker_available(spec, names) and not is_disabled
            pub["disabled_by_admin"] = is_disabled
            pub["footprint_mb"] = None
        pub["is_seed"] = any(spec["id"] == s["id"] for s in SEED_MODELS)
        out.append(pub)
    return out


def build_embedder(con, model_id: str, prompting: str, dim: int | None):
    """Instantiate the embedder for a run-config, or raise a readable error."""
    spec = get_spec(con, model_id)
    if spec is None:
        raise ValueError(f"unknown model '{model_id}'")
    if spec["provider"] == "onnx":
        if not assets.present(spec):
            raise ValueError(f"model '{model_id}' assets not fetched yet — fetch it first")
        return OnnxEmbedder(spec, str(assets.model_dir(model_id)), prompting=prompting, dim=dim)
    return BrokerEmbedder(spec, prompting=prompting, dim=dim)


def build_reranker(con, model_id: str):
    """Instantiate a cross-encoder reranker for the two-stage path, or raise a readable error."""
    spec = get_spec(con, model_id)
    if spec is None:
        raise ValueError(f"unknown reranker '{model_id}'")
    if (spec.get("kind") or "embedder") != "reranker":
        raise ValueError(f"'{model_id}' is not a reranker")
    if not assets.present(spec):
        raise ValueError(f"reranker '{model_id}' assets not fetched yet — fetch it first")
    return RerankOnnx(spec, str(assets.model_dir(model_id)))
