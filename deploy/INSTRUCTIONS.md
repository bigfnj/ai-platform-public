# Deploy the platform (interactive terminal) — quick runbook

This brings up the unified shell (EDU-Suite federated in) behind Caddy as
containers, with the GPU/media layer running **native** on Windows. Run these on
the workstation in a normal PowerShell (the Docker credential helper needs an
interactive logon session, which the agent shell doesn't have).

Frontends are already built and the code is on `main`. You just start the native
broker and bring up the containers. `<repo>` below is your checkout of this repository.

---

## 0. Prereqs (usually already true)

- **Docker Desktop is running** (engine started). If not, launch it and wait until
  the whale icon is steady.
- **Ollama is running** (native, `localhost:11434`).

---

## 1. Terminal 1 — start the broker (native, on 0.0.0.0)

`0.0.0.0` is for the LAN: another machine using this broker as its upstream reaches it there.
The containers do not need it, because Docker Desktop delivers `host.docker.internal` traffic
from 127.0.0.1 (measured 2026-10-08; `Get-BrokerBindHost` in `installer/lib-runtime.ps1` has the
detail per runtime); bind `127.0.0.1`
when nothing on the LAN uses this broker. Leave this running.

```powershell
cd <repo>
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --app-dir services\broker --host 0.0.0.0 --port 11500
```

Sanity check (new terminal): `curl http://127.0.0.1:11500/healthz` should return `ok`.

---

## 2. Terminal 2 — build + start the stack

One service at a time, never a bare full-stack `up`: that recreates caddy, which rebinds the host
port and churns the Windows NAT (the caddy `ports` comment in `docker-compose.yml`).
`rebuild-stale.ps1` builds and starts every service compose builds, one at a time with
`--no-deps`; it leaves pulled images alone (`caddy`, `openmaic-app`; its header), so they are
started once by name. `openmaic-app` has no `build:` stanza: build or pull its image first and
name the tag in `OPENMAIC_APP_IMAGE`.

```powershell
cd <repo>\deploy
.\rebuild-stale.ps1                                   # every built service, one at a time
docker compose up -d --no-deps caddy openmaic-app     # once: the pulled images
```

The first run pulls the base images and installs deps, so give it a few minutes. When it
finishes:

```powershell
docker compose ps          # every service should be "running"/"up"
```

---

## 3. Open it

- From this machine: **http://localhost:1111** (or `http://platform.localhost:1111`). Plain HTTP,
  so there is no certificate to accept: compose publishes Caddy's port 80 as host 1111 and no 443
  (`docker-compose.yml`, caddy `ports`), and every site in `Caddyfile` is an `http://` address.

You should see the shell with its rails down the left. Open one and use it once (ask a rail's
assistant something, say) and watch the call go through the broker.

---

## Gotchas

- **Under Docker Desktop the containers reach the broker on `127.0.0.1` or `0.0.0.0`**
  (`Get-BrokerBindHost` in `installer/lib-runtime.ps1` says why per runtime); only a Docker
  engine inside WSL needs `0.0.0.0` for its containers. If jobs fail with a "broker unreachable" error, check that the broker
  from step 1 is running.
- **Drive share (edu-suite only; the public snapshot withholds that rail, and the compose file
  there has no `dashboard` service):** the job library bind-mounts `D:\edu-suite-library`.
  If Docker says the mount is denied, share the `D:` drive in Docker Desktop → Settings →
  Resources → File sharing (or set `$env:EDU_LIBRARY_HOST` to a shared path before
  (re)creating the `dashboard` service, `docker compose up -d --no-deps dashboard`).
- **Blank content / "coming soon" for an app?** The mounted frontend dists may
  be stale (or never built). Rebuild them, then `docker compose restart gateway`: the shell's,
  and each rail's frontend whose dist the gateway mounts (its `volumes` in `docker-compose.yml`
  name every one). The shell, for example:
  ```powershell
  cd <repo>\apps\platform\frontend ; npm run build
  ```

- **Rail data in named volumes** (job-aid's `job_aid_data`, for one; the public snapshot
  withholds that rail): a rail's database and uploads live on its compose volume, seeded once
  from the host. New data stays in the volume; to reseed or pull it back, use `docker cp` / a
  one-shot `docker run -v <volume>:/data ...`.

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
powershell -ExecutionPolicy Bypass -File <repo>\deploy\install-services.ps1
```

It fetches NSSM automatically and installs the services as **LocalSystem** — no
password (the Admin account here is passwordless, which Windows refuses to use as a
service account). The Course Builder service is installed only where the checkout carries
that rail (`rails\course-builder`); without it the script says so and skips that service,
and `-SkipCourseBuilder` skips it anywhere. Likewise the broker's voice engines are pointed
at `rails\ai-voice\native` only where it exists. It points them at your profile's models/caches via env vars
(`OLLAMA_MODELS`, `HF_HOME`), and Coqui XTTS at your `%LOCALAPPDATA%` through `TTS_HOME`, which
it needs: Coqui finds its model folder through a per-user registry value LocalSystem does not
have, so without it the speech worker dies before it loads. With it, the ~2 GB XTTS model you
already downloaded is reused, not fetched again.
Then:
- Verify: `curl http://127.0.0.1:11500/healthz`
- Manage: `nssm restart platform-broker`, `nssm status platform-broker`; logs in `deploy\logs`.
  NSSM 2.24 rotates them at 10 MB but cannot prune the rotated copies, and this stack runs no
  watchdog, so `rebuild-stale.ps1` prunes them to the newest ten of each, at most once a day, when
  it runs (as does `installer\smoke-test.ps1`, every run).
