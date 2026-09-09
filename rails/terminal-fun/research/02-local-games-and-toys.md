# Local games & toys (run inside our own container)

The reassuring finding: **most of the good stuff ships as distro packages**, so there's
little-to-no "repo harvesting." We install them in the rail's Docker image and launch each
as a sandboxed PTY subprocess. Debian/Ubuntu package names below (Alpine has many too).

## Toys / eye-candy (no input; great for the "watch" shelf)

| Toy | Debian package | What it does |
|---|---|---|
| asciiquarium | `asciiquarium` | Animated ASCII aquarium. Crowd-pleaser. |
| cmatrix | `cmatrix` | Matrix "digital rain". |
| sl | `sl` | A steam locomotive chuffs across the screen. |
| pipes.sh | (script / `pipes-sh`) | Animated pipes screensaver. |
| cbonsai | `cbonsai` | Grows a procedural bonsai tree. |
| hollywood | `hollywood` | Fake "hacker" busy-screen (uses byobu/panes). |
| no-more-secrets | `nms` / `sneakers` | The *Sneakers* movie decrypt-reveal effect. |
| cowsay + fortune + lolcat | `cowsay` `fortune-mod` `lolcat` | A talking cow says a rainbow fortune. |
| figlet / toilet | `figlet` `toilet` | Big ASCII banner text. |
| aafire (libaa) | `libaa-bin` | ASCII fire. |

## Games (interactive; the "play" shelf)

| Game | Debian package | Category |
|---|---|---|
| NetHack (console) | `nethack-console` | Roguelike (the deep one) |
| bsdgames collection | `bsdgames` | 30+ classics: adventure, hangman, robots, snake, tetris(?), trek, worm, wump, hunt, sail, canfield… |
| Bastet | `bastet` | "Bastard" Tetris |
| ninvaders | `ninvaders` | Space Invaders |
| Moon Buggy | `moon-buggy` | Side-scroll jumper |
| greed | `greed` | Grid puzzle |
| nsnake | `nsnake` | Snake |
| nudoku | `nudoku` | Sudoku |
| Angband / Crawl | `angband`, `crawl` | Roguelikes |
| gnugo | `gnugo` | Go (board) |
| 2048 | build (`2048.c`) or `2048` where packaged | Puzzle |

## Interactive fiction (a nice bundled shelf)

- `frotz` (Z-machine interpreter, package `frotz`) + freely-distributable story files
  (e.g. the *Adventure*/Colossal Cave `.z5`). Zork itself is not freely redistributable —
  use the freeware IF that is (there's a large legal corpus at the IF Archive).

## The awesome-ttygames list, quantified

- **932 entries** in `games.yaml`.
- **No `license` field and no `language` field** on any entry — so you cannot filter or
  bulk-build safely; each candidate needs a manual license + build check.
- **~67 entries** have a `play:` (telnet/ssh) line → those overlap with the remote address
  book, not the local image.
- Languages (only visible in prose): mostly C/C++, plus Bash, Python, Go, Rust, Assembly,
  BASIC, Lisp, even sed/awk one-offs → wildly heterogeneous build systems.

**Takeaway:** treat awesome-ttygames as a *menu to hand-pick from*, not a corpus to
automate. The packaged games above already cover the crowd-pleasers with none of the build
pain.
