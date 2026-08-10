"""The catalog of fun. The backend OWNS this; /api/catalog returns the display
fields + the how-to-play `info` (never the launch argv). WS /ws/{id} looks up the
argv and runs it.

Item.allow_input=False  -> a "watch" tile: the frontend won't send keystrokes and
                           the backend ignores input; no idle-kill (absolute cap only).
Item.idle_timeout       -> seconds of no keystrokes before a "play" session is closed
                           (0 = never idle-kill). Watch tiles leave this 0.
Item.info               -> a short how-to-play blurb (goal, controls, how to quit) shown
                           behind the tile's ⓘ button so a newcomer isn't dropped blind
                           into a 20-year-old game.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_LEAVE = "\n\nLeave: the ← Menu button."


@dataclass(frozen=True)
class Item:
    id: str
    label: str
    icon: str
    category: str
    argv: list[str] = field(default_factory=list)
    allow_input: bool = True
    idle_timeout: int = 0
    info: str = ""


# Picker sections, in display order.
CATEGORIES: list[tuple[str, str]] = [
    ("screensavers", "Screensavers"),
    ("hacker", "Hacker Vibes"),
    ("toys", "Toys"),
    ("roguelikes", "Roguelikes"),
    ("arcade", "Arcade & Puzzle"),
]

# argv[0] is resolved on a fixed PATH at launch (see pty_session), so bare names are fine.
ITEMS: list[Item] = [
    # --- Screensavers (watch) ---
    Item("starwars", "Star Wars", "🌌", "screensavers", ["ascii-movie", "play"], allow_input=False,
         info="The original Star Wars, rendered entirely in ASCII animation — the famous telnet "
              "classic, self-hosted here.\n\nJust watch: it plays through and loops. Nothing to press." + _LEAVE),
    Item("asciiquarium", "ASCII Aquarium", "🐟", "screensavers", ["asciiquarium"], allow_input=False,
         info="A little aquarium drawn in text — fish, the odd shark, a passing submarine.\n\nWatch only." + _LEAVE),
    Item("cmatrix", "Matrix Rain", "🟩", "screensavers", ["cmatrix", "-ab"], allow_input=False,
         info="The falling green 'digital rain' from The Matrix.\n\nWatch only." + _LEAVE),
    Item("pipes", "Pipes", "🧵", "screensavers", ["pipes.sh"], allow_input=False,
         info="The classic pipes screensaver: colored pipes wander and turn across the screen.\n\nWatch only." + _LEAVE),
    Item("cbonsai", "Bonsai", "🌳", "screensavers", ["cbonsai", "-l", "-i", "-w", "4"], allow_input=False,
         info="Grows a new procedurally-generated bonsai tree, over and over.\n\nWatch only." + _LEAVE),
    Item("nyancat", "Nyan Cat", "🌈", "screensavers", ["nyancat"], allow_input=False,
         info="The Nyan Cat, flying on a rainbow, forever.\n\nWatch only." + _LEAVE),
    Item("sl", "Steam Locomotive", "🚂", "screensavers", ["sl", "-e"], allow_input=False,
         info="A steam locomotive chuffs across the screen — the classic gag for fat-fingering "
              "'sl' instead of 'ls'.\n\nWatch only (it runs once)." + _LEAVE),
    # --- Hacker vibes ---
    Item("genact", "Fake Activity (genact)", "💻", "hacker", ["genact"], allow_input=False,
         info="A fake 'busy hacker' generator — endless realistic-looking compiling, downloads, "
              "and boot logs. Perfect for looking impossibly productive.\n\nWatch only." + _LEAVE),
    Item("hollywood", "Hollywood Hacker", "🎬", "hacker", ["hollywood-run"], allow_input=False,
         info="The fake 'movie hacker' screen — splits into panels of scrolling code, matrix rain, "
              "logs, and system monitors.\n\nWatch only (give it a second to spin up its panes)." + _LEAVE),
    Item("nms", "Decrypt FX (nms)", "🕵️", "hacker", ["nms-demo"], allow_input=True,
         info="The 'decrypt' effect from the movie Sneakers: the text appears scrambled, then "
              "decodes into readable characters.\n\nPress any key to start the decryption." + _LEAVE),
    # --- Toys ---
    Item("cowsay", "Fortune Cow", "🐄", "toys", ["cowfortune"], allow_input=False,
         info="A cow tells your fortune in glorious rainbow color — a fresh one every few seconds.\n\nWatch only." + _LEAVE),
    Item("bofh", "BOFH Excuse", "🖨️", "toys", ["bofh"], allow_input=False,
         info="The 'Bastard Operator From Hell' excuse server — an endless supply of absurd tech "
              "excuses for any outage.\n\nWatch only." + _LEAVE),
    Item("banner", "ASCII Banners", "🔤", "toys", ["bannershow"], allow_input=False,
         info="A rotating show of big ASCII-art banner text.\n\nWatch only." + _LEAVE),
    # --- Roguelikes (play) ---
    Item("nethack", "NetHack", "🗡️", "roguelikes", ["nethack"], idle_timeout=1800,
         info="NetHack — the legendary dungeon-crawling roguelike. Descend the Mazes of Menace and "
              "retrieve the Amulet of Yendor. Deep, hard, and wonderful.\n\n"
              "Move: arrow keys, or h/j/k/l (left/down/up/right); y/u/b/n for diagonals.\n"
              "Handy keys: i = inventory · , = pick up · > = down stairs · < = up · s = search · "
              "o = open door · ? = in-game help.\n"
              "At the start, press y to accept a random character, or pick a role.\n\n"
              "Quit: Shift+S to save, or type  #quit  and confirm."),
    Item("crawl", "Dungeon Crawl", "⚔️", "roguelikes", ["crawl"], idle_timeout=1800,
         info="Dungeon Crawl Stone Soup — a friendlier modern roguelike. Explore, fight, and dive "
              "for the Orb of Zot.\n\n"
              "Beginner tips: press  o  to auto-explore the level, and  Tab  to auto-attack the "
              "nearest monster — those two do most of the work.\n"
              "Move: arrow keys or h/j/k/l + y/u/b/n diagonals. i = inventory · , = pick up · "
              "> / < = stairs · ? = help.\n"
              "First time? Choose 'Tutorial' at the opening menu.\n\n"
              "Quit: Ctrl+Q then confirm (or S to save)."),
    Item("adventure", "Colossal Cave", "🕯️", "roguelikes", ["adventure"], idle_timeout=1800,
         info="Colossal Cave Adventure — the original text adventure (1976). You explore a cave by "
              "typing commands.\n\n"
              "Type simple words: go north (or just n / s / e / w / up / down), look, get lamp, "
              "on (light the lamp), inventory (or i).\n"
              "Goal: find the treasures and get out alive. Type  help  in-game for hints.\n\n"
              "Quit: type  quit."),
    # --- Arcade & puzzle (play) ---
    Item("ninvaders", "Space Invaders", "👾", "arcade", ["ninvaders"], idle_timeout=1800,
         info="Space Invaders in the terminal — blast the descending aliens before they reach you.\n\n"
              "Move: ← / → arrow keys.  Fire: Spacebar.\n\nPause: p.  Quit: q."),
    Item("bastet", "Bastet (Tetris)", "🟦", "arcade", ["bastet"], idle_timeout=1800,
         info="Bastet — 'Bastard Tetris'. It's Tetris, but it deliberately deals you the least "
              "helpful piece it can. Fiendish.\n\n"
              "Move: ← / → .  Rotate: ↑ .  Soft drop: ↓ .  Hard drop: Spacebar.\n\nPause: p.  Quit: q."),
    Item("moonbuggy", "Moon Buggy", "🌙", "arcade", ["moon-buggy"], idle_timeout=1800,
         info="Moon Buggy — drive across the lunar surface and jump your buggy over the craters.\n\n"
              "Jump: Spacebar.  Fire at obstacles: a  or  l.\n\nQuit: q."),
    Item("g2048", "2048", "🔢", "arcade", ["2048"], idle_timeout=1800,
         info="2048 — slide the numbered tiles; when two equal tiles touch, they merge. Work your "
              "way up to the 2048 tile (and beyond).\n\n"
              "Move tiles: arrow keys (or W / A / S / D).\n\nQuit: q."),
    Item("robots", "Robots", "🤖", "arcade", ["robots"], idle_timeout=1800,
         info="Robots — evil robots chase you and kill on contact, but they're clumsy: lure them "
              "into smashing into each other or the scrap piles.\n\n"
              "Move like a compass: h/j/k/l + y/u/b/n for diagonals.  t = teleport (random, risky) · "
              "w = wait it out.\n\nQuit: q."),
    Item("snake", "Snake", "🐍", "arcade", ["worm"], idle_timeout=1800,
         info="Snake — steer your ever-growing worm to eat the numbers; the bigger the number, the "
              "more you grow. Don't hit the walls or your own tail.\n\n"
              "Move: arrow keys (or h/j/k/l).\n\nQuit: q."),
]

_BY_ID: dict[str, Item] = {i.id: i for i in ITEMS}


def item_by_id(item_id: str) -> Item | None:
    return _BY_ID.get(item_id)


def public_catalog() -> list[dict]:
    """Display view for the frontend (label/icon/category/watch + how-to-play info +
    whether the AI can tune it + whether it supports save/resume). Never leaks the launch argv."""
    from terminal_fun_app.tunables import is_tunable
    from terminal_fun_app.saves import is_saveable

    return [
        {"id": i.id, "label": i.label, "icon": i.icon, "category": i.category,
         "watch": not i.allow_input, "info": i.info, "tunable": is_tunable(i.id),
         "saveable": is_saveable(i.id)}
        for i in ITEMS
    ]
