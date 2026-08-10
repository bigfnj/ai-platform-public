# Terminal Fun — Backlog

Known bugs / follow-ups. Newest first.

## Save/resume for NetHack + Dungeon Crawl — BUILT 2026-08-06

Scoped by the owner to the two games with real resumable state, and shipped:
- **Per-owner save store** (`saves.py`) on a new persistent volume (`terminal_fun_data:/data`,
  `TERMINAL_FUN_DATA_DIR`). `PtySession` now accepts a caller-owned HOME (seeded before launch, not
  wiped on close) so the WS handler can capture the save before teardown; identity comes from the
  existing `x-platform-user` header.
- **Crawl** saves HOME-relative (`$HOME/.crawl`) — captured/restored with the sandbox HOME.
- **NetHack** runs under a per-owner player name (`-u <token>`, `token = nh+sha1(owner)[:8]`), so its
  files in the shared world-writable `/var/games/nethack/save` never collide; capture copies the
  owner's token-named files out (and clears them from the shared dir), restore copies them back.
- **API**: `GET /api/saves` (which games have a save) + `DELETE /api/saves/{id}` (discard).
- **Frontend**: a "resume" chip on a saveable tile when a save exists, save/resume instructions +
  a "Discard saved game" button in the how-to-play modal.
- **Verified in-container** end-to-end against the real volume + `/var/games/nethack/save` perms
  (`funuser` writes both; crawl + nethack capture/restore round-trip). Unit tests: `backend/tests/test_saves.py`.
- **One spot-check left for a real playthrough:** confirm live NetHack writes its save file with the
  `-u` token in the name (source formats it `save/<uid><plname>`, so it should; capture globs on the
  token substring). Play a quick game, Shift+S, reopen the tile, confirm it resumes.

---

### Original design notes (kept for reference)

**Scope decision (owner, 2026-08-06): NetHack + Dungeon Crawl only.** The audit found these are the
only two games with real, resumable save state. Everything else has none: the screensavers/hacker/toy
tiles are watch-only, and the arcade games (Invaders, Bastet, 2048, Robots, Snake, Moon Buggy) are
session-only (a high score at most). So Save/Resume appears on exactly two tiles.

### Architecture findings (from the 2026-08-06 investigation)
- **Each session gets an ephemeral tmpfs HOME** (`tempfile.mkdtemp`, set as `HOME` + `cwd`) that is
  `shutil.rmtree`'d in `PtySession.close()` — so all game state dies on exit today.
- **The rail is shared + stateless today:** `main.py` has NO per-user identity (no `X-Platform-User`)
  and the compose service has NO persistent volume. Save/resume must ADD both.
- **Crawl saves HOME-relative** (`$HOME/.crawl/`) — clean: it's captured/restored with the sandbox HOME.
- **NetHack (Debian) saves to a SHARED system dir** `/var/games/nethack/save/`, keyed by uid — outside
  HOME and shared across all sessions (same sandbox user). Per-user isolation needs one of: (a) relocate
  its playground via `NETHACKDIR`/`HACKDIR` to a HOME dir (seed/symlink the read-only data files there so
  saves land in HOME), or (b) swap the user's save file in/out of `/var/games/nethack/save/` around each
  session with a lock (racy if two users play at once). Option (a) is cleaner. `/etc/nethack/sysconf`
  exposes `CHECK_SAVE_UID` (can disable savefile UID checks) which may simplify (b).

### Build plan
1. **Capability metadata** — add `saveable: bool` + `save_dirs: list[str]` (HOME-relative) to catalog
   `Item` for nethack + crawl. For NetHack, also set the env in `pty_session` so it saves HOME-relative
   (option a above); verify the exact resulting path in-container before wiring capture.
2. **Per-user layer** — add `X-Platform-User` identity to `main.py` (fail-closed like the other rails)
   and a persistent volume (e.g. `terminal_fun_data:/data`); store saves at `/data/saves/<owner>/<id>/`.
3. **Lifecycle hooks** — capture the save dirs into the user's store **before** `close()`'s `rmtree`;
   on launch of a saveable game, seed the fresh HOME from the store if a save exists. (Delicate ordering
   around the existing teardown — this is the part to get right.)
4. **Frontend** — a "Resume" affordance on a saveable tile when a save exists; copy explaining you save
   via the game's own key (NetHack Shift+S / Crawl S) then leave.
5. Tests + deploy (new volume → `docker compose up -d --build --no-deps terminal-fun`).

Component: `frontend/src/module.tsx` (tiles/launch) + `backend/terminal_fun_app/{main,pty_session,catalog}.py`.
Status: **BUILT** 2026-08-06 (see the top of this section); these are the original design notes.

## AI Chat should stack newest-at-top, not append at the bottom (open — noticed 2026-07-27)
The docked AI Chat has the **input box pinned at the top** of the dock, with the message log growing
**below** it. New messages currently **append to the bottom** of the log, so the newest message ends
up farthest from the input. Confirmed with a 2-message test (screenshot): the user's "message 1"
rendered at the top of the log, then the assistant reply, then **"message two" landed at the very
bottom**.

**Desired:** the newest message should land **directly under the input** (top of the log) and push the
older messages **down**. So the read order becomes newest → oldest, top to bottom — message 2 above
the reply above message 1.

**Where to look:** `frontend/src/module.tsx` — the chat log is `.ft .chat .log { … display:flex;
flex-direction:column; overflow:auto; … }` (~line 68) and messages render in append order. Cleanest
fix is to **render newest-first** (reverse the render order, or prepend to the messages array) rather
than `flex-direction:column-reverse` (which fights the top-anchored input + scroll). Then pin the log
scroll to the **top** on each new message (not the usual scroll-to-bottom), and confirm the existing
"chat clears on item-nav" reset still leaves an empty log anchored at the top. Note the user/assistant
turn pairing must still read correctly once reversed.

**Status:** DONE. `module.tsx` renders the log newest-first (`msgs.map(...).reverse()`, ~line 348)
and pins the scroll to the top on each new message (`logRef.scrollTo({ top: 0 })`, ~line 154), so
the newest message lands directly under the top-pinned input. Verified 2026-08-03.
