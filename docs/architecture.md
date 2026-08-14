# Architecture (v0)

Full design rationale and host facts: [checkpoint.md](checkpoint.md).
This file is the short, repo-local version.

## Principle: everything talks to the broker

```
                +------------------------------------------+
   apps  ---->  |  GPU / MODEL BROKER  (FastAPI, :11500)    |  ----> Ollama (:11434)
 (edu-suite,    |  - the ONLY thing that touches the GPU    |        [XTTS/SDXL/whisper later]
  recipe, ...)  |  - serialized heavy-op gate               |
                |  - one-heavy-model policy + VRAM view     |
                +------------------------------------------+
```

Apps never call `localhost:11434`. They import `platform_core.broker_client` and
call the broker by URL. That is what makes apps portable (find the broker by
config, not a hardcoded host) and what lets the broker keep two apps from both
trying to load 15 GB at once.

## VRAM policy (the whole reason the broker exists)

RTX 4090 = 24 GB. Only **one heavy generative model (~14-17 GB) fits at a time**,
alongside the ~1.1 GB embedder (`bge-m3`).

- Generative models are **heavy**; embedding models are **light**.
- A single async gate serializes heavy operations (chat, model load) so requests
  queue instead of colliding.
- Before loading/serving a heavy model, the broker evicts any *other* heavy model
  (`keep_alive: 0`). Embedders are allowed to stay resident alongside one heavy
  model.
- Belt-and-suspenders: also recommended to set `OLLAMA_MAX_LOADED_MODELS=1` on the
  Ollama service so it auto-evicts even if a request bypasses the broker.

## v0 broker API (Ollama-only)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Liveness + Ollama reachability + version |
| GET | `/v1/models` | Installed models, classified heavy/embed |
| GET | `/v1/ps` | Currently loaded models + per-model VRAM |
| GET | `/v1/status` | Full view: Ollama up, loaded, GPU VRAM, queue depth |
| POST | `/v1/load` | Warm a model (evicts other heavy models first) |
| POST | `/v1/unload` | Unload a model (`keep_alive: 0`) |
| POST | `/v1/chat` | Chat completion (non-streaming in v0) |
| POST | `/v1/embed` | Embeddings |

TTS / image / whisper endpoints get added when edu-suite needs them.
