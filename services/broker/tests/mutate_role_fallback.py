"""Mutation harness for model fallback. NOT a pytest file -- it rewrites the tree under test.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_role_fallback.py

Run this, not just pytest, before trusting a change to the resolver.

A fallback is a feature whose failure mode is SILENCE. If it substitutes across capabilities,
@vision answers a question about a photo it never saw, with a 200 and no error anywhere. If it
stops announcing, a broken role map looks healthy forever. Neither shows up in a status code, so
every mutation here removes one of the five things that keep a degraded state visible, and
requires a named test to notice.
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
# The interpreter running THIS harness, not a hardcoded REPO/.venv. A git worktree has no .venv,
# so the harness died there with FileNotFoundError before mutating anything; and the venv's
# editable installs point at ONE checkout, so the child run is also handed this tree's own
# source roots (_TREE_ENV) -- otherwise it mutates one tree and tests another.
PY = Path(sys.executable)
_TREE_ENV = dict(os.environ, PYTHONPATH=os.pathsep.join(
    str(REPO / p) for p in ("services/broker", "apps/platform/backend",
                            "packages/platform_core", "packages/edu-media-core/src")))
SUITE = ["tests/test_role_fallback.py", "tests/test_role_audit.py", "tests/test_role_map.py",
         "tests/test_openai_compat.py"]

B = "app/broker.py"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a vision role falls back to a model that cannot see",
        B,
        '        if category == "vision":\n'
        '            digest = self._digests.get(name)\n'
        '            return "vision" in await self._capabilities(name, digest)',
        '        if category == "vision":\n'
        '            return True',
        "test_vision_never_falls_back_to_a_model_that_cannot_see",
    ),
    (
        "the capability gate trusts the editorial category instead of the backend",
        B,
        "        # chat / reasoning / code / other: any generative model can stand in. Refusing across\n"
        "        # those would mean claiming a reasoning model cannot hold a conversation.\n"
        "        return self._class(name) == \"heavy\"",
        "        return category_of(name, klass=self._class(name)) == category",
        "test_a_generative_role_may_substitute_across_chat_and_reasoning",
    ),
    (
        "the substitution is silent",
        B,
        '        print(f"[broker] MODEL FALLBACK: {fb.requested} -> \'{fb.original}\' is {fb.reason}; "\n'
        '              f"serving \'{fb.model}\' ({fb.category}) instead", file=sys.stderr, flush=True)',
        "        pass",
        "test_every_fallback_writes_a_log_line",
    ),
    (
        # Re-anchored 2026-10-14 when the check went through _is_disabled. Same mutation.
        "a disabled model is eligible as its own substitute",
        B,
        "            if not name or name == wanted or _is_disabled(name, disabled):",
        "            if not name or name == wanted:",
        "test_a_disabled_model_is_never_chosen_as_the_substitute",
    ),
    (
        "refusal becomes a silent pick",
        B,
        "        if not scored:\n            raise NoSubstituteError(role, wanted, category, reason)",
        "        if not scored:\n            return Fallback(wanted, f'@{role}', wanted, reason, category)",
        "test_vision_never_falls_back_to_a_model_that_cannot_see",
    ),
    (
        "an unknown role is handed to Ollama as a model name",
        B,
        "        if role not in roles:\n            raise UnknownRoleError(role, sorted(roles))",
        "        if False:\n            raise UnknownRoleError(role, sorted(roles))",
        "test_unknown_role_raises_instead_of_becoming_a_model_name",
    ),
    (
        # Re-anchored 2026-10-14 when the check became image_backend's (any case). Same mutation.
        "media roles are substituted like Ollama models",
        B,
        "        if backend is not None:\n            return backend, None",
        "        if False:\n            return backend, None",
        "test_a_media_role_is_never_substituted",
    ),
    # --- a role's image pattern matched in any case, as /v1/image draws it (added 2026-10-14)
    (
        "resolve_ref matches a role's image pattern exactly again",
        B,
        "        backend = image_backend(pattern)\n        if backend is not None:\n"
        "            return backend, None",
        "        backend = pattern if pattern in MEDIA_IMAGE_BACKENDS else None\n"
        "        if backend is not None:\n            return backend, None",
        "test_a_media_role_pinned_in_any_case_resolves_to_its_backend[FLUX-Schnell-flux-schnell]",
    ),
    (
        "resolve_ref answers a media role with its pattern as written",
        B,
        "        if backend is not None:\n            return backend, None",
        "        if backend is not None:\n            return pattern, None",
        "test_a_media_role_pinned_in_any_case_resolves_to_its_backend[FLUX-Schnell-flux-schnell]",
    ),
    (
        "the roles view matches a role's image pattern exactly again",
        B,
        "            backend = image_backend(pattern)\n            if backend is not None:\n",
        "            backend = pattern if pattern in MEDIA_IMAGE_BACKENDS else None\n"
        "            if backend is not None:\n",
        "test_a_media_role_pinned_in_any_case_shows_installed[FLUX-Schnell-flux-schnell]",
    ),
    (
        "the roles view reports a media role resolved as written",
        B,
        '                out.append({"role": role, "pattern": pattern, "resolved": backend,',
        '                out.append({"role": role, "pattern": pattern, "resolved": pattern,',
        "test_a_media_role_pinned_in_any_case_shows_installed[SDXL-Turbo-sdxl-turbo]",
    ),
    (
        "the roles view reports a media role served as written",
        B,
        '                            "serving": backend, "fallback": None})',
        '                            "serving": pattern, "fallback": None})',
        "test_a_media_role_pinned_in_any_case_shows_installed[FLUX-Schnell-flux-schnell]",
    ),
    (
        "the audit matches a role's image pattern exactly again",
        B,
        "            if image_backend(pattern) is not None:\n                continue",
        "            if pattern in MEDIA_IMAGE_BACKENDS:\n                continue",
        "test_media_backends_are_exempt_in_any_case",
    ),
    (
        "the :latest tolerance is dropped, so a healthy role reports degraded",
        B,
        '        if name not in installed and f"{name}:latest" not in installed:',
        "        if name not in installed:",
        "test_an_untagged_install_is_not_treated_as_missing",
    ),
    (
        "a read view announces, so the Rails tab floods the log",
        B,
        "        scored.sort(reverse=True)\n"
        "        return Fallback(model=scored[0][1], requested=f\"@{role}\", original=wanted,",
        "        scored.sort(reverse=True)\n"
        "        self._announce(Fallback(scored[0][1], f\"@{role}\", wanted, reason, category))\n"
        "        return Fallback(model=scored[0][1], requested=f\"@{role}\", original=wanted,",
        "test_a_read_view_plans_without_announcing",
    ),
    (
        "the header sanitiser is removed",
        B,
        '            return str(v).replace("\\r", " ").replace("\\n", " ")[:200]',
        "            return str(v)",
        "test_the_headers_are_safe_to_put_on_a_response",
    ),
    (
        "the overlay is allowed to introduce a role with no backstop",
        "app/config.py",
        '    "ai-playground": "nemotron-3-nano:4b",',
        "",
        "test_every_shipped_overlay_role_has_a_default_backstop",
    ),
    (
        # Re-anchored 2026-10-14 when the check went through _is_disabled and read the set the
        # caller hands in. Same mutation.
        "a disabled model is dispatched anyway on the OpenAI surface",
        B,
        "        if _is_disabled(name, disabled):\n"
        "            if not substitute:\n"
        "                return name, None",
        "        if False:\n"
        "            if not substitute:\n"
        "                return name, None",
        "test_disabling_the_target_is_honoured_by_the_role_pointing_at_it",
    ),
    # --- the disabled checks are :latest-tolerant (audit 2026-10-14) ---------------------------
    (
        "the resolver's disabled check is a plain `in` again, so a role pinned untagged serves "
        "its disabled :latest install with no fallback",
        B,
        "        if _is_disabled(name, disabled):\n"
        "            if not substitute:",
        "        if name in disabled:\n"
        "            if not substitute:",
        "test_a_role_pinned_untagged_falls_back_when_its_latest_install_is_disabled",
    ),
    (
        "the OpenAI surface's disabled check is a plain `in` again, so `qwen3` is served while "
        "`qwen3:latest` is disabled",
        B,
        # Re-anchored 2026-10-14 when resolve_for_external began reading disabled.json once for
        # resolve_ref and this check. Same mutation.
        "        if _is_disabled(resolved, disabled):",
        "        if resolved in disabled:",
        "test_an_untagged_name_is_refused_when_its_latest_install_is_disabled",
    ),
    (
        "the audit's disabled check is a plain `in` again, so the startup audit passes the role",
        B,
        "            if _is_disabled(name, self.settings.disabled()):\n"
        "                out.append(",
        "            if name in self.settings.disabled():\n"
        "                out.append(",
        "test_a_role_pinned_untagged_is_named_when_its_latest_install_is_disabled",
    ),
    (
        "the substitute planner's disabled check is a plain `in` again, so an untagged name in "
        "disabled.json does not stop its :latest install standing in",
        B,
        "            if not name or name == wanted or _is_disabled(name, disabled):",
        "            if not name or name == wanted or name in disabled:",
        "test_an_untagged_name_in_disabled_json_is_never_chosen_as_the_substitute",
    ),
    (
        "the OpenAI listing's disabled check is a plain `in` again, offering a model it refuses",
        B,
        '            if m.get("name") and not _is_disabled(m["name"], disabled)',
        '            if m.get("name") and m["name"] not in disabled',
        "test_an_untagged_name_in_the_disabled_set_hides_its_latest_install",
    ),
    (
        "the model list's disabled flag is a plain `in` again",
        B,
        '                "disabled": _is_disabled(name, disabled),',
        '                "disabled": name in disabled,',
        "test_the_model_list_marks_a_latest_install_disabled_under_its_untagged_name",
    ),
    (
        "the tolerance drops :latest from the asked-for name only, not from the disabled set",
        B,
        "    return any(_strip_latest(d) == key for d in disabled)",
        "    return any(d == key for d in disabled)",
        "test_a_role_pinned_untagged_falls_back_when_its_latest_install_is_disabled",
    ),
    (
        "the tolerance drops :latest from the disabled set only, not from the asked-for name",
        B,
        "    key = _strip_latest(name)\n"
        "    return any(",
        "    key = name\n"
        "    return any(",
        "test_an_untagged_name_in_disabled_json_is_never_chosen_as_the_substitute",
    ),
    (
        "a concrete disabled model is substituted instead of refused",
        B,
        # Re-anchored 2026-10-07. The delegation guard landed between these two lines
        # and the call gained `tags=tags`, so the old two-line pattern matched nothing and this
        # mutation reported PATTERN on every run since -- the guard it exists to test went
        # untested, and nothing ran the harness from where anyone would see it. Re-anchored
        # again 2026-10-14, when the role map became one read handed to resolve_ref.
        '        if model.startswith("@"):\n            roles = self.settings.roles()',
        '        if True:\n            roles = self.settings.roles()',
        "test_a_model_the_caller_spelled_out_is_still_refused_not_substituted",
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=_TREE_ENV)


def main() -> int:
    parse_harness_args(MUTATIONS)
    code, out, hung = run_suite()
    if code != 0:
        # A baseline that ran out of time is its own verdict, and nothing below can mean
        # anything after it.
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
            fired = code != 0
            named = named_failed(out, expected)
            # A hang broke SOMETHING, but nothing showed the named test failing, so it is not
            # FIRED. Its own verdict, so it is never mistaken for one.
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
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
