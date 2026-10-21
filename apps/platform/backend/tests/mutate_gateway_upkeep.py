"""Mutation harness for the gateway's upkeep fixes. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_gateway_upkeep.py
    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_gateway_upkeep.py --list

(--list names every case and the module it lives in, and runs nothing.)

Run this before trusting a change to the rail relay (repeated headers, the stream's encoding,
dot segments; and since 2026-10-14 its fidelity: the client's encoding and raw query, the front
door's credentials and the platform's cookies kept from the rail both ways, HEAD relayed and
answered by the shell and healthz), the WebSocket re-check, the broker-error relay (the
broker's 409 relayed as a 409 at a token revoke and a model toggle), the Models-tab toggle, the
speech body bounds, a read-aloud whose client leaves (since 2026-10-15: its broker request
ends), user deletion, the Rail Manager's user count and (since 2026-10-14) the
setting it names for a rail this box does not run, on the card and in the toggle's refusal, the
peers and rooms sent only to the tiles that read them (since 2026-10-15, HTTP, root assets and
WebSocket), the scheduler's re-entrancy guard and served times, PLATFORM_ALLOWED_WS_ORIGINS's
parsing and compose pass-through, or the BrokerClient error fields those rely on.

Every one of these failed quietly before: a merged Set-Cookie still answers 200, a revoked
terminal stays open, a failed read silently re-enables every disabled model. Each mutation
names the test that must fail; another test failing is FIRED-WRONG-TEST.

WHERE A CASE LIVES. This file holds the cases that quote only the gateway's own code and expect
only tests every tree runs. A case that quotes a rail's tile id, or whose expected tests need a
rail's tile to run, lives in upkeep_cases/<rail>.py and goes wherever that rail goes: the public
snapshot withholds the withheld rails' modules (tools/publish.json), as mutate_rails_cli_switch.py
does with cli_switch_cases/. RAIL_MODULES names every module; load_rail_cases() refuses one it
does not name, a case that rewrites a file neither the gateway's nor its rail's, and, in this
repo, a missing module. In a published tree a missing module is named by its file and skipped,
and only where the tree does not carry its rail either.
"""
from __future__ import annotations

import importlib
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

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:       # imported from elsewhere, not run as a script
    sys.path.insert(0, str(HERE))
from upkeep_cases import GATEWAY  # noqa: E402  (this directory)

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
BROKER = REPO / "services" / "broker"
PY = sys.executable
T = "apps/platform/backend/tests/"
SUITE = [T + f for f in (
    "test_proxy_relay_headers.py", "test_proxy_relay_fidelity.py", "test_ws_recheck.py",
    "test_broker_tokens.py",
    "test_model_toggle.py", "test_speech_bounds.py", "test_tts_light_abort.py",
    "test_user_residue_and_reach.py",
    "test_scheduler_reentry.py", "test_scheduler.py", "test_rails_tab_probe_count.py",
    "test_rail_upstreams.py", "test_gate_queries.py", "test_rails_shared_roles.py",
    "test_compose_passthrough.py", "test_config_ws_origins.py", "test_rail_toggle_message.py",
)] + ["packages/platform_core/tests/test_broker_client_errors.py"]

MAIN = "apps/platform/backend/platform_gateway_app/main.py"
SCHED = "apps/platform/backend/platform_gateway_app/scheduler.py"
MODELS = "apps/platform/backend/platform_gateway_app/models.py"
CLIENT = "packages/platform_core/platform_core/broker_client.py"
RAILS = "apps/platform/backend/platform_gateway_app/rails_models.py"
COMPOSE = "deploy/docker-compose.yml"
INSTALLER_COMPOSE = "deploy/installer/docker-compose.installer.yml"
CONFIG = "apps/platform/backend/platform_gateway_app/config.py"

