"""Mutation harness for the broker's settings READERS. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_settings_files.py

Run this before trusting a change to how the broker reads a hand-edited settings file
(tokens.json, disabled.json, upstreams.json, voice_engines.json, the voice registry), to the
token store's parse-once cache, to the OpenAI surface's body parser, or to the order
require_token does its work in.

Each of these fails only on a file nobody has written yet: a UTF-16 save from Windows PowerShell
5.1, a file nested past the parser's stack. Narrow one `except` back to JSONDecodeError and every
test that writes ordinary JSON still passes, while the next bad save takes every request to a 500
(tokens.json), stops the broker starting (upstreams.json with a delegated role) or fails every
role chat (disabled.json). Each mutation names the test that must notice; another test failing
instead is FIRED-WRONG-TEST.
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
SUITE = ["tests/test_settings_files_unreadable.py", "tests/test_tokens_store.py",
         "tests/test_auth_token.py", "tests/test_voice_catalog.py", "tests/test_openai_compat.py"]

C = "app/config.py"
M = "app/main.py"
O = "app/openai_compat.py"
V = "app/voice.py"
EMPTY = "test_an_unparseable_settings_file_reads_as_its_empty_value"
BODY = "test_an_unparseable_openai_body_is_an_openai_shaped_400"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    # --- the four readers catch only what they caught before ---------------------------------
    # tokens() parses in _parse_tokens since its cache (below) arrived.
    (
        "tokens() lets a UTF-16 store raise again, so every request is a 500",
        C,
        "    except (ValueError, RecursionError):\n        return []\n    if not isinstance(data, list):",
        "    except json.JSONDecodeError:\n        return []\n    if not isinstance(data, list):",
        f"{EMPTY}[tokens-utf-16]",
    ),
    (
        "tokens() lets a store nested too deeply raise again",
        C,
        "    except (ValueError, RecursionError):\n        return []\n    if not isinstance(data, list):",
        "    except ValueError:\n        return []\n    if not isinstance(data, list):",
        f"{EMPTY}[tokens-deep]",
    ),
    (
        "disabled() lets a UTF-16 file raise again, so every role chat fails",
        C,
        "        except (OSError, ValueError, RecursionError):\n            pass\n        return set()",
        "        except (OSError, json.JSONDecodeError):\n            pass\n        return set()",
        f"{EMPTY}[disabled-utf-16]",
    ),
    (
        "disabled() lets a file nested too deeply raise again",
        C,
        "        except (OSError, ValueError, RecursionError):\n            pass\n        return set()",
        "        except (OSError, ValueError):\n            pass\n        return set()",
        f"{EMPTY}[disabled-deep]",
    ),
    (
        "upstreams() lets a UTF-16 registry raise again, so a delegated role stops the boot",
        C,
        "        except (OSError, ValueError, RecursionError):\n            return {}",
        "        except (OSError, json.JSONDecodeError):\n            return {}",
        "test_a_delegated_role_with_an_unparseable_registry_does_not_stop_the_audit[utf-16]",
    ),
    (
        "voice_engines() lets a UTF-16 overlay raise again",
        C,
        "        except (OSError, ValueError, RecursionError):\n            pass\n        return merged",
        "        except (OSError, json.JSONDecodeError):\n            pass\n        return merged",
        f"{EMPTY}[voice_engines-utf-16]",
    ),
    # --- tokens() parses once per version of the file (added with the writers' refusals) -------
    (
        "tokens() opens the store on every call again",
        C,
        "        if seen is None or seen[0] != sig:",
        "        if True:",
        "test_an_unchanged_store_is_read_once",
    ),
    (
        "the signature drops the file's identity, so a same-size, same-tick replace is missed",
        C,
        "    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)",
        "    return (st.st_mtime_ns, st.st_size)",
        "test_a_file_replaced_by_someone_else_is_read_again",
    ),
    (
        "this broker's own write keeps the old parse, so a revoke can be served stale",
        C,
        "        finally:\n"
        "            # Written or refused, the next read goes to the file (see tokens()).\n"
        "            self._tokens_seen = None",
        "        finally:\n            pass",
        "test_this_brokers_own_revoke_lands_even_where_the_file_looks_unchanged",
    ),
    (
        "a read that failed is remembered, locking every named token out",
        C,
        "            except OSError:\n                return []\n            seen = (sig, _parse_tokens(raw))",
        "            except OSError:\n                self._tokens_seen = (sig, [])\n"
        "                return []\n            seen = (sig, _parse_tokens(raw))",
        "test_a_read_that_fails_is_not_remembered",
    ),
    (
        "tokens() hands out the cached rows themselves",
        C,
        "        return [dict(r) for r in seen[1]]",
        "        return seen[1]",
        "test_what_tokens_returns_is_a_copy",
    ),
    (
        "a deleted store is still answered from the last parse",
        C,
        "        except OSError:\n            self._tokens_seen = None\n            return []",
        "        except OSError:\n            seen = self._tokens_seen\n"
        "            return [dict(r) for r in seen[1]] if seen else []",
        "test_a_deleted_store_reads_as_empty_at_once",
    ),
    # --- require_token's order (its decision is `_authorise` since the gate arrived) ----------
    (
        "require_token reads the token store before the healthz exemption again",
        M,
        '    if request.url.path == "/healthz":\n'
        "        return\n"
        "    try:\n",
        "    request.app.state.settings.tokens()\n"
        '    if request.url.path == "/healthz":\n'
        "        return\n"
        "    try:\n",
        "test_healthz_reads_no_token_store",
    ),
    (
        "require_token reads the token store before it compares the shared secret again",
        M,
        "    env_token = settings.auth_token\n    # Keyed on the STORE EXISTING",
        "    env_token = settings.auth_token\n    settings.tokens()\n    # Keyed on the STORE EXISTING",
        "test_the_shared_secret_is_accepted_without_reading_the_token_store",
    ),
    # --- the OpenAI body parser ----------------------------------------------------------------
    (
        "the OpenAI body parser lets a body that is not UTF-8 escape as a 500 again",
        O,
        "    except UnicodeDecodeError:\n"
        '        return error(400, "invalid JSON body: it is not UTF-8 text")\n'
        "    except RecursionError:\n"
        '        return error(400, "invalid JSON body: it is nested too deeply to parse")\n'
        "    except ValueError as exc:\n"
        '        return error(400, f"invalid JSON body ({type(exc).__name__})")\n',
        "",
        f"{BODY}[/openai/v1/chat/completions-not-utf8]",
    ),
    (
        "the OpenAI body parser lets a body nested too deeply escape as a 500 again",
        O,
        "    except RecursionError:\n"
        '        return error(400, "invalid JSON body: it is nested too deeply to parse")\n',
        "",
        f"{BODY}[/openai/v1/embeddings-deep]",
    ),
    # --- the voice registry --------------------------------------------------------------------
    (
        "the voice registry is read as plain utf-8 again, so a BOM is a 503",
        V,
        '        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))',
        '        data = json.loads(Path(path).read_text(encoding="utf-8"))',
        "test_a_voice_registry_with_a_bom_loads",
    ),
    (
        "a voice registry nested too deeply escapes as a RecursionError again",
        V,
        "    except (OSError, ValueError, RecursionError) as exc:",
        "    except (OSError, ValueError) as exc:",
        "test_a_voice_registry_nested_too_deeply_is_a_registry_error",
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
        # "restore" could rewrite an LF file as CRLF. mutate_bytes matches the pattern in the
        # file's own line endings; restore_exact proves the original bytes are back.
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
