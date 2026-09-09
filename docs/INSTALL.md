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
| **A container runtime** — Podman *or* Docker Desktop *or* Docker Engine in WSL2 | runs the shell + rail containers | winget / WSL |
| **Ollama** | the LLM host (native on Windows) | winget |
| **Python 3.11+** | the torch-free broker venv only | winget |
| ~20 GB free disk | images + two models | — |
| ~2 GB free **system RAM** at logon | the Podman VM's startup reservation | — |

No Node.js is needed — the frontends are built inside the container image. No HuggingFace token —
there's no media pipeline. Recipe icons ship pre-rendered in the seed.

**Container runtime (tri-mode).** The installer auto-detects, in preference order:

| Mode | When it's chosen | How it's driven |
|---|---|---|
| `podman` | `podman` is on PATH | daemonless; Linux containers run in a `podman machine` VM (Hyper-V provider, which keeps WSL out of the runtime path entirely). Driven with the standalone `docker-compose.exe` over Podman's Docker-compatible API pipe. |
| `desktop` | Docker Desktop is installed | native `docker compose` |
| `wsl` | WSL2 present, no Windows Docker | `wsl docker compose`; the installer can install the engine into WSL for you |

Each mode resolves the container→host address differently, which is why `extra_hosts` is
`host.docker.internal:${WINDOWS_HOST:-host-gateway}` everywhere. `host-gateway` is a literal only
Docker Desktop resolves. Podman uses **gvproxy** (`192.168.127.254`), which dials from the host's
own loopback — so containers reach a `127.0.0.1`-bound Ollama with **no inbound firewall rule**.
WSL mode uses the dynamic default-route gateway and *does* need the rule, which the elevated
broker-service step adds (scoped to the private WSL/Docker range). Force a mode with
`-RuntimeMode podman|desktop|wsl`.

**A note on RAM (Podman/Hyper-V).** The container VM uses Hyper-V dynamic memory, and Hyper-V must
reserve the whole *startup* allocation before the VM will boot. `podman machine init --memory 8192`
sets startup **and** maximum to 8 GB, which fails outright on a machine whose RAM is already
committed — and since that happens at logon, the platform is simply absent with no visible error.
The installer therefore caps the startup reservation at 2 GB and leaves the ceiling at 8 GB, so the
VM boots under memory pressure and still balloons up under load. To check or change it by hand:

```powershell
Get-VMMemory -VMName podman-machine-default
Set-VMMemory -VMName podman-machine-default -StartupBytes 2GB -MinimumBytes 512MB -MaximumBytes 8GB
```

## Run it

Clone the repo, then from an ordinary PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1
```

Prefer to stay in the terminal? Add `-Console` for an in-terminal install — same flow, no window:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1 -Console
```

- **Prerequisites** — the doctor shows ✓/✗; **Install missing** runs winget for the gaps.
- **Super-admin** — set the username + password you'll log in with.
- **Rails** — Admin (always) + Terminal Fun (default), then five optional: Recipe Book (ships
  with a seed corpus you can rebuild from the admin UI), Co-Worker, SMB Partner Enablement,
  Gemini CX, and Meeting Atlas. Every one of them is compiled into the gateway image whether or
  not you tick it, because the shell resolves its federated remotes at build time; ticking the box
  is what starts the backend and puts the tile in the nav.