#: Every module in upkeep_cases/ that holds a rail's cases, by file stem (the rail's directory
#: under rails/ with `-` spelled `_`).
RAIL_MODULES = ("edu_suite", "iep")
#: The public snapshot withholds tools/publish.py: that file's absence is how a harness knows it
#: runs there (mutate_rails_cli_switch.py reads it the same way).
PUBLISHED = not (REPO / "tools" / "publish.py").is_file()

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "the relay copies headers through a dict again, merging two Set-Cookie lines",
        MAIN,
        "    items = (upstream_headers.multi_items() if hasattr(upstream_headers, \"multi_items\")\n"
        "             else upstream_headers.items())",
        "    items = upstream_headers.items()",
        ["test_the_ordinary_relay_keeps_each_set_cookie_line",
         "test_the_streaming_relay_keeps_each_set_cookie_line"],
    ),
    (
        "the relay stops dropping the headers it sets itself",
        MAIN,
        "        if k.lower() not in _RELAY_DROP:",
        "        if True:",
        ["test_the_ordinary_relay_keeps_each_set_cookie_line",
         "test_the_streaming_relay_keeps_each_set_cookie_line"],
    ),
    (
        "the browser's whole Cookie header reaches the rail again, session included",
        MAIN,
        "    rail_cookies = _rail_cookie_header(request.headers)",
        '    rail_cookies = request.headers.get("cookie")',
        ["test_the_session_and_access_cookies_are_not_forwarded"],
    ),
    (
        "Cloudflare Access's cookie is forwarded",
        MAIN,
        '_PLATFORM_COOKIES = {"cf_authorization"}',
        "_PLATFORM_COOKIES: set[str] = set()",
        ["test_the_session_and_access_cookies_are_not_forwarded"],
    ),
    (
        "the cookie names are compared case-sensitively, so PLATFORM_SESSION passes",
        MAIN,
        "            if name and name.lower() not in drop:",
        "            if name and name not in drop:",
        ["test_a_case_variant_or_a_second_cookie_line_does_not_smuggle_it_through"],
    ),
    (
        "a renamed session cookie is forwarded because only the default name is stripped",
        MAIN,
        '    return _PLATFORM_COOKIES | {"platform_session", str(session).lower()}',
        '    return _PLATFORM_COOKIES | {"platform_session"}',
        ["test_a_renamed_session_cookie_is_the_one_stripped",
         "test_a_renamed_session_cookie_cannot_be_set_by_a_rail_either"],
    ),
    # --- the relay's fidelity (test_proxy_relay_fidelity.py, added 2026-10-14) -----------------
    (
        "a client that names no encoding is sent httpx's gzip again",
        MAIN,
        '    headers.setdefault("accept-encoding", "identity")\n',
        "",
        ["test_a_client_that_names_no_encoding_is_sent_none"],
    ),
    (
        "repeated query keys collapse to the last one again",
        MAIN,
        "    query = request.url.query\n",
        "    query = str(httpx.QueryParams(request.query_params))\n",
        ["test_the_query_string_reaches_the_rail_byte_for_byte"],
    ),
    (
        "the front door's credential headers reach the rail again",
        MAIN,
        '               and k.lower() != "cookie" and k.lower() not in _CREDENTIAL_HEADERS\n'
        "               and not k.lower().startswith(_CREDENTIAL_PREFIXES)}",
        '               and k.lower() != "cookie"}',
        ["test_cloudflare_and_bearer_credentials_stay_at_the_gateway"],
    ),
    (
        "a browser's Authorization reaches the rail again",
        MAIN,
        '_CREDENTIAL_HEADERS = frozenset({"authorization", "proxy-authorization", '
        '"cf-access-jwt-assertion"})',
        '_CREDENTIAL_HEADERS = frozenset({"proxy-authorization", "cf-access-jwt-assertion"})',
        ["test_cloudflare_and_bearer_credentials_stay_at_the_gateway"],
    ),
    (
        "an Access service token's Cf-Access-Client-* headers reach the rail again",
        MAIN,
        '_CREDENTIAL_PREFIXES = ("cf-access-client-",)',
        "_CREDENTIAL_PREFIXES: tuple[str, ...] = ()",
        ["test_cloudflare_and_bearer_credentials_stay_at_the_gateway"],
    ),
    (
        "a rail's Set-Cookie for the platform's own cookies reaches the browser again",
        MAIN,
        '        if k.lower() == "set-cookie" and _set_cookie_name(v) in platform:\n'
        "            continue\n",
        "",
        ["test_a_rail_cannot_set_or_clear_the_platform_session_or_access_cookie",
         "test_a_renamed_session_cookie_cannot_be_set_by_a_rail_either"],
    ),
    (
        "a Set-Cookie's name is compared case-sensitively, so PLATFORM_SESSION passes",
        MAIN,
        '    return line.split(";", 1)[0].split("=", 1)[0].strip().lower()',
        '    return line.split(";", 1)[0].split("=", 1)[0].strip()',
        ["test_a_rail_cannot_set_or_clear_the_platform_session_or_access_cookie"],
    ),
    (
        "HEAD on a rail's API path is a 405 again",
        MAIN,
        '    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],',
        '    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],',
        ["test_head_on_a_rail_api_path_reaches_the_rail"],
    ),
    (
        "healthz takes no HEAD again",
        MAIN,
        '@app.api_route("/api/platform/healthz", methods=["GET", "HEAD"])',
        '@app.get("/api/platform/healthz")',
        ["test_head_on_healthz_answers"],
    ),
    (
        "the shell takes no HEAD again",
        MAIN,
        '    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], name="spa")',
        '    @app.api_route("/{full_path:path}", methods=["GET"], name="spa")',
        ["test_head_on_the_shell_answers_like_its_get",
         "test_an_unknown_api_path_is_still_a_404_by_head_or_get"],
    ),
    (
        "a HEAD for a GET-only platform route reads as 'no such endpoint'",
        MAIN,
        '            allowed = _methods_at(f"/{full_path}") if request.method == "HEAD" else set()',
        "            allowed: set[str] = set()",
        ["test_head_on_a_platform_route_is_still_a_405_naming_its_methods"],
    ),
    (
        "the method lookup counts the catch-all itself, so an unknown path is a 405",
        MAIN,
        '        if (isinstance(route, APIRoute) and route.name != "spa"\n',
        "        if (isinstance(route, APIRoute)\n",
        ["test_an_unknown_api_path_is_still_a_404_by_head_or_get"],
    ),
    (
        "the stream asks the rail for whatever encoding the browser accepts again",
        MAIN,
        '        headers["accept-encoding"] = "identity"\n',
        "",
        ["test_the_streaming_relay_asks_for_an_uncompressed_body"],
    ),
    (
        "a dot segment is relayed, escaping the /api/ prefix",
        MAIN,
        "    if _has_dot_segment(path):\n"
        "        raise HTTPException(status_code=400, detail=\"'.' and '..' path segments are not allowed\")",
        "    if False:\n"
        "        raise HTTPException(status_code=400, detail=\"'.' and '..' path segments are not allowed\")",
        ["test_a_dot_segment_is_refused_before_anything_is_sent"],
    ),
    (
        "the dot check matches substrings, refusing an ordinary dotted name",
        MAIN,
        '    return any(seg in (".", "..") for seg in path.replace(',
        '    return ".." in path or any(seg in (".", "..") for seg in path.replace(',
        ["test_names_that_merely_contain_dots_still_pass"],
    ),
    (
        "a live WebSocket is never re-checked",
        MAIN,
        "    if still_allowed is not None:\n        tasks.add(asyncio.create_task(watch()))",
        "    if False:\n        tasks.add(asyncio.create_task(watch()))",
        ["test_a_revoked_session_ends_the_socket_with_4403"],
    ),
    (
        "a re-check that raises counts as a yes",
        MAIN,
        "            except Exception:  # noqa: BLE001 - a check that cannot run is not a yes\n"
        "                ok = False",
        "            except Exception:  # noqa: BLE001 - a check that cannot run is not a yes\n"
        "                ok = True",
        ["test_a_check_that_raises_is_not_a_yes"],
    ),
    (
        "the re-check ignores a revoked entitlement",
        MAIN,
        "        return (again is not None and again.id == uid and app_name in\n"
        "                entitled_app_ids(db, again, APP_IDS, all_access=again.is_superadmin))",
        "        return again is not None and again.id == uid",
        ["test_the_recheck_says_no_once_the_entitlement_is_revoked"],
    ),
    # The token route's two lines, not the Claude switch's: since the switch route began relaying
    # the broker's 400/409 it has used the same two lines, so each anchor carries the line after it, which
    # differs (the token route falls straight through to its 502; the switch tests a 404 next).
    (
        "the token relay splits the broker's message on ': ' again",
        MAIN,
        "            raise HTTPException(status_code=exc.status, detail=exc.detail or str(exc)) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc",
        "            raise HTTPException(status_code=exc.status, detail=str(exc).split(\": \", 2)[-1]) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc",
        ["test_a_bad_label_is_a_400_carrying_the_brokers_whole_sentence"],
    ),
    (
        "the first-mint refusal is relayed as 'broker unreachable'",
        MAIN,
        "        if exc.status in (400, 409):\n"
        "            raise HTTPException(status_code=exc.status, detail=exc.detail or str(exc)) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc",
        "        if exc.status == 400:\n"
        "            raise HTTPException(status_code=exc.status, detail=exc.detail or str(exc)) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc",
        ["test_the_first_mint_refusal_reaches_the_admin_as_a_409"],
    ),
    (
        "BrokerClient keeps the raw body as the detail",
        CLIENT,
        "    if isinstance(body, dict) and isinstance(body.get(\"detail\"), str):\n"
        "        return body[\"detail\"]",
        "    if False:\n        return body[\"detail\"]",
        ["test_a_fastapi_refusal_keeps_its_whole_detail_and_status"],
    ),
    (
        "a 200 that is not JSON escapes as a decode error again",
        CLIENT,
        "        try:\n            return resp.json()\n        except ValueError as exc:",
        "        try:\n            return resp.json()\n        except ZeroDivisionError as exc:",
        ["test_a_200_that_is_not_json_is_a_broker_error_not_a_decode_error"],
    ),
    (
        "image() drops the caller's model",
        CLIENT,
        "        if model:\n            payload[\"model\"] = model",
        "        if False:\n            payload[\"model\"] = model",
        ["test_image_can_name_a_backend"],
    ),
    (
        "the Models-tab toggle starts from an empty list when the read fails",
        MAIN,
        "    try:\n        disabled = set(await app.state.broker.disabled())\n"
        "    except BrokerError as exc:\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc\n"
        "    if body.enabled:",
        "    disabled = await _disabled_set()\n    if body.enabled:",
        ["test_a_failed_read_writes_nothing_and_is_a_502"],
    ),
    (
        "the Models tab asks /v1/status for resident names again",
        MAIN,
        "        resident = await broker.ps()",
        "        resident = await broker.status()",
        ["test_the_models_tab_reads_residency_from_ps_not_status"],
    ),
    (
        "the speech text bound is dropped",
        MAIN,
        "    text: str = Field(max_length=_TTS_LIGHT_MAX_CHARS)",
        "    text: str",
        ["test_tts_light_body_refuses_what_the_broker_refuses"],
    ),
    (
        "the transcribe suffix pattern is dropped",
        MAIN,
        "    suffix: str | None = Field(default=None, pattern=_SUFFIX_PATTERN)",
        "    suffix: str | None = None",
        ["test_transcribe_body_refuses_what_the_broker_refuses",
         "test_the_unsafe_suffix_is_refused_at_the_gateway"],
    ),
    (
        "the upload bound is dropped",
        MAIN,
        "    audio_b64: str = Field(max_length=_TRANSCRIBE_MAX_B64)",
        "    audio_b64: str",
        ["test_transcribe_body_refuses_what_the_broker_refuses"],
    ),
    # --- a read-aloud whose client leaves ends the broker's request (added 2026-10-15) --------
    (
        "the read-aloud waits for the broker after its client has gone",
        MAIN,
        "        if not task.done() and left.result():\n            raise _ClientLeft()\n",
        "        if False:\n            raise _ClientLeft()\n",
        ["test_a_read_whose_client_leaves_ends_the_brokers_request"],
    ),
    (
        "the client's leaving is asked through is_disconnected(), which the middlewares keep "
        "from ever saying so",
        MAIN,
        "        while (await request.receive())[\"type\"] != \"http.disconnect\":\n            pass\n",
        "        while not await request.is_disconnected():\n            await asyncio.sleep(0.05)\n",
        ["test_a_read_whose_client_leaves_ends_the_brokers_request"],
    ),
    (
        "the broker call is left running once the client has gone",
        MAIN,
        "        left.cancel()\n        if not task.done():\n            task.cancel()\n",
        "        left.cancel()\n",
        ["test_a_read_whose_client_leaves_ends_the_brokers_request"],
    ),
    (
        "a deleted user's theme row survives for the next account on that id",
        MODELS,
        "    theme: Mapped[\"UserTheme | None\"] = relationship(\n"
        "        cascade=\"all, delete-orphan\", uselist=False, single_parent=True\n"
        "    )",
        "    theme: Mapped[\"UserTheme | None\"] = relationship(uselist=False, viewonly=True)",
        ["test_deleting_a_user_deletes_their_theme_row",
         "test_the_next_account_on_a_reused_id_starts_from_the_default_theme"],
    ),
    (
        "the Rail Manager counts Entitlement rows, missing every super-admin",
        MAIN,
        "    grants = {rid: len(reach.get(rid, set()) | supers) for rid in APP_IDS}",
        "    grants = {rid: len(reach.get(rid, set())) for rid in APP_IDS}",
        ["test_a_super_admin_counts_for_every_rail"],
    ),
    (
        "the Rail Manager counts a super-admin's own rows twice",
        MAIN,
        "    grants = {rid: len(reach.get(rid, set()) | supers) for rid in APP_IDS}",
        "    grants = {rid: len(reach.get(rid, set())) + len(supers) for rid in APP_IDS}",
        ["test_a_person_is_counted_once_and_rows_still_count"],
    ),
    # --- the Rail Manager's cause on a rail this box does not run (added 2026-10-14) ----------
    (
        "the card sends no cause, so the shell guesses PLATFORM_ENABLED_APPS",
        MAIN,
        '            "blocked_by": _rail_blocked_by(rid),\n',
        "",
        ["test_the_card_says_which_setting_keeps_each_rail_off",
         "test_a_rail_an_admin_switched_off_has_no_cause",
         "test_the_card_and_the_refusal_name_the_same_setting"],
    ),
    (
        "every rail off the box is blamed on PLATFORM_ENABLED_APPS again",
        MAIN,
        '    return "disabled_apps" if rid in set(settings.disabled_apps) else "not_enabled"',
        '    return "not_enabled"',
        ["test_the_card_says_which_setting_keeps_each_rail_off",
         "test_a_rail_disabled_on_this_box_names_platform_disabled_apps",
         "test_the_card_and_the_refusal_name_the_same_setting"],
    ),
    (
        "a rail this box runs is given a cause",
        MAIN,
        "    if rid in set(settings.enabled_apps):\n        return None\n",
        "",
        ["test_the_card_says_which_setting_keeps_each_rail_off",
         "test_a_rail_an_admin_switched_off_has_no_cause",
         "test_an_installed_rail_still_switches_on"],
    ),
    (
        "the toggle's refusal works out its own cause again, and names the other list",
        MAIN,
        "    blocked = _rail_blocked_by(rid)\n",
        '    blocked = None if rid in set(app.state.settings.enabled_apps) else "not_enabled"\n',
        ["test_a_rail_disabled_on_this_box_names_platform_disabled_apps",
         "test_the_card_and_the_refusal_name_the_same_setting"],
    ),
    (
        "a second Run now starts while the first still runs",
        SCHED,
        "    if key in _in_flight:\n        raise AlreadyRunning(",
        "    if False:\n        raise AlreadyRunning(",
        ["test_a_second_run_now_while_the_first_runs_is_refused"],
    ),
    (
        "a failing run leaves its task marked as running for ever",
        SCHED,
        "    finally:\n        _in_flight.discard(key)\n    row = db.execute(",
        "    finally:\n        pass\n    row = db.execute(",
        ["test_the_guard_is_released_after_a_run_even_a_failing_one"],
    ),
    (
        "the tick fires a task a Run now still holds",
        SCHED,
        "            if key in _in_flight:\n                continue",
        "            if False:\n                continue",
        ["test_a_tick_skips_a_task_a_run_now_is_still_running"],
    ),
    # "every static asset request works out peers and rooms again", "an API request stops
    # carrying peers and rooms" and "a slot no longer says which other rails its role feeds" are
    # in upkeep_cases/ since 2026-10-15: only a tile that reads a list, or shares a role, sees them.
    (
        "a slot names its own rail as sharing its role",
        RAILS,
        '            others = [lbl for lbl in users[slot["role"]] if lbl != rail["label"]]',
        '            others = list(users[slot["role"]])',
        ["test_an_unshared_slot_says_so"],
    ),
    (
        "compose stops passing the session lifetime to the gateway",
        COMPOSE,
        "      PLATFORM_SESSION_TTL_HOURS: ${PLATFORM_SESSION_TTL_HOURS:-168}\n",
        "",
        ["test_the_named_settings_reach_the_gateway",
         "test_every_offered_gateway_setting_is_passed_through"],
    ),
    (
        "compose stops passing the describe cap to the gateway",
        COMPOSE,
        "      PLATFORM_DESCRIBE_CAP: ${PLATFORM_DESCRIBE_CAP:-8}\n",
        "",
        ["test_the_named_settings_reach_the_gateway",
         "test_every_offered_gateway_setting_is_passed_through"],
    ),
    (
        "served schedule times lose their UTC offset again",
        SCHED,
        "    if when.tzinfo is None:\n        when = when.replace(tzinfo=dt.timezone.utc)",
        "    if False:\n        when = when.replace(tzinfo=dt.timezone.utc)",
        ["test_served_times_say_they_are_utc"],
    ),
    # --- the broker's 409, relayed as a 409 (added 2026-10-14) ----------------------------------
    (
        "a revoke the broker cannot write is relayed as a 502 again",
        MAIN,
        "as at the mint route, not a 502 that reads as an outage.\n        if exc.status == 409:",
        "as at the mint route, not a 502 that reads as an outage.\n        if False:",
        ["test_a_revoke_the_broker_cannot_write_is_a_409_in_its_words"],
    ),
    (
        "a model toggle the broker cannot write is relayed as a 502 again",
        MAIN,
        "not as a 502 that reads as the broker failing.\n        if exc.status == 409:\n"
        "            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc\n"
        '    return {"name": body.name, "enabled": body.enabled}',
        "not as a 502 that reads as the broker failing.\n        if False:\n"
        "            raise HTTPException(status_code=409, detail=exc.detail or str(exc)) from exc\n"
        "        raise HTTPException(status_code=502, detail=_broker_detail(exc)) from exc\n"
        '    return {"name": body.name, "enabled": body.enabled}',
        ["test_a_write_the_broker_refuses_is_a_409_in_its_words"],
    ),
    # --- PLATFORM_ALLOWED_WS_ORIGINS (test_config_ws_origins.py, added 2026-10-14) ------------
    (
        "the WS origins are JSON-decoded again, so a bare origin kills the gateway at import",
        CONFIG,
        "    allowed_ws_origins: Annotated[tuple[str, ...], NoDecode] = ()",
        "    allowed_ws_origins: tuple[str, ...] = ()",
        ["test_a_bare_origin_a_comma_list_and_a_json_list_all_load",
         "test_blank_or_an_empty_list_keeps_the_same_origin_default"],
    ),
    (
        "the WS origins lose the app lists' parser",
        CONFIG,
        "        return cls._parse_enabled_apps(v, info)",
        "        return v",
        ["test_a_bare_origin_a_comma_list_and_a_json_list_all_load",
         "test_blank_or_an_empty_list_keeps_the_same_origin_default"],
    ),
    (
        "compose stops passing the WS origins to the gateway",
        COMPOSE,
        '      PLATFORM_ALLOWED_WS_ORIGINS: "${PLATFORM_ALLOWED_WS_ORIGINS:-[]}"\n',
        "",
        ["test_both_compose_files_pass_it_to_the_gateway_with_an_empty_list_default",
         "test_every_offered_gateway_setting_is_passed_through"],
    ),
    (
        "compose passes a blank default, which a gateway image from before stops on",
        COMPOSE,
        '"${PLATFORM_ALLOWED_WS_ORIGINS:-[]}"',
        "${PLATFORM_ALLOWED_WS_ORIGINS:-}",
        ["test_both_compose_files_pass_it_to_the_gateway_with_an_empty_list_default"],
    ),
    (
        "the lean installer's compose stops passing the WS origins to the gateway",
        INSTALLER_COMPOSE,
        '      PLATFORM_ALLOWED_WS_ORIGINS: "${PLATFORM_ALLOWED_WS_ORIGINS:-[]}"\n',
        "",
        ["test_both_compose_files_pass_it_to_the_gateway_with_an_empty_list_default"],
    ),
    # --- each list goes only to the tile that reads it (2026-10-15). The cases that quote a
    # tile's line in either allowlist, or that only the reading tiles' tests see, are in
    # upkeep_cases/; these expect only what a tree without those tiles still runs. ------------
    (
        "the access gate works out and sends peers to every tile again",
        MAIN,
        "                if seg in PEER_TILES:\n"
        "                    request.state.platform_peers = peer_usernames(db, user)\n",
        "                request.state.platform_peers = peer_usernames(db, user)\n",
        ["test_an_api_request_to_a_rail_that_reads_neither_works_out_neither",
         "test_recipe_book_is_sent_neither_list_and_costs_no_workspace_query",
         "test_a_root_asset_works_out_only_its_owners_list"],
    ),
    (
        "the access gate works out and sends rooms to every tile again",
        MAIN,
        "                if seg in ROOM_TILES:\n"
        "                    request.state.platform_rooms = room_ids(db, user)\n",
        "                request.state.platform_rooms = room_ids(db, user)\n",
        ["test_an_api_request_to_a_rail_that_reads_neither_works_out_neither",
         "test_recipe_book_is_sent_neither_list_and_costs_no_workspace_query",
         "test_a_root_asset_works_out_only_its_owners_list"],
    ),
    (
        "every tile's WebSocket handshake carries peers again",
        MAIN,
        "        peers = peer_usernames(db, user) if app_name in PEER_TILES else []\n",
        "        peers = peer_usernames(db, user)\n",
        ["test_each_websocket_handshake_carries_only_the_list_its_tile_reads"],
    ),
    (
        "every tile's WebSocket handshake carries rooms again",
        MAIN,
        "        rooms = room_ids(db, user) if app_name in ROOM_TILES else []\n",
        "        rooms = room_ids(db, user)\n",
        ["test_each_websocket_handshake_carries_only_the_list_its_tile_reads"],
    ),
    (
        # Anchored on the opening alone, which the snapshot keeps too: there publish empties
        # the tuple to `()`, and `("recipe-book", )` is a tuple in both trees.
        "a rail that reads no peers is listed for them",
        CONFIG,
        "PEER_TILES: tuple[str, ...] = (",
        'PEER_TILES: tuple[str, ...] = ("recipe-book", ',
        ["test_an_api_request_to_a_rail_that_reads_neither_works_out_neither",
         "test_recipe_book_is_sent_neither_list_and_costs_no_workspace_query",
         "test_each_websocket_handshake_carries_only_the_list_its_tile_reads",
         "test_a_root_asset_works_out_only_its_owners_list"],
    ),
    (
        "a rail that reads no rooms is listed for them",
        CONFIG,
        "ROOM_TILES: tuple[str, ...] = (",
        'ROOM_TILES: tuple[str, ...] = ("recipe-book", ',
        ["test_an_api_request_to_a_rail_that_reads_neither_works_out_neither",
         "test_recipe_book_is_sent_neither_list_and_costs_no_workspace_query",
         "test_each_websocket_handshake_carries_only_the_list_its_tile_reads",
         "test_a_root_asset_works_out_only_its_owners_list"],
    ),
]

