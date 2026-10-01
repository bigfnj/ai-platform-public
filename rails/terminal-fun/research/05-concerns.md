# Concerns (read before building)

Ordered roughly by importance. None are blockers; several shape the design.

## 1. Sandbox the local game processes
Games are interactive native programs, and some have **shell-escape / subprocess features**
(classic NetHack `!` shell, pager/editor invocation, `O`ptions that run programs; MUD
clients that spawn things). Since we run them *inside our own container* (no host SSH, unlike
workstation), an escape is contained — but harden anyway:
- Run the container as a **non-root user**; `no-new-privileges`; drop Linux capabilities.
- **No host bind-mounts.** Read-only rootfs where feasible; a small writable per-session
  scratch/home under `tmpfs`.
- Strip the game's environment: minimal `PATH`, no shell on PATH if a game would otherwise
  reach one, `SHELL=/bin/false` where games honor it.
- One process per session, killed on disconnect (workstation already does `process.terminate()`),
  plus the absolute-time watchdog.
- Resource caps (ulimits/cgroup) are good hygiene even though pure resource-exhaustion is
  out of the platform's security-review scope.

## 2. Outbound connections: fixed allowlist only
The `remote` launcher must dial **only catalog-fixed hosts**. The browser sends an item id,
never a host/port. This is the difference between a curated address book and a
**user-controlled-host SSRF** (which is exactly the class the platform security review cares
about). Never add a "type your own telnet host" box without treating it as a real SSRF/egress
decision.

## 3. Telnet is plaintext
The user⇆platform hop is `wss` (TLS via Cloudflare), so the plaintext is only
container⇆remote-server. That's fine for public game/animation content (no secrets), but:
- **Don't let users type credentials** into telnet BBSes/MUDs expecting privacy; add a
  one-line "this is an unencrypted public service" notice on those items.
- **Prefer SSH** where the service offers it (NetHack NAO does). Where we self-host, keep it
  local (no plaintext egress at all).

## 4. Content curation (this is an education-adjacent, family platform)
The telnet world includes adult-oriented **MUCKs** (FurryMUCK, etc.) and edgier BBSes. Ship a
**family-friendly** catalog: the toys, the roguelikes, telehack, chess, mapscii, and at most a
couple of well-run all-ages MUDs. Exclude the MUCKs and anything we can't vouch for. Add a
`content_rating` field so this is an explicit, reviewable choice, not an accident.

## 5. Liveness / fragility of public servers
Public telnet hosts die or go IPv6-only (towel.blinkenlights.nl already has). Mitigate:
- A periodic **health check** per remote item; show "temporarily offline" instead of a hung
  black screen, and hide dead items from the menu.
- **Self-host the headliners** (Star Wars asciimation especially) so the marquee experience
  doesn't depend on someone else's box.

## 6. Licensing (only if we vendor source)
Packaged games (Tier 1) are already license-cleared by Debian — installing isn't
redistribution of source, so this is low-risk. **If** we vendor source (Tier 2), each repo's
license (GPL/BSD/MIT/…) applies: keep the `LICENSE`/copyright, and maintain a `NOTICES` file.
awesome-ttygames has **no license metadata**, so never bulk-vendor from it — check each repo
by hand. Story files for interactive fiction: use the freely-redistributable IF corpus, not
commercial Infocom Zork.

## 7. UX details that bite if ignored
- **Idle timeout**: workstation kills on idle. Passive "watch" items (asciiquarium, Star Wars)
  have no keystrokes — they'd get killed. Make idle-timeout **per-item** (0 = don't kill;
  keep an absolute cap + maybe a gentle "still watching?" nudge).
- **View-only vs interactive**: `allow_input:false` items shouldn't forward keystrokes.
- **Terminal size / rendering**: some games assume ≥80×24 or specific `TERM`; set a sane
  `TERM` (e.g. `xterm-256color`) and surface a "make your window bigger" hint.
- **Concurrency**: each session is a process/connection; a per-user session cap keeps one
  person from opening 50 aquariums. (Resource-shaped, but cheap to add.)

## 8. Reuse the platform's existing protections
It's entitlement-gated behind the gateway + Cloudflare Access + login, exactly like every
other rail, and the gateway already enforces entitlements on the **WebSocket** path
(confirmed in the platform security review). Keep the backend unreachable except via the
gateway (bind internal, no published port), same as workstation.
