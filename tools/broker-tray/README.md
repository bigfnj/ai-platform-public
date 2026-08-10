# BrokerTray

A lightweight system-tray status/control for the platform GPU/Model Broker. It runs
in your interactive session (a Windows service can't show a tray icon — Session 0
isolation) and talks only to the broker's HTTP API, so it controls Ollama and the
media models uniformly. Auto-polls every 15 seconds; no manual refresh.

## Icon

The Ollama llama (from uxwing), with its muzzle as the status light:

- **Green nose** — a model is loaded (ready in VRAM)
- **Yellow nose** — Ollama is up but no model is loaded (cold)
- **Red nose** — the broker or Ollama is unreachable

The linework follows the Windows taskbar theme (black on a light taskbar, white on a
dark one). Hover for a tooltip; double-click to open the dashboard.

The 6 icons (2 themes x 3 states) are baked into the exe from `icons/*.png`. To
regenerate them from `ollama-icon.svg` after tweaking colors/geometry:
`<DevToolbox venv python> render_icons.py` (needs Playwright).

## Right-click menu

- **⚠ warning line** (top, only when something needs attention): broker unreachable,
  Ollama down, or no models installed. Also pops a fading toast when a warning first
  appears (not repeated every poll).
- **⬆ Update Ollama to vX (on next restart)** (only when a newer release exists) — stages a
  **hands-off** update instead of sending you to the download page: it downloads the exact
  `OllamaSetup.exe` in the background and registers a one-shot **SYSTEM "at startup"** scheduled
  task that, on the next boot (when Ollama isn't busy), stops the `ollama` service, silent-installs
  (Inno Setup `/VERYSILENT /SUPPRESSMSGBOXES /DIR=<installdir>`), restarts the service, and
  self-cleans. No manual MSI, no closing apps, no relaunch. While staged the menu shows
  "✓ Ollama update staged — installs on next restart" with a **Cancel** option. Release check runs
  once at startup via GitHub. Logic is in `update-ollama.ps1`; logs to
  `%ProgramData%\BrokerTray\ollama-update\update.log`.
- **Unload now** — evict the loaded model(s) from VRAM.
- **Load model ▸** — every installed model; a checkmark marks the loaded one(s).
- **Unload after ▸** — 5 / 15 / 30 / 60 minutes / Never (the broker keep-alive);
  the choice is checkmarked and applied to the loaded model immediately.
- **Autorun (start at login)** — checkbox; creates/removes a Startup-folder shortcut.
- **Open dashboard**, **Exit**.

## Resolved 2026-07-27 (post-monorepo-cutover fixes)

- **Dead dashboard link** — the URL was hardcoded to `http://platform.localhost`, but caddy now
  maps host **1111**. Fixed by making the URL **runtime-editable**: menu → *Edit dashboard
  location…* (persisted to `HKCU\Software\BrokerTray`, env `DASHBOARD_URL` fallback, default
  `http://platform.localhost:1111`). No rebuild needed to change it again.
- **Tray blind to media (FLUX/SDXL/XTTS) work** — those run in the short-lived worker and never
  appear in Ollama's `ps`, so the nose light read "no model" while the GPU was busy rendering.
  Fixed: broker `/v1/status.media.active` now reports `{op, model}` for the in-flight media job;
  the tray shows a greyed "model · op (rendering…)" row at the top and turns the nose green.

## Resolved 2026-08-03 (seamless Ollama update)

- **Updating Ollama used to mean manual friction** — download a 1.5 GB MSI, let the installer nag
  about closing `ollama`/`explorer`, install, and hope the NSSM service came back. The **⬆ Update
  Ollama** item now does it hands-off (see the menu section above + `update-ollama.ps1`): background
  download → one-shot SYSTEM at-startup task → stop service, silent-install, restart on next boot.
  Verified the install-dir resolution (from the service's `Parameters\Application`) and the SYSTEM
  at-startup task registration on this box.
  - *Minor remaining niceties (low priority now):* the release check still runs only at startup, and
    the 15s status poll could reconnect more gracefully if Ollama restarts mid-session.

## Build (no .NET SDK needed)

```powershell
powershell -ExecutionPolicy Bypass -File tools\broker-tray\build.ps1
```

Produces `BrokerTray.exe` next to the source (built-in .NET Framework `csc`).

## Run

Double-click `BrokerTray.exe`. It reads `BROKER_URL` (default `http://127.0.0.1:11500`).
For authenticated brokers it reads `BROKER_AUTH_TOKEN` from the environment, falling back to the
repo-local `deploy/.env`, and sends it only to broker `/v1/*` requests.
Toggle **Autorun** in the menu to have it start at login — it replaces the Ollama
tray you disabled, and the headless broker/Ollama services keep serving before login.
