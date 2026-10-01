# Workstation — hardening plan

The Workstation rail is the highest-value target on the platform: an authenticated browser
session becomes a live shell on the host, as `Admin`. It already inherits the platform's real
defenses (Cloudflare Access → gateway login → per-app entitlement → SSH-client-only, no
published SSH port). This document is the plan to close the residual gaps.

Each item lists **why**, the **current state** (grounded in the code), the **change**, the
**files** it touches, rough **effort**, and whether it needs a **deploy** (and which service).
Deploys follow the platform rule: rebuild only the changed service, never a full-stack `up`.

Threat model in one line: an attacker who has *already* cleared Cloudflare Access and the
gateway login (a stolen admin session, a malicious page in an admin's browser, or a
network-position attacker inside the Docker net). Everything below is defense-in-depth for
"the perimeter was breached or bypassed."

**Owner intent (2026-07-25):** this rail exists to give remote SSH into the box *from the
internet*, gated by Cloudflare Access rather than an exposed port. So the plan keeps it
internet-reachable and hardens the gate, rather than locking it to the LAN.

## Implementation status (2026-07-25 — DEPLOYED + LIVE-VERIFIED)

Shipped: shell dist rebuilt, gateway + workstation rebuilt/recreated (caddy untouched), the
admin super-admin migration ran, and P0.1 host-key pinning is live. Verified end to end on
the running platform. Changes are in the working tree (not yet committed).

| Item | State |
|------|-------|
| P0.2 loud insecure-flag warning + healthz field | ✅ live (warning fired; healthz `insecure_host_key`) |
| P1.3 idle + absolute session timeout | ✅ live (watchdog) |
| P2.1 audit log (rotating, 30-day retention) | ✅ live (connect/disconnect lines written) |
| P1.1 three roles + decouple + delegation | ✅ live (admin super; limited admin can't grant workstation) |
| P1.2 WS Origin allowlist | ✅ live (cross-site handshake rejected) |
| P1.4 de-root container (read-only, cap_drop) | ✅ live (non-root appuser uid 10001) |
| P0.1 pin SSH host key + turn off insecure skip | ✅ live (known_hosts pinned; real connect verified; skip OFF) |
| P2.4 tighten Cloudflare Access policy | ⏳ owner task (dashboard) |
| P2.2 least-priv SSH landing account | ⏸ declined for now (daily-driver friction) |

---

## P0 — quick wins (low effort, high value, do first)

### P0.1 — Pin the SSH host key (`known_hosts`), drop the insecure skip

- **Why:** with `INSECURE_SKIP_HOST_KEY_CHECK=true` the backend trusts *any* host answering on
  `host.docker.internal:2222`. A process that grabs that port inside the Docker net could MITM
  the SSH session and harvest whatever is typed (including secrets pasted into `claude`/`codex`).
- **Current:** `deploy/.env` sets the skip to `true`; `_connect()` then passes
  `known_hosts=None` ([main.py](backend/workstation_app/main.py) `_connect`). The compose service
  already mounts `deploy/workstation-keys → /keys:ro` and points `WORKSTATION_KNOWN_HOSTS_PATH`
  at `/keys/known_hosts`, so the plumbing exists and is simply unused.
- **Change:** capture the target's host key once (`ssh-keyscan -p 2222 host` from a trusted
  position, or read the host's `C:\ProgramData\ssh\ssh_host_ed25519_key.pub` and format a
  `known_hosts` line), write it to `deploy/workstation-keys/known_hosts`, then set
  `WORKSTATION_INSECURE_SKIP_HOST_KEY_CHECK=false` in `deploy/.env`.
- **Files:** `deploy/workstation-keys/known_hosts` (new, gitignored), `deploy/.env`.
- **Effort:** ~15 min. **Deploy:** recreate `workstation` (env change) — `docker compose up -d workstation`.

### P0.2 — Fail loudly if the insecure skip is ever on

- **Why:** the skip is a first-run convenience that quietly persisted into production. It should
  never be able to ship silently again.
- **Current:** the flag is read and honored with no signal.
- **Change:** at startup (lifespan) log a `WARNING` when `insecure_skip_host_key_check` is true;
  surface the same on `/api/healthz` (e.g. `{"ok":true,"insecure_host_key":true}`) so a smoke
  test can assert it's off in prod.
- **Files:** `backend/workstation_app/main.py`.
- **Effort:** ~15 min. **Deploy:** rebuild `workstation`.

### P0.3 — ~~Public edge block (LAN-only)~~ — DROPPED (conflicts with intent)

