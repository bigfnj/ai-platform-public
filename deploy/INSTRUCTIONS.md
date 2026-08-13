# Deploy the platform (interactive terminal) — quick runbook

This brings up the unified shell (EDU-Suite federated in) behind Caddy as
containers, with the GPU/media layer running **native** on Windows. Run these on
the workstation in a normal PowerShell (the Docker credential helper needs an
interactive logon session, which the agent shell doesn't have).

Frontends are already built and the code is on `main`. You just start the native
broker and bring up the containers.

---

## 0. Prereqs (usually already true)

- **Docker Desktop is running** (engine started). If not, launch it and wait until
  the whale icon is steady.
- **Ollama is running** (native, `localhost:11434`).

---

## 1. Terminal 1 — start the broker (native, on 0.0.0.0)

`0.0.0.0` is required so the containers can reach it via `host.docker.internal`.
Leave this running.

```powershell
cd D:\.claude\projects\platform
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500
```

Sanity check (new terminal): `curl http://127.0.0.1:11500/healthz` should return `ok`.

---

## 2. Terminal 2 — build + start the stack

```powershell
cd D:\.claude\projects\platform\deploy
docker compose up -d --build
```

First run pulls `python:3.11-slim` + `caddy:2` and installs deps, so give it a few
minutes. When it finishes:

```powershell
docker compose ps          # all three (dashboard, gateway, caddy) should be "running"/"up"
```

---

## 3. Open it

- From this machine: **https://platform.localhost**
  (accept the local-CA cert warning — Caddy uses its own internal CA).
- Prefer no cert prompt for a quick look? Edit `Caddyfile` and change
  `platform.localhost {` to `http://platform.localhost {`, then
  `docker compose restart caddy`, and use **http://platform.localhost**.

You should see the shell with EDU-Suite on the left rail. Pick **Just Translate**,
upload a small `.txt`, **Start job**, and watch it finish through the broker.

---

## Gotchas

- **Broker must be on `0.0.0.0`** (step 1), not `127.0.0.1`, or the containers
  can't reach it and jobs fail with a "broker unreachable" error.
- **Drive share:** the job library bind-mounts `D:\edu-suite-library`. If Docker
  says the mount is denied, share the `D:` drive in Docker Desktop → Settings →
  Resources → File sharing (or set `$env:EDU_LIBRARY_HOST` to a shared path before
  `docker compose up`).
- **Blank content / "coming soon" for an app?** The mounted frontend dists may
  be stale (or never built). Rebuild them, then `docker compose restart gateway`:
  ```powershell
  cd apps/platform/frontend ; npm run build
  cd rails/edu-suite/apps/dashboard/frontend ; npm run build
  ```

---

## Useful commands

```powershell
docker compose logs -f            # tail all logs (Ctrl+C to stop tailing)
docker compose logs gateway       # just the gateway
docker compose down               # stop + remove the containers (keeps volumes)
```

---

## Survive reboot: install the native services (optional, run elevated)

The containers already auto-restart via Docker Desktop. To also bring up the native
GPU layer (broker + Ollama) after a reboot without logging in, install them as
services (as **you**, so your models/caches are found):

```powershell
# In an ELEVATED (Administrator) PowerShell:
powershell -ExecutionPolicy Bypass -File D:\.claude\projects\platform\deploy\install-services.ps1
```

It fetches NSSM automatically and installs the services as **LocalSystem** — no
password (the Admin account here is passwordless, which Windows refuses to use as a
service account). It points them at your profile's models/caches via env vars
(`OLLAMA_MODELS`, `HF_HOME`). One caveat: Coqui XTTS has no cache env override, so its
~2GB model re-downloads once under the service profile on first use, then stays cached.
Then:
- Verify: `curl http://127.0.0.1:11500/healthz`
- Manage: `nssm restart platform-broker`, `nssm status platform-broker`; logs in `deploy\logs`.
- If the media worker has GPU trouble under the service (Session 0), the fallback is
  running the broker in your logged-on session instead.

**Ollama note:** if Ollama already starts on boot, disable that first (Task Manager
→ Startup apps → Ollama → Disable) or run the script with `-SkipOllama`,
or you'll get two servers fighting over port 11434.

## Users & access (multi-tenant)

The gateway requires login and shows each user only the apps they're entitled to.

- **First-run admin.** On first bring-up (empty auth DB) an admin is seeded. Set
  `PLATFORM_ADMIN_USER` / `PLATFORM_ADMIN_PASSWORD` in `deploy/.env` beforehand, or
  leave the password blank and read the generated one once from
  `docker compose logs gateway`. Change it after first login.
- **Managing users.** Log in as an admin and open the **⚙ Admin** entry in the left
  rail: create users, set/reset passwords, tick which apps each may see, grant/revoke
  admin. Admins see every app; everyone else sees only their entitled apps.
- **Enforcement is server-side.** Hiding an app from a user's rail is backed by the
  gateway — a user with no entitlement gets 403 on both that app's API
  (`/<app>/api/*`) and its bundle (`/<app>/…`), so the block is real, not cosmetic.
- **The auth DB** lives in the `gateway_data` Docker volume (survives rebuilds).

## HTTPS: when to turn it on

Caddy currently serves plain **http://platform.localhost**. On this machine that's
fine — browsers treat `localhost` as a secure context, so nothing is degraded.

Turn HTTPS on the moment any of these becomes true:
- **Another device on the LAN uses it** — otherwise uploads (worksheets/PII) and,
  later, login cookies cross the network in cleartext, readable by anything on the wire.
- **You expose the login publicly** — the platform now has login; its session cookie
  must travel over HTTPS with the `Secure` flag (set `PLATFORM_COOKIE_SECURE=true`) or
  it can be sniffed and replayed.
- **You expose it beyond the LAN** (Cloudflare Tunnel/Access, a domain) — HTTPS is
  mandatory there; Cloudflare/OAuth refuse plain HTTP.

It's near-zero cost to flip with Caddy:
1. In `deploy/Caddyfile`, change `http://platform.localhost {` back to
   `platform.localhost {` (Caddy then auto-issues + auto-renews an internal-CA cert),
   or to your real domain for a publicly-trusted cert.
2. Set `PLATFORM_COOKIE_SECURE=true` in `deploy/.env` so the session cookie is marked
   `Secure` (keep it false on plain-http localhost, or the cookie won't be sent back).
3. `docker compose up -d gateway caddy`
4. For other LAN devices to trust an internal-CA cert, export Caddy's root CA:
   ```powershell
   docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .\caddy-root.crt
   ```
   Install `caddy-root.crt` into each device's "Trusted Root Certification Authorities".
   (On a real public domain instead, Caddy fetches a normally-trusted cert
   automatically — no CA import needed.)

## If anything errors

Copy the failing command's output (or `docker compose logs`) back to me and I'll
sort it. Once it's up, tell me and I'll verify the running stack (endpoints +
a real job end-to-end through `https://platform.localhost`).
