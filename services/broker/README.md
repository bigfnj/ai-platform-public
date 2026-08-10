# GPU / Model Broker (v0)

The single owner of the GPU. Apps never touch Ollama directly — they call this.
v0 is Ollama-only. See `../../docs/architecture.md` for the why.

## Run (Windows PowerShell, from the repo root `D:\.claude\projects\platform`)

```powershell
# one-time: create a venv and install
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e packages\platform_core
pip install -e "services\broker[dev]"

# run the broker
uvicorn app.main:app --app-dir services\broker --host 127.0.0.1 --port 11500
```

## Endpoints

```
GET  /healthz        liveness + Ollama reachability
GET  /v1/status      full view: reachable, loaded models, GPU VRAM, queue depth
GET  /v1/models      installed models, each classified heavy | embed
GET  /v1/ps          currently loaded models + per-model VRAM
POST /v1/load        {"model": "...", "keep_alive": -1}  -> evicts other heavy models first
POST /v1/unload      {"model": "..."}
POST /v1/chat        {"model": "...", "messages": [{"role":"user","content":"hi"}]}
POST /v1/embed       {"model": "bge-m3", "input": "text or [texts]"}
```

## Smoke test

```powershell
curl http://127.0.0.1:11500/v1/status
curl -X POST http://127.0.0.1:11500/v1/chat -H "content-type: application/json" `
  -d '{"model":"llama3.1:8b","messages":[{"role":"user","content":"say hi in 3 words"}]}'
```

## VRAM policy

At most one **heavy** (generative) model resident at a time; loading/serving one
evicts any other heavy model. **Embedding** models (name matches a hint like
`bge`/`embed`) are light and may stay resident alongside, so `/v1/embed` is not
gated. Recommended belt-and-suspenders on the Ollama service:
`OLLAMA_MAX_LOADED_MODELS=1`.
