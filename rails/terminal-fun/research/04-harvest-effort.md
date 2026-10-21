# How much energy to "harvest the multitude of repos"?

Short answer: **far less than it looks, if we're disciplined — and a lot, if we try to
harvest everything.** The value is front-loaded; the long tail is a trap.

## Effort tiers

### Tier 0 — Remote services (zero harvest)
Just a curated address book (~15–25 entries) + a health-check pass. No cloning, no building.
- **Effort: ~half a day** to curate, verify liveness, and write catalog entries.
- Covers the headliners: Star Wars, telehack (60+ games by itself!), chess, backgammon,
  mapscii, a couple of MUDs.

### Tier 1 — Packaged local games/toys (near-zero harvest)
`apt-get install` a set of Debian packages into the image; no source vendoring.
- ~30–50 games/toys (asciiquarium, cmatrix, sl, nethack-console, bsdgames [30+ in one!],
  bastet, ninvaders, moon-buggy, greed, nsnake, cowsay/fortune/lolcat, cbonsai, nms…).
- **Effort: ~1–2 days** for the Dockerfile, a smoke test that each launches and renders in
  xterm, and catalog entries. This is where most of the *breadth* comes from.

### Tier 2 — Hand-picked source builds (bounded harvest)
The best games *not* in the distro (e.g. a specific vitetris/2048/tint you love from an
asciinema demo). Per game: clone → **check the license** → build → add to the image → test.
- **Effort: ~30–60 min each.** Pick ~10–20 → **~1–3 days**.
- Linear and predictable *because it's curated*. Keep a `NOTICES` file of licenses.

### Tier 3 — Bulk-harvest all of awesome-ttygames (NOT recommended)
- **932 entries, no license metadata, no language metadata**, many abandoned or dead-linked,
  every repo a different build system (C/C++/Go/Rust/Python/Bash/Asm/Lisp/sed…).
- Cost is effectively unbounded: per-repo build spelunking, toolchain sprawl, image bloat
  into the gigabytes, and an ever-growing security/maintenance surface — for games nobody
  picked. **Energy: very high. Marginal value: very low.**

## Recommended scope

**~40–80 total items** across Tier 0 + Tier 1 + a small Tier 2 → **~3–5 focused days** for a
great v1, versus weeks-to-months chasing Tier 3. Ship the packaged breadth + the telnet
headliners first; add hand-picked source games only when one is genuinely worth it.

## The one thing worth *building* rather than harvesting

Self-host the fragile crown jewels so the rail doesn't depend on someone else's flaky box:
- A local **Star Wars ASCIImation** streamer (asciimation.co.nz frame data via an
  ascii-telnet-server / the `jimmckeeth/blinkenlights` clone) → immune to
  towel.blinkenlights.nl being IPv6-only/down.
- Optionally a small "movies" shelf of other ASCII animations we control.
- **Effort: ~half a day**, and it removes the biggest liveness risk in Tier 0.