#: How many of MUTATIONS are this file's own; the rail modules' cases follow them.
CORE_CASES = len(MUTATIONS)


def load_rail_cases(package: Path = HERE / "upkeep_cases", published: bool = PUBLISHED,
                    modules: tuple[str, ...] = RAIL_MODULES) -> tuple[dict[str, list], list[str]]:
    """({module: its MUTATIONS}, withheld): every rail module's cases, and the file names of the
    modules this tree withholds.

    A module may be absent only from a published tree, and only where that tree does not carry
    its rail (rails/<the module, with _ spelled ->) either; anything else is a module lost by
    mistake. That, a module file `modules` does not name, and a case that rewrites a file neither
    the gateway's (upkeep_cases.GATEWAY) nor its own rail's would each move cases out of every
    run without a word, so each stops every command, --list included."""
    stray = sorted(p.name for p in package.glob("*.py")
                   if p.stem not in modules and p.stem != "__init__")
    if stray:
        raise SystemExit(f"mutate_gateway_upkeep: {package.name}/{', '.join(stray)} is not in "
                         f"RAIL_MODULES, so its cases would never run")
    found: dict[str, list] = {}
    withheld: list[str] = []
    for mod in modules:
        rail = mod.replace("_", "-")
        if not (package / f"{mod}.py").is_file():
            if not published or (REPO / "rails" / rail).is_dir():
                raise SystemExit(f"mutate_gateway_upkeep: {package.name}/{mod}.py is missing, so "
                                 f"{rail}'s cases cannot run")
            withheld.append(f"{mod}.py")
            continue
        cases = importlib.import_module(f"{package.name}.{mod}").MUTATIONS
        wrong = [m[0] for m in cases if m[1] not in GATEWAY and not m[1].startswith(f"rails/{rail}/")]
        if wrong:
            raise SystemExit(f"mutate_gateway_upkeep: {package.name}/{mod}.py holds cases that "
                             f"rewrite another rail's files: {wrong}")
        found[mod] = cases
    return found, withheld


