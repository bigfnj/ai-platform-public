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

The platform gateway buffered HTTP when this rail was built, so the live "typing" answer travels
over the gateway's **WebSocket** proxy (`/ai-playground/ws/rag`), not SSE. Locally, tokens come from the broker's
additive `/v1/chat/stream`; in NIM mode, from NVIDIA's streaming completions. Both feed the
same browser protocol: a `sources` frame, then `token` frames, then `done`. While nothing
flows (a cold model load, or a question waiting on the Claude CLI below) a no-op `ping` frame
goes out every 20 s, so an idle-socket cutoff at the edge (~100 s) cannot strand the answer.

## The Claude CLI option

The local `generation` slot (`@ai-playground`) can reach Claude: when the deployment's owner
switches that role on in Admin > Rails, the broker answers **the owner's own questions** from
the owner's Claude CLI subscription and everybody else's from the local model, as before. The
switch defaults off, so nothing changes until an owner turns it on. `/api/rag/ask` and
`/ws/rag`'s local branch pass the viewer as `requester`, on this role only
(`broker._cli_requester`); the NIM branch never reaches the broker, and the Embedding Lab's
"about" draft (on the shared `@chat` role) names nobody. The header chip reads "Claude" for
exactly the viewers whose question would go there.

A Claude answer, or the local answer after a Claude fallback, is announced by a `backend`
frame before its first token (`{"type": "backend", "backend": "claude", "model": "<id>"}` or
`{"type": "backend", "backend": "ollama", "reason": "<why>"}`), and the demo shows who answered
above the answer instead of leaving the local model's label to describe it. If Claude stops
after its first token, no local answer follows: the socket sends an `error` frame and the text
already shown is incomplete. The rail waits `AI_PLAYGROUND_BROKER_TIMEOUT` (600 s by default)
for the broker, which has to outlast the broker's `BROKER_CLI_TIMEOUT` plus one local answer,
since a fallback runs inside the same broker request.

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
