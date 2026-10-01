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

import subprocess
import sys
from pathlib import Path

BROKER = Path(__file__).resolve().parents[1]
REPO = BROKER.parents[1]
PY = REPO / ".venv" / "Scripts" / "python.exe"
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
        "a disabled model is eligible as its own substitute",
        B,
        "            if not name or name == wanted or name in disabled:",
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
        "media roles are substituted like Ollama models",
        B,
        "        if pattern in MEDIA_IMAGE_BACKENDS:\n            return pattern, None",
        "        if False:\n            return pattern, None",
        "test_a_media_role_is_never_substituted",
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
        "a disabled model is dispatched anyway on the OpenAI surface",
        B,
        "        if name in self.settings.disabled():\n"
        "            if not substitute:\n"
        "                return name, None",
        "        if False:\n"
        "            if not substitute:\n"
        "                return name, None",
        "test_disabling_the_target_is_honoured_by_the_role_pointing_at_it",
    ),
    (
        "a concrete disabled model is substituted instead of refused",
        B,
        '        if model.startswith("@"):\n            return await self.resolve_ref(model, substitute=True)',
        '        if True:\n            return await self.resolve_ref(model, substitute=True)',
        "test_a_model_the_caller_spelled_out_is_still_refused_not_substituted",
    ),
]


def run_suite() -> tuple[int, str]:
    p = subprocess.run([str(PY), "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"],
                       cwd=BROKER, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main() -> int:
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = BROKER / relpath
        original = path.read_text(encoding="utf-8")
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = run_suite()
            fired = code != 0
            named = expected in out
            verdict = "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
        finally:
            path.write_text(original, encoding="utf-8")

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
