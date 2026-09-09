# v1 catalog — local-only (curated 2026-07-25)

Decision: **everything runs or is hosted locally** inside the rail's own container.
No dial-out to third-party telnet/ssh servers. Famous services that are only available
as someone else's hosted box are excluded (see bottom), with a local stand-in where one exists.

Type = Watch (view-only, ignore keystrokes) or Play (interactive, needs stdin).

## Chosen — ranked by fame/discussion

| # | Item | Type | Local hosting | Why chosen |
|---|---|---|---|---|
| 1 | Star Wars ASCIImation | Watch | `gabe565/ascii-movie` (Go, container image `ghcr.io/gabe565/ascii-movie`) or play `sw1.txt`; Python original `nitram509/ascii-telnet-server` | The icon of terminal fun (the towel.blinkenlights classic), now self-contained |
| 2 | asciiquarium | Watch | apt `asciiquarium` | Beloved ASCII aquarium; universal crowd-pleaser |
| 3 | cmatrix | Watch | apt `cmatrix` | Matrix digital rain; instantly recognizable |
| 4 | NetHack | Play | apt `nethack-console` | The legendary deep roguelike; headliner of every telnet-games list |
| 5 | sl | Watch | apt `sl` | The famous mistyped-`ls` steam locomotive gag |
| 6 | cowsay + fortune + lolcat | Watch | apt `cowsay fortune-mod lolcat` | Ubiquitous Unix humor; rainbow talking cow |
| 7 | no-more-secrets | Watch* | build (tiny C, `bartobri/no-more-secrets`) | The *Sneakers* decrypt-reveal effect; heavily shared |
| 8 | nyancat | Watch | apt `nyancat` | The meme animation; a known telnet novelty, done locally |
| 9 | Dungeon Crawl Stone Soup | Play | apt `crawl` | Most-played modern roguelike after NetHack |
| 10 | Bastet | Play | apt `bastet` | "Bastard Tetris" — evil-AI Tetris people share |
| 11 | ninvaders | Play | apt `ninvaders` | Space Invaders in the terminal |
| 12 | 2048 | Play | build `2048.c` (trivial) | One of the most-played puzzle games of the 2010s |
| 13 | bsdgames pack | Play | apt `bsdgames` | 30+ classics in one: Colossal Cave adventure, hangman, robots, worm, trek, wump, canfield |
| 14 | cbonsai | Watch | apt `cbonsai` (newer) or build | Procedural bonsai; beloved aesthetic toy |
| 15 | pipes.sh | Watch | apt `pipes-sh` / script | Classic animated "pipes" screensaver |
| 16 | hollywood | Watch | apt `hollywood` | The meme "movie hacker" busy-screen |
| 17 | Colossal Cave (Frotz) | Play | apt `frotz` + free IF story files | The original text adventure; roots of the genre |
| 18 | moon-buggy | Play | apt `moon-buggy` | Famous, simple side-scroller; great pick-up game |
| 19 | figlet / toilet | Watch | apt `figlet toilet` | Big ASCII banner text; staple novelty |
| 20 | BOFH excuse server | Watch | self-host (fortune file / tiny generator) | The towel.blinkenlights `:666` classic, trivially local |

\* no-more-secrets needs one keypress to trigger the reveal.

## Optional / heavier (include deliberately)

| Item | Type | Local hosting | Cost note |
|---|---|---|---|
| mapscii (world map) | Play | npm + offline MBTiles tiles (`rastapasta/mapscii`) | Pulls in Node + map-tile data → image bloat |
| chess | Play | apt `gnuchess` (text) | Local engine replaces the FICS server; plain UI |

## Sourcing summary

- **apt (no harvest):** items 2–6, 8–11, 13–19 + gnuchess. The bulk.
- **tiny source build:** Star Wars (ascii-movie), no-more-secrets, 2048.
- **self-host asset:** Star Wars movie file; BOFH excuse list.
- ~20 core items ≈ a couple hundred MB of packages. mapscii is the only weighty add.

## Excluded — famous but NOT locally hostable

| Item | Why out | Local stand-in |
|---|---|---|
| Telehack (telehack.com) | Closed, proprietary hosted sim; not open-source | Its individual games run locally (NetHack, adventure…) |
| Live MUDs/MUCKs (Aardwolf, Achaea, FurryMUCK…) | Third-party persistent servers; hosting one is its own project + content curation | Could self-host an open MUD later |
| FICS chess / FIBS backgammon | Remote game servers | `gnuchess`; `bsdgames` backgammon |
| Multi-Trek (mtrek.com) | Remote multiplayer server, no open server to run | — |
| Google BBS | Browser novelty, not a terminal service | — |

## Sources for the self-host mechanisms

- Star Wars: https://github.com/gabe565/ascii-movie , https://github.com/nitram509/ascii-telnet-server
- mapscii: https://github.com/rastapasta/mapscii (offline MBTiles supported)
- no-more-secrets: https://github.com/bartobri/no-more-secrets
