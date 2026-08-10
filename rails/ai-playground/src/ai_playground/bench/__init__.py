"""Embedding Lab: a provider-abstracted benchmark for text-embedding models.

The second AI Playground demo. Where RAG *uses* one embedder (bge-m3), the Embedding Lab
*compares* many, over a chosen corpus + a labeled query set, and reports the retrieval
metrics that actually decide a model swap: Recall@1/@3, MRR, cosine separation, per-embed
latency, output dim and on-disk footprint.

Two execution substrates behind one interface (``bench.providers``):
  * **broker**  — embeds through the platform GPU broker (any Ollama-served model).
  * **onnx**    — onnxruntime on CPU with assets fetched from Hugging Face (the
                  "model file beside the exe" path recall.py / desktopPet actually ship).

Everything else is model-agnostic: the registry (``bench.registry``) declares each model's
provider, prompting templates and Matryoshka dims; the engine (``bench.engine``) runs any
selection of (model, prompting, dim) run-configs and scores them the same way.
"""