- **Decision (2026-07-25):** the rail exists specifically to give the owner remote SSH into the
  box *from the internet*, gated by Cloudflare Access (no exposed port). Blocking the public path
  would defeat that. **Do not block it.** The exposure control that fits the intent is tightening
  the CF Access policy for the shell — see **P2.4**, promoted to the recommended exposure control.
- **Residual risk to accept knowingly:** with the public path kept open, the CF Access policy *is*
  the front door. Its strength (identity scope, session length, second factor) is the security
  boundary. Keep it strict (P2.4). Host-key pinning (P0.1) and the admin decouple (P1.1) remain
  worth doing regardless of exposure.

---

## P1 — defense-in-depth (moderate effort, meaningful risk reduction)

### P1.1 — Three roles (user / admin / super-admin), decouple apps from admin, delegation ✅

Owner-approved 2026-07-25 and implemented. Expanded from "just workstation" to every app, and
modelled as an explicit **three-role** concept with **`admin` as the super-admin**.

- **Roles:**
  - **user** — reaches exactly the apps explicitly entitled to them.
  - **admin** — can manage users (the ⚙ panel), but reaches apps only via explicit entitlements
    (a host shell included), and can only *grant* apps they themselves hold.
  - **super-admin** — the platform root of trust (the seed owner, `admin`): all-access to
    every app, the only role that can grant/revoke super-admin or edit another super-admin. Solves
    bootstrap (a newly catalogued app is grantable by the owner) and self-lockout.
- **Why:** admin used to be transitive to *everything*, including a root shell — any admin auto-saw
  every app. Access should be earned per app, not implied by the admin bit.
