# deploy/

Container stack, service installer, and operational scripts.

## How to install

Use the lean one-command installer described in [`docs/INSTALL.md`](../docs/INSTALL.md). It handles
Docker/Podman detection, broker service install, Caddy, and logon persistence automatically.

```powershell
irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex
```

## Files

| File / Dir | Purpose |
|---|---|
| `installer/install.ps1` | GUI + console + doctor install wizard |
| `installer/install-native.ps1` | Broker venv + NSSM service install |
| `installer/lib-runtime.ps1` | Shared helpers: Podman/Docker detection, atomic .env writes, compose wrappers |
| `installer/platform-startup.ps1` | Logon startup script (Podman mode); installed as a Startup-folder shortcut |
| `installer/platform-startup.sh` | Logon startup script (WSL/Docker-in-WSL2 mode) |
| `installer/smoke-test.ps1` | Phase-gated smoke tests: `-Stage runtime\|data\|build\|e2e\|persistence` |
| `installer/docker-compose.installer.yml` | Lean stack: bundled gateway + rail backends + caddy |
| `installer/Caddyfile` | Caddy config for the lean install (plain HTTP on :1111) |
| `installer/env.lean.example` | .env template for the lean stack |
| `docker-compose.yml` | Full stack (all rails, separate frontend mounts, 24 GB VRAM) |
| `Dockerfile.gateway.bundled` | Multi-stage image: bakes all rail frontends into the gateway |
| `activate-model-roles.ps1` | One-shot: restart broker + write per-rail `@role` vars into .env |
| `install-services.ps1` | Low-level: broker + Ollama NSSM service install |
| `logs/` | Runtime logs (gitignored) |

## Topology

```
Browser → localhost:1111 → caddy (container)
              └─ → gateway:8700 (container, bundled frontends)
                    ├─ /terminal-fun/  → terminal-fun:8730 (container)
                    ├─ /recipe-book/   → recipe-book:8830  (container)
                    ├─ /co-worker/     → co-worker:8860    (container)
                    └─ host.docker.internal:11500 → broker (NATIVE, Windows service)
                                                        └─ Ollama :11434 (native)
```

**Container runtime:** Podman 6.x (Hyper-V provider, default) or Docker Desktop/Engine.
- Podman: `docker-compose.exe` (standalone) drives the Docker-compat API pipe — no `DOCKER_HOST` override needed.
- Host address: `WINDOWS_HOST=192.168.127.254` (gvproxy, static for Hyper-V); no inbound firewall rule needed.
- WSL/Docker-in-WSL2: `wsl docker compose`, `WINDOWS_HOST` = dynamic WSL→Windows gateway IP.

## Access

`http://localhost:1111` — the corporate proxy bypasses `localhost`; `platform.localhost` is intercepted.
