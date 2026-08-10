# Embedding Lab — design & operations

The Embedding Lab is the AI Playground's second demo: a benchmark suite for text-embedding
models. It answers the question that actually decides a model swap — *on our own data, how much
retrieval quality does model/config X buy, and what does it cost?* — instead of trusting a
leaderboard average.

## Why it exists

It grew out of a throwaway CPU harness comparing `bge-small` (what `recall.py` / desktopPet ship)
against newer embedders. The lesson from that harness is baked into the design: **retrieval
quality is dominated by prompting and by testing on your own corpus, not by model size.** A naive
"bigger model" swap can *regress*; the right query prefix can swing Recall@1 by ten points. So the
lab makes prompting and Matryoshka dim first-class, replayable knobs, and always scores on a corpus
+ query set you choose.

## Architecture

```
bench/providers.py   BrokerEmbedder | OnnxEmbedder   — one embed(text, is_query) over two substrates
bench/registry.py    SEED_MODELS + DB overlay        — the model catalog, availability, factory
bench/assets.py      HF fetch / presence / footprint — onnx model files in the models volume
bench/querysets.py   seed + uploaded query sets      — {q, targets:[source…]}
bench/engine.py      run(configs) -> metrics         — R@1/R@3/MRR/sep/latency, per config
bench/pull.py        optional direct Ollama pull      — add a broker model from the UI
```

Two execution substrates, one interface:

- **broker** — embeds through the platform broker's `/v1/embed` (any Ollama-served model, on the
  GPU). Latency here is GPU + queue + HTTP. This is how you "bench a model as it ships": pull it in
  Ollama, register it.
- **onnx** — runs an int8 ONNX graph on the **CPU** via onnxruntime, tokenizing with the model's own
  `tokenizer.json`. This reproduces the "model file beside the exe, no server" path. Assets are
  fetched from Hugging Face into the models volume (`/srv/var/models/<id>/`), so the image stays lean.

A benchmark **run-config** is `(model, prompting, dim)`:

- **prompting** — `none` (raw text), `bge-query` (the BGE retrieval instruction on the query side),
  or `model` (the model's own query/doc templates, e.g. EmbeddingGemma's `task: search result |
  query: …` or arctic's trained prefix). Selecting several prompting modes for one model produces
  several columns, so "prefix vs no-prefix" is a single run.
- **dim** — Matryoshka truncation (keep the first N coordinates + renormalize). `native` plus each
  declared MRL dim.

The engine re-embeds the whole corpus with each config (the corpus's stored `bge-m3` vectors are
irrelevant here), ranks every labeled query by cosine, and reports:

| metric | meaning |
|---|---|
| R@1 / R@3 | share of queries whose top-1 / top-3 chunk is from a target source |
| MRR | mean reciprocal rank of the first chunk from a target source |
| **sep** | mean cosine margin (best target − best distractor). The steadier signal on small sets. |
| ms/query | single-item query embed latency (comparable across providers) |
| dim / MB | output dim after truncation; on-disk footprint for onnx models |

Runs stream over `/ws/bench` (a `meta` frame, `progress` per config, then `done` with all results)
and are persisted to `bench_run` for history.

## Seed content

- Corpus **Embedding Concepts** — 16 short docs on distinct retrieval/AI-infra topics, worded so the
  queries don't keyword-match (a real semantic test). Baked at `seed/corpora/embedding-concepts/`.
- Query set **Embedding concepts** — 16 `{q, targets}` pairs, `seed/querysets/embedding-concepts.json`.
- Seed models (registry `SEED_MODELS`): broker `bge-m3`, `embeddinggemma`, `qwen3-embedding:0.6b`;
  onnx `bge-small`, `bge-base`, `arctic-embed-m-v1.5`, `embeddinggemma-onnx`.

## Adding a model (no code change)

**Broker (Ollama) model** — `ollama pull <tag>` on the broker box (or the UI's *Pull via Ollama*
button, which hits the host daemon best-effort), then add a registry entry:

```json
{ "id": "nomic-embed-text", "label": "Nomic Embed v1.5", "provider": "broker",
  "broker_model": "nomic-embed-text:latest", "native_dim": 768, "mrl_dims": [768, 512, 256],
  "query_template": "search_query: {text}", "doc_template": "search_document: {text}" }
```

**ONNX (CPU) model** — add a registry entry with the HF repo + file list, then click *Fetch from HF*
(or `POST /api/bench/models/<id>/fetch`):

```json
{ "id": "gte-small", "label": "gte-small · ONNX int8 CPU", "provider": "onnx",
  "hf_repo": "Xenova/gte-small", "onnx_file": "onnx/model_quantized.onnx",
  "files": ["onnx/model_quantized.onnx", "tokenizer.json"],
  "pooling": "cls", "native_dim": 384, "mrl_dims": [] }
```

`pooling` is `graph` (use a baked `sentence_embedding` output), `cls`, or `mean`. Set `pad_len` if the
export needs a fixed sequence length (EmbeddingGemma's rotary op does — 512). Registry entries added
in the UI are admin-only and persist in SQLite; seed models can't be deleted.

**Updating an existing model** — re-pull the Ollama tag (broker) or delete + re-fetch (onnx); the
availability badge and footprint refresh on the next models list.

## API

```
GET    /api/bench/models                 registry + availability + footprint
POST   /api/bench/models                 add/replace a model (admin)
DELETE /api/bench/models/{id}?purge=     remove (purge=true also deletes onnx files) (admin)
POST   /api/bench/models/{id}/fetch      download onnx assets from HF (admin)
POST   /api/bench/models/{id}/pull       best-effort Ollama pull (admin)
GET    /api/bench/corpora                corpora to bench over (shared RAG corpora + owner uploads)
GET    /api/bench/querysets              labeled query sets
POST   /api/bench/querysets/upload       upload a query set (JSON: {name, queries:[{q,targets}]})
DELETE /api/bench/querysets/{id}
POST   /api/bench/run                    buffered run (fallback)
WS     /ws/bench                          streamed run (meta / progress / done)
GET    /api/bench/runs, /runs/{id}       run history
```

## Ops notes

- onnxruntime needs `libgomp1` (installed in the Dockerfile) on Debian slim. Still torch-free.
- ONNX assets live on the `ai_playground_data` volume under `models/`, so they survive restarts and
  aren't baked into the image. First fetch of each model needs internet (Hugging Face).
- Broker embedding can't be CPU-forced (`/v1/embed` takes no options), so broker latency reflects the
  GPU. The onnx provider is the honest CPU/footprint number.
- A fresh boot ingests the seed corpus via the broker; if the GPU is cold the first seed embed waits
  on a model load. Query sets load without the broker.
