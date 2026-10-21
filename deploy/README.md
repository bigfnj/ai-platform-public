# Platform hosting — the unified shell behind Caddy (P4, federated)

Hosts the whole platform (the shell with edu-suite federated in) as containers,
with the GPU/media layer staying **native** on Windows. This supersedes
`edu-suite/deploy` (which hosted the dashboard standalone); that Dockerfile is
reused here for the dashboard backend.

## Topology

```
Browser ── Caddy (container, host :1111 -> :80, plain HTTP as shipped)
              └─ reverse_proxy ─> gateway (container, :8700)
                    ├─ serves the shell SPA + the edu-suite federated remote
                    ├─ /edu-suite/api/* ─> dashboard (container, :8800)  [edu-suite backend]
                    └─ /api/platform/*  ─> broker (NATIVE, host.docker.internal:11500)
                                              └─ Ollama + XTTS + SDXL (native GPU)
```

Every frontend dist (the shell's and each rail's) is **mounted** read-only into the gateway, not
baked in (the gateway's `volumes` in `docker-compose.yml`), so rebuilding a frontend needs
`npm run build` and no image rebuild. The gateway decides which dists to mount when it starts
(`_mount_spa` and `_mount_app_remotes` in its `main.py`), and a dist missing at that moment stays
unmounted until it restarts (a missing shell dist leaves `/` answering 404). So restart it,
`docker compose restart gateway`, after building a dist that was missing and after rebuilding the
shell's. A rebuilt rail dist that was already mounted is served live: hard-refresh the page.

## Prerequisites (on the host)

`<repo>` below is your checkout of this repository.

1. **Build the frontends the gateway mounts**: the shell, and each rail's `frontend/` (the full
   list is the gateway's `volumes`). The shell and recipe-book, for example:
   ```powershell
   cd <repo>\apps\platform\frontend ; npm ci ; npm run build
   cd <repo>\rails\recipe-book\frontend ; npm ci ; npm run build
   ```
2. **Start the broker on 0.0.0.0** (so another machine on the LAN can use it as its upstream;
   Docker Desktop containers reach a broker bound to 127.0.0.1 too, as `Get-BrokerBindHost` in
   `installer/lib-runtime.ps1` records), with the media venv `BROKER_MEDIA_PYTHON` names available
   for the media worker (all native, alongside Ollama; see `services/broker/README.md`):
   ```powershell
   cd <repo>
   .\.venv\Scripts\Activate.ps1
   uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500
   ```

## Build + run

One service at a time, never a bare full-stack `docker compose up`: that recreates caddy, which
rebinds the host port and churns the Windows NAT (the caddy `ports` comment in
`docker-compose.yml`).

```powershell
cd <repo>\deploy
docker compose up -d --build --no-deps <service>    # one service
.\rebuild-stale.ps1                                 # every service compose builds, one at a time
```

`rebuild-stale.ps1` leaves pulled images alone (`caddy`, `openmaic-app`; its header says so), so a
box's very first bring-up also needs `docker compose up -d --no-deps caddy openmaic-app` once.
`openmaic-app` has no `build:` stanza: build or pull its image first and name the tag in
`OPENMAIC_APP_IMAGE` (the compose comment at `openmaic-app:`).

On a podman box (the lean installer stack) the same script works unchanged: it picks the runtime
the way the other ops scripts do (`-RuntimeMode`, else `PLATFORM_RUNTIME` in `deploy\.env`, else
the CLI that lists the gateway) and the stack the gateway container was created from (`-Stack
full|lean`), and on the lean stack composes with `--env-file deploy\.env`, the box's
`PLATFORM_COMPOSE_OVERRIDE` and the enabled rails' profiles. A service whose build context is
outside the checkout (an override building `openmaic-app` from another clone) is skipped and named
unless you ask for it with `-Service`.

Open **http://localhost:1111** (or `http://platform.localhost:1111`) from this machine. Caddy
serves plain HTTP only: compose publishes the container's port 80 as host 1111 and no 443
(`docker-compose.yml`, caddy `ports`), and every site in `Caddyfile` is an `http://` address.
For HTTPS, `INSTRUCTIONS.md` (its HTTPS section) gives two paths: a tunnel, such as Cloudflare
Tunnel, that ends TLS at its edge and forwards plain HTTP to port 1111, or a certificate from
Caddy's own internal CA for the LAN. The `Caddyfile`'s redirect block 308-redirects the bare-HTTP
LAN origins it names to the public HTTPS hostname, because a bare-HTTP LAN origin is not a secure
context (no mic, and no session cookie once `PLATFORM_COOKIE_SECURE=true`).

The job library defaults to `D:\edu-suite-library` (override with `EDU_LIBRARY_HOST`).

## Survive reboot

- Every service uses `restart: always` (`docker-compose.yml`); set Docker Desktop to start at login.
- Run the **native broker** (and Ollama) as Windows services (NSSM/WinSW) so the GPU
  layer comes up without a login. Wrap:
  `<platform venv>\Scripts\python.exe -m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500`
  (working dir = the platform repo root).

## Note on Docker credentials

A build that has to pull a base image (`docker compose up -d --build --no-deps <service>` on a
cold cache) must run in an **interactive terminal**. In an agent /
non-interactive shell, Docker Desktop's `credsStore: desktop` helper fails ("logon
session does not exist") even for anonymous public pulls.
