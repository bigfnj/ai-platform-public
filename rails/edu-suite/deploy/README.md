# edu-suite — hosting (P4)

Run the edu-suite dashboard as a container behind Caddy, with the GPU/model layer
staying **native** on Windows. This is the "actually host it" step of hosting-ai P4.

## Topology

```
Browser ── Caddy (container, :80/:443, auto local HTTPS)
              └─ reverse_proxy ─> dashboard (container, :8800)
                                     └─ BROKER_URL ─> host.docker.internal:11500
                                                         └─ platform broker (NATIVE)
                                                              └─ Ollama + XTTS + SDXL (native GPU)
```

The dashboard image is **torch-free** (all model work goes to the broker), so it is
a slim `python:3.11-slim` image. The broker, Ollama, and the XTTS/SDXL worker venv
stay native on the host — the container reaches them via `host.docker.internal`.

## Run

1. **Start Ollama** (native, as usual).

2. **Start the broker on 0.0.0.0** so the container can reach it (native, from the
   platform repo with its venv active):

   ```powershell
   uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500
   ```

   The broker still spawns the media worker in edu-suite's venv (`BROKER_MEDIA_*`),
   all native. `--host 0.0.0.0` is required: `127.0.0.1` is not reachable from the
   container. It stays behind the host firewall (inbound default-block) + Caddy.

3. **Bring up the stack** (from this folder):

   ```powershell
   docker compose up -d --build
   ```

   Reuses the existing job library at `D:\edu-suite-library` by default (override
   with `EDU_LIBRARY_HOST`). Docker Desktop must have that drive shared.

4. **Open** https://edu.localhost (from this machine). See the Caddyfile header for
   LAN access + root-CA trust, or switch to `http://edu.localhost` for quick testing.

## Survive reboot (Windows services)

- The stack restarts automatically (`restart: unless-stopped`) once Docker Desktop
  is set to start at login.
- Run the **native broker** as a Windows service so hosting survives a reboot
  without a login. With NSSM (already available on this box) or WinSW, wrap:
  `<platform venv>\Scripts\python.exe -m uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500`
  (working dir = the platform repo root). Do the same for Ollama if it isn't
  already a service.

## Notes / next

- This hosts edu-suite standalone at its own hostname (its API + pages are all
  under `/`, so no path-prefix rewriting is needed). Federating it into the unified
  shell (one product, one URL) is P3 and needs the dashboard API namespaced under
  `/api` first.
- Media calls are one-per-item today; batching (broker `/v1/image` already accepts a
  list) is a pending perf follow-up.