- **What shipped:**
  1. **Model:** new `users.is_superadmin` column (additive `ALTER TABLE` migration since
     `create_all` doesn't add columns to an existing table).
  2. **Decouple:** `entitled_app_ids(..., all_access=user.is_superadmin)` — only a super-admin is
     all-access; everyone else sees only their `Entitlement` rows. All 3 call sites (HTTP gate,
     app list, WS proxy) pass `all_access`.
  3. **One-time migration (`_migrate_roles`, Setting-flag guarded):** elevate the seed owner to
     super-admin and backfill every existing admin with explicit entitlements to all current apps,
     so nothing breaks at flip; later revocations stick and new apps are not auto-granted.
  4. **Delegation (`_grantable` + `_apply_grant`):** an actor may only add/remove apps within their
     own grant set; a target's apps the actor can't grant are frozen (preserved). Role guards:
     only a super-admin grants super-admin / edits or deletes a super-admin; last-super-admin and
     last-admin protected.
  5. **Frontend:** ⚙ Admin shows the role, greys/disables apps the actor can't grant, a role
     selector (super-admin option only for a super-admin), and "all apps (super-admin)".
- **Files:** `auth.py`, `main.py`, `models.py`, `catalog.py`, `web/src/{types,platformApi}.ts`,
  `apps/platform/frontend/src/AdminPage.tsx`. **Deploy:** rebuild `gateway` + shell dist.
- **Verified:** isolated in-memory-SQLite test of the migration, decouple, and delegation
  (limited admin cannot grant `workstation`; super-admin can; frozen apps preserved).

### P1.2 — Origin allowlist on the WS handshake (anti-CSWSH)

- **Plain terms:** only let the terminal be opened from the platform's own web page, not from some
  random site that tricked you into clicking. `SameSite=lax` and CF Access already stop nearly all
  of this, so it's a cheap belt-and-suspenders.
- **Current:** `ws_proxy` authenticates cookie + entitlement but never inspects `Origin`
  ([main.py](../../apps/platform/backend/platform_gateway_app/main.py) L465).
- **Change:** before `ws.accept()`, reject (close `4403`) any handshake whose `Origin` is not in a
  configured allowlist of the platform's own hosts. Applies to every rail's WS, not just this one.
- **Files:** `.../platform_gateway_app/main.py`, `.../config.py` (`allowed_ws_origins`).
- **Effort:** ~45 min. **Deploy:** rebuild `gateway`.

### P1.3 — Idle + absolute session timeout

- **Plain terms:** if you open a terminal and walk away, it stays a live shell forever. This
  auto-closes it after some idle time and a hard maximum, so a forgotten tab isn't a standing door.
- **Current:** the backend bridges PTY ⇆ WS until one side closes; nothing ages out an idle
  session ([main.py](backend/workstation_app/main.py) `ws_terminal`).
- **Change:** track last-input time; if no client input for `IDLE_SECS` (e.g. 900) or the session
  exceeds `MAX_SECS` (e.g. 8 h), send a `\x04` status line and close. A watchdog task alongside
  `pump_in`/`pump_out`.
- **Files:** `backend/workstation_app/main.py`, `backend/workstation_app/config.py`
  (`idle_secs`, `max_secs`).
- **Effort:** ~1 h. **Deploy:** rebuild `workstation`.

### P1.4 — Container hardening (shrink the blast radius)

- **Plain terms:** the little browser-to-SSH bridge runs as the most powerful user inside its
  container with a writable disk. Make it run as a nobody with a read-only disk and no extra
  powers, so a hijack of that process buys very little. No change to how you use it.
- **Current:** `Dockerfile.workstation` has no `USER`; compose has no `security_opt` / `cap_drop`
  / `read_only`.
- **Change:** add a non-root `USER` in the Dockerfile (able to read `/keys`), and on the compose
  service set `read_only: true` (+ `tmpfs: /tmp`), `security_opt: ["no-new-privileges:true"]`,
  `cap_drop: ["ALL"]`. Key stays `:ro`.
- **Files:** `deploy/Dockerfile.workstation`, `deploy/docker-compose.yml`.
- **Effort:** ~1 h incl. verifying the key is still readable. **Deploy:** rebuild `workstation`.

---

## P2 — optional / higher-touch

### P2.1 — Session audit trail with rotation + retention (owner-approved)

- **Why:** for the highest-value app, being able to answer "who opened a shell, when, which
  preset" is worth having before you need it.
- **Change:** structured connect/disconnect log lines from the backend — timestamp,
  `x-platform-user` (forwarded by the gateway as a header), preset, remote exit status — written
  to a dedicated log file on a mounted volume. **Rotate** (size or daily) and **auto-delete after
  a retention window** (`AUDIT_RETENTION_DAYS`, default **30**), so it doesn't grow without bound.
  Metadata only by default; full PTY-output recording is a separate, more sensitive opt-in (see
  note) and stays off unless explicitly enabled.
- **Files:** `backend/workstation_app/main.py` (read `x-platform-user`, emit audit lines +
  prune-on-startup/rotate), `backend/workstation_app/config.py` (`audit_enabled`,
  `audit_dir`, `audit_retention_days`), `deploy/docker-compose.yml` (a small audit volume).
- **Effort:** ~1.5–2 h. **Deploy:** rebuild `workstation`.
- **Note:** full PTY recording captures whatever scrolls past, including secrets — keep it a
  distinct flag with its own retention, and access-control the volume.

### P2.2 — Least-privilege SSH landing account — NOT recommended for now

- **What it is:** land the terminal in a *non-admin* Windows account (claude/codex installed for
  it) instead of `Admin`, so a hijacked session can't reconfigure the box without escalating.
- **Why deferred:** this is the owner's daily driver on their own machine; a non-admin landing
  means constant elevation and fights the intended use. The CF Access gate + audit log cover much
  of the same risk. Revisit only if another user is ever added, or if a task-specific low-priv
  preset is wanted.

### P2.3 — Per-user WS concurrency cap / reconnect throttle

- Bounds resource abuse and reconnect storms. Low security value while admin-only; more relevant
  if access widens. ~1 h in the gateway WS proxy. Rebuild `gateway`.

### P2.4 — Cloudflare Access: dedicated, strict policy for the shell path (recommended exposure control)

- **Why:** with the rail kept internet-reachable (by design), the CF Access policy *is* the front
  door. Give the shell its own Access application scoped to `/workstation*`, a single identity
  (the owner), a short session duration, and require a hardware key or WARP. This raises the bar
  before a request ever reaches the tunnel, without giving up remote access.
- **Note:** Cloudflare dashboard config, not code. Replaces the dropped P0.3 as the exposure
  control. **Effort:** ~30 min. **Deploy:** none (edge config).

---

## Suggested order

1. **P0.1 + P0.2** — pin the host key and make the insecure flag loud. Small diffs, immediate
   risk reduction, no schema changes.
2. **P1.1** — decouple all apps from admin + migration + delegation. The biggest defense-in-depth
   win; land the migration and decouple together so the owner never loses access.
3. **P2.1** — audit trail with rotation + 30-day retention.
4. **P1.3 + P1.4** — idle timeout and container least-privilege.
5. **P1.2** — Origin allowlist (platform-wide belt-and-suspenders).
6. **P2.4** — tighten the CF Access policy for the shell path (the exposure control for keeping it
   internet-reachable). Owner does this in the Cloudflare dashboard.
