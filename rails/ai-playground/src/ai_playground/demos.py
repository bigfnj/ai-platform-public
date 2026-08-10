"""The demo registry for the multi-demo rail.

RAG is the first demo. Add entries here as more AI demos are built — each maps to a
frontend panel plus backend routes / a WebSocket handler. Keeping the list server-driven
means the frontend picker updates without a rebuild.
"""
from __future__ import annotations

DEMOS = [
    {
        "id": "rag",
        "title": "RAG over documents",
        "icon": "\U0001F4DA",  # 📚
        "blurb": "Ask questions grounded in a document corpus. Answers stream token-by-token "
                 "with inline citations; retrieval is transparent cosine search over broker "
                 "embeddings. Flip generation between your local GPU and NVIDIA NIM live.",
        "status": "ready",
    },
    {
        "id": "embed-bench",
        "title": "Embedding Lab",
        "icon": "\U0001F9EA",  # 🧪
        "blurb": "Benchmark embedding models head-to-head on your own corpus + labeled queries. "
                 "Compares GPU broker models and CPU int8-ONNX 'beside-the-exe' models on the "
                 "same footing: Recall@1/@3, MRR, cosine separation, latency, dim and footprint. "
                 "Toggle prompting (none / bge-prefix / model-default) and Matryoshka dims; add "
                 "and fetch new models as they ship.",
        "status": "ready",
    },
]
