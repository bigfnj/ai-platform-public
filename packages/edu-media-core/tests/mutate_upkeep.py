"""Mutation harness for edu-media-core's upkeep fixes. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe packages\\edu-media-core\\tests\\mutate_upkeep.py

Run this before trusting a change to the translation cache's write path, the clipart template,
``__all__``, the tesseract lookup, ``images.generate_image`` or ``search_clipart``.

Each guard fails quietly: a lost cache entry is a translation paid for twice, an in-process
SDXL is a second heavy model on a card the broker thinks it owns, and an unchecked clipart URL
is a request to the LAN with nothing on screen to show it happened.

Since 2026-10-20 it covers this suite's stand-ins for the heavy optional deps too
(edu_media_stand_ins.py, held by test_stand_ins.py): one left in sys.modules hands an empty module
to a test core's process runs after this suite, with nothing failing here. The controls are
rewrites that mean the same, and must SURVIVE.
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

CORE = Path(__file__).resolve().parents[1]          # packages/edu-media-core
REPO = CORE.parents[1]
PY = sys.executable
SRC = CORE / "src" / "edu_media_core"
SUITE = [str(CORE / "tests" / "test_upkeep.py"), str(CORE / "tests" / "test_stand_ins.py")]
STAND_INS = str(CORE / "tests" / "edu_media_stand_ins.py")

TR, BM, CA = str(SRC / "translate.py"), str(SRC / "broker_media.py"), str(SRC / "cache.py")
IM, PL, INIT = str(SRC / "images.py"), str(SRC / "present_levels.py"), str(SRC / "__init__.py")

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "translate_cached writes back its pre-call snapshot again, losing a concurrent result",
        TR,
        "    cache.put(path, key, result)\n",
        "    store[key] = result\n    cache.save(path, store)\n",
        "test_two_jobs_translating_at_once_both_keep_their_result",
    ),
    (
        "the broker path writes back its pre-call snapshot again",
        BM,
        "    _cache.put(path, key, result)\n",
        "    store[key] = result\n    _cache.save(path, store)\n",
        "test_the_broker_path_keeps_a_concurrent_result_too",
    ),
    (
        "a failed cache write raises again, discarding a translation already produced",
        CA,
        "    except OSError as exc:\n        print(f\"[edu_media_core.cache] could not record",
        "    except ZeroDivisionError as exc:\n        print(f\"[edu_media_core.cache] could not record",
        "test_a_failed_cache_write_still_returns_the_translation",
    ),
    (
        "the broker path carries its own copy of the clipart prompt again",
        BM,
        "from .images import _PROMPT_TMPL as _IMG_PROMPT  # noqa: E402",
        "_IMG_PROMPT = \"simple flat cartoon illustration of {subject}\"",
        "test_the_clipart_prompt_is_one_definition",
    ),
    (
        "__all__ forgets a shipped module",
        INIT,
        '           "cache", "profiles"]',
        '           "profiles"]',
        "test_all_lists_every_shipped_module_but_the_private_one",
    ),
    (
        "tesseract falls back to a hardcoded per-user path again",
        PL,
        '    return os.getenv("TESSERACT_CMD") or shutil.which("tesseract") or "tesseract"',
        '    return os.getenv("TESSERACT_CMD") or shutil.which("tesseract") or "/home/x/tesseract"',
        "test_tesseract_has_no_per_user_fallback",
    ),
    (
        "generate_image loads SDXL in this process again, outside the GPU gate",
        IM,
        "        return broker_media.generate_image(subject, out_path, force=force, steps=steps,\n"
        "                                           size=size)",
        "        get_sdxl()\n        return Path(out_path)",
        "test_generate_image_goes_through_the_broker_not_this_process",
    ),
    (
        "the header's reads wait on the 1200 s chat timeout again",
        BM,
        "def _get(path: str, *, timeout: float = _READ_TIMEOUT):",
        "def _get(path: str, *, timeout: float = _TIMEOUT):",
        "test_header_reads_use_the_short_read_timeout",
    ),
    (
        "search_clipart fetches any URL a result names",
        IM,
        "            if not url or not _public_http_url(url):",
        "            if not url:",
        "test_only_a_public_https_result_is_fetched",
    ),
    (
        "one public address is enough, so a host that also resolves privately passes",
        IM,
        "    return bool(addrs) and all(ipaddress.ip_address(a.split(\"%\")[0]).is_global for a in addrs)",
        "    return bool(addrs) and any(ipaddress.ip_address(a.split(\"%\")[0]).is_global for a in addrs)",
        "test_only_a_public_https_result_is_fetched",
    ),
    (
        "any scheme is fetched as long as the host is public",
        IM,
        '    if parts.scheme not in ("http", "https") or not parts.hostname:',
        "    if not parts.hostname:",
        "test_only_a_public_https_result_is_fetched",
    ),
    (
        "redirects are followed, walking a public URL past the check to the LAN",
        IM,
        ',\n                                 allow_redirects=False)',
        ')',
        "test_no_redirect_is_followed",
    ),
    # -- the 2026-10-14 audit: no helper nothing calls
    (
        "the single-clip synthesize_wav comes back with no caller",
        BM,
        "def synthesize_wavs(\n",
        "def synthesize_wav(text, lang, out_path):\n    return _post(\"/v1/tts\", {})\n\n\n"
        "def synthesize_wavs(\n",
        "test_broker_media_carries_no_uncalled_helper",
    ),
    (
        "the uncalled load_cache re-export comes back",
        BM,
        "def clear_cache(path: str | Path | None = None) -> None:\n",
        "def load_cache(path):\n    return _cache.load(path)\n\n\n"
        "def clear_cache(path: str | Path | None = None) -> None:\n",
        "test_broker_media_carries_no_uncalled_helper",
    ),
    (
        "the clear_cache a caller uses is removed with the rest",
        BM,
        "def clear_cache(path: str | Path | None = None) -> None:\n    _cache.clear(path)\n",
        "",
        "test_the_cache_re_export_a_caller_uses_is_kept",
    ),
    # -- 2026-10-20: the stand-ins stay inside the imports that need them
    (
        "the stand-ins are left in sys.modules after the import, for the whole session",
        STAND_INS,
        "            if sys.modules.get(name) is module:\n                del sys.modules[name]\n",
        "            pass\n",
        "test_no_stand_in_is_left_for_a_test_collected_beside_this_suite",
    ),
    (
        "a stand-in replaces a module already imported, and leaving takes the real one out",
        STAND_INS,
        "        if name not in sys.modules:\n",
        "        if True:\n",
        "test_a_module_already_imported_is_neither_replaced_nor_removed",
    ),
]

#: Rewrites that mean the same. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "the stand-in's identity checked by lookup and comparison rather than get()",
        STAND_INS,
        "            if sys.modules.get(name) is module:\n",
        "            if name in sys.modules and sys.modules[name] is module:\n",
    ),
]


def run_suite() -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CORE / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT: the suite ran past 600s"
    return p.returncode, p.stdout + p.stderr


def _control(label: str, path_s: str, old: str, new: str) -> str:
    """Apply one control, run the suite, restore the file byte for byte: SURVIVED, or why not."""
    path = Path(path_s)
    original_bytes = path.read_bytes()
    n, mutated = mr.mutate_bytes(original_bytes, old, new)
    if n != 1:
        return f"PATTERN (found {n} times)"
    try:
        path.write_bytes(mutated)
        code, out = run_suite()
    finally:
        mr.restore_exact(path, original_bytes)
    if code == 0:
        return "SURVIVED"
    tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
    print("      " + "\n      ".join(tail))
    return "TIMEOUT" if code == 124 else "FIRED"


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, path_s, old, new, expected in MUTATIONS:
        path = Path(path_s)
        if not path.is_file():
            # present_levels.py is withheld from the public snapshot. Said, never silent.
            print(f"  SKIPPED           {label}  ({path.name} is not in this tree)")
            continue
        original_bytes = path.read_bytes()
        with open(path, encoding="utf-8", newline="") as fh:
            original = fh.read()
        if "\r\n" in original:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {path.name}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(original.replace(old, new))
            code, out = run_suite()
            verdict = ("TIMEOUT" if code == 124 else
                       "FIRED" if code != 0 and mr.named_failed(out, expected) else
                       "FIRED-WRONG-TEST" if code != 0 else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original_bytes)

    controls: list[tuple[str, str]] = []
    for label, path_s, old, new in CONTROLS:
        verdict = _control(label, path_s, old, new)
        controls.append((label, verdict))
        print(f"  control {verdict:9} {label}")

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    bad_controls = [c for c in controls if c[1] != "SURVIVED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test; "
          f"{len(controls) - len(bad_controls)}/{len(controls)} controls survived")
    for label, verdict in bad + bad_controls:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not bad_controls and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
