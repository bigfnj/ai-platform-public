# GPU / Model Broker (v0)

The single owner of the GPU. Apps never touch Ollama directly — they call this.
v0 is Ollama-only. See `../../docs/architecture.md` for the why.

## Run (Windows PowerShell, from the repo root `D:\.ai-work\projects\platform`)

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

## Delegating a role to another broker

One broker per box was the only shape until a rail needed a bigger card than the one it was
installed on. Rather than teach every rail to find a second broker — a per-rail URL, a per-rail
token, and a contract rule for each — **a role may name a registered remote and this broker
forwards the call**. The rail asks its own broker for `@openmaic` exactly as before and does not
know the answer came from somewhere else.

Register remotes in `services/broker/upstreams.json` (gitignored; it holds tokens — see
`upstreams.example.json`). It is hot-read, so adding a box takes effect on the next request:

```json
{ "offsite": { "url": "http://192.168.1.11:11500", "token": "bt_..." } }
```

Then point a role at it with a **double colon**:

```json
{ "openmaic": "offsite::mistral-small3*:24b" }
```

The separator is doubled because a single colon is already the model/tag separator. `gemma3:4b`
would otherwise parse as upstream `gemma3` and model `4b` — and would do so only on a box that
happened to register an upstream named after a model family, which is the worst kind of bug to
own. No Ollama name contains `::`.

Four behaviours worth knowing:

- **The glob is resolved by the upstream**, not here. It knows its inventory; this box does not,
  and on a small card would usually match nothing and turn a good delegation into a missing model.
- **A delegated call does not take the local GPU gate.** The single slot models *this* card, and
  holding it for a generation running elsewhere would serialise local work behind something that
  cannot contend with it.
- **An unregistered upstream is not silently run locally.** The value stays whole, matches
  nothing, and shows as a missing chip. `PUT /v1/roles` refuses it outright, and the startup audit
  names it.
- **A broken or absent registry degrades to local-only**, never to an exception. Delegation is an
  enhancement; it must not be able to take the GPU layer down.

Two endpoints support the admin picker: `GET /v1/upstreams` lists every broker a role may name
(`local` first, each with reachability and whether our token is accepted), and
`GET /v1/models?upstream=<name>` lists what that box has installed — because once a rail is
pointed off-site, the choices have to come from the machine that will run it.

Availability note: an off-site rail depends on *both* brokers. If this one is down, the rail stops
even though the remote card is fine.

## VRAM policy

At most one **heavy** (generative) model resident at a time; loading/serving one
evicts any other heavy model. **Embedding** models (name matches a hint like
`bge`/`embed`) are light and may stay resident alongside, so `/v1/embed` is not
gated. Recommended belt-and-suspenders on the Ollama service:
`OLLAMA_MAX_LOADED_MODELS=1`.
