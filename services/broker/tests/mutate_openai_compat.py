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
import sys

# This directory is sys.path[0] when the harness runs as a script. Every suite run is bounded:
# a mutation that hangs a test is reported as TIMEOUT instead of hanging the harness.
from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = pathlib.Path(__file__).resolve().parent.parent
SUITE = ("tests/test_openai_compat.py tests/test_openai_compat_media.py "
         "tests/test_gated_hang_up.py")

# (label, file, exact source to replace, replacement, test expected to fail)
MUTATIONS = [
    (
        # Re-anchored 2026-10-14 when the check went through _is_disabled, and again when
        # disabled.json became one read handed to resolve_ref and this check. Same mutation.
        "disabled models are served anyway",
        "app/broker.py",
        "        if _is_disabled(resolved, disabled):\n"
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
        "a chat role on the images route answers 502 instead of the broker's 400 (added "
        "2026-10-14)",
        "app/openai_compat.py",
        "    except NotAnImageModel as exc:",
        "    except KeyError as exc:",
        "test_a_role_that_is_not_an_image_role_is_refused_400_not_rendered",
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
    # --- no substituted embedder; bounded speech and transcription input ---
    (
        "embeddings substitute another embedder again",
        "app/broker.py",
        "        model, _fb = await self.resolve_for_external(str(body.get(\"model\") or \"\"),\n"
        "                                                     substitute=False)",
        "        model, _fb = await self.resolve_for_external(str(body.get(\"model\") or \"\"),\n"
        "                                                     substitute=True)",
        "test_a_missing_embedder_is_refused_not_substituted",
    ),
    (
        # Re-anchored 2026-10-14 when the call began handing resolve_ref its role map. Same
        # mutation.
        "resolve_for_external ignores substitute=False for a role",
        "app/broker.py",
        "            if substitute:\n"
        "                return await self.resolve_ref(model, substitute=True, roles=roles,",
        "            return await self.resolve_ref(model, substitute=True, roles=roles,",
        "test_a_disabled_embedder_behind_a_role_is_refused_not_substituted",
    ),
    (
        "the speech route takes any length of input again",
        "app/openai_compat.py",
        "    if len(text) > TTS_LIGHT_MAX_CHARS:",
        "    if False:",
        "test_speech_input_over_the_limit_is_refused_before_the_worker",
    ),
    (
        "the transcription route reads the whole upload before its size check again",
        "app/openai_compat.py",
        "    if file.size is not None and file.size > _MAX_AUDIO_BYTES:\n"
        "        return too_big\n"
        "    raw = await file.read(_MAX_AUDIO_BYTES + 1)",
        "    raw = await file.read()",
        "test_an_upload_over_the_limit_is_refused_without_reading_it_whole",
    ),
    (
        "the transcription size limit is dropped",
        "app/openai_compat.py",
        "    if len(raw) > _MAX_AUDIO_BYTES:\n"
        "        return too_big",
        "    if False:\n"
        "        return too_big",
        "test_an_upload_of_unknown_size_over_the_limit_is_still_refused",
    ),
    # --- a stream that ends in send() gives the GPU gate back before the request ends
    # --- (added 2026-10-14) ---
    (
        "the chat stream answers with a plain StreamingResponse again, so a stream that ends in "
        "send() holds the GPU gate until a collection",
        "app/openai_compat.py",
        "from app.streaming import ClosingStreamingResponse",
        "from fastapi.responses import StreamingResponse as ClosingStreamingResponse",
        "test_a_stream_that_ends_in_send_gives_the_gate_back_before_the_request_ends",
    ),
    (
        "closing the relay no longer closes the broker's stream, so the gate comes back after "
        "the request, whenever the finalizer gets to it",
        "app/openai_compat.py",
        # Re-anchored 2026-10-14 when the route began handing on its resolution. Same mutation.
        "            async with contextlib.aclosing(broker.openai_chat_stream(body, resolved=resolved)) "
        "as up:",
        "            async with contextlib.nullcontext(broker.openai_chat_stream(body, "
        "resolved=resolved)) as up:",
        "test_a_stream_that_ends_in_send_gives_the_gate_back_before_the_request_ends",
    ),
    # --- an image backend named in any case is that backend (added 2026-10-14) ---
    (
        "the images route compares backend names exactly again, so FLUX-Schnell draws SDXL",
        "app/openai_compat.py",
        '        known = image_backend(model) or "sdxl-turbo"',
        '        known = model if model in ("sdxl-turbo", "flux-schnell") else "sdxl-turbo"',
        "test_a_backend_named_in_any_case_is_that_backend[FLUX-Schnell-flux-schnell]",
    ),
    # --- a reader that stops reading gives the GPU gate back (added 2026-10-14) ---
    (
        "a send is never bounded, so a reader that stopped holds the GPU gate",
        "app/streaming.py",
        "            with anyio.move_on_after(limit) as waited:",
        "            with anyio.CancelScope() as waited:",
        "test_a_stream_whose_reader_stopped_gives_the_gate_back",
    ),
    # --- one resolution a request, the one that runs (added 2026-10-14) ---
    (
        # Re-anchored 2026-10-15 when the call took client_gone on its next line. Same mutation.
        "the buffered route resolves up front and the broker call resolves again",
        "app/openai_compat.py",
        "            return JSONResponse(await broker.openai_chat(body, resolved=resolved,\n",
        "            return JSONResponse(await broker.openai_chat(body,\n",
        "test_an_at_role_chat_is_resolved_once_per_request[buffered]",
    ),
    (
        "the streamed route resolves up front and the broker stream resolves again",
        "app/openai_compat.py",
        "broker.openai_chat_stream(body, resolved=resolved)) as up:",
        "broker.openai_chat_stream(body)) as up:",
        "test_an_at_role_chat_is_resolved_once_per_request[streamed]",
    ),
    # --- an images caller who hangs up stops its gated job (added 2026-10-15) -----------------
    (
        "the images route hands the broker no way to ask whether its caller is there, so a caller "
        "who left holds the GPU gate",
        "app/openai_compat.py",
        "            size=width, model=known, client_gone=request.is_disconnected)\n",
        "            size=width, model=known, client_gone=None)\n",
        "test_a_gated_job_whose_caller_hangs_up_stops_and_frees_the_gate[openai_image]",
    ),
    (
        "an images caller who hung up is answered as a failure (502), not 499",
        "app/openai_compat.py",
        "    except ClientGone as exc:\n"
        "        # Nobody is listening; 499 for the access log, as /v1/image answers.\n"
        "        return error(499, str(exc), type_=\"api_error\", code=\"client_closed_request\")\n",
        "",
        "test_a_gated_job_whose_caller_hangs_up_stops_and_frees_the_gate[openai_image]",
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded(
        [sys.executable, "-m", "pytest", *SUITE.split(), "-q", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=BROKER,
    )


def main() -> int:
    parse_harness_args(MUTATIONS)
    # A baseline that is not green makes every FIRED below meaningless: the suite
    # would be red before anything was mutated.
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time." if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.")
        print(out[-2000:])
        return 2
    passed = [line for line in out.splitlines() if "passed" in line]
    print(f"baseline green: {passed[-1].strip() if passed else out.strip()[:80]}\n")

    results = []
    for label, relpath, old, new, expected_test in MUTATIONS:
        path = BROKER / relpath
        # Bytes, not text: see mutate_bytes. A text round trip can change line endings.
        original = path.read_bytes()
        count, mutated = mutate_bytes(original, old, new)
        if count != 1:
            # Loudly, rather than silently mutating nothing and reporting SURVIVED.
            print(f"  ERROR  {label}: pattern found {count} times in {relpath}, expected 1")
            results.append((label, "PATTERN", expected_test))
            continue
        try:
            path.write_bytes(mutated)
            code, out, hung = run_suite()
            fired = code != 0
            named = named_failed(out, expected_test)
            # A hang broke SOMETHING, but nothing showed the named test failing, so it is not
            # FIRED. Its own verdict, so it is never mistaken for one.
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            print(f"  {verdict:17} {label}")
            if fired and not named:
                print(f"      expected {expected_test} to fail; pytest did not report it failed")
            results.append((label, verdict, expected_test))
        finally:
            restore_exact(path, original)

    # Restoration has to be proven, not assumed: a harness that leaves the tree
    # mutated turns every later green run into a lie.
    code, _, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")

    bad = [r for r in results if r[1] != "FIRED"]
    print(f"\n{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    if bad:
        for label, verdict, expected in bad:
            print(f"  {verdict}: {label}  (expected {expected})")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