- **Install** — writes `deploy\.env`, drops in the lean `roles.json`, creates the broker venv,
  registers the `platform-broker` NSSM service (media off; Ollama runs on :11434 via its own app,
  so there's no second server to conflict with) + BrokerTray, builds and starts the bundled subset
  through the detected runtime, then enables **Open :1111**.
- **Podman mode also installs a logon startup shortcut** — Podman has no daemon, so `restart:
  always` needs something to bring the machine + stack back after a reboot. (Docker Desktop mode
  doesn't need this; its daemon handles it.)

Doctor only, no window: `powershell -File deploy\installer\install.ps1 -Check`.

When it finishes, the browser opens `http://platform.localhost:1111`; log in with the
super-admin you set.

## Models: check these against your card before you start the broker

`services/broker/roles.json` in this repo is the **24 GB** map. It points `@chat` at
`mistral-small3*:24b`, `@chat-large` / `@reasoning` / `@code` at `qwen3.6*:27b`, and `@vision` /
`@recipe` at `gemma4*:26b`. On a smaller card every role resolves to a model that neither fits nor
is installed: red "missing" chips on every rail, and roughly 60 GB of pulls that cannot help.

`install.ps1` handles this for you — it copies `deploy/installer/roles.lean.json` over the file,
which puts everything on `gemma3:4b` plus `bge-m3` (two pulls, about 4.5 GB). **You only meet the
problem if you start the broker without running the installer.** If you did, size the map yourself:

```powershell
deploy\installer\modelplan.ps1 -VramGb 8            # what fits, and what it would pull
deploy\installer\modelplan.ps1 -Rails recipe-book,terminal-fun -Json
```

Two caveats worth knowing before you pick:

- **`roles.lean.json` is sized for the rails the installer offers**, not for every rail in the
  tree. If you enable `edu-suite` or `ai-playground` by hand, their `@edu` and `@ai-playground`
  roles are not in it and their first model call fails. `modelplan.ps1` covers whatever rail set
  you pass it, so prefer it over copying the lean file when your set differs.
- **Image generation has no verified path below 12 GB.** `@recipe-icon` defaults to
  `flux-schnell`, which `model-catalog.json` floors at 12 GB; the 8 GB alternative, `sdxl-turbo`,
  is still marked `estimated` because nobody has measured it on a small card. This costs you
  nothing on a lean install: the media pipeline is off (`BROKER_MEDIA_ENABLED=false`) and
  recipe-book's icons ship pre-rendered in its seed, so nothing calls the role.

## Running the tests

`run-tests.ps1` needs a venv that the installer does not create, because the installer builds a
broker venv for the running service rather than a development one. Do not reuse that venv for
tests: it is the interpreter the `platform-broker` NSSM service runs from.

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e packages\platform_core -e services\broker -e apps\platform\backend
.venv\Scripts\python.exe -m pip install pytest
.\run-tests.ps1 -Doctor      # confirms the three packages import
.\run-tests.ps1              # core + every rail, roughly 100 s
```

`-Doctor` exists because editable installs record absolute paths and go stale silently when a
checkout moves. That failure once went unnoticed for three days, with no suite running at all.

## Breaking changes when updating an existing install

Your `deploy\.env` is not tracked, so an update never touches it. These three changed underneath it:

- **`MEETING_ATLAS_MEETILY_DB_MOUNT` now names the database FILE, not the directory holding it**,
  and its partner `MEETING_ATLAS_MEETILY_DB_IN_CONTAINER` is gone. An `.env` carrying the old
  directory form will bind a directory onto a file path. Point it at the `.sqlite` itself and
  delete the second variable.
- **`co-worker`'s internal port moved 8860 to 8890.** Consistent within compose, so a full rebuild
  is fine; a partial `up` against a gateway built before the move is not.
- **`RECIPE_BOOK_LLM_MODEL` is gone.** Every call site passes an explicit `@role`, so the fallback
  was unreachable. An entry left in your `.env` is inert, not harmful.

## Safety

The installer **refuses to run if it detects an existing platform** (the `platform-broker`
service, `platform-*` containers, or a `deploy\.env`). Test it on a **clean machine or VM** — it
is not meant to be layered onto a box that already runs the full stack. (`-Force` overrides the
guard; don't, unless you know why.)

## What it builds (under `deploy/installer/`)

- `install.ps1` — the GUI/console front-end + doctor + elevated provisioning.
- `lib-runtime.ps1` — the container-runtime abstraction shared by the installer and the startup
  script: podman/desktop/wsl dispatch (`Invoke-Compose`, `Invoke-VolumeCli`), podman machine
  bring-up and recovery, container→host address resolution, and the atomic `.env` writer.
- `install-native.ps1` — broker venv + `platform-broker` NSSM service (parameterized;
  `-SkipOllama` leaves Ollama to its own app, which the lean installer uses).
- `platform-watchdog.ps1` / `register-watchdog.ps1` / [`WATCHDOG.md`](../deploy/installer/WATCHDOG.md)
  — an optional NSSM/LocalSystem service that owns the platform lifecycle: boot start with no
  logon, healthz polling with a two-strike restart, and a session handoff so the podman machine
  stays owned by the logged-on user. **For Podman/WSL installs**; a Docker Desktop box does not
  need it (its daemon already restarts containers). Not registered by the installer — run
  `register-watchdog.ps1` deliberately, after reading `WATCHDOG.md`.
- `platform-startup.ps1` / `.sh` / `-launcher.vbs` — logon startup for the daemonless runtimes
  (Podman via a Startup-folder shortcut; WSL keeps the VM alive).
- `Dockerfile.gateway.bundled` (in `deploy/`) — multi-stage image that **bakes** the shell +
  chosen rail frontends in (no host Node, no runtime bind-mounts).
- `docker-compose.installer.yml` — the bundled gateway + rail backends + caddy.
- `roles.lean.json` / `env.lean.example` / `Caddyfile` — the lean config templates.
