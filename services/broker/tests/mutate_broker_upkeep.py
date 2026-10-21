"""Mutation harness for the broker's upkeep fixes. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_broker_upkeep.py [label-substring ...]

Run this before trusting a change to the role labels, eviction's name comparison, the voice
registry's error path, the image-embedder allowlist, the bound on ungated media jobs, the
installed-list reuse on the audit, substitute and roles-view paths, list_models' probes, the
Rails tab read's one local list (roles_inventory), .env.example, how the broker behaves while
Ollama is not up (the 503, the once-per-outage log line, the deferred startup audit), when
Ollama's HTTP client is built (on first use, once), what a /v1/status poll and a roles sweep
repeat (the card's VRAM cache in app/gpu.py, status's reads together, one token read, one read
of each settings file per sweep, per status poll, per @role chat, per owner's CLI call and per
OpenAI-dialect @role, a down Ollama dialled once per poll), the order a closed stream ends in (its inner stream before the GPU gate, at all three
relays), /v1/image's refusal of a model that draws no images (and its backend names in any
case), or a media job's own directory, its big JSON kept off the event loop, and the startup
sweep of what a broker that died mid-job left (a media job's directory or a voice job's), a
voice job's own removal of its directory, and a read-aloud whose caller hangs up (/v1/tts_light,
and since 2026-10-15 /openai/v1/audio/speech, stops its job and answers 499; so does a gated
job's, on /v1/image, /v1/tts, /v1/tts_batch and /v1/voice/synthesize, freeing the GPU gate, and a
buffered local chat's on /v1/chat and /openai/v1/chat/completions, and a light-media job's on
/v1/embed_image, /v1/transcribe and /openai/v1/audio/transcriptions, its slot back), or how
broker.py takes Broker's CLI methods from broker_cli.py (run in its own namespace, refusing a name
the class has, recording each file it ran).

Every one of these fails quietly. Drop a ROLE_RAIL entry and a queue row reads `null`; drop the
`:latest` tolerance and a 15 GB model reloads on every call with every call still a 200; drop
the allowlist and any token holder picks what the worker downloads. None shows as an error.

Each mutation names the test that must fail; another test failing is FIRED-WRONG-TEST.
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
for _k in ("CLI_LIVE", "JOB_AID_LIVE_TESTS"):
    _TREE_ENV.pop(_k, None)
SUITE = ["tests/test_role_map.py", "tests/test_media.py", "tests/test_voice_catalog.py",
         "tests/test_embed_image_model.py", "tests/test_broker_hot_paths.py",
         "tests/test_broker_env_example.py", "tests/test_role_audit.py",
         "tests/test_boot_without_ollama.py", "tests/test_stream_close.py",
         "tests/test_media_job_dir.py", "tests/test_tts_light.py", "tests/test_gated_hang_up.py",
         "tests/test_chat_hang_up.py", "tests/test_light_media_hang_up.py",
         "tests/test_broker_methods.py"]

B = "app/broker.py"
BC = "app/broker_cli.py"   # Broker's CLI methods, run in broker.py's namespace (see there)
G = "app/gpu.py"
V = "app/voice.py"
M = "app/main.py"
O = "app/ollama.py"
D = "app/media.py"
W = "app/media_worker.py"
C = "app/config.py"
OC = "app/openai_compat.py"
E = ".env.example"
KILLED = "test_a_killed_worker_leaves_nothing_in_the_system_temp"
SWEPT = "test_the_broker_removes_a_dead_brokers_job_directory_at_startup_and_leaves_a_young_one"
VOICE_SWEPT = ("test_the_broker_removes_a_dead_brokers_voice_job_directory_at_startup_and_leaves_a_"
               "young_one")
VOICE_WAITS = "test_the_voice_sweep_waits_an_hour_or_three_voice_timeouts_if_longer"
VOICE_HELD = "test_a_voice_job_whose_text_is_held_a_moment_still_removes_its_directory"
VOICE_GIVES_UP = ("test_a_voice_job_s_directory_refused_every_time_is_logged_and_the_job_keeps_its_"
                  "own_error")
OFF_LOOP = "test_the_spec_and_the_result_go_to_disk_off_the_event_loop"
HUNG_UP_READ = "test_a_read_whose_caller_hangs_up_stops_its_job"
HUNG_UP_GATED = "test_a_gated_job_whose_caller_hangs_up_stops_and_frees_the_gate"
HUNG_UP_CHAT = "test_a_buffered_local_chat_whose_caller_hangs_up_frees_the_gate"
HUNG_UP_LIGHT = "test_a_light_media_job_whose_caller_hangs_up_stops_and_frees_its_slot"
STATUS_ROLES_ONCE = "test_a_status_poll_reads_roles_json_once"
DELEGATED_ONCE = "test_a_delegated_role_reads_each_settings_file_once_per_call"
UPSTREAMS_ONCE = "test_a_roles_sweep_reads_upstreams_json_once"
DOWN_503 = "test_a_route_that_needs_ollama_answers_503_naming_it_while_it_is_down"
ONCE = "test_an_outage_is_logged_once_and_the_next_outage_once_more"
SCHEDULE = "test_the_retry_backs_off_to_a_minute_and_gives_up_loudly_after_30_minutes"
VIEWS_ONCE = "test_roles_views_read_the_installed_list_once_however_many_roles_substitute"
INVENTORY_ONCE = "test_the_rails_tab_read_lists_this_box_s_models_from_the_sweep_s_one_read"
LAZY = "test_a_broker_builds_no_http_client_until_it_asks_ollama_something"
SENT_ALIKE = "test_whether_ollama_sent_a_failed_request_is_answered_alike_before_its_client_exists"
STATUS_ONCE = "test_five_concurrent_status_polls_spawn_nvidia_smi_once"
STATUS_CLOSE = "test_closing_the_broker_reaps_a_probe_nobody_waits_for_any_more"
TOKEN_ONCE = "test_a_status_poll_reads_the_cli_token_file_once"
SWEEP_READS = "test_a_roles_sweep_reads_each_settings_file_once"
CLOSE_ORDER = "test_a_closed_stream_closes_ollama_s_stream_before_it_frees_the_gate"
OWNER_ONCE = "test_an_owner_s_cli_call_reads_each_settings_file_once"
OPENAI_ONCE = "test_an_openai_dialect_role_reads_each_settings_file_once"
CALL_TOKEN_ONCE = "test_an_owner_s_cli_call_reads_the_token_file_once"
WAITED_TOKEN = ("test_an_owner_s_cli_call_that_waited_reads_the_token_file_again_and_carries_"
                "that_read")

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "a per-rail role loses its rail label, so its queue rows read source: null",
        B,
        '    "servicenow-rag": "ServiceNow", "openmaic": "OpenMAIC",\n',
        '    "openmaic": "OpenMAIC",\n',
        "test_every_per_rail_default_role_names_its_rail",
    ),
    (
        "co-worker's role loses its rail label, the gap the manifests now guard",
        B,
        '    "co-worker-synthesis": "Co-Worker",\n',
        "",
        "test_every_role_a_rail_manifest_declares_names_its_rail",
    ),
    (
        "a role's label drifts from its rail's manifest",
        B,
        '    "bouquet-vision": "Bouquet Builder", "bouquet-writer": "Bouquet Builder",',
        '    "bouquet-vision": "Bouquet", "bouquet-writer": "Bouquet Builder",',
        "test_every_role_a_rail_manifest_declares_names_its_rail",
    ),
    (
        "eviction compares names exactly again, unloading the untagged role's own model",
        B,
        "            if name and _strip_latest(name) != kept and self._class(name) == \"heavy\":",
        "            if name and name != keep and self._class(name) == \"heavy\":",
        "test_evict_keeps_an_untagged_role_s_model_that_ollama_lists_as_latest",
    ),
    (
        "eviction strips every tag, so qwen3:8b passes for the qwen3:4b being kept",
        B,
        "        kept = _strip_latest(keep) if keep else None\n",
        "        kept = keep.split(':')[0] if keep else None\n"
        "        _strip_latest = lambda n: n.split(':')[0]  # noqa: E731\n",
        "test_evict_still_tells_two_tags_of_one_family_apart",
    ),
    (
        "the catalog route lets a broken registry escape as a 500 again",
        M,
        "    except VoiceRegistryError as exc:\n"
        "        raise HTTPException(status_code=503, detail=str(exc)) from exc",
        "    except ZeroDivisionError as exc:\n"
        "        raise HTTPException(status_code=503, detail=str(exc)) from exc",
        "test_an_unreadable_registry_is_a_503_naming_the_file",
    ),
    (
        "load_registry passes a non-registry through",
        V,
        "    if not isinstance(data, dict) or not isinstance(data.get(\"voices\", []), list):",
        "    if False:",
        "test_an_unreadable_registry_is_a_503_naming_the_file",
    ),
    (
        "a broken registry is a ValueError again, read by synthesize as 'no such voice'",
        V,
        "class VoiceRegistryError(RuntimeError):",
        "class VoiceRegistryError(ValueError):",
        "test_a_broken_registry_is_not_mistaken_for_an_unknown_voice",
    ),
    (
        "synthesize answers a broken registry with its 502 again, unlike the catalog's 503",
        M,
        "    except VoiceRegistryError as exc:\n        # The catalog's status for the same fault",
        "    except ZeroDivisionError as exc:\n        # The catalog's status for the same fault",
        "test_synthesize_answers_an_unreadable_registry_as_the_catalog_does",
    ),
    (
        "any image embedder id is loaded, so a token holder picks the worker's download",
        B,
        "        if model and model not in allowed:",
        "        if False:",
        "test_an_unlisted_model_is_refused_before_the_worker",
    ),
    (
        "an unnamed embedder is no longer the listed default",
        B,
        "            spec[\"model\"] = model or allowed[0]",
        "            spec[\"model\"] = model",
        "test_no_model_means_the_listed_default",
    ),
    (
        "the ungated media jobs run unbounded again",
        B,
        "        async with self._light_media:\n",
        "        if True:\n",
        "test_ungated_media_jobs_are_bounded",
    ),
    (
        "the light media bound is taken from the GPU gate instead",
        B,
        "        async with self._light_media:\n",
        "        async with self.gate.hold(model=None, source=None):\n",
        "test_ungated_media_jobs_never_wait_on_the_gpu_gate",
    ),
    # --- a read-aloud whose caller hangs up stops its job (added 2026-10-15) ------------------
    (
        "a read-aloud's job is awaited plainly again, so it synthesises for a caller who left",
        B,
        "            self._run_light_media(self._media_python_for(\"kokoro_tts\"), spec), client_gone,\n",
        "            self._run_light_media(self._media_python_for(\"kokoro_tts\"), spec), None,\n",
        HUNG_UP_READ,
    ),
    (
        "/v1/tts_light hands the broker no way to ask whether its caller is there",
        M,
        "            req.text, voice=req.voice, lang_code=req.lang_code, speed=req.speed,\n"
        "            client_gone=request.is_disconnected,\n",
        "            req.text, voice=req.voice, lang_code=req.lang_code, speed=req.speed,\n"
        "            client_gone=None,\n",
        HUNG_UP_READ,
    ),
    (
        "a read whose caller hung up is answered as a failure (502), not 499",
        M,
        "    except ClientGone as exc:\n"
        "        # Nobody is listening; 499 for the access log, as /v1/chat answers.\n"
        "        return JSONResponse(status_code=499, content={\"detail\": str(exc)})\n",
        "",
        HUNG_UP_READ,
    ),
    (
        # Added 2026-10-15: the OpenAI dialect's speech route watches its caller as well.
        "/openai/v1/audio/speech hands the broker no way to ask whether its caller is there",
        OC,
        "            client_gone=request.is_disconnected)\n",
        "            client_gone=None)\n",
        HUNG_UP_READ + "[openai]",
    ),
    (
        "an OpenAI speech caller who hung up is answered as a failure (502), not 499",
        OC,
        "    except ClientGone as exc:\n"
        "        # Nobody is listening; 499 for the access log, as /v1/tts_light answers.\n"
        "        return error(499, str(exc), type_=\"api_error\", code=\"client_closed_request\")\n",
        "",
        HUNG_UP_READ + "[openai]",
    ),
    # --- a gated job whose caller hangs up stops and frees the gate (added 2026-10-15) --------
    (
        "a gated media job is awaited plainly again, so a caller who left holds the GPU gate",
        B,
        "        return await self._while_there(self._gated_media(spec), client_gone, "
        "every=_GONE_TICK_S,\n",
        "        return await self._while_there(self._gated_media(spec), None, "
        "every=_GONE_TICK_S,\n",
        HUNG_UP_GATED + "[image]",
    ),
    (
        "a gated voice job is awaited plainly again, so a caller who left holds the GPU gate",
        B,
        "            gated(), client_gone, every=_GONE_TICK_S,\n",
        "            gated(), None, every=_GONE_TICK_S,\n",
        HUNG_UP_GATED + "[voice]",
    ),
    (
        "gated /v1/image hands the broker no way to ask whether its caller is there",
        M,
        "            size=req.size, model=req.model, client_gone=request.is_disconnected,\n",
        "            size=req.size, model=req.model, client_gone=None,\n",
        HUNG_UP_GATED + "[image]",
    ),
    (
        "gated /v1/tts hands the broker no way to ask whether its caller is there",
        M,
        "        return await get_broker().tts(segments, client_gone=request.is_disconnected)\n",
        "        return await get_broker().tts(segments, client_gone=None)\n",
        HUNG_UP_GATED + "[tts]",
    ),
    (
        "gated /v1/tts_batch hands the broker no way to ask whether its caller is there",
        M,
        "        return await get_broker().tts_batch(items, client_gone=request.is_disconnected)\n",
        "        return await get_broker().tts_batch(items, client_gone=None)\n",
        HUNG_UP_GATED + "[tts_batch]",
    ),
    (
        "gated /v1/voice/synthesize hands the broker no way to ask whether its caller is there",
        M,
        "                                                   client_gone=request.is_disconnected)\n",
        "                                                   client_gone=None)\n",
        HUNG_UP_GATED + "[voice]",
    ),
    (
        # Re-anchored 2026-10-16 when _hung_up's docstring named the light-media jobs too. Same
        # mutation.
        "a gated job whose caller hung up is answered as a failure (502), not 499",
        M,
        "    listening; 499 for the access log, as /v1/chat answers.\"\"\"\n"
        "    return JSONResponse(status_code=499,",
        "    listening; 499 for the access log, as /v1/chat answers.\"\"\"\n"
        "    return JSONResponse(status_code=502,",
        HUNG_UP_GATED,
    ),
    # --- a buffered local chat whose caller hangs up frees the gate (added 2026-10-15) --------
    (
        "a buffered local chat is awaited plainly again, so a caller who left holds the GPU gate",
        B,
        "            client_gone, every=_GONE_TICK_S, doing=\"the local model was answering\")\n",
        "            None, every=_GONE_TICK_S, doing=\"the local model was answering\")\n",
        HUNG_UP_CHAT + "[v1]",
    ),
    (
        "a buffered OpenAI chat is awaited plainly again, so a caller who left holds the GPU gate",
        B,
        "        return await self._while_there(self._openai_chat_gated(body, resolved), client_gone,\n",
        "        return await self._while_there(self._openai_chat_gated(body, resolved), None,\n",
        HUNG_UP_CHAT + "[openai]",
    ),
    (
        "the OpenAI chat route hands the broker no way to ask whether its caller is there",
        OC,
        "                                                         client_gone=request.is_disconnected),\n",
        "                                                         client_gone=None),\n",
        HUNG_UP_CHAT + "[openai]",
    ),
    (
        "a buffered OpenAI chat whose caller hung up is answered as a failure (502), not 499",
        OC,
        "        except ClientGone as exc:\n"
        "            # Nobody is listening, and the GPU gate is free again (Broker.openai_chat asks the\n",
        "        except ZeroDivisionError as exc:\n"
        "            # Nobody is listening, and the GPU gate is free again (Broker.openai_chat asks the\n",
        HUNG_UP_CHAT + "[openai]",
    ),
    # --- a light-media job whose caller hangs up gives its slot back (added 2026-10-16) -------
    (
        "an image embedding is awaited plainly again, so a caller who left holds a light-media slot",
        B,
        "            self._run_light_media(self.settings.media_python, spec), client_gone,\n",
        "            self._run_light_media(self.settings.media_python, spec), None,\n",
        HUNG_UP_LIGHT + "[embed_image]",
    ),
    (
        "a transcription is awaited plainly again, so a caller who left holds a light-media slot",
        B,
        "            self._run_light_media(self._media_python_for(\"transcribe\"), spec), client_gone,\n",
        "            self._run_light_media(self._media_python_for(\"transcribe\"), spec), None,\n",
        HUNG_UP_LIGHT + "[transcribe]",
    ),
    (
        "/v1/embed_image hands the broker no way to ask whether its caller is there",
        M,
        "        return await get_broker().embed_image(req.images, model=req.model,\n"
        "                                              client_gone=request.is_disconnected)\n",
        "        return await get_broker().embed_image(req.images, model=req.model,\n"
        "                                              client_gone=None)\n",
        HUNG_UP_LIGHT + "[embed_image]",
    ),
    (
        "/v1/transcribe hands the broker no way to ask whether its caller is there",
        M,
        "            req.audio_b64, suffix=req.suffix, language=req.language,\n"
        "            client_gone=request.is_disconnected,\n",
        "            req.audio_b64, suffix=req.suffix, language=req.language,\n"
        "            client_gone=None,\n",
        HUNG_UP_LIGHT + "[transcribe]",
    ),
    (
        "/openai/v1/audio/transcriptions hands the broker no way to ask whether its caller is there",
        OC,
        "            audio_b64, suffix=suffix, language=language, client_gone=request.is_disconnected)\n",
        "            audio_b64, suffix=suffix, language=language, client_gone=None)\n",
        HUNG_UP_LIGHT + "[openai_transcription]",
    ),
    (
        "an OpenAI transcription caller who hung up is answered as a failure (502), not 499",
        OC,
        "    except ClientGone as exc:\n"
        "        # Nobody is listening; 499 for the access log, as /v1/transcribe answers.\n",
        "    except ZeroDivisionError as exc:\n"
        "        # Nobody is listening; 499 for the access log, as /v1/transcribe answers.\n",
        HUNG_UP_LIGHT + "[openai_transcription]",
    ),
    (
        "audit_roles re-fetches the installed list for every role",
        B,
        "                name, _fb = await self.resolve_ref(f\"@{role}\", substitute=False, tags=tags)",
        # What the removed Broker._resolve did (2026-10-14): the same resolve, no list passed.
        "                name, _fb = await self.resolve_ref(f\"@{role}\", substitute=False)",
        "test_audit_roles_reads_the_installed_list_once",
    ),
    (
        "planning a substitute ignores the list its caller already holds",
        B,
        "        tags = tags if tags is not None else await self.ollama.tags()\n"
        "        self._digests = {str(m.get(\"name\") or \"\"): m.get(\"digest\") for m in tags}\n"
        "\n"
        "        scored:",
        "        tags = await self.ollama.tags()\n"
        "        self._digests = {str(m.get(\"name\") or \"\"): m.get(\"digest\") for m in tags}\n"
        "\n"
        "        scored:",
        "test_audit_roles_reads_the_installed_list_once",
    ),
    # The roles views, the live box's 6-7 s /v1/roles: each substitute path refetching the list.
    # Re-anchored 2026-10-14 when each call also began handing on the sweep's disabled set (and
    # the sweep its role map). Same mutations.
    (
        "a role whose glob matches nothing plans its substitute on a fresh list read",
        B,
        '            fb = await self._plan_substitute(role, pattern, "not installed", tags=tags,\n',
        '            fb = await self._plan_substitute(role, pattern, "not installed",\n',
        VIEWS_ONCE,
    ),
    (
        "a role whose model is not installed plans its substitute on a fresh list read",
        B,
        '            fb = await self._plan_substitute(role, name, "not installed", tags=tags,\n',
        '            fb = await self._plan_substitute(role, name, "not installed",\n',
        VIEWS_ONCE,
    ),
    (
        "a role whose model is disabled plans its substitute on a fresh list read",
        B,
        '            fb = await self._plan_substitute(role, name, "disabled", tags=tags, '
        "disabled=disabled)",
        '            fb = await self._plan_substitute(role, name, "disabled", disabled=disabled)',
        VIEWS_ONCE,
    ),
    (
        "the roles sweep resolves each role on a fresh list read",
        B,
        '                serving, fb = await self.resolve_ref(f"@{role}", substitute=True,\n'
        "                                                     announce=False, tags=tags,\n",
        '                serving, fb = await self.resolve_ref(f"@{role}", substitute=True,\n'
        "                                                     announce=False,\n",
        VIEWS_ONCE,
    ),
    # --- /v1/status and /v1/roles: the work every poll repeated (audit 2026-10-14) -------------
    (
        # Re-anchored 2026-10-14 when Ollama's two reads moved into _ollama_status. Same mutation.
        "status spawns nvidia-smi on every poll again",
        B,
        "            self._ollama_status(), self._vram_cache().read(), return_exceptions=True)",
        "            self._ollama_status(), gpu.vram(), return_exceptions=True)",
        STATUS_ONCE,
    ),
    (
        "polls that find the card's figure stale each start their own probe",
        G,
        "        if task is None or task.done() or task.get_loop() is not loop:\n"
        "            task = self._task = loop.create_task(self._refresh())",
        "        if True:\n"
        "            task = self._task = loop.create_task(self._refresh())",
        STATUS_ONCE,
    ),
    (
        "the window never closes, so the card's figures freeze at the first read",
        G,
        "        if self._at is not None and time.monotonic() - self._at < self._ttl:",
        "        if self._at is not None:",
        "test_a_poll_after_the_window_reads_the_card_again",
    ),
    (
        "the shipped window is longer than a poller's interval",
        G,
        "VRAM_TTL_S = 2.0",
        "VRAM_TTL_S = 30.0",
        "test_the_shipped_window_is_shorter_than_any_poller_s_interval",
    ),
    (
        "a poll that hangs up cancels the probe every other poll is waiting on",
        G,
        "        return await asyncio.shield(task)",
        "        return await task",
        "test_a_poll_that_hangs_up_does_not_cancel_the_probe_another_is_waiting_on",
    ),
    (
        "closing the broker leaves a probe nobody waits for running",
        B,
        "        if self._vram is not None:\n"
        "            await self._vram.aclose()",
        "        if False:\n"
        "            await self._vram.aclose()",
        STATUS_CLOSE,
    ),
    (
        "closing the cache waits out the probe instead of cancelling it",
        G,
        "            task.cancel()\n"
        "            with contextlib.suppress(BaseException):",
        "            pass\n"
        "            with contextlib.suppress(BaseException):",
        STATUS_CLOSE,
    ),
    (
        # Re-anchored 2026-10-14 with the one above. Same mutation.
        "status reads Ollama and the card one after another again",
        B,
        "        (version, loaded), card = await asyncio.gather(\n"
        "            self._ollama_status(), self._vram_cache().read(), return_exceptions=True)",
        "        (version, loaded), card = [await c for c in (\n"
        "            self._ollama_status(), self._vram_cache().read())]",
        "test_status_reads_ollama_and_the_card_together",
    ),
    (
        "a version that answered is dropped when only the resident list failed",
        B,
        "        if not reachable:\n"
        "            loaded = []",
        "        if not reachable:\n"
        "            loaded, version = [], None",
        "test_status_still_answers_whichever_read_fails",
    ),
    (
        "a card read that raised reaches the status body as the exception",
        B,
        '            "gpu": None if isinstance(card, BaseException) else card,',
        '            "gpu": card,',
        "test_a_card_read_that_raises_is_reported_as_no_card",
    ),
    (
        "nvidia-smi is looked up on PATH on every probe again",
        G,
        "    exe = _nvidia_smi()",
        '    exe = shutil.which("nvidia-smi")',
        "test_nvidia_smi_is_looked_up_on_path_once",
    ),
    (
        "cli_state reads the CLI token file twice again",
        BC,
        "        blockers += self._cli_env_blockers(token_read)",
        "        blockers += self._cli_env_blockers()",
        TOKEN_ONCE,
    ),
    (
        "the blocker check ignores the token read it is handed",
        BC,
        "        _token, problem = token_read or s.cli_token()",
        "        _token, problem = s.cli_token()",
        TOKEN_ONCE,
    ),
    (
        # Re-anchored 2026-10-14 when the call began handing on upstreams.json too. Same mutation.
        "the sweep stops handing on its disabled set, so every role reads disabled.json",
        B,
        "                                                     roles=roles, disabled=disabled,\n",
        "                                                     roles=roles,\n",
        SWEEP_READS,
    ),
    (
        # Re-anchored with the one above. Same mutation.
        "the sweep stops handing on its role map, so every role reads roles.json",
        B,
        "                                                     roles=roles, disabled=disabled,\n",
        "                                                     disabled=disabled,\n",
        SWEEP_READS,
    ),
    (
        "resolve_ref reads roles.json whatever map it is handed",
        B,
        "        roles = self.settings.roles() if roles is None else roles",
        "        roles = self.settings.roles()",
        SWEEP_READS,
    ),
    (
        "resolve_ref reads disabled.json whatever set it is handed",
        B,
        "        if disabled is None:\n"
        "            disabled = self.settings.disabled()\n",
        "        disabled = self.settings.disabled()\n",
        SWEEP_READS,
    ),
    (
        "the substitute planner reads disabled.json whatever set it is handed",
        B,
        "        disabled = self.settings.disabled() if disabled is None else disabled",
        "        disabled = self.settings.disabled()",
        SWEEP_READS,
    ),
    (
        "a role whose glob matches nothing plans its substitute on a fresh disabled.json read",
        B,
        '            fb = await self._plan_substitute(role, pattern, "not installed", tags=tags,\n'
        "                                             disabled=disabled)",
        '            fb = await self._plan_substitute(role, pattern, "not installed", tags=tags)',
        SWEEP_READS,
    ),
    (
        "a role whose model is not installed plans its substitute on a fresh disabled.json read",
        B,
        '            fb = await self._plan_substitute(role, name, "not installed", tags=tags,\n'
        "                                             disabled=disabled)",
        '            fb = await self._plan_substitute(role, name, "not installed", tags=tags)',
        SWEEP_READS,
    ),
    # --- a closed stream closes its inner stream before the gate (audit 2026-10-14) ------------
    (
        "a closed platform stream leaves Ollama's to the finalizer, after the gate is back",
        B,
        "            async with aclosing(self.ollama.chat_stream(\n"
        "                model, messages, options=options, keep_alive=keep_alive, format=format,\n"
        "                think=think,\n"
        "            )) as inner:\n"
        "                async for chunk in inner:\n"
        "                    yield chunk",
        "            async for chunk in self.ollama.chat_stream(\n"
        "                model, messages, options=options, keep_alive=keep_alive, format=format,\n"
        "                think=think,\n"
        "            ):\n"
        "                yield chunk",
        CLOSE_ORDER + "[ollama]",
    ),
    (
        "a closed OpenAI stream leaves Ollama's to the finalizer, after the gate is back",
        B,
        '            async with aclosing(self.ollama.openai_chat_stream({**body, "model": model})) '
        "as inner:\n"
        "                async for chunk in inner:\n"
        "                    yield chunk",
        '            async for chunk in self.ollama.openai_chat_stream({**body, "model": model}):\n'
        "                yield chunk",
        CLOSE_ORDER + "[openai]",
    ),
    (
        "a closed delegated stream leaves the remote's stream and its connection to the finalizer",
        B,
        "            async with aclosing(up.chat_stream(model, messages, options=options,\n"
        "                                               keep_alive=keep_alive, format=format,\n"
        "                                               think=think)) as remote:\n"
        "                async for chunk in remote:\n"
        "                    yield chunk",
        "            async for chunk in up.chat_stream(model, messages, options=options,\n"
        "                                              keep_alive=keep_alive, format=format,\n"
        "                                              think=think):\n"
        "                yield chunk",
        "test_a_closed_delegated_stream_closes_the_remote_stream_with_it",
    ),
    # --- /v1/image refuses a model that draws no images (audit 2026-10-14) -------------------
    (
        # Re-anchored 2026-10-14 when the check became image_backend's (any case). Same mutation.
        "an image call for a model that draws none renders SDXL under its name again",
        B,
        "        if backend is None:\n"
        "            shown = ",
        "        if False:\n"
        "            shown = ",
        "test_an_image_call_for_a_model_that_is_not_an_image_backend_is_refused",
    ),
    (
        # Re-anchored with the one above. Same mutation.
        "the image check judges the name asked for, so an image role is refused",
        B,
        "        backend = image_backend(model)",
        "        backend = image_backend(asked)",
        "test_an_image_role_and_both_backends_still_render",
    ),
    (
        "an image backend's name is compared exactly again, so FLUX-Schnell is a 400",
        B,
        '    folded = name.lower() if isinstance(name, str) else ""',
        '    folded = name if isinstance(name, str) else ""',
        "test_an_image_backend_named_in_any_case_renders_under_its_own_name[FLUX-Schnell-flux-schnell]",
    ),
    (
        "the name as asked goes to the worker and the queue, not the backend's own spelling",
        B,
        "        model = backend\n"
        "        return await self._run_media({",
        "        return await self._run_media({",
        "test_an_image_backend_named_in_any_case_renders_under_its_own_name[SDXL-Turbo-sdxl-turbo]",
    ),
    (
        "/v1/image answers a model that draws none with a 502",
        M,
        "    except NotAnImageModel as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc",
        "    except KeyError as exc:\n"
        "        raise HTTPException(status_code=400, detail=str(exc)) from exc",
        "test_the_image_route_answers_400_for_a_chat_role",
    ),
    (
        "the roles sweep reads the local list before probing any remote",
        B,
        "        tags, *probes = await asyncio.gather(_installed(), *(_probe(n) for n in names))",
        "        tags = await _installed()\n"
        "        probes = await asyncio.gather(*(_probe(n) for n in names))",
        "test_the_roles_sweep_reads_the_local_list_while_the_remotes_are_probed",
    ),
    (
        "list_models probes capabilities one model at a time again",
        B,
        "        all_caps = await asyncio.gather(*(_caps(m, c) for m, c in zip(models, classes)))",
        "        all_caps = [await _caps(m, c) for m, c in zip(models, classes)]",
        "test_list_models_probes_capabilities_together",
    ),
    # The Rails tab's read: the sweep and the picker's local list share one /api/tags.
    (
        "list_models reads the installed list again although its caller holds one",
        B,
        "        models = tags if tags is not None else await self.ollama.tags()",
        "        models = await self.ollama.tags()",
        INVENTORY_ONCE,
    ),
    (
        # Re-anchored 2026-10-14 when the inventory began handing the sweep its registry too.
        "the Rails tab's sweep reads its own list instead of the shared one",
        B,
        "            self._roles_sweep(also_probe=set(registry), tags_read=tags_read, registry=registry),",
        "            self._roles_sweep(also_probe=set(registry), registry=registry),",
        INVENTORY_ONCE,
    ),
    (
        ".env.example ships keep_alive=-1 against a 30m default again",
        E,
        "BROKER_DEFAULT_LOAD_KEEP_ALIVE=30m",
        "BROKER_DEFAULT_LOAD_KEEP_ALIVE=-1",
        "test_every_value_stated_outright_is_the_code_default",
    ),
    (
        ".env.example names a setting the broker never reads",
        E,
        "BROKER_DEFAULT_LOAD_KEEP_ALIVE=30m",
        "BROKER_DEFAULT_LOAD_KEEP_ALIVE=30m\nBROKER_HOST=127.0.0.1",
        "test_every_variable_the_example_names_is_a_setting",
    ),
    # --- Ollama not up: the 503, the log line, the deferred audit ----------------------------
    (
        "an unreachable Ollama escapes the routes as a 500 and a traceback again",
        M,
        "@app.exception_handler(httpx.TransportError)",
        "@app.exception_handler(ZeroDivisionError)",
        DOWN_503,
    ),
    (
        "every failed request logs the outage again",
        O,
        "        first = not self._outage_noted\n",
        "        first = True\n",
        ONCE,
    ),
    (
        # Re-anchored 2026-10-14 when the client moved into the lazy `_client` property (two
        # columns further in). Same mutation.
        "an answer from Ollama no longer ends the outage, so the next one is never logged",
        O,
        '                                           event_hooks={"response": [self._answered]})',
        "                                           event_hooks={})",
        ONCE,
    ),
    (
        "any transport failure is reported as an Ollama outage",
        M,
        "    if sent is None or not sent(exc):\n",
        "    if sent is None:\n",
        "test_a_transport_error_from_elsewhere_is_not_blamed_on_ollama",
    ),
    (
        "a boot before Ollama skips the role audit silently again",
        M,
        '        audit = asyncio.create_task(audit_when_ready(broker), name="deferred-role-audit")',
        "        audit = None",
        "test_a_boot_before_ollama_defers_the_audit_and_prints_it_once_ollama_answers",
    ),
    (
        "shutdown leaves the deferred audit running",
        M,
        "        if audit is not None and not audit.done():\n",
        "        if False:\n",
        "test_shutdown_cancels_an_audit_still_waiting_for_ollama",
    ),
    (
        "the retry wait never caps, so a late Ollama waits hours for its audit",
        M,
        "        delay = min(delay * 2, _AUDIT_RETRY_CAP)",
        "        delay = delay * 2",
        SCHEDULE,
    ),
    (
        "the retry gives up after one minute instead of thirty",
        M,
        "_AUDIT_RETRY_BUDGET = 30 * 60.0",
        "_AUDIT_RETRY_BUDGET = 60.0",
        SCHEDULE,
    ),
    (
        "an audit Ollama never let run ends in silence, which reads as a pass",
        M,
        '    print(f"[broker] ROLE WARNING: role audit skipped: Ollama never answered at "\n'
        '          f"{broker.ollama.base_url} in {_AUDIT_RETRY_BUDGET / 60:g} minutes, so the role map "\n'
        '          "was not checked. Restart the broker once Ollama is up.", file=sys.stderr, flush=True)',
        "    pass",
        SCHEDULE,
    ),
    # --- Ollama's HTTP client is built on first use, once (added 2026-10-14) -------------------
    (
        "Ollama's HTTP client is built with the broker again, half a second for every Broker()",
        O,
        "        self._http: httpx.AsyncClient | None = None\n",
        "        self._http: httpx.AsyncClient | None = httpx.AsyncClient(base_url=self.base_url)\n",
        LAZY,
    ),
    (
        "a fresh HTTP client is built for every request to Ollama",
        O,
        "        if self._http is None:\n"
        "            self._http = httpx.AsyncClient(",
        "        if True:\n"
        "            self._http = httpx.AsyncClient(",
        LAZY,
    ),
    (
        "closing a broker that never asked Ollama anything builds a client to close",
        O,
        "        if self._http is not None:\n"
        "            await self._http.aclose()",
        "        await self._client.aclose()",
        LAZY,
    ),
    (
        "before the client exists, sent() compares with a base URL httpx would not hold, so a "
        "sibling path reads as an Ollama outage",
        O,
        '                      else base.copy_with(raw_path=base.raw_path + b"/"))',
        "                      else base)",
        SENT_ALIKE,
    ),
    # --- a media job leaves nothing on disk; its big JSON stays off the loop (added 2026-10-14)
    (
        "the worker makes its transcribe clip in the system temp again",
        W,
        '    tmp = tempfile.mkdtemp(prefix="broker-stt-", dir=_scratch(spec))',
        '    tmp = tempfile.mkdtemp(prefix="broker-stt-")',
        KILLED + "[transcribe-cancelled]",
    ),
    (
        "the worker makes its tts directory in the system temp again",
        W,
        "    with tempfile.TemporaryDirectory(dir=_scratch(spec)) as td:",
        "    with tempfile.TemporaryDirectory() as td:",
        KILLED + "[tts-cancelled]",
    ),
    (
        "the spec names no scratch directory, so the worker uses the system temp",
        D,
        '        await _in_thread(threads, _write_json, in_path, {**spec, "scratch_dir": str(scratch)})',
        "        await _in_thread(threads, _write_json, in_path, spec)",
        "test_a_finished_job_makes_its_temp_files_in_its_own_directory",
    ),
    (
        "the job's directory is only rmdir'd, so whatever the worker left keeps it",
        D,
        "            shutil.rmtree(path)\n"
        "            return True",
        "            path.rmdir()\n"
        "            return True",
        KILLED + "[tts-timed-out]",
    ),
    (
        "the spec is written in the event loop again",
        D,
        '        await _in_thread(threads, _write_json, in_path, {**spec, "scratch_dir": str(scratch)})',
        '        _write_json(in_path, {**spec, "scratch_dir": str(scratch)})',
        OFF_LOOP,
    ),
    (
        "the result is read in the event loop again",
        D,
        "        result = await _in_thread(threads, _read_result, out_path)",
        "        result = _read_result(out_path)",
        OFF_LOOP,
    ),
    (
        "the removal does not wait for a thread still writing the spec",
        D,
        "    for fut in threads:\n"
        "        with contextlib.suppress(BaseException):    # its failure was the job's to report\n"
        "            await fut\n",
        "",
        "test_a_cancelled_job_removes_its_directory_only_once_its_writing_thread_ends",
    ),
    (
        "a removal a moment's hold refuses is not tried again",
        D,
        "_REMOVE_TRIES = 5",
        "_REMOVE_TRIES = 1",
        "test_a_refused_removal_is_tried_again",
    ),
    (
        # Re-anchored 2026-10-15 when the line stopped naming the media jobs (a voice job's
        # directory gives up there too). Same mutation.
        "a removal refused every time leaves no log line",
        D,
        '    print(f"[broker] job directory {path} could not be removed after {_REMOVE_TRIES} "\n'
        '          f"tries ({failure.__class__.__name__}: {failure.strerror or failure}); the next "\n'
        '          "startup\'s sweep removes it", file=sys.stderr, flush=True)',
        "    pass",
        "test_a_removal_refused_every_time_is_logged_once",
    ),
    # --- what a broker that died mid-job left, swept at the next start (added 2026-10-15) -----
    (
        "the startup never sweeps the media jobs' directories, so a dead broker's spec stays",
        M,
        "    await asyncio.to_thread(broker.sweep_media_temp)\n",
        "",
        SWEPT,
    ),
    (
        # Re-anchored 2026-10-15 when the sweep began taking the voice jobs' name too. Same
        # mutation: the CLI calls' name rule, the function's default, is used.
        "the media sweep looks for the CLI calls' names, so it never finds a job's directory",
        D,
        "    return sweep_call_temps(root, older_than=older_than, name=name)",
        "    return sweep_call_temps(root, older_than=older_than)",
        SWEPT,
    ),
    (
        # Re-anchored 2026-10-15 on the one name rule both kinds are built by. Same mutation.
        "the media sweep takes any name with the prefix, another program's folder included",
        D,
        '    return re.compile(re.escape(prefix) + r"[a-z0-9_]{8}")',
        '    return re.compile(re.escape(prefix) + r".*")',
        SWEPT,
    ),
    (
        # Re-anchored 2026-10-15 on the media jobs' row of the sweep. Same mutation: no timeout
        # counts, so the hour alone decides.
        "the media sweep waits an hour whatever BROKER_MEDIA_TIMEOUT is, so it can clear a long "
        "job still running",
        B,
        '        for kind, name, timeout in (("media", media.JOB_DIR_NAME, s.media_timeout),\n',
        '        for kind, name, timeout in (("media", media.JOB_DIR_NAME, 0),\n',
        "test_the_media_sweep_waits_an_hour_or_three_media_timeouts_if_longer[three-timeouts]",
    ),
    (
        # Re-anchored 2026-10-15 on the print both kinds share. Same mutation.
        "a media sweep that removes or cannot remove a directory says nothing",
        B,
        '                print(f"[broker] {kind} temp sweep: removed {gone} stale {kind} job "\n',
        '                (lambda *a, **k: None)(f"[broker] {kind} temp sweep: removed {gone} stale '
        '{kind} job "\n',
        "test_a_media_sweep_that_cannot_remove_a_directory_says_so_and_the_broker_starts",
    ),
    # --- a voice job's directory, swept by the same rule (added 2026-10-15) ---------------------
    (
        "the startup never sweeps the voice jobs' directories, so a dead broker's text stays",
        B,
        '        for kind, name, timeout in (("media", media.JOB_DIR_NAME, s.media_timeout),\n'
        '                                    ("voice", voice.JOB_DIR_NAME, s.voice_timeout)):\n',
        '        for kind, name, timeout in (("media", media.JOB_DIR_NAME, s.media_timeout),):\n',
        VOICE_SWEPT,
    ),
    (
        "the voice sweep looks for the media jobs' names, so it never finds a voice job's directory",
        B,
        '("voice", voice.JOB_DIR_NAME, s.voice_timeout)',
        '("voice", media.JOB_DIR_NAME, s.voice_timeout)',
        VOICE_SWEPT,
    ),
    (
        "the voice sweep ages a voice job by BROKER_MEDIA_TIMEOUT, so it can clear one still running",
        B,
        '("voice", voice.JOB_DIR_NAME, s.voice_timeout)',
        '("voice", voice.JOB_DIR_NAME, s.media_timeout)',
        VOICE_WAITS + "[three-voice-timeouts]",
    ),
    (
        "the voice sweep drops the hour's floor, so a short BROKER_VOICE_TIMEOUT clears another "
        "broker's job in flight",
        B,
        "                    root, older_than=max(hour, 3 * float(timeout)), name=name)",
        "                    root, older_than=3 * float(timeout), name=name)",
        VOICE_WAITS + "[an-hour-floor]",
    ),
    (
        "a voice job's directory takes a name the voice sweep does not match",
        V,
        "    tmp = Path(tempfile.mkdtemp(prefix=JOB_DIR_PREFIX))",
        '    tmp = Path(tempfile.mkdtemp(prefix="broker-voice-job-"))',
        "test_a_voice_job_makes_its_directory_under_the_name_the_sweep_matches",
    ),
    (
        "a media sweep that fails ends the voice sweep too",
        B,
        '                      f"({exc.__class__.__name__}: {exc})", file=sys.stderr, flush=True)\n'
        "                continue\n",
        '                      f"({exc.__class__.__name__}: {exc})", file=sys.stderr, flush=True)\n'
        "                return 0, 0\n",
        "test_a_sweep_of_one_kind_that_fails_leaves_the_other_swept_and_the_broker_starts",
    ),
    # --- a voice job removes its directory as a media job does (added 2026-10-15) -------------
    (
        "the voice job's removal unlinks and rmdirs once again, so a held file replaces the job's "
        "answer",
        V,
        "        await settle(asyncio.ensure_future(asyncio.to_thread(media._remove_job_dir, tmp)))",
        "        for p in (txt_path, out_path):\n"
        "            p.unlink(missing_ok=True)\n"
        "        try:\n"
        "            tmp.rmdir()\n"
        "        except OSError:\n"
        "            pass",
        VOICE_HELD,
    ),
    (
        "the voice job's removal is one rmtree that ignores errors, so a held file keeps the "
        "directory",
        V,
        "        await settle(asyncio.ensure_future(asyncio.to_thread(media._remove_job_dir, tmp)))",
        '        await asyncio.to_thread(__import__("shutil").rmtree, tmp, True)',
        VOICE_HELD,
    ),
    (
        "the voice job's removal is not waited for through a second cancellation",
        V,
        "        await settle(asyncio.ensure_future(asyncio.to_thread(media._remove_job_dir, tmp)))",
        "        await asyncio.to_thread(media._remove_job_dir, tmp)",
        "test_a_voice_job_cancelled_again_while_it_removes_its_directory_ends_once_it_is_gone",
    ),
    (
        "the give-up line names the media jobs again, though a voice job's directory gives up there",
        D,
        '    print(f"[broker] job directory {path} could not be removed after {_REMOVE_TRIES} "\n',
        '    print(f"[broker] media job directory {path} could not be removed after {_REMOVE_TRIES} "\n',
        VOICE_GIVES_UP + "[fails]",
    ),
    # --- settings files read once per status poll, @role chat and sweep (added 2026-10-14) ----
    (
        "cli_state judges the switch file against a second read of roles.json",
        BC,
        "        eff = s.cli_effective(roles_read=roles_read)",
        "        eff = s.cli_effective()",
        STATUS_ROLES_ONCE + "[switch-file]",
    ),
    (
        # Re-anchored 2026-10-14 when cli_effective began reading roles_read() itself when
        # handed none. Same mutation.
        "cli_effective ignores the read it is handed",
        C,
        "        known, overlay_problem = roles_read if roles_read is not None else self.roles_read()",
        "        known, overlay_problem = self.roles_read()",
        STATUS_ROLES_ONCE + "[switch-file]",
    ),
    (
        # Re-anchored 2026-10-14: the note takes its word on roles.json from the read the names
        # were judged against, whoever made it. Same mutation.
        "the note on a lost role reads roles.json again although it was handed the read",
        C,
        "            # From the read the names were judged against, never a second one.\n"
        "            if overlay_problem:\n",
        "            # From the read the names were judged against, never a second one.\n"
        "            if self.roles_read()[1]:\n",
        STATUS_ROLES_ONCE + "[switch-file-naming-a-lost-role]",
    ),
    (
        "route() hands resolve_ref no role map, so an @role chat reads it twice",
        B,
        "        return (None, *await self.resolve_ref(ref, substitute=substitute, roles=roles,\n",
        "        return (None, *await self.resolve_ref(ref, substitute=substitute,\n",
        "test_an_at_role_chat_reads_roles_json_once[chat]",
    ),
    (
        "a delegated route reads the registry again to build the box's client",
        B,
        "                return self._upstream(up_name, registry), rest, None",
        "                return self._upstream(up_name), rest, None",
        DELEGATED_ONCE,
    ),
    (
        "a delegated route splits its value against a registry read of its own",
        B,
        "            up_name, rest = self.settings.delegate_ref(value, registry)",
        "            up_name, rest = self.settings.delegate_ref(value)",
        DELEGATED_ONCE,
    ),
    (
        "_upstream reads the registry whatever it is handed",
        B,
        "        spec = (self.settings.upstreams() if registry is None else registry)[name]",
        "        spec = self.settings.upstreams()[name]",
        UPSTREAMS_ONCE + "[roles_view]",
    ),
    (
        "the sweep splits each delegated role against a fresh registry read",
        B,
        '        delegated = [self.settings.delegate_ref(p, registry) for p in roles.values() if "::" in p]',
        '        delegated = [self.settings.delegate_ref(p) for p in roles.values() if "::" in p]',
        UPSTREAMS_ONCE + "[roles_view]",
    ),
    (
        "the sweep's rows split each delegated role against a fresh registry read",
        B,
        "                up_name, rest = self.settings.delegate_ref(pattern, registry)",
        "                up_name, rest = self.settings.delegate_ref(pattern)",
        UPSTREAMS_ONCE + "[roles_view]",
    ),
    (
        "delegate_ref reads the registry whatever it is handed",
        C,
        "        registry = self.upstreams() if registry is None else registry",
        "        registry = self.upstreams()",
        UPSTREAMS_ONCE + "[roles_view]",
    ),
    (
        "resolve_ref reads the registry for a role naming a box nobody registered",
        B,
        "            up_name, _rest = self.settings.delegate_ref(pattern, upstreams)",
        "            up_name, _rest = self.settings.delegate_ref(pattern)",
        UPSTREAMS_ONCE + "[roles_view]",
    ),
    (
        "the inventory hands the sweep no registry, so the sweep reads it again",
        B,
        "            self._roles_sweep(also_probe=set(registry), tags_read=tags_read, registry=registry),",
        "            self._roles_sweep(also_probe=set(registry), tags_read=tags_read),",
        UPSTREAMS_ONCE + "[roles_inventory]",
    ),
    (
        "a sweep with no delegated role reads upstreams.json anyway",
        B,
        "            registry = self.settings.upstreams() if wanted else {}",
        "            registry = self.settings.upstreams()",
        SWEEP_READS,
    ),
    (
        "a down Ollama is dialled for the resident list as well, two failed connects a poll",
        B,
        "        except Exception as exc:  # noqa: BLE001 - status must never raise\n"
        "            return exc, exc",
        "        except Exception as exc:  # noqa: BLE001 - status must never raise\n"
        "            version = exc",
        "test_a_down_ollama_is_dialled_once_per_poll",
    ),
    # --- an owner's CLI call and an OpenAI-dialect @role read each file once (added 2026-10-14)
    (
        "cli_effective reads roles.json for the names and again for the note",
        C,
        "        known, overlay_problem = roles_read if roles_read is not None else self.roles_read()",
        "        known, overlay_problem = roles_read if roles_read is not None else (\n"
        "            self.roles(), self.roles_read()[1])",
        OWNER_ONCE + "[chat-switched-with-a-model]",
    ),
    (
        "the decision reads the switch file whatever it is handed",
        BC,
        "        if switches is None:\n            switches = s.cli_effective()\n",
        "        switches = s.cli_effective()\n",
        OWNER_ONCE + "[chat-switched-with-a-model]",
    ),
    (
        # Re-anchored 2026-10-14 when the attempt began carrying the token read too. Same
        # mutation: the switch-file read is dropped, the token read kept.
        "the attempt carries no read to its slot, so the re-decision reads again",
        BC,
        "        return _CliAttempt(model=model, who=who, rail=rail, token=self._cli_active_seq,\n"
        "                           switches=switches, token_read=token_read)",
        "        return _CliAttempt(model=model, who=who, rail=rail, token=self._cli_active_seq,\n"
        "                           token_read=token_read)",
        OWNER_ONCE + "[chat-switched-with-a-model]",
    ),
    (
        # Re-anchored 2026-10-14 when the re-decision began taking the token read on the next
        # line. Same mutation.
        "a call that took a free slot is decided again on a fresh read",
        BC,
        "                att.model, requester, None if waits else att.switches,\n",
        "                att.model, requester, None,\n",
        OWNER_ONCE + "[chat_stream-switched-with-a-model]",
    ),
    (
        "the model a call passes is read from the switch file again",
        BC,
        "            att.cli_model = self._cli_model_for(att.model, att.switches)\n",
        # Re-anchored 2026-10-15: `switches` is required now, so dropping it would "fire" on
        # the TypeError. The second read is the mutation.
        "            att.cli_model = self._cli_model_for(att.model,\n"
        "                                                self.settings.cli_effective())\n",
        OWNER_ONCE + "[chat-switched-with-a-model]",
    ),
    (
        # Re-anchored 2026-10-14, as the free-slot case above. Same mutation.
        "a call that waited is decided again on its arrival's read",
        BC,
        "                att.model, requester, None if waits else att.switches,\n",
        "                att.model, requester, att.switches,\n",
        "test_an_owner_s_cli_call_that_waited_for_its_slot_reads_each_file_once_more",
    ),
    # --- the token file, read once per owner call that took a free slot (2026-10-14) ---------
    (
        "the decision reads the token file whatever it is handed",
        BC,
        "        if token_read is None:\n            token_read = s.cli_token()\n",
        "        token_read = s.cli_token()\n",
        CALL_TOKEN_ONCE + "[chat]",
    ),
    (
        "the attempt carries no token read to its slot, so the re-decision reads it again",
        BC,
        "                           switches=switches, token_read=token_read)",
        "                           switches=switches)",
        CALL_TOKEN_ONCE + "[chat]",
    ),
    (
        "a call that took a free slot reads the token file again when it gets it",
        BC,
        "                None if waits else att.token_read)\n",
        "                None)\n",
        CALL_TOKEN_ONCE + "[chat_stream]",
    ),
    (
        "a call that waited is decided again on its arrival's token read",
        BC,
        "                None if waits else att.token_read)\n",
        "                att.token_read)\n",
        WAITED_TOKEN,
    ),
    (
        "the re-decision's token read is not kept, so a call that waited carries its arrival's",
        BC,
        "            still, why_now, att.switches, att.token_read = self._cli_decide(\n",
        "            still, why_now, att.switches, _token_read = self._cli_decide(\n",
        WAITED_TOKEN,
    ),
    (
        "the slot hands the client no token read, so the client reads the file again",
        BC,
        "            att.client = self._cli_client(timeout=left, model=att.cli_model,\n"
        "                                          token_read=att.token_read)\n",
        "            att.client = self._cli_client(timeout=left, model=att.cli_model)\n",
        CALL_TOKEN_ONCE + "[chat]",
    ),
    (
        "the client reads the token file whatever it is handed",
        BC,
        "        token, _problem = token_read or self.settings.cli_token()\n",
        "        token, _problem = self.settings.cli_token()\n",
        CALL_TOKEN_ONCE + "[chat_stream]",
    ),
    (
        "an OpenAI-dialect @role's resolve reads the role map again",
        B,
        "                return await self.resolve_ref(model, substitute=True, roles=roles,\n",
        "                return await self.resolve_ref(model, substitute=True,\n",
        OPENAI_ONCE,
    ),
    (
        "an OpenAI-dialect @role naming a box nobody registered reads the registry again",
        B,
        "                                              upstreams=registry)\n"
        "        disabled = self.settings.disabled()\n",
        "                                              upstreams=None)\n"
        "        disabled = self.settings.disabled()\n",
        OPENAI_ONCE,
    ),
    (
        "an OpenAI-dialect embedder's disabled check reads disabled.json again",
        B,
        "        if _is_disabled(resolved, disabled):\n            raise ModelDisabledError(resolved)",
        "        if _is_disabled(resolved, self.settings.disabled()):\n"
        "            raise ModelDisabledError(resolved)",
        OPENAI_ONCE,
    ),
    # Added 2026-10-16 with broker_cli.py: Broker's CLI methods are written there and RUN in
    # broker.py's namespace (broker.py, "where Broker's methods live").
    (
        "broker_cli.py's methods read a copy of broker.py's names, so a patch of app.broker "
        "misses them",
        B,
        '    exec(compile(path.read_bytes(), str(path), "exec"), globals(), ns)\n',
        '    exec(compile(path.read_bytes(), str(path), "exec"), dict(globals()), ns)\n',
        "test_a_patch_of_app_broker_s_history_caps_reaches_the_method_broker_cli_py_writes",
    ),
    (
        "a file of methods may bind a name Broker has, and silently replaces it",
        B,
        "        if taken:\n",
        "        if False:\n",
        "test_a_file_that_binds_a_name_the_class_has_is_refused_and_changes_nothing",
    ),
    (
        "a file of methods that ran is not recorded, so a file never run looks the same",
        B,
        "        _METHOD_FILES.append(path)\n",
        "",
        "test_every_file_of_methods_runs_once",
    ),
]


def run_suite() -> SuiteRun:
    return run_bounded([str(PY), "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=_TREE_ENV)


def main() -> int:
    only = parse_harness_args(MUTATIONS, filter=True).only
    chosen = [m for m in MUTATIONS if not only or any(o in m[0] for o in only)]
    code, out, hung = run_suite()
    if code != 0:
        print("BASELINE TIMEOUT -- the unmutated suite did not finish in time.\n" if hung
              else "BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in chosen:
        path = BROKER / relpath
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
