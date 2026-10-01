"""Mutation check for the /openai/v1 surface's three load-bearing controls.

A passing suite proves nothing about whether these tests can FAIL. Each mutation
below breaks exactly one control and the run must go red; a mutation that SURVIVES
means the test covering it asserts something that is true either way.

Run:  python tests/mutate_openai_compat.py      (from services/broker)
Exit 0 only when every mutation fired.

Not a pytest file on purpose -- it rewrites the source tree under test, so it must
never be collected by an ordinary run.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

BROKER = pathlib.Path(__file__).resolve().parent.parent
SUITE = "tests/test_openai_compat.py tests/test_openai_compat_media.py"

# (label, file, exact source to replace, replacement, test expected to fail)
MUTATIONS = [
    (
        "disabled models are served anyway",
        "app/broker.py",
        "        if resolved in self.settings.disabled():\n"
        "            raise ModelDisabledError(resolved)\n",
        "        pass\n",
        "test_disabled_model_is_refused_with_403_not_502",
    ),
    (
        "the GPU gate is skipped for buffered chat",
        "app/broker.py",
        "        async with self.gate.hold(model=model, source=rail or \"openai-compat\",\n"
        "                                  fallback=fb.as_dict() if fb is not None else None):\n"
        "            await self._evict_other_heavy(keep=model)\n"
        "            return await self.ollama.openai_chat({**body, \"model\": model})",
        "        await self._evict_other_heavy(keep=model)\n"
        "        return await self.ollama.openai_chat({**body, \"model\": model})",
        "test_the_gpu_gate_is_taken",
    ),
    (
        "the @role is passed to Ollama unresolved",
        "app/broker.py",
        "            return await self.ollama.openai_chat({**body, \"model\": model})",
        "            return await self.ollama.openai_chat(body)",
        "test_role_is_resolved_before_ollama_sees_it",
    ),
    (
        "caller fields are dropped instead of forwarded",
        "app/ollama.py",
        "        resp = await self._client.post(\"/v1/chat/completions\", json={**body, \"stream\": False})",
        "        resp = await self._client.post(\"/v1/chat/completions\", "
        "json={\"model\": body.get(\"model\"), \"messages\": body.get(\"messages\"), \"stream\": False})",
        "test_caller_fields_survive_the_hop",
    ),
    # --- media: every one of these is a SILENT corruption if unguarded ---
    (
        "mp3 is accepted and a wav is returned as if it were one",
        "app/openai_compat.py",
        '_AUDIO_FORMATS = {"wav", "pcm"}',
        '_AUDIO_FORMATS = {"wav", "pcm", "mp3", "opus", "aac", "flac"}',
        "test_speech_refuses_formats_it_cannot_encode",
    ),
    (
        "the RIFF header is passed off as pcm sample data",
        "app/openai_compat.py",
        '        audio = audio[44:] if audio[:4] == b"RIFF" else audio\n',
        "",
        "test_pcm_strips_the_riff_header",
    ),
    (
        "out-of-range speed is silently clamped instead of refused",
        "app/openai_compat.py",
        "        if not 0.5 <= speed <= 2.0:\n"
        "            return error(400, \"speed must be between 0.5 and 2.0 on this server\",\n"
        "                         param=\"speed\")",
        "        speed = min(2.0, max(0.5, speed))",
        "test_speed_outside_kokoro_range_is_refused_not_clamped",
    ),
    (
        "an unhostable url response_format is accepted",
        "app/openai_compat.py",
        '    if fmt != "b64_json":',
        "    if False:",
        "test_url_response_format_is_refused",
    ),
    (
        "the image count cap is removed",
        "app/openai_compat.py",
        "    if not 1 <= n <= _MAX_IMAGES:",
        "    if not 1 <= n <= 10_000:",
        "test_n_is_capped",
    ),
    (
        "a run that produced no images reports success",
        "app/openai_compat.py",
        "    if not images:",
        "    if False:",
        "test_all_images_failing_is_a_502_not_an_empty_success",
    ),
    (
        "an unsafe upload filename is used as a suffix anyway",
        "app/openai_compat.py",
        "        if _SUFFIX_RE.match(candidate):\n            suffix = candidate",
        "        suffix = candidate",
        "test_unsafe_filename_yields_no_suffix_rather_than_a_repaired_one",
    ),
]


def run_suite() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *SUITE.split(), "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=BROKER, capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main() -> int:
    # A baseline that is not green makes every FIRED below meaningless: the suite
    # would be red before anything was mutated.
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.")
        print(out[-2000:])
        return 2
    passed = [line for line in out.splitlines() if "passed" in line]
    print(f"baseline green: {passed[-1].strip() if passed else out.strip()[:80]}\n")

    results = []
    for label, relpath, old, new, expected_test in MUTATIONS:
        path = BROKER / relpath
        original = path.read_text(encoding="utf-8")
        count = original.count(old)
        if count != 1:
            # Loudly, rather than silently mutating nothing and reporting SURVIVED.
            print(f"  ERROR  {label}: pattern found {count} times in {relpath}, expected 1")
            results.append((label, "PATTERN", expected_test))
            continue
        try:
            path.write_text(original.replace(old, new), encoding="utf-8")
            code, out = run_suite()
            fired = code != 0
            named = expected_test in out
            verdict = "FIRED" if fired and named else ("FIRED-WRONG-TEST" if fired else "SURVIVED")
            print(f"  {verdict:17} {label}")
            if fired and not named:
                print(f"      expected {expected_test} to fail; it did not appear in the output")
            results.append((label, verdict, expected_test))
        finally:
            path.write_text(original, encoding="utf-8")

    # Restoration has to be proven, not assumed: a harness that leaves the tree
    # mutated turns every later green run into a lie.
    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")

    bad = [r for r in results if r[1] != "FIRED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    if bad:
        for label, verdict, expected in bad:
            print(f"  {verdict}: {label}  (expected {expected})")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
