# Platform hosting — the unified shell behind Caddy (P4, federated)

Hosts the whole platform (the shell with edu-suite federated in) as containers,
with the GPU/media layer staying **native** on Windows. This supersedes
`edu-suite/deploy` (which hosted the dashboard standalone); that Dockerfile is
reused here for the dashboard backend.

## Topology

```
Browser ── Caddy (container, :80/:443, internal-CA HTTPS)
              └─ reverse_proxy ─> gateway (container, :8700)
                    ├─ serves the shell SPA + the edu-suite federated remote
                    ├─ /edu-suite/api/* ─> dashboard (container, :8800)  [edu-suite backend]
                    └─ /api/platform/*  ─> broker (NATIVE, host.docker.internal:11500)
                                              └─ Ollama + XTTS + SDXL (native GPU)
```

Both frontend dists are **mounted** into the gateway (not baked in), so rebuilding
a frontend just needs `npm run build` + a gateway restart, no image rebuild.

## Prerequisites (on the host)

1. **Build both frontends** (mounted into the gateway):
   ```powershell
   cd D:\.claude\projects\platform\apps\platform\frontend ; npm ci ; npm run build
   cd D:\.claude\projects\edu-suite\apps\dashboard\frontend ; npm ci ; npm run build
   ```
2. **Start the broker on 0.0.0.0** (so containers can reach it), with edu-suite's
   venv available for the media worker (all native, alongside Ollama):
   ```powershell
   cd D:\.claude\projects\platform
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500
   ```

## Build + run

```powershell
cd D:\.claude\projects\platform\deploy
docker compose up -d --build
```

Open **https://platform.localhost** (from this machine). For LAN access, change the
site address in `Caddyfile` to this box's name/IP and import Caddy's root CA on
other devices, or use `http://platform.localhost` for quick testing.

The job library defaults to `D:\edu-suite-library` (override with `EDU_LIBRARY_HOST`).

## Survive reboot

- Containers use `restart: unless-stopped`; set Docker Desktop to start at login.
- Run the **native broker** (and Ollama) as Windows services (NSSM/WinSW) so the GPU
  layer comes up without a login. Wrap:
  `<platform venv>\Scripts\python.exe -m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500`
  (working dir = the platform repo root).

## Note on Docker credentials

`docker compose up --build` must run in an **interactive terminal**. In an agent /
non-interactive shell, Docker Desktop's `credsStore: desktop` helper fails ("logon
session does not exist") even for anonymous public pulls.
