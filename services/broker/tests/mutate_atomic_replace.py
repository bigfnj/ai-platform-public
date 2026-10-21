"""Mutation harness for the broker's atomic replace. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_atomic_replace.py

Run this before trusting a change to `app/fsutil.py` or to any settings writer in
`app/config.py` (roles.json, the Claude switch file, tokens.json, disabled.json), or to how a
write that outlasts the retries is refused (the 409 and the temp file's removal).

Every way this goes wrong is quiet where the tests usually run. Drop the retry and nothing fails
until an endpoint file filter happens to scan the file mid-write, and then a repoint or a token
mint is a 500 that passes on the next click. Retry on every OS and a real permission problem on
Linux waits out the backoff before saying so. A writer that goes back to a bare `Path.replace`
looks identical in review. Each mutation names the test in test_atomic_replace.py that must
notice; another test failing instead is FIRED-WRONG-TEST.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# This directory is sys.path[0] when the harness runs as a script. Every suite run is bounded:
# a mutation that hangs a test is reported as TIMEOUT instead of hanging the harness.
from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
REPO = BROKER.parents[1]
PY = Path(sys.executable)
# This tree's own source roots first: the venv's editable installs point at ONE checkout.
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "packages/platform_core")))
SUITE = ["tests/test_atomic_replace.py"]

F = "app/fsutil.py"
C = "app/config.py"
M = "app/main.py"
WRITER = "test_every_settings_writer_survives_a_held_file"
REFUSED = "test_a_file_held_past_the_retries_is_a_refusal_naming_it_and_leaves_no_temp_file"
ROUTE = "test_each_settings_route_answers_a_held_file_with_a_409"
BOUNDED = "test_the_retries_are_bounded_and_the_last_error_is_raised"
GUARD = "test_no_writer_in_the_broker_renames_a_file_any_other_way"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "the retry is removed, so a held file fails the write at once",
        F,
        "            if wait is None or not retrying:\n"
        "                raise\n"
        "            time.sleep(wait)",
        "            raise",
        "test_a_replace_held_twice_lands_on_the_third_try",
    ),
    (
        "the replace is retried on every OS",
        F,
        '    return sys.platform == "win32"',
        "    return True",
        "test_off_windows_the_first_permission_error_is_raised",
    ),
    (
        "the test override is ignored",
        F,
        "    if RETRY_ON_PERMISSION_ERROR is not None:\n"
        "        return RETRY_ON_PERMISSION_ERROR\n",
        "    if False:\n"
        "        return RETRY_ON_PERMISSION_ERROR\n",
        "test_the_override_pins_the_retry_either_way",
    ),
    (
        "the retries double",
        F,
        "    for wait in (*_BACKOFF, None):",
        "    for wait in (*_BACKOFF, *_BACKOFF, None):",
        BOUNDED,
    ),
    (
        "a file that never comes free is reported as written",
        F,
        "            if wait is None or not retrying:\n"
        "                raise\n",
        "            if wait is None:\n"
        "                return\n"
        "            if not retrying:\n"
        "                raise\n",
        BOUNDED,
    ),
    (
        "every OSError is retried, a missing temp file included",
        F,
        "        except PermissionError:",
        "        except OSError:",
        "test_only_a_permission_error_is_retried",
    ),
    # --- each writer goes back to a bare Path.replace ----------------------------------------
    # Re-anchored 2026-10-14: set_role, the token store and set_disabled now write through one
    # helper (config._write_settings_file), so each case inlines a bare rename at its call site,
    # and one more takes the retry out of the helper itself.
    (
        "the shared settings writer renames without the retry",
        C,
        "        replace_with_retry(tmp, path)",
        "        tmp.replace(path)",
        f"{WRITER}[roles]",
    ),
    (
        "set_role renames without the retry",
        C,
        '        _write_settings_file(self.roles_path(), json.dumps(current, indent=2) + "\\n")',
        "        path = self.roles_path()\n"
        '        tmp = path.with_name(path.name + ".tmp")\n'
        '        tmp.write_text(json.dumps(current, indent=2) + "\\n", encoding="utf-8")\n'
        "        tmp.replace(path)",
        f"{WRITER}[roles]",
    ),
    (
        "the Claude switch file renames without the retry",
        C,
        "                replace_with_retry(tmp, p)",
        "                tmp.replace(p)",
        f"{WRITER}[cli-switch]",
    ),
    (
        "the token store renames without the retry",
        C,
        '            _write_settings_file(self.tokens_path(), json.dumps(rows, indent=2) + "\\n")',
        "            path = self.tokens_path()\n"
        '            tmp = path.with_name(path.name + ".tmp")\n'
        '            tmp.write_text(json.dumps(rows, indent=2) + "\\n", encoding="utf-8")\n'
        "            tmp.replace(path)",
        f"{WRITER}[tokens]",
    ),
    (
        "set_disabled renames without the retry",
        C,
        '        _write_settings_file(self.disabled_path(), json.dumps(clean, indent=2) + "\\n")',
        "        path = self.disabled_path()\n"
        '        tmp = path.with_name(path.name + ".tmp")\n'
        '        tmp.write_text(json.dumps(clean, indent=2) + "\\n", encoding="utf-8")\n'
        "        tmp.replace(path)",
        f"{WRITER}[disabled]",
    ),
    # --- a file held past the retries is a refusal, with no temp file left (2026-10-14) -------
    (
        "a held file escapes the shared writer as a PermissionError, a 500, again",
        C,
        "    except OSError as exc:\n"
        "        with contextlib.suppress(OSError):\n"
        "            tmp.unlink(missing_ok=True)\n"
        "        raise SettingsWriteError(",
        "    except ZeroDivisionError as exc:\n"
        "        with contextlib.suppress(OSError):\n"
        "            tmp.unlink(missing_ok=True)\n"
        "        raise SettingsWriteError(",
        f"{REFUSED}[roles]",
    ),
    (
        "the shared writer leaves its temp file behind",
        C,
        "        with contextlib.suppress(OSError):\n"
        "            tmp.unlink(missing_ok=True)\n"
        "        raise SettingsWriteError(",
        "        raise SettingsWriteError(",
        f"{REFUSED}[tokens]",
    ),
    (
        "the switch file's refusal leaves its temp file behind",
        C,
        "                with contextlib.suppress(OSError):\n"
        "                    tmp.unlink(missing_ok=True)\n"
        "                raise CliRolesFileError(",
        "                raise CliRolesFileError(",
        f"{REFUSED}[cli-switch]",
    ),
    (
        "the refusal names the file's full path",
        C,
        '            f"{path.name} could not be written',
        '            f"{path} could not be written',
        f"{REFUSED}[disabled]",
    ),
    (
        "PUT /v1/roles answers a held file with a 500 again",
        M,
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc\n"
        "    except SettingsWriteError as exc:\n"
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc\n"
        "    except ValueError as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc\n"
        '    return {"roles"',
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc\n"
        "    except ValueError as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc\n"
        '    return {"roles"',
        f"{ROUTE}[PUT /v1/roles]",
    ),
    (
        "POST /v1/tokens answers a held file with a 500 again",
        M,
        "    except SettingsWriteError as exc:\n"
        "        raise HTTPException(status_code=409, detail=str(exc)) from exc\n"
        '    return {"token": plain',
        '    return {"token": plain',
        f"{ROUTE}[POST /v1/tokens]",
    ),
    (
        "DELETE /v1/tokens answers a held file with a 500 again",
        M,
        "        found = get_broker().settings.revoke_token(token_id)\n"
        "    except SettingsWriteError as exc:",
        "        found = get_broker().settings.revoke_token(token_id)\n"
        "    except ZeroDivisionError as exc:",
        f"{ROUTE}[DELETE /v1/tokens]",
    ),
    (
        "PUT /v1/disabled answers a held file with a 500 again",
        M,
        "        get_broker().settings.set_disabled(req.names)\n"
        "    except SettingsWriteError as exc:",
        "        get_broker().settings.set_disabled(req.names)\n"
        "    except ZeroDivisionError as exc:",
        f"{ROUTE}[PUT /v1/disabled]",
    ),
    # A writer no test above drives: only the source scan can see it.
    (
        "a new writer elsewhere renames a temp file directly",
        "app/voice.py",
        '        txt_path.write_text(text, encoding="utf-8")\n',
        '        txt_path.with_suffix(".tmp").write_text(text, encoding="utf-8")\n'
        '        txt_path.with_suffix(".tmp").replace(txt_path)\n',
        GUARD,
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([str(PY), "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=_TREE_ENV)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = BROKER / relpath
        # Bytes, not text: read_text/write_text translate line endings on Windows, so a
        # "restore" could rewrite an LF file as CRLF. The pattern is matched in the file's own
        # line endings and only its span replaced (mutate_bytes); restore_exact proves the
        # original bytes are back.
        original = path.read_bytes()
        n, mutated = mutate_bytes(original, old, new)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0 and named_failed(out, expected)
            verdict = "TIMEOUT" if hung else "FIRED" if fired else (
                "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-10:]
                print("      " + "\n      ".join(tail))
        finally:
            restore_exact(path, original)

    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