- If the media worker has GPU trouble under the service (Session 0), the fallback is
  running the broker in your logged-on session instead.

**Lean box note:** on a box set up by the lean installer (`deploy\installer\install.ps1`), whose
broker runs with media off, the script registers the broker the way that installer does: bound
to loopback outside WSL mode, `BROKER_MEDIA_ENABLED=false`, its role map and disabled list in
`deploy\local\`, so `-RolesFile` / `-DisabledFile` are refused there (`-Stack full|lean` overrides
the detection). Pass `-SkipOllama -SkipCourseBuilder` there to touch the broker alone.

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

## HTTPS: when it matters, and where it comes from

Caddy serves plain HTTP, at **http://localhost:1111** and `http://platform.localhost:1111` on
this machine. That is fine here: browsers treat `localhost` as a secure context, so nothing is
degraded.

HTTPS is required the moment any of these becomes true:
- **Another device on the LAN uses it** — otherwise uploads (worksheets/PII) and,
  later, login cookies cross the network in cleartext, readable by anything on the wire.
- **You expose the login publicly** — the platform now has login; its session cookie
  must travel over HTTPS with the `Secure` flag (set `PLATFORM_COOKIE_SECURE=true`) or
  it can be sniffed and replayed.
- **You expose it beyond the LAN** (Cloudflare Tunnel/Access, a domain) — HTTPS is
  mandatory there; Cloudflare/OAuth refuse plain HTTP.

As shipped, Caddy terminates no TLS: compose publishes only host port 1111 to Caddy's port 80
and no 443 (`docker-compose.yml`, the caddy `ports` comment), and every site in `Caddyfile` is an
`http://` address. HTTPS can come from either of two places.

**A tunnel** (Cloudflare Tunnel, or a service like it) needs no change to Caddy's ports. TLS ends
at the tunnel's edge, and the tunnel forwards plain HTTP to port 1111:
1. Point the tunnel's public hostname at port 1111 on this machine (`http://localhost:1111` when
   the tunnel's agent runs here), and list that hostname as an `http://` address on the
   `Caddyfile` site that reverse-proxies to `gateway:8700`.
2. To send other LAN devices there, use the `Caddyfile`'s redirect block, which 308-redirects
   the bare-HTTP LAN origins it names to the public hostname: put your own names and hostname in
   it, or delete it.

**Caddy's own certificate**, for the LAN without a tunnel:
1. In `deploy/Caddyfile`, serve the machine's LAN hostname as an `https://` site that
   reverse-proxies to `gateway:8700`, and take that name out of the redirect block. The global
   `local_certs` option makes Caddy issue the site's certificate from its internal CA, and renew
   it.
2. Publish Caddy's port 443 with a mapping such as `- "8443:443"` under the caddy `ports` in
   `docker-compose.yml`, then recreate caddy once, alone: `docker compose up -d --no-deps caddy`.
   That rebinds its host ports and can briefly drop the Windows NAT, so pick a quiet moment.
   Then browse to `https://<lan-hostname>:8443`.
3. For other devices to trust the certificate, export Caddy's root CA:
   ```powershell
   docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .\caddy-root.crt
   ```
   and install `caddy-root.crt` in each device's "Trusted Root Certification Authorities".

A publicly trusted certificate from Caddy itself needs `local_certs` removed and Caddy reachable
from the internet on port 80 or 443 for the ACME challenge.

Either way:
1. Set `PLATFORM_COOKIE_SECURE=true` in `deploy/.env` so the session cookie is marked `Secure`,
   then recreate the gateway alone: `docker compose up -d --no-deps gateway`.
2. Local use keeps working at `http://localhost:1111`: for plain HTTP to a loopback host
   (`localhost`, `127.0.0.1`, `::1`) the gateway issues a non-Secure cookie anyway
   (`apps/platform/backend/platform_gateway_app/main.py`, `_cookie_secure_for`).

## If anything errors

Start with the failing command's own output, then `docker compose ps` for any service that is not
running and `docker compose logs <service>` for that service. Once the stack is up, check it end
to end: `curl http://127.0.0.1:11500/healthz` for the broker, then log in at
`http://localhost:1111` and run one real job through a rail.
