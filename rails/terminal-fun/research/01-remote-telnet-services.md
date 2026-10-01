# Remote services (dial-out telnet/ssh) — curated address book

These need **zero code harvesting**. The rail backend opens an outbound TCP/telnet (or
ssh) connection from inside the container and pipes it to the browser terminal. The
catalog is a **fixed allowlist** — the browser only ever sends an item id, never a host
(that keeps it from being a user-controlled-host SSRF).

Liveness of public telnet servers rots over time — health-check before shipping, and
consider **self-hosting the crown jewels** (see concerns doc). Ports are 23 unless noted.

## Showpieces / "watch" (no input needed)

| Item | Host | Port | Notes / liveness |
|---|---|---|---|
| Star Wars ASCIImation | `towel.blinkenlights.nl` | 23 | The classic. **Flaky / reportedly IPv6-only now** (`telnet -6`). Strongly consider self-hosting from asciimation.co.nz data or the `jimmckeeth/blinkenlights` clone. |
| BOFH excuse server | `towel.blinkenlights.nl` | 666 | Random "bastard operator from hell" excuse. |
| Nyan Cat | `miku.acm.uiuc.edu` | 23 | Needs `telnet -t vtnt`. Liveness uncertain — verify. |
| World map (zoomable) | `mapscii.me` | 23 | Braille/ASCII world map; mouse-zoom. Usually up. |

## Sandboxes / retro computing

| Item | Host | Port | Notes |
|---|---|---|---|
| Telehack | `telehack.com` | 23 | **Best single anchor**: simulated 1985 ARPANET/usenet, a BASIC interpreter, and ~25k hosts — and **60+ text games in one endpoint**. Very stable. |
| Google BBS (web, not telnet) | masswerk.at/googleBBS/ | — | 1980s-style Google; browser-only, listed for flavor. |

## Games over the wire

| Item | Host | Port | Category | Notes |
|---|---|---|---|---|
| NetHack (NAO) | `nethack.alt.org` | 23 | Roguelike | **Prefer SSH** (`ssh nethack@alt.org`); telnet also works. Uses dgamelaunch menu. |
| Chess (FICS) | `freechess.org` | 5000 | Board | Free Internet Chess Server. |
| Backgammon (FIBS) | `fibs.com` | 4321 | Board | First Internet Backgammon Server. |
| Multi-Trek | `mtrek.com` | 1701 | Space combat | Star-Trek-inspired multiplayer. |
| Infinite Cave Adventure | `dungeon.name` | 20028 | Roguelike | From awesome-ttygames. |
| 2048 (ssh) | `ascii.town` | — | Puzzle | `ssh play@ascii.town`. |
| Anonymine (ssh) | `anonymine-demo.oskog97.com` | 2222 | Minesweeper | `ssh play@…`. |
| Intricacy (ssh) | `sshgames.thegonz.net` | — | Puzzle | `ssh intricacy@…`. |

## MUDs / MUCKs (multiplayer worlds) — curate for content

Historic and fun, but many are **adult-oriented social spaces** (esp. the MUCKs). For a
family/education-adjacent platform, ship at most a couple of well-known **all-ages MUDs**
and skip the MUCKs. Listed here for completeness, not endorsement.

| World | Host | Port | Note |
|---|---|---|---|
| Aardwolf MUD | `aardmud.org` | 23 | Large, well-run classic MUD. Reasonable all-ages default. |
| Zombie MUD | `zombiemud.org` | 23 | Online since 1994. |
| Achaea | `achaea.com` | 23 | Commercial fantasy MUD (Iron Realms). |
| Darker Realms | `mud.darkerrealms.org` | 2000 | Oldest continually-running LPMud in the US. |
| Legend of the Red Dragon | `darkrealms.ca` | 23 | The BBS-era LORD door game. |
| *(MUCKs: FurryMUCK, FluffMUCK, Winter's Oasis, Noir Haven, Flipside)* | — | — | **Excluded** — adult/social, not appropriate to surface here. |

## Directories to mine later (if we want more)

- Telnet BBS Guide (telnetbbsguide.com), Synchronet list (synchro.net), The MUD Connector
  (mudconnect.com), telnet.org/htm/places.htm, Chipkin's list.
