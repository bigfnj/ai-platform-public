# Sources

Harvested 2026-07-25.

## Primary (requested)

- **awesome-ttygames** — https://ligurio.github.io/awesome-ttygames/
  - Repo: https://github.com/ligurio/awesome-ttygames (list is CC0/public-domain; the *games* are not)
  - Machine-readable source: `games.yaml` in that repo. **932 entries.** Fields per entry:
    `name`, `url`, `info`, `screencast` (asciinema), `wikipedia`, `play` (telnet/ssh/http),
    `shot` (commented out). **No `license` field. No `language` field.** ~67 entries have a
    `play:` (telnet/ssh) line.
- **Fun with Telnet (Brandon Rozek)** — https://brandonrozek.com/blog/fun-with-telnet/
  - Points onward to https://www.telnet.org/htm/places.htm
  - Security note from the author: telnet is plaintext; don't type secrets.
- **Acute Terminal Fun (mewbies)** — http://www.mewbies.com/acute_terminal_fun_telnet_public_servers_watch_star_wars_play_games_etc.htm
  - Big list of telnet servers, MUDs, MUCKs, BBS directories, and self-hosting tools.

## Secondary (found while researching)

- The Lost Worlds of Telnet (The New Stack) — https://thenewstack.io/the-lost-worlds-of-telnet/
- Places to Telnet (directory) — https://telnet.org/htm/places.htm
- Telnet BBS Guide — https://www.telnetbbsguide.com/
- Synchronet BBS list — https://www.synchro.net/
- The MUD Connector (MUD directory) — https://www.mudconnect.com/
- NetHack public servers — https://nethackwiki.com/wiki/Public_server ; NAO: https://alt.org/nethack/
- dgamelaunch (the menu/login front-end most public game servers use) —
  https://github.com/paxed/dgamelaunch
- Star Wars asciimation archive — https://www.asciimation.co.nz/
- towel.blinkenlights clone (self-host the Star Wars stream) —
  https://github.com/jimmckeeth/blinkenlights ; ascii-telnet-server (ASCII movie streamer)
- Chipkin telnet server list — https://store.chipkin.com/articles/telnet-list-of-telnet-servers

## Internal (the template we'd build on)

- `platform/apps/workstation/backend/workstation_app/main.py` — FastAPI `/ws/{preset}`,
  browser terminal ⇆ PTY-over-SSH, binary WS frame protocol, idle/absolute timeouts, audit log.
- `platform/apps/workstation/frontend/src/module.tsx` — `@xterm/xterm` ^5.5 + `@xterm/addon-fit`,
  React 18, `@originjs/vite-plugin-federation`, exposes `./module`.
- Rail wiring reference: `platform/apps/platform/backend/platform_gateway_app/catalog.py`,
  `platform/deploy/docker-compose.yml`, `platform/apps/platform/frontend/src/App.tsx`.
