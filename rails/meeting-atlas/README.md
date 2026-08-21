# Meeting Atlas

A platform rail that indexes [Meetily](https://github.com/Zackriya-Solutions/meetily)
recordings and rolls them up by day, week and month — with every summary treated as a
claim to be checked rather than a fact.

Does **no inference** and never calls the broker. Re-transcription and summarisation are
owned by an external co-work task that writes sidecar files this rail reads. See
[INGEST.md](./INGEST.md).

- Declared shape: [rail.json](./rail.json) — `status_route: null`, `model_slots: []`
- Ports: backend **8740**, vite dev **5290**
- Served at `/meeting-atlas/`, API at `/meeting-atlas/api/*`

## Views

| View | What it answers |
|---|---|
| **Day** | What did today look like — timeline ribbon, per-meeting cards, and a digest of what got decided |
| **Week** | Load by weekday, a day×hour heatmap of when meetings land, week digest |
| **Month** | Calendar shaded by time in meetings, week-over-week trend, month digest |
| **Actions** | Every action item across every meeting, grouped by owner, with its evidence |
| **Search** | Substring across every transcript, summary and action item; click a hit to land on the line |
| Meeting detail | Summary beside a scrubable transcript wired to the audio, plus talk density, pace, pauses and (when supplied) per-speaker share |

## API

| Route | Purpose |
|---|---|
| `GET /api/healthz` | liveness; whether the recordings mount and the Meetily DB are visible |
| `GET /api/meetings` | the index — every meeting, plus enough summary data for the roll-ups |
| `GET /api/meetings/{id}` | one meeting's transcript and parsed summary |
| `GET /api/meetings/{id}/audio` | the recording, range-capable so the player can seek |
| `POST /api/reindex` | rebuild from disk — the hook an ingest task calls |

## Configuration

Env prefix `MEETING_ATLAS_` (see [backend/.env.example](./backend/.env.example)).

| Variable | Default | Notes |
|---|---|---|
| `RECORDINGS_DIR` | `/data/recordings` | the recordings tree, mounted **read-only** |
| `MEETILY_DB` | *(empty)* | optional, read-only. See below |
| `DISPLAY_TZ` | `America/Los_Angeles` | recordings are UTC; everything shown is local |
| `AUTOREINDEX_SECONDS` | `300` | `0` = only `POST /api/reindex` triggers a rebuild |
| `SERVE_AUDIO` | `true` | |

Host paths are set in `deploy/.env` — `MEETING_ATLAS_RECORDINGS_MOUNT` and, optionally,
`MEETING_ATLAS_MEETILY_DB_MOUNT` + `MEETING_ATLAS_MEETILY_DB_IN_CONTAINER`. Both fall back
to empty named volumes, so a missing mount yields an empty rail rather than a broken stack.

## Local development

```bash
# backend
cd backend && pip install -e . && \
  MEETING_ATLAS_RECORDINGS_DIR="$HOME/Music/meetily-recordings" \
  uvicorn meeting_atlas_app.main:app --port 8740

# frontend (proxies /meeting-atlas/api -> 127.0.0.1:8740)
cd frontend && npm install && npm run dev   # http://localhost:5290
```

---

## Things about this data that are easy to get wrong

All of these are real, all found in the shipped data.

**`display_time` in `transcripts.json` is UTC.** A meeting recorded at 06:59 Pacific has
`display_time: "13:59:33"` on segment 0. The folder name's *leading* timestamp is local and
its *trailing* one is UTC. The indexer uses none of them: `metadata.json`'s `created_at` is
the single canonical instant, converted once. Read any of the other three naively and every
meeting renders hours off.

**Windows has no zoneinfo database.** `ZoneInfo("America/Los_Angeles")` raises there unless
the `tzdata` package is installed — which is why it is a declared dependency. If it still
fails, `config.tz()` falls back to the machine's **local** zone and logs it, never silently
to UTC. A UTC fallback would shift every meeting by hours while looking entirely plausible.

**The real title lives only in the Meetily SQLite.** The recording folder holds an
auto-generated name like `Meeting 2026-08-20_06-59-29`; the database has the
real title a human typed. Same for Meetily's own generated summary,
which is JSON with the markdown buried at `english_cache.markdown`. The DB must be **copied
before reading** (including `-wal`/`-shm`) because the Meetily app holds it open in WAL mode.

**`confidence` is hardcoded to 0.85** on every segment. It is not a quality signal and the
UI never shows it.

**`speaker` is NULL for every row.** No diarization exists in Meetily. The rail reports
pauses (gaps ≥2s) instead of pretending to know who spoke, and only renders per-speaker
stats when an enriched transcript actually supplies them.

**The mount is 9p.** A Windows bind mount through Podman/Hyper-V rejects
rename-over-an-existing-file — the trap that silently broke every `co-worker` triage write
once its state file existed. This rail is mounted read-only and keeps its index in memory,
so it has no write path at all. Keep it that way.

## Deploying a change to the frontend

The deployed stack runs from `deploy/installer/docker-compose.installer.yml`, whose gateway
uses `Dockerfile.gateway.bundled` and **bakes every rail dist into the image**. The dist
bind-mounts in `deploy/docker-compose.yml` belong to the dev/full-stack gateway, not the one
actually running — so a host `npm run build` alone deploys nothing, and neither does
rebuilding the wrong compose file.

The rail must also be in `PLATFORM_ENABLED_APPS` in the install root's `deploy/.env`, or the
gateway will not route to it however well the image is built.

```bash
cd ~/ai-platform
# 1. add meeting-atlas to PLATFORM_ENABLED_APPS in deploy/.env, and set
#    MEETING_ATLAS_RECORDINGS_MOUNT to the host recordings path
# 2. start the backend and rebuild the gateway image (the dist is baked in)
podman compose --env-file deploy/.env -f deploy/installer/docker-compose.installer.yml   --profile meeting-atlas up -d meeting-atlas
podman compose --env-file deploy/.env -f deploy/installer/docker-compose.installer.yml   build --no-cache gateway
podman compose --env-file deploy/.env -f deploy/installer/docker-compose.installer.yml   up -d --force-recreate gateway
# 3. verify the artifact landed in the RUNNING container, not just the build log
podman exec platform-gateway-1 ls /app/dist/meeting-atlas/assets | grep remoteEntry
podman exec platform-gateway-1 printenv PLATFORM_ENABLED_APPS
```

The build cache will happily report success while serving the old bundle. Grep the artifact
in the running container or you have not verified anything.
