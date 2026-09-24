# Course Builder Rail

Turn any local markdown knowledge corpus into an OpenMAIC course-source document.

Point it at a directory, index it once with the local embed model (`@embed`, bge-m3), then
generate a grounded `.md` from a course prompt — structured by an exam blueprint and ready
to upload into OpenMAIC as course material.

---

## How it works

```
Local corpus (.md files)
  └─► [Index] bge-m3 chunks + embeds → DuckDB (~200 MB for 7k files)
        └─► [Build] cosine retrieval weighted by exam domain %
              └─► optional LLM condensation (local @chat or offsite @openmaic)
                    └─► grounded course-source .md  → upload to OpenMAIC
```

The index step is the slow one (~10–20 min for 7k files, depends on embed batch latency).
Run it once; re-run only when the corpus changes.

---

## Quick start — native dev (no Docker)

```powershell
# 1. Start the backend
.\deploy\start-native.ps1 `
  -BlueprintCsv "C:\path\to\blueprint-domains.csv" `
  -IndexPath    "C:\Users\you\.course-builder-index.duckdb"

# 2. Start the frontend dev server (in another terminal)
cd frontend
npm install
npm run dev
# → http://127.0.0.1:5351
```

Then in the browser:
1. **Corpus Index** panel → paste your corpus directory path → click **Build Index**
2. Watch the progress log; indexing streams live.
3. **Build Course** panel → type a prompt → pick a blueprint → choose condensation tier → **Generate .md**
4. Click **Download .md** → upload the file in OpenMAIC via the document import button.

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CB_BROKER_URL` | `http://127.0.0.1:11500` | Platform broker |
| `CB_EMBED_ROLE` | `@embed` | Broker role for embedding (bge-m3) |
| `CB_CONDENSE_ROLE` | `@openmaic` | Broker role for LLM condensation |
| `CB_INDEX_PATH` | `~/.course-builder-index.duckdb` | DuckDB vector store |
| `CB_BLUEPRINT_CSV` | _(empty)_ | Path to `blueprint-domains.csv` |
| `BROKER_AUTH_TOKEN` | _(empty)_ | Shared broker control-plane token |
| `PLATFORM_STANDALONE` | `0` | Set to `1` to skip gateway identity gate |

---

## Ports

| Surface | Port |
|---|---|
| Backend (uvicorn) | **8901** |
| Frontend dev (vite) | **5351** |

---

## Condensation tiers

| UI label | Role | What runs |
|---|---|---|
| Raw | — | No LLM; pure retrieval output. Largest file, fastest. |
| Local | `@chat` | gemma3:4b on this box. Coexists with bge-m3 on an 8 GB card. |
| Offsite | `@openmaic` | mistral-small3.2:24b (offsite). Best quality. |

---

## Blueprint CSV format

The file must have these columns (any order, extra columns ignored):

```
credential_code, credential_name, domain_name, weight_percent, sub_skill
```

The `blueprint-domains.csv` in `ai-notes/research/` already matches this format — point
`CB_BLUEPRINT_CSV` at it.

---

## Dockerized deployment

```yaml
# Add to deploy/docker-compose.yml under services:
course-builder:
  build:
    context: ../rails/course-builder
    dockerfile: deploy/Dockerfile
  environment:
    CB_BROKER_URL:    http://host.docker.internal:11500
    CB_EMBED_ROLE:    ${CB_EMBED_ROLE:-@embed}
    CB_CONDENSE_ROLE: ${CB_CONDENSE_ROLE:-@openmaic}
    CB_INDEX_PATH:    /data/index.duckdb
    CB_BLUEPRINT_CSV: ${CB_BLUEPRINT_CSV:-}
    BROKER_AUTH_TOKEN: ${BROKER_AUTH_TOKEN:-}
  extra_hosts:
    - "host.docker.internal:${WINDOWS_HOST:-host-gateway}"
  volumes:
    - course_builder_index:/data
    - ${CB_CORPUS_PATH:-/nonexistent}:/corpus:ro
  expose:
    - "8901"
  restart: always
```

When containerized, set `CB_CORPUS_PATH` to the directory you want to index and enter `/corpus`
in the UI (the container sees it there). The index volume persists across rebuilds.

---

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/capabilities` | Header chips; broker + model state |
| `GET` | `/api/healthz` | Liveness: broker up + index present |
| `GET` | `/api/blueprints` | Blueprint list from CSV |
| `GET` | `/api/roles` | Broker role table |
| `POST` | `/api/index/start` | Start index job |
| `GET` | `/api/index/events` | SSE stream of index progress |
| `GET` | `/api/index/status` | Index stats + job status |
| `DELETE` | `/api/index` | Drop the current index |
| `POST` | `/api/build/start` | Start build job |
| `GET` | `/api/build/events` | SSE stream of build progress |
| `GET` | `/api/build/result` | Download last built `.md` |
