# AI Playground (rail)

A multi-demo platform rail — a home to showcase AI projects on the shared GPU broker.

- **RAG over documents** — ask a question, get a grounded, cited answer that streams
  token-by-token with retrieved passages alongside. Generation flips **live between the local
  GPU (through the broker) and NVIDIA NIM** (external cloud); retrieval always runs locally on
  `bge-m3` via the broker.
- **Embedding Lab** — benchmark embedding models head-to-head on a corpus + labeled query set.
  Compares **broker (GPU)** and **CPU int8-ONNX "beside-the-exe"** models on the same footing
  (Recall@1/@3, MRR, cosine separation, latency, dim, footprint), with prompting and Matryoshka
  dims as per-model knobs. Add and fetch new models as they ship. See `docs/EMBED_LAB.md`.

## Contract

- **Backend:** FastAPI `/api` surface built by `create_api()` (`ai_playground.api:create_api`),
  plus a `/ws/rag` WebSocket for the streamed answer. All model work goes through the broker
  (`/v1/embed`, `/v1/chat`, and the streaming `/v1/chat/stream`) — never Ollama directly.
- **Frontend:** a React module-federation remote under `frontend/` (`base: /ai-playground/`,
  exposes `./module`) that adopts the shell theme.
- **Identity:** the gateway injects `X-Platform-User` / `X-Platform-Admin`; uploaded corpora
  are owner-scoped, seed corpora are shared and read-only.
- **Data:** seed corpora are baked into the image under `seed/corpora/<name>/*.md` and ingested
  on first boot; mutable state (SQLite + uploads) lives on the `/srv/var` volume.

## Streaming

The platform gateway buffers HTTP, so the live "typing" answer travels over the gateway's
**WebSocket** proxy (`/ai-playground/ws/rag`), not SSE. Locally, tokens come from the broker's
additive `/v1/chat/stream`; in NIM mode, from NVIDIA's streaming completions. Both feed the
same browser protocol: a `sources` frame, then `token` frames, then `done`.

## Run standalone (dev)

```powershell
# from rails/ai-playground, with the broker up on :11500
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn --factory ai_playground.api:create_api --port 8850
# GET http://127.0.0.1:8850/api/health
```

Env of note: `AI_PLAYGROUND_BROKER_URL`, `AI_PLAYGROUND_CHAT_MODEL` (default local Nemotron;
`@ai-playground` in the container), `AI_PLAYGROUND_EMBED_MODEL` (`@embed`),
`AI_PLAYGROUND_NVIDIA_API_KEY` (enables the NIM toggle; absent ⇒ greyed out).
