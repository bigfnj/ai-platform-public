"""Mutation harness for the terminal session's bookkeeping. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe rails\\terminal-fun\\backend\\tests\\mutate_audit_guards.py

Run this, not just pytest, before trusting a change to dims.py, pty_session.PtySession's start /
resize / cleanup, main.ws_terminal's resize, apply, start-failure, session-cap and save paths,
saves.py's restore / capture, the catalog's launch lines, or the lean compose file's
terminal-fun volume.

Every one of these fails QUIETLY. An unclamped resize leaks two fds and a HOME on the next
launch and the toy simply says "failed to start"; a reservation taken after an await lets the
per-user cap admit as many sessions as there are tabs; a lean install with no volume loses
every save on its documented repair path with no message; a restore that fails partway, its
session then captured, replaces a player's save with a partial tree. Each guard is broken on
purpose below to prove a test notices.

Each mutation names the test(s) that must fail for it. A red run that fails some OTHER test is
FIRED-WRONG-TEST, not FIRED: it proves the tree broke, not that the guard is held. CONTROLS are
rewrites the suite cannot see, and each must SURVIVE (2026-10-20, with the store swap's retry of
a rename Windows refuses for a moment).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# The shared runner (tools/tests/mutation_runner.py): a timeout kills the whole process tree,
# and a restore is byte for byte and verified. Found by walking up, so it runs from any tree.
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents
                            if (p / "tools" / "tests" / "mutation_runner.py").is_file())
                       / "tools" / "tests"))
import mutation_runner as mr  # noqa: E402

REPO = Path(__file__).resolve().parents[4]
RAIL_SRC = REPO / "rails" / "terminal-fun" / "backend"
PY = sys.executable
# Two invocations, not one: the rail and tools/tests have their own rootdir and conftest, and
# mixing them in one run makes collection depend on argument order.
SUITES = [
    (["rails/terminal-fun/backend/tests"], [RAIL_SRC]),
    (["tools/tests/test_lean_compose_volumes.py"], []),
]
RUN_TIMEOUT = 300

M = "rails/terminal-fun/backend/terminal_fun_app/main.py"
P = "rails/terminal-fun/backend/terminal_fun_app/pty_session.py"
C = "rails/terminal-fun/backend/terminal_fun_app/catalog.py"
LEAN = "deploy/installer/docker-compose.installer.yml"
G = "rails/terminal-fun/frontend/src/chatGuard.ts"
NODE = "test_chat_guard_passes_under_node"
VP = "rails/terminal-fun/frontend/src/visiblePoll.ts"
VP_NODE = "test_visible_poll_passes_under_node"
S = "rails/terminal-fun/backend/terminal_fun_app/saves.py"
WS_RESTORE = "test_a_restore_that_fails_midway_leaves_the_stored_save_byte_for_byte"
WS_RAISES = "test_a_restore_that_raises_is_a_failed_restore_too"
WS_CAPTURE = "test_a_capture_that_fails_at_the_session_s_end_keeps_the_save_and_says_so"
WS_TOLD = "test_the_player_is_told_once_in_the_terminal_and_the_game_waits_for_a_key"
WS_LEAVES = "test_a_player_who_leaves_at_the_notice_starts_no_game"
WS_NETHACK = "test_a_failed_nethack_restore_keeps_the_save_and_leaves_the_shared_dir_clean"
WS_RESTORED = "test_a_restored_session_is_still_captured_with_no_notice_and_no_key"
CRAWL_RESTORE = "test_a_crawl_restore_that_fails_midway_says_so_and_leaves_no_tree"
CRAWL_CAPTURE = "test_a_crawl_capture_that_fails_midway_keeps_the_stored_save_whole"
NH_CAPTURE = "test_a_nethack_capture_that_fails_midway_keeps_the_stored_save_whole"
NH_RESTORE = "test_a_nethack_restore_that_fails_midway_says_so_and_leaves_none_of_its_files"
ROUND_TRIP = "test_a_restore_and_a_capture_that_succeed_say_so_and_round_trip"
LINK_COPY = "test_a_crawl_capture_copies_links_as_links_and_follows_none"
LINK_CRAWL_DIR = "test_a_crawl_capture_never_stores_what_a_linked_crawl_dir_points_at"
LINK_CHMOD = "test_share_with_game_never_chmods_through_a_link"
LINK_CHMOD_ROOT = "test_share_with_game_leaves_a_linked_root_alone"
LINK_NH_CAPTURE = "test_a_nethack_capture_never_copies_a_link_and_clears_it"
LINK_NH_RESTORE = "test_a_nethack_restore_never_writes_through_a_name_left_in_the_shared_dir"
STAGED = "test_a_crawl_restore_copies_nothing_into_the_session_home_until_it_is_whole"
WALK_CHOICE = "test_the_copy_walks_by_descriptor_exactly_where_the_platform_can"
SHARED_OPEN = "test_the_shared_dir_is_opened_along_its_path_without_following_a_link"
RETRY_WITHIN = "test_a_refused_store_swap_is_asked_again_within_its_bound_and_takes_effect"
RETRY_RESTORE = "test_a_restore_whose_swap_is_refused_for_a_moment_still_restores"
RETRY_GIVES_UP = "test_a_store_swap_refused_past_its_bound_gives_up_and_keeps_the_save"
RETRY_ONLY = "test_only_a_refusal_is_asked_again"
RETRY_WINDOWS = "test_the_swap_is_asked_again_on_windows_alone"

# (label, [(file, old, new), ...], tests that must fail). Every `old` must occur exactly once.
MUTATIONS: list[tuple[str, list[tuple[str, str, str]], list[str]]] = [
    (
        "the resize frame stores the raw numbers again (the audit's finding)",
        [(M, '                        c = clamp_dim(size["cols"], dims["cols"])\n',
             '                        c = int(size["cols"])\n')],
        ["test_a_resize_frame_is_clamped_before_it_is_stored_or_applied",
         "test_the_next_launch_gets_the_clamped_size_not_the_poison"],
    ),
    (
        "a PtySession can be constructed with a size the ioctl cannot pack",
        [(P, "        self.cols = clamp_dim(cols, 80)\n", "        self.cols = max(1, cols)\n")],
        ["test_a_session_cannot_hold_a_size_the_ioctl_cannot_encode"],
    ),
    (
        "PtySession.resize() is unclamped, so struct.pack raises",
        [(P, "        self.cols, self.rows = clamp_dim(cols, self.cols), clamp_dim(rows, self.rows)\n",
             "        self.cols, self.rows = max(1, cols), max(1, rows)\n")],
        ["test_resize_is_clamped_too_and_packs"],
    ),
    (
        "a failed launch keeps its session HOME",
        [(P, "            self._discard_own_home()\n            raise\n", "            raise\n")],
        ["test_an_exec_failure_releases_both_fds_and_the_home",
         "test_a_failure_before_the_pty_still_removes_the_home"],
    ),
    (
        "a failed launch keeps the pty master fd",
        [(P, "            with contextlib.suppress(OSError):\n                os.close(master_fd)\n"
             "            raise\n",
             "            raise\n")],
        ["test_an_exec_failure_releases_both_fds_and_the_home"],
    ),
    (
        "over-correction: a failed launch deletes the CALLER's save HOME",
        [(P, "    def _discard_own_home(self) -> None:\n        if self._own_home and self.home:\n",
             "    def _discard_own_home(self) -> None:\n        if self.home:\n")],
        ["test_a_caller_owned_home_is_left_for_the_caller"],
    ),
    (
        "the handler breaks out of a failed start without closing the session",
        [(M, "                await session.close()\n                break\n",
             "                break\n")],
        ["test_a_session_that_fails_to_start_is_closed_and_releases_its_slot"],
    ),
    (
        "an await between the cap check and the reservation (the race, restored)",
        [(M, "    _sessions[user] += 1\n    in_task = asyncio.create_task(pump_in())\n",
             "    await asyncio.sleep(0)\n    _sessions[user] += 1\n"
             "    in_task = asyncio.create_task(pump_in())\n")],
        ["test_concurrent_handshakes_cannot_pass_the_cap_together"],
    ),
    (
        "a session never gives its slot back",
        [(M, "        _sessions[user] -= 1\n", "")],
        ["test_a_session_that_fails_to_start_is_closed_and_releases_its_slot",
         "test_sequential_sessions_still_get_in"],
    ),
    (
        "a falsy non-object apply frame resets the toy's tuning",
        [(M, "                        if not isinstance(incoming, dict):\n"
             "                            continue\n", "")],
        ["test_a_non_object_apply_frame_changes_nothing"],
    ),
    (
        "a tunable item gets its dead catalog argv back",
        [(C, 'Item("cmatrix", "Matrix Rain", "\U0001f7e9", "screensavers", [],',
             'Item("cmatrix", "Matrix Rain", "\U0001f7e9", "screensavers", ["cmatrix", "-ab"],')],
        ["test_tunable_items_carry_no_dead_argv"],
    ),
    (
        "the lean install's terminal-fun loses its data volume again",
        [(LEAN, "      - terminal_fun_data:/data\n", "")],
        ["test_every_shared_service_persists_the_same_paths_in_the_lean_install"],
    ),
    # --- a failed restore never costs the player their save (2026-10-15) ---------------------
    (
        "a session whose restore failed is captured anyway (the save wipe, in the handler)",
        [(M, "            if restored:\n                with contextlib.suppress(Exception):\n",
             "            if True:\n                with contextlib.suppress(Exception):\n")],
        [WS_RESTORE, WS_RAISES, WS_NETHACK],
    ),
    (
        "restore reports a failure as a success (the save wipe, in saves.py)",
        [(S, "            _remove(p)\n        return False\n", "            _remove(p)\n        return True\n")],
        [CRAWL_RESTORE, WS_RESTORE],
    ),
    (
        "a restore that raises is read as restored",
        [(M, "        restored = False\n        with contextlib.suppress(Exception):\n",
             "        restored = True\n        with contextlib.suppress(Exception):\n")],
        [WS_RAISES],
    ),
    (
        "over-correction: no session is captured",
        [(M, "            if restored:\n                with contextlib.suppress(Exception):\n",
             "            if False:\n                with contextlib.suppress(Exception):\n")],
        [WS_RESTORED],
    ),
    (
        "over-correction: a restore that worked reports a failure",
        [(S, "        return True\n    except Exception:  # noqa: BLE001 -- any failure is a failed restore",
             "        return False\n    except Exception:  # noqa: BLE001 -- any failure is a failed restore")],
        [WS_RESTORED, ROUND_TRIP],
    ),
    (
        "a failed copy leaves its partial tree beside the destination",
        [(S, "    except BaseException:\n        shutil.rmtree(new, ignore_errors=True)\n        raise\n",
             "    except BaseException:\n        raise\n")],
        [CRAWL_RESTORE, CRAWL_CAPTURE],
    ),
    (
        "a swap leaves the previous copy beside the new one",
        [(S, "        raise\n    shutil.rmtree(old, ignore_errors=True)\n", "        raise\n")],
        [ROUND_TRIP],
    ),
    (
        "a Crawl capture copies straight over the stored save again",
        [(S, '                _replace_tree(_store(user, "crawl") / "dot_crawl", _copy_of(src))\n',
             '                shutil.rmtree(_store(user, "crawl") / "dot_crawl", ignore_errors=True)\n'
             '                _copy_of(src)(_store(user, "crawl") / "dot_crawl")\n')],
        [CRAWL_CAPTURE, WS_CAPTURE],
    ),
    (
        "a NetHack capture empties the store before it copies again",
        [(S, '                _replace_tree(_store(user, "nethack"), fill)\n',
             '                shutil.rmtree(_store(user, "nethack"), ignore_errors=True)\n'
             '                _store(user, "nethack").mkdir(parents=True)\n'
             '                fill(_store(user, "nethack"))\n')],
        [NH_CAPTURE],
    ),
    (
        "a capture that failed reports a success, so nothing logs it",
        [(S, "        return False\n    finally:\n        held.close()\n\n\ndef abandon(",
             "        return True\n    finally:\n        held.close()\n\n\ndef abandon(")],
        [CRAWL_CAPTURE, NH_CAPTURE, WS_CAPTURE],
    ),
    (
        "a NetHack restore that fails leaves the files it copied in the shared dir",
        [(S, "        for p in placed:\n            _remove(p)\n        return False\n",
             "        return False\n")],
        [NH_RESTORE],
    ),
    (
        "a NetHack restore leaves its partial copy in the shared dir",
        [(S, "        with contextlib.suppress(OSError):\n            os.unlink(tmp)\n        raise\n",
             "        raise\n")],
        [NH_RESTORE],
    ),
    (
        "a failed NetHack restore's new game stays in the shared dir",
        [(M, "            else:\n                saves.abandon(user, item_id)\n",
             "            else:\n                pass\n")],
        [WS_NETHACK],
    ),
    (
        "the player is never told their save could not be loaded",
        [(M, "            await _say(ws, _SAVE_KEPT)\n", "")],
        [WS_TOLD],
    ),
    (
        "the notice goes to the status line, which a live page does not show",
        [(M, "            await _say(ws, _SAVE_KEPT)\n", "            await _status(ws, _SAVE_KEPT)\n")],
        [WS_TOLD],
    ),
    (
        "the new game starts without the player's key, clearing the notice",
        [(M, "            if not await _first_key(keyed, in_task, wd_task):\n                return\n", "")],
        [WS_LEAVES],
    ),
    (
        "a key never reaches the gate, so the new game never starts",
        [(M, "                    keyed.set()\n", "")],
        [WS_RESTORE, WS_TOLD],
    ),
    # --- nothing follows a link the game could have left (2026-10-16) -------------------------
    (
        "copytree follows the links it lists again (the finding)",
        [(S, "    return lambda new: shutil.copytree(src, new, symlinks=True, dirs_exist_ok=True,\n",
             "    return lambda new: shutil.copytree(src, new, symlinks=False, dirs_exist_ok=True,\n")],
        [LINK_COPY],
    ),
    (
        "a file copy follows a link made since copytree listed it",
        [(S, "    return shutil.copy2(src, dst, follow_symlinks=False)\n",
             "    return shutil.copy2(src, dst)\n")],
        [LINK_COPY],
    ),
    (
        "a .crawl that is itself a link is captured",
        [(S, "            if src.is_symlink():\n"
             "                return False         # not a tree the game wrote: following it stores its target\n",
             "")],
        [LINK_CRAWL_DIR],
    ),
    (
        "a restored save's links are chmodded through",
        [(S, "        if p.is_symlink():\n            continue\n", "")],
        [LINK_CHMOD],
    ),
    (
        "a linked root is chmodded through, and everything under its target",
        [(S, "    if path.is_symlink():\n        return\n", "")],
        [LINK_CHMOD_ROOT],
    ),
    (
        "NetHack's shared dir lists a link as a file again, so its target is stored",
        [(S, "            if pat.match(p.name) and (links if p.is_symlink() else p.is_file())]\n",
             "            if pat.match(p.name) and p.is_file()]\n")],
        [LINK_NH_CAPTURE],
    ),
    (
        "a link left in NetHack's shared dir is never cleared",
        [(S, "            for f in _nh_files(nh, _nh_any_re(name), links=True):\n",
             "            for f in _nh_files(nh, _nh_any_re(name)):\n")],
        [LINK_NH_CAPTURE],
    ),
    (
        "a NetHack restore copies onto the name in the shared dir again, writing through it",
        [(S, "                _place_file(f, dest)         # a partial copy never reaches dest\n",
             "                shutil.copy2(f, dest)\n")],
        [LINK_NH_RESTORE],
    ),
    (
        "a Crawl restore stages its copy inside the session HOME again",
        [(S, "                _replace_tree(dest, _copy_of(saved), stage=Path(home).parent)\n",
             "                _replace_tree(dest, _copy_of(saved))\n")],
        [STAGED],
    ),
    # --- a directory swapped for a link mid-copy: the walk by descriptor (2026-10-16) ----------
    # The walk itself runs only on Linux, where test_saves.py's last tests show it; these two hold
    # the choice of it, which every platform's suite pins.
    (
        "the copy never walks by descriptor, so a directory swapped mid-walk is followed",
        [(S, "    if _BY_DESCRIPTOR:\n        return lambda new: _copy_tree_by_descriptor(src, new)\n",
             "    if False:\n        return lambda new: _copy_tree_by_descriptor(src, new)\n")],
        [WALK_CHOICE],
    ),
    (
        "the copy walks by descriptor on a platform that has no such walk",
        [(S, "    if _BY_DESCRIPTOR:\n        return lambda new: _copy_tree_by_descriptor(src, new)\n",
             "    if True:\n        return lambda new: _copy_tree_by_descriptor(src, new)\n")],
        [WALK_CHOICE, LINK_COPY],
    ),
    # --- NetHack's shared dir opened by descriptor along its path (2026-10-16) ----------------
    # Its real links run only on Linux (test_saves.py's last tests); these break what the stood-in
    # os.open of SHARED_OPEN sees on every platform.
    (
        "the shared dir is taken by its path again, so a link on it is followed",
        [(S, "    if not _PINNED_DIRS:\n        yield nh\n        return\n",
             "    if True:\n        yield nh\n        return\n")],
        [SHARED_OPEN],
    ),
    (
        "the shared dir's path is opened following links",
        [(S, "            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)\n",
             "            inner = os.open(part, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)\n")],
        [SHARED_OPEN],
    ),
    # --- a swap Windows refuses for a moment, asked again within a bound (2026-10-20) ---------
    (
        "a refused rename of the store swap is never asked again (a capture lost to a scanner)",
        [(S, "    for wait in _RENAME_RETRIES if _RETRY_RENAME else ():\n", "    for wait in ():\n")],
        [RETRY_WITHIN, RETRY_RESTORE],
    ),
    (
        "a rename refused past the bound is passed over as done (the stored save then goes)",
        [(S, "            time.sleep(wait)\n    os.rename(src, dst)\n", "            time.sleep(wait)\n")],
        [RETRY_WITHIN, RETRY_GIVES_UP],
    ),
    (
        "any error of a rename is asked again, a full disk's too",
        [(S, "        except PermissionError:\n            time.sleep(wait)\n",
             "        except OSError:\n            time.sleep(wait)\n")],
        [RETRY_ONLY],
    ),
    (
        "the swap is never asked again on Windows, where a scanner refuses it",
        [(S, '_RETRY_RENAME: bool = os.name == "nt"\n', "_RETRY_RENAME: bool = False\n")],
        [RETRY_WINDOWS],
    ),
    (
        "a restore writes into the shared dir by its path again",
        [(S, "            nh = held.enter_context(_shared_dir(make=True))\n",
             "            nh = _nh_dir()\n            nh.mkdir(parents=True, exist_ok=True)\n")],
        [SHARED_OPEN],
    ),
    (
        "a capture reads and clears the shared dir by its path again",
        [(S, "            nh = held.enter_context(_shared_dir())\n", "            nh = _nh_dir()\n")],
        [SHARED_OPEN],
    ),
    (
        "abandon clears the shared dir by its path again",
        [(S, "        with contextlib.suppress(Exception), _shared_dir() as nh:\n",
             "        with contextlib.suppress(Exception):\n            nh = _nh_dir()\n")],
        [SHARED_OPEN],
    ),
    (
        "discard clears the shared dir by its path again",
        [(S, "        with contextlib.suppress(OSError), _shared_dir() as nh:\n",
             "        with contextlib.suppress(OSError):\n            nh = _nh_dir()\n")],
        [SHARED_OPEN],
    ),
    # --- a reply belongs to the screen it was asked from (frontend, under node) ------------
    (
        "a reply from a screen no longer open is shown and applied (the audit's finding)",
        [(G, "  if (sentFrom !== current) return { show: false, apply: null }\n", "")],
        [NODE],
    ),
    (
        "screens are compared by item id, so a toy re-opened after a detour takes the old reply",
        [(G, "  if (sentFrom !== current) return", "  if (sentFrom.item !== current.item) return")],
        [NODE],
    ),
    (
        "tuning for a different item than the one asked from is applied",
        [(G, " && action.item === sentFrom.item) {", ") {")],
        [NODE],
    ),
    # --- the header poll skips a hidden page (frontend, under node) --------------------------
    (
        "a hidden page's ticks still poll (the audit's finding)",
        [(VP, "  const stopTimer = every(() => { if (!env.hidden()) load() })\n",
              "  const stopTimer = every(() => { load() })\n")],
        [VP_NODE],
    ),
    (
        "no catch-up on return, so the chip stays stale until the next tick",
        [(VP, "  const stopListening = env.onVisibilityChange(() => { if (!env.hidden()) load() })\n",
              "  const stopListening = env.onVisibilityChange(() => {})\n")],
        [VP_NODE],
    ),
    (
        "the visibility listener outlives the component",
        [(VP, "    stopTimer()\n    stopListening()\n", "    stopTimer()\n")],
        [VP_NODE],
    ),
]

#: Rewrites the suite cannot see, so each must SURVIVE: a case that fires on any rewrite of its
#: line shows nothing about the guard. (label, [(file, old, new), ...])
CONTROLS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("the swap's waits written as a doubling",
     [(S, "_RENAME_RETRIES: tuple[float, ...] = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)\n",
          "_RENAME_RETRIES: tuple[float, ...] = tuple(0.01 * 2 ** n for n in range(6))\n")]),
    ("the swap's refusal caught as a one-name tuple",
     [(S, "        except PermissionError:\n            time.sleep(wait)\n",
          "        except (PermissionError,):\n            time.sleep(wait)\n")]),
]


def _env(roots: list[Path]) -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree's roots, and nothing else: the repo venv's editable installs point at one
    # checkout, and a worktree run without this would test somebody else's code.
    env["PYTHONPATH"] = os.pathsep.join(str(r) for r in roots)
    # No .pyc: a mutation and its restore can share an mtime and a size, and the import system
    # would then trust a stale compile of the other one.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PLATFORM_STANDALONE", None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c",
         "import terminal_fun_app.main as m, terminal_fun_app.dims as d;"
         "print(m.__file__); print(d.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env([RAIL_SRC]), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(RAIL_SRC):
            return f"imported {f}, which is not under {RAIL_SRC}"
    return None


def run_suite(extra: tuple[str, ...] = ()) -> tuple[int, str]:
    """Every suite; the worst exit code wins. -1 means a run timed out."""
    worst, out = 0, ""
    for files, roots in SUITES:
        try:
            p = mr.run_tree([PY, "-B", "-m", "pytest", *files, "-q", "--no-header",
                             "-p", "no:cacheprovider", *extra],
                            cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=_env(roots), timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout if isinstance(exc.stdout, str) else ""
            return -1, out + partial + f"\nTIMEOUT after {RUN_TIMEOUT} s"
        out += (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0 and worst == 0:
            worst = p.returncode
    return worst, out


def failed_names(out: str) -> set[str]:
    """Test function names on pytest's FAILED lines, parametrize ids dropped."""
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::", 1)[1].split(" ", 1)[0].split("[", 1)[0])
    return names


def _apply(label: str, edits: list[tuple[str, str, str]],
           original_bytes: dict[Path, bytes]) -> bool:
    """Write a case's edits in each file's own line endings, keeping each file's original bytes
    in `original_bytes` for the restore; False (nothing more written) at the first anchor not
    found exactly once."""
    for rel, old, new in edits:
        path = REPO / rel
        original_bytes.setdefault(path, path.read_bytes())
        current = path.read_text(encoding="utf-8")
        n = current.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {rel}, expected 1")
            return False
        mr.write_like(path, current.replace(old, new), original_bytes[path])
    return True


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    # Every named test must EXIST before its absence from a FAILED line can mean anything: a
    # renamed test would otherwise turn every mutation naming it into a silent SURVIVED.
    _, collected = run_suite(("--collect-only",))
    named = {t for _, _, tests in MUTATIONS for t in tests}
    missing = sorted(t for t in named if t not in collected)
    if missing:
        print("NAMED TESTS NOT COLLECTED -- " + ", ".join(missing))
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    summaries = [ln for ln in out.splitlines() if " passed" in ln or " skipped" in ln]
    # The witness that the node-backed tests RAN: skipped (no node on PATH), every mutation of a
    # frontend helper would read as SURVIVED, or worse, a guard would never be exercised at all.
    skipped = [ln for ln in summaries if "skipped" in ln]
    if skipped:
        print("BASELINE SKIPPED TESTS -- " + " | ".join(skipped))
        return 2
    print("baseline green: " + " | ".join(summaries) + "\n")

    results: list[tuple[str, str]] = []
    for label, edits, expected in MUTATIONS:
        original_bytes: dict[Path, bytes] = {}
        try:
            if not _apply(label, edits, original_bytes):
                results.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = ("TIMEOUT" if code == -1 else
                       "FIRED" if fired and not missing else
                       "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    controls: list[tuple[str, str]] = []
    for label, edits in CONTROLS:
        original_bytes = {}
        try:
            if not _apply(label, edits, original_bytes):
                controls.append((label, "PATTERN"))
                continue
            code, out = run_suite()
            verdict = "TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED"
            controls.append((label, verdict))
            print(f"  {verdict:17} (control) {label}")
            if verdict != "SURVIVED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            for path, raw in original_bytes.items():
                mr.restore_exact(path, raw)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls SURVIVED")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
