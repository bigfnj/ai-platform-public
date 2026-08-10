# Platform conversion — ai-playground

How the standalone **GPU RAG Enablement Kit** (`nvidia-devrel-prep/rag-enablement-kit`, a
Python `http.server` + SSE app that hit Ollama/NVIDIA directly) became this rail. The
standalone kit is untouched — this is a *fresh extract of its pipeline logic*, not a subtree.

## What changed vs the standalone kit

| Concern | Standalone kit | This rail |
|---|---|---|
| Inference | OpenAI client → Ollama `:11434` or NVIDIA directly | **Broker** (`/v1/embed`, `/v1/chat`, `/v1/chat/stream`) by `@role`; NIM stays a direct external call for the toggle |
| Streaming | SSE from the app's own server | **WebSocket** `/ws/rag` (gateway buffers HTTP; its WS proxy is live) fed by the broker's new `/v1/chat/stream` |
| Corpus | one baked folder, re-embedded per switch | SQLite-cached corpora: **seed** (baked, shared) + **user uploads** (owner-scoped); retrieval always local so no per-user re-embed |
| Retrieval on NIM | re-embedded the corpus on the NIM embedder | **retrieval stays local** (bge-m3 via broker); only *generation* flips (hybrid: cheap local retrieval + hosted generation) |
| Identity | none | gateway `X-Platform-User` / `X-Platform-Admin` |
| UI | vanilla HTML + inline JS, NVIDIA-green theme | React module-federation remote adopting the **shell theme** |
| Shape | single RAG app | **multi-demo** rail (demo picker; RAG is #1) |

## Shared-infra change (one, additive)

The broker gained a token-streaming endpoint so this (and every future) rail can stream:
`POST /v1/chat/stream` (NDJSON) in `services/broker/app/{ollama.py,broker.py,main.py}`. The
buffered `/v1/chat` is unchanged. Rails call it **directly** (not through the buffering
gateway) and relay tokens over their WebSocket.

## Platform wiring touched (see the repo checklist)

- Gateway: `catalog.py` (APP_CATALOG entry), `config.py` (`app_ai_playground_url`,
  `enabled_apps`, `ai_playground_dist`, `app_backends()`/`resolved_app_dists()`),
  `rails_models.py` (`RAIL_MODEL_SLOTS["ai-playground"]`).
- Deploy: `docker-compose.yml` (the `ai-playground` service + gateway `depends_on`/env/dist
  mount + `ai_playground_data` volume); `services/broker/roles.json` (`@ai-playground`);
  `deploy/.env` + `activate-model-roles.ps1` (role + NIM key).
- Shell: `vite.config.ts` (remote + dev proxy with `ws:true`), `remotes.d.ts`, `App.tsx`
  (lazy import + render branch), `deskpet/quips/ai-playground.json` + `deskpet/lines.ts`.
- Broker attribution: `ROLE_RAIL["ai-playground"]` in `services/broker/app/broker.py` so
  queued jobs show as this rail in the admin queue.

## Notes / gotchas

- `@ai-playground` maps to local **nemotron-3-nano:4b** (NVIDIA's own, fast, non-reasoning) —
  keeps the demo end-to-end NVIDIA locally and streams cleanly (a reasoning model would emit
  `thinking` frames with empty content, i.e. a visible pause before tokens).
- Seed ingestion runs in a background thread on first boot (needs the broker up); the corpus
  picker shows the chunk count once ready.
- NIM key lives only in `deploy/.env` → injected as `AI_PLAYGROUND_NVIDIA_API_KEY`; absent ⇒
  the UI reports `nim.available=false` and greys the toggle.
