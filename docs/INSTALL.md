# Installing AI-Platform (lean)

A one-window GUI installer that stands up a **lean** platform on a modest box: the **admin
shell** + **Terminal Fun** + whichever of the **optional rails** you tick (listed under
[Run it](#run-it)), running on two small Ollama models
(`gemma3:4b` + `bge-m3`) with **no HuggingFace token and no image/TTS pipeline**. It's designed
for an **8 GB-VRAM** Windows machine.

> The full 24 GB stack (all rails, FLUX/XTTS media, HTTPS exposure) is a separate, manual setup:
> [`deploy/README.md`](../deploy/README.md) and [`deploy/INSTRUCTIONS.md`](../deploy/INSTRUCTIONS.md)
> cover its containers and the native broker, and the broker's media settings are in
> [`services/broker/README.md`](../services/broker/README.md). This installer deliberately targets
> a smaller, reproducible subset.

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

In WSL mode that address is the Windows end of the WSL network adapter, so a service the
containers reach there must listen beyond loopback. The broker does (it binds `0.0.0.0` in WSL
mode). So does the Ollama app on a WSL box: the installer sets `OLLAMA_HOST=0.0.0.0:11434` in your
user environment (the way Ollama's own FAQ sets it on Windows) and restarts the app if it is
running, so the two calls a container makes to Ollama itself work there: Admin → Models → Delete
(`PLATFORM_OLLAMA_URL`) and AI Playground's Embedding Lab pull and weekly re-pull
(`AI_PLAYGROUND_OLLAMA_URL`). Model calls go through the broker either way. Neither the broker nor
Ollama has a firewall-free reach: the elevated step's block rule, `AI-Platform: broker/ollama, block
all but WSL and loopback`, keeps every remote address but loopback and `172.16.0.0/12` off `:11500`
and `:11434` (Ollama has no authentication). A box installed before keeps its older
`AI-Platform: broker, block all but WSL and loopback` rule, a subset of the new one. If Ollama runs
as something other than its app (a service, or `ollama serve` in a terminal), the installer says so
and leaves it to you to restart it with that variable. `apply.ps1 -Execute` sets it too when run
unelevated. Its flow elevates once, up front, possibly as another account, so an elevated run adds
the block rule, sets nothing in your environment, and prints what to do: set
`OLLAMA_HOST=0.0.0.0:11434` for your account (Settings → "Edit environment variables for your
account") and restart the Ollama app, or run `apply.ps1 -Execute` again unelevated. Podman and
Docker Desktop boxes need none of this, and the installer changes nothing there.

**WSL's mirrored networking** (`networkingMode=mirrored` in `%USERPROFILE%\.wslconfig`) is not
supported. There the default route inside WSL is your LAN router, not Windows, and Microsoft's way
for Linux to reach Windows is `127.0.0.1`, which inside a container is the container itself; no
source documents an address that takes a container on a Docker bridge network to a Windows
service in that mode. The installer and the WSL logon script detect it and say so, and leave
`WINDOWS_HOST` as it was rather than write the router's address. Switch to NAT (`networkingMode=nat`,
then `wsl --shutdown`), or set `WINDOWS_HOST` in `deploy\.env` to an address you have checked from
inside a container.

The installer records the mode it set up as `PLATFORM_RUNTIME=` in `deploy\.env`. The startup
script, the smoke test, `apply.ps1`, the preflight board and `activate-model-roles.ps1` read that
line first, all through `Resolve-PlatformRuntime` (`deploy\installer\lib-runtime.ps1`), and only
infer the runtime (from which CLI lists `platform-gateway-1`) on a box that declares none. If
another runtime is running the gateway, they follow the line anyway and print a warning naming
both. A `-RuntimeMode` overrides the line, but not on every script and not with every value: the
smoke test and `apply.ps1` take `podman|desktop|wsl`, the startup script and
`activate-model-roles.ps1` take only `podman|desktop` (their `ValidateSet`s), and the preflight
board has no `-RuntimeMode` at all.

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
- **Rails** — Admin (always) + Terminal Fun (default), then the optional ones: Recipe Book (ships
  with a seed corpus you can rebuild from the admin UI), Co-Worker, SMB Partner Enablement,
  Gemini CX, Meeting Atlas, AI Playground (RAG demo + Embedding Lab) and OpenMAIC (needs the
  pre-built `openmaic-app` image). That list is `$OptionalRails` in `install.ps1`, and
  `tools/tests/test_doc_claims.py` fails when this sentence stops naming the same set. Every one
  of them is compiled into the gateway image whether or not you tick it, because the shell
  resolves its federated remotes at build time; ticking the box is what starts the backend and
  puts the tile in the nav. The other rails in `rails/` are full-stack only; they have no service
  in `docker-compose.installer.yml` and the installer cannot start them.
- **Install** — writes `deploy\.env`, drops in the lean `roles.json`, creates the broker venv,
  registers the `platform-broker` NSSM service (media off; Ollama runs on :11434 via its own app,
  so there's no second server to conflict with) + BrokerTray, builds and starts the bundled subset
  through the detected runtime, then enables **Open :1111**.
- **Podman mode also installs a logon startup shortcut** — Podman has no daemon, so `restart:
  always` needs something to bring the machine + stack back after a reboot. (Docker Desktop mode
  doesn't need this; its daemon handles it.)

Doctor only, no window: `powershell -ExecutionPolicy Bypass -File deploy\installer\install.ps1 -Check`.

Nothing opens by itself when it finishes. The window says "Install complete. Click "Open
:1111"", and that button opens `http://localhost:1111`; the console install prints the same
address. Log in with the super-admin you set. (`localhost` rather than `platform.localhost`,
because a managed browser can route `*.localhost` through a corporate proxy.)

## Models: check these against your card before you start the broker

`services/broker/roles.json` in this repo is the **24 GB** map. It points `@chat` at
`mistral-small3*:24b`, `@chat-large` / `@reasoning` / `@code` at `qwen3.6*:27b`, and `@vision` /
`@recipe` at `gemma4*:26b`. On a smaller card every role resolves to a model that neither fits nor
is installed: red "missing" chips on every rail, and roughly 60 GB of pulls that cannot help.

`install.ps1` handles this for you — it writes `deploy/installer/roles.lean.json` to the box's own
`deploy/local/roles.json` and registers the broker to read it there (`BROKER_ROLES_FILE`, with the
disabled-model list beside it in `deploy/local/disabled.json`), which puts everything on
`gemma3:4b` plus `bge-m3` (two pulls, about 4.5 GB). `deploy/local/` is git-ignored, so updating
the checkout never touches it, and Admin → Rails edits land there too. An install from before
2026-10-14 copied the lean map over the tracked `services/broker/roles.json` instead; `get.ps1`
refuses to update such a box and prints the three steps that move its map to `deploy/local/`.
**You only meet the problem if you start the broker without running the installer.** If you did,
size the map yourself:

```powershell
deploy\installer\modelplan.ps1 -VramGb 8            # what fits, and what it would pull
deploy\installer\modelplan.ps1 -Rails recipe-book,terminal-fun -Json
```

Two caveats worth knowing before you pick:

- **`roles.lean.json` is sized for the rails the installer offers**, not for every rail in the
  tree. Every chat and embed role the optional rails declare is in it, `@ai-playground` and
  `@openmaic` included; `@recipe-icon` is the one absent, and nothing calls it on a lean box
  (see the next caveat). Bring up a rail from outside that set (`edu-suite`, `iep`, `finance`,
  `bouquet` or `job-aid`, for instance) and its role is absent from the lean file, so it falls
  through to the 24 GB `DEFAULT_ROLES` pin
  underneath (`services/broker/app/config.py`, `roles()` merges the overlay *over* the defaults).
  That pin is not installed on a lean box, so every call to it runs on a capability-preserving
  substitute — `gemma3:4b`, the only heavy model the installer pulled — and the broker writes a
  `MODEL FALLBACK` line for each one. Degraded and loud rather than broken, but still not the
  model the rail was tuned against. `modelplan.ps1` covers whatever rail set you pass it, so
  prefer it over copying the lean file when your set differs.
- **Image generation has no verified path below 12 GB.** `@recipe-icon` defaults to
  `flux-schnell`, which `model-catalog.json` floors at 12 GB; the 8 GB alternative, `sdxl-turbo`,
  is still marked `estimated` because nobody has measured it on a small card. This costs you
  nothing on a lean install: the media pipeline is off (`BROKER_MEDIA_ENABLED=false`) and
  recipe-book's icons ship pre-rendered in its seed, so nothing calls the role.

## Running the tests

`run-tests.ps1` needs a venv that the installer does not create, because the installer builds a
broker venv for the running service rather than a development one. Do not reuse that venv for
tests: it is the interpreter the `platform-broker` NSSM service runs from. `run-tests.ps1` picks
`.venv-test` first and falls back to `.venv` (its `$VenvNames`), so a dev box with only `.venv`
runs as before.

```powershell
python -m venv .venv-test
.venv-test\Scripts\python.exe -m pip install -e packages\platform_core -e services\broker -e apps\platform\backend
.venv-test\Scripts\python.exe -m pip install pytest jsonschema httpx2 pyyaml
.\run-tests.ps1 -Doctor      # every rail's declared dependencies; prints a pip line for the missing
#   run the `pip install ...` line it printed, then -Doctor again until it exits 0
.\run-tests.ps1              # core + every rail, 3-6 min (measured 180-330 s)
```

The third line is the test tooling no manifest declares: `pytest`; `jsonschema`, without which
the manifest-schema test skips; `httpx2`, the backend Starlette's `TestClient` prefers, so without
it every TestClient suite warns (runtime code keeps using `httpx`); and `pyyaml`, which the publish
gate and the rebuild-plan test use to read compose files. Everything else comes
from the manifests. `-Doctor` reads every `pyproject.toml` (core's and each rail's), checks each
declared dependency is installed, at the declared version and importable, reports by rail, and
prints one `pip install` line for what is missing. Run it again after any `git pull` that touched
a rail: a rail whose dependency is missing fails at pytest collection, and the plain run reports
that as an ordinary test failure.

Some declared dependencies stay out of this venv on purpose, and `-Doctor` lists them as
`absent (by design: ...)` with the reason. The GPU media stack (torch, torchaudio, transformers,
TTS, diffusers, accelerate) is the important one. Never install it here: doing so once upgraded
numpy and broke every image path, and the tests stub it. A version mismatch is reported but kept
out of the pip line, for the same reason. Resolve it by hand.

`-Doctor` also prints where `platform_core` and `platform_gateway_app` import from, because editable
installs record absolute paths and go stale silently when a checkout moves. That failure once
went unnoticed for three days, with no suite running at all.

`run-tests.ps1` exits 0 when every target ran and passed, 1 when one failed, and 2 when nothing
failed but a target checked nothing: every test deselected, no test found, or a node suite on a
box without `node`.

## Other tools in `tools/`

Three standalone scripts, stdlib or close to it, each with its usage in its own header:

- **`tools/venv_doctor.py`** is what `run-tests.ps1 -Doctor` runs (`--rail <id>` narrows it). With
  `--requirements` it prints every declared dependency the tests need as one requirements list,
  for building a test venv from scratch in a CI job.
- **`tools/deploy_snapshot.py`** records what a running deployment serves: every enabled app's
  status route through the gateway with a real admin session, the broker's `/v1/roles`, and each
  running container's image. `--env <deploy\.env> --out after.json --diff before.json` lists what
  changed and exits 1 if anything did, so it can gate a redeploy. Credentials come from the env
  file, and values that name a credential are written as `<redacted>`, but a snapshot is still
  deployment detail: keep it on the box.
- **`tools/check_commit_domains.py`** refuses commits whose author or committer email domain is
  outside its allowlist (`ALLOWED_DOMAINS`), judging only the commits between a base and a head:
  `python tools/check_commit_domains.py <base-sha> <head-sha>`. A CI job or a pre-push hook can run
  it over a push's range (the public snapshot ships no workflow that does). Edit the allowlist for
  your own domains first.

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
  `-SkipOllama` leaves Ollama to its own app, which the lean installer uses). The venv is
  installed from `services/broker/pyproject.toml`, and a re-run reinstalls it whenever the broker
  does not import with it; `-PlanOnly` lists what it installs.
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