RAIL_CASES, WITHHELD = load_rail_cases()
for _cases in RAIL_CASES.values():
    MUTATIONS += _cases

RUN_TIMEOUT = 300


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first: the venv's editable installs point at one checkout.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k in ("CLI_LIVE", "JOB_AID_LIVE_TESTS"):
        env.pop(k, None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree(
        [PY, "-B", "-c", "import platform_gateway_app.main as m, platform_core.broker_client as c;"
                         "print(m.__file__); print(c.__file__)"],
        cwd=REPO, capture_output=True, text=True, env=_env(), timeout=RUN_TIMEOUT)
    if p.returncode != 0:
        return f"import failed:\n{p.stdout}{p.stderr}"
    for f in p.stdout.split():
        if not Path(f).resolve().is_relative_to(REPO):
            return f"imported {f}, which is not under {REPO}"
    return None


def run_suite() -> tuple[int, str]:
    try:
        p = mr.run_tree([PY, "-B", "-m", "pytest", *SUITE, "-q", "--no-header",
                         "-p", "no:cacheprovider"],
                        cwd=REPO, capture_output=True, text=True, env=_env(),
                        timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return -1, out + f"\nTIMEOUT after {RUN_TIMEOUT} s"
    return p.returncode, p.stdout + p.stderr


def failed_names(out: str) -> set[str]:
    names = set()
    for ln in out.splitlines():
        if ln.startswith("FAILED ") and "::" in ln:
            names.add(ln.split("::")[-1].split(" ", 1)[0].split("[", 1)[0])
    return names


def main(argv: list[str] | None = None) -> int:
    listing = mr.parse_harness_args(None, argv=argv).listing
    # A module this published tree withholds, with its rail, is named by its file on every run.
    # Its cases are not: they quote the withheld rail's tile ids, or name its tests.
    for name in WITHHELD:
        print(f"  SKIPPED  upkeep_cases/{name}: its rail is not in this published tree")
    if listing:
        # One line per case, tab-separated: where it lives, then its label. Nothing is run.
        where = ["core"] * CORE_CASES + [mod for mod, cases in RAIL_CASES.items() for _ in cases]
        for mod, m in zip(where, MUTATIONS):
            print("\t".join((mod, m[0])))
        print(f"{len(MUTATIONS)} mutations" + (f"; {len(WITHHELD)} modules withheld" if WITHHELD else ""))
        return 0
    bad_import = witness()
    if bad_import:
        print(f"WRONG TREE -- {bad_import}")
        return 2
    code, out = run_suite()
    if code != 0:
        print("BASELINE IS NOT GREEN -- fix that before trusting any mutation result.\n")
        print(out[-2500:])
        return 2
    print(f"baseline green: {out.strip().splitlines()[-1]}\n")

    results: list[tuple[str, str]] = []
    for label, relpath, old, new, expected in MUTATIONS:
        path = REPO / relpath
        original_bytes = path.read_bytes()
        original = original_bytes.decode("utf-8")
        if "\r\n" in original:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        n = original.count(old)
        if n != 1:
            print(f"  ERROR  {label}: pattern found {n} times in {relpath}, expected 1")
            results.append((label, "PATTERN"))
            continue
        try:
            mr.write_like(path, original.replace(old, new), original_bytes)
            code, out = run_suite()
            failed = failed_names(out)
            missing = [t for t in expected if t not in failed]
            fired = code not in (0, -1)
            verdict = "TIMEOUT" if code == -1 else "FIRED" if fired and not missing else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                if missing:
                    print(f"      expected to fail but did not: {', '.join(missing)}")
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
                print("      " + "\n      ".join(tail))
        finally:
            mr.restore_exact(path, original_bytes)

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    for label, verdict in bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
