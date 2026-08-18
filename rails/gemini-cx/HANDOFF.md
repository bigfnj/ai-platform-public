# Gemini CX rail — handoff

Read this first when resuming. Built 2026-08-18.

## What exists and what is proven

The rail is complete and wired into the platform. Verified on this box against the live broker:

| Check | Result |
|---|---|
| `tsc --noEmit` on the frontend | exit 0, clean |
| `py_compile` on all backend modules | clean |
| Corpus ingest (`force=True`) via live broker | **307 chunks, 17 collections, 1024 dims, 21.6s**, zero failures |
| Question deck validation | 7 groups, **37 questions**, zero problems |
| Gateway config / catalog / admin Rails wiring | resolves (`gemini-cx` appears in the admin Rails view) |
| Broker role wiring | `roles()` merges to `gemma3:4b`, `ROLE_RAIL` attributes jobs to "Gemini CX" |
| Compose YAML (both files) | parses; service, profile, volume, gateway env all present |
| `lib-runtime.ps1` | parses clean; `gemini-cx` added to `Get-ComposeProfiles` |
| Grounded answers, 3 trap questions | correct on all three (see below) |

**NOT yet done: the rail has never run inside a container, and the gateway image has not been
rebuilt.** Everything above was verified out-of-container against the live broker. The container
build is the remaining step.

## The three trap answers, verified

Asked through the real retrieval + prompt + `gemma3:4b` path:

- *"How many languages does GECX support for voice versus text?"* → correctly separated **40+
  text** from **10 audio-to-audio**, and further separated the wider speech stack (125
  recognition / 220+ voices). 9.2s.
- *"Can I deploy the Shopping agent or Food Ordering agent today?"* → **No**, cited "Commerce
  agents coming soon", flagged the contradiction with the January announcement, told the reader to
  confirm with the account team. 2.6s.
- *"How is GECX priced?"* → per session not per seat, three separate component meters, and
  **refused to state a base session price** because the corpus marks it unverifiable. 7.1s.

That third one is the important result: the model declined to invent a number when the corpus told
it the number was unpublished. That behaviour is the whole point of the grounding contract in
`config.SYSTEM_PROMPT` — if a future change breaks it, that is a regression, not a style change.

Latencies of 2.6–9.2s are why the UI streams over `WS /ws/ask` with a buffered POST fallback.

## To deploy it

Two things must happen, in this order.

**1. The broker must learn the role.** The live broker runs from the **install clone**
(`%USERPROFILE%\ai-platform`), so it does not yet know `gemini-cx-rag`. This bit the first e2e run:
`@gemini-cx-rag` resolved to itself and Ollama 404'd on the literal name. After the install clone
pulls, `roles.json` carries `"gemini-cx-rag": "gemma3:4b"` and is **hot-read — no restart needed**.
The `DEFAULT_ROLES` entry in `services/broker/app/config.py` is code, so it needs a broker service
restart, but it is only a fallback: `roles()` is `DEFAULT_ROLES | json`, so the JSON entry alone is
sufficient.

**2. Enable and build.** Add `gemini-cx` to `PLATFORM_ENABLED_APPS` in the install clone's
`deploy/.env` (gitignored, so it does not travel with the commit), then rebuild the gateway image
— the frontend dist is **baked in**, a host `npm run build` deploys nothing — and bring up the new
service with its profile:

```powershell
podman build -f deploy/Dockerfile.gateway.bundled -t platform-gateway-bundled:latest .
docker-compose --env-file deploy\.env -f deploy\installer\docker-compose.installer.yml `
  --profile recipe-book --profile co-worker --profile smb-partner-enablement --profile gemini-cx `
  up -d --build
```

`Initialize-ComposeVolumes` must create `platform_gemini_cx_data` first — the volume is declared
`external: true`, so a missing one is an error rather than a silent empty volume. Compose usually
needs the SSH-tunnel workaround since the watchdog owns the podman pipe; `podman build` does not.

Then hard-refresh the browser — the shell caches the federated `remoteEntry.js`.

## Things that will trip you up

- **`pip install .` does not reload a running uvicorn.** Kill the PID and use
  `--reload --reload-dir src`.
- **`GEMINI_CX_STANDALONE=1` is required outside the gateway**, or identity fails closed and every
  request is a 401.
- **`_same_model()` is tag-tolerant on purpose.** `@embed` resolves to `bge-m3` while Ollama
  reports `bge-m3:latest`. Simplifying it to `==` makes the health endpoint report both models cold
  forever.
- **`config.ensure_dirs()` is load-bearing.** `store.init()` calls it before touching SQLite, and
  the container mounts an empty volume at `/srv/var`. It was missing in the first draft and would
  have crashed the rail at startup.
- **The empty-collection and orphan-collection purges in `ingest.py` are not redundant.** Without
  them, emptying or renaming a collection leaves its chunks indexed and cited forever. This cost
  the sibling SMB rail weeks.

## Open items

`BACKLOG.md` has the ordered list. The two that matter most:

1. **Three facts the corpus deliberately refuses to state** and should be filled by hand: base
   per-session prices (the pricing page defeated automated extraction), the actual 40+ and
   10-language enumerations, and whether "Personal Intelligence" (Forrester-sourced) is real.
2. **A staleness signal.** Every file carries `As of:` / `Verified:` front matter and nothing
   surfaces it. GECX launched January 2026 and is moving; the Commerce Agents page flipping from
   "coming soon" to GA would invalidate several answers at once.
