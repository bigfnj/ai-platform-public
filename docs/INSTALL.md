# Installing AI-Platform (lean)

A one-window GUI installer that stands up a **lean** platform on a modest box: the **admin
shell** + **Terminal Fun** + optional **Recipe Book**, running on two small Ollama models
(`gemma3:4b` + `bge-m3`) with **no HuggingFace token and no image/TTS pipeline**. It's designed
for an **8 GB-VRAM** Windows machine.

> The full 24 GB stack (all rails, FLUX/XTTS media, Cloudflare exposure) is a separate, manual
> setup documented in `CLAUDE.md` / `CLAUDE.local.md`. This installer deliberately targets a
> smaller, reproducible subset.

## What you need (the installer checks all of this)

| Requirement | Notes | Auto-install |
|---|---|---|
| Windows 10/11 | | |
| **NVIDIA GPU ≥ 8 GB** + driver | the AI models run here | — |
| **Docker Desktop** (running) | the shell + rail containers | winget |
| **Ollama** | the LLM host (native service) | winget |
| **Python 3.11+** | the torch-free broker venv only | winget |
| ~20 GB free disk | images + two models | — |

No Node.js is needed — the frontends are built inside the Docker image. No HuggingFace token —
there's no media pipeline. Recipe icons ship pre-rendered in the seed.

## Run it

**Fastest — one line, no manual clone.** From any PowerShell:

```powershell
irm https://raw.githubusercontent.com/bigfnj/ai-platform-public/main/get.ps1 | iex
```

This ensures git, enables Windows long-paths, clones the repo to `%USERPROFILE%\ai-platform-public`
(override with `$env:AIPLATFORM_DIR`; pin a tag with `$env:AIPLATFORM_REF`), and opens an
interactive menu that launches the installer below. It runs non-elevated; the installer
self-elevates only for provisioning. *(Piping a remote script to `iex` runs whatever is at that URL
— read it first at the raw link, and point `AIPLATFORM_REF` at a tag for a pinned install.)*

**Manual — already cloned.** From an ordinary PowerShell in the repo:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1
```

Prefer to stay in the terminal? Add `-Console` for a colorful, in-terminal install — same flow,
no window (this is also option 2 in the one-liner's menu):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1 -Console
```

- **Prerequisites** — the doctor shows ✓/✗; **Install missing** runs winget for the gaps.
- **Super-admin** — set the username + password you'll log in with.
- **Rails** — Admin (always) + Terminal Fun (default) + Recipe Book (optional; ships with a seed
  corpus you can rebuild from the admin UI).
- **Install** — writes `deploy\.env`, drops in the lean `roles.json`, creates the broker venv,
  registers the `platform-broker` NSSM service (media off; Ollama runs on :11434 via its own app,
  so there's no second server to conflict with), `docker compose build && up` the bundled subset,
  then enables **Open :1111**.

Doctor only, no window: `powershell -File deploy\installer\install.ps1 -Check`.

When it finishes, the browser opens `http://platform.localhost:1111`; log in with the
super-admin you set.

## Safety

The installer **refuses to run if it detects an existing platform** (the `platform-broker`
service, `platform-*` containers, or a `deploy\.env`). Test it on a **clean machine or VM** — it
is not meant to be layered onto a box that already runs the full stack. (`-Force` overrides the
guard; don't, unless you know why.)

## What it builds (under `deploy/installer/`)

- `install.ps1` — the GUI + doctor + elevated provisioning.
- `install-native.ps1` — broker venv + `platform-broker` NSSM service (parameterized; `-InstallOllama`
  defaults on for the full stack, but the lean installer passes `-InstallOllama:$false` and relies on
  the Ollama app's own :11434 autostart).
- `Dockerfile.gateway.bundled` (in `deploy/`) — multi-stage image that **bakes** the shell +
  chosen rail frontends in (no host Node, no runtime bind-mounts).
- `docker-compose.installer.yml` — the bundled gateway + rail backends + caddy.
- `roles.lean.json` / `env.lean.example` / `Caddyfile` — the lean config templates.
