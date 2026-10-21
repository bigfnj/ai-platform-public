"""Mutation harness for the bodies the gateway carries. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe apps\\platform\\backend\\tests\\mutate_request_body.py

Run this before trusting a change to `_forward`'s body handling, `_capped_body`,
`_request_ceiling`, config.MAX_REQUEST_BYTES or the PLATFORM_MAX_REQUEST_BYTES default, or to
RC035 in tools/rail_conformance.py; or to the streamed response (`_RelayedResponse`,
`_CutShortGuard`, `_RELAY_DROP`); or to the relay's two clients (`_relay_clients`, their limits,
the idle expiry they share, `_KEEPALIVE_EXPIRY`, which requests count as uploads); or to the
platform routes' own ceiling (`_PlatformBodyCeiling`, `_PLATFORM_BODY_MAX`,
`_PLATFORM_BODY_ROOM`).

The relay used to read every proxied body whole with `await request.body()`, and every way back
to that is quiet: a buffered relay hands the rail exactly the same bytes, and a relay with no
ceiling answers the same 200 to every upload a test is likely to send. The same is true of the
response: a relay that reads the rail's answer whole hands the client the same bytes, only
later and after holding all of them. Each mutation names the tests that must fail; another test
failing is FIRED-WRONG-TEST. Each CONTROL rewrites the code without changing what it does and
must SURVIVE: one that fires means a test pins wording or order rather than behaviour.
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
BACKEND = REPO / "apps" / "platform" / "backend"
CORE = REPO / "packages" / "platform_core"
PY = sys.executable
T = "apps/platform/backend/tests/"
# The first path decides pytest's config file (asyncio_mode lives in the backend's pyproject).
SUITE = [T + f for f in (
    "test_proxy_request_body.py", "test_proxy_relay_headers.py", "test_proxy_sse_and_auth.py",
    "test_root_assets.py", "test_compose_passthrough.py", "test_proxy_response_stream.py",
    "test_proxy_upload_pool.py", "test_platform_body_ceiling.py", "test_proxy_keepalive.py",
)] + ["tools/tests/test_rc035_request_ceiling.py"]

MAIN = "apps/platform/backend/platform_gateway_app/main.py"
CONFIG = "apps/platform/backend/platform_gateway_app/config.py"
# RC035's rule file, which tools/rail_conformance.py runs (its "where the rules live").
RC = "tools/rail_conformance_rules/rc028_rc035.py"
COMPOSE = "deploy/docker-compose.yml"

IEP_CEILING = "test_the_iep_tile_has_the_26_mb_default_not_the_folder_ceiling"

STREAMED = "test_a_streamed_body_reaches_the_rail_as_it_arrives_and_intact"
CHUNKED_OVER = "test_a_chunked_body_past_the_ceiling_is_cut_off_one_chunk_later"
DECLARED_OVER = "test_a_declared_300_mb_body_to_a_25_mb_rail_is_refused_before_a_byte_is_read"
AT_CEILING = "test_a_body_exactly_at_the_ceiling_reaches_the_rail_byte_for_byte"
ONE_OVER = "test_one_byte_over_the_ceiling_is_refused"
NO_BODY = "test_a_request_with_no_body_sends_none_and_reads_none"
WATCH = "test_watch_builder_refuses_13_mb_that_the_default_would_admit"
OPENMAIC = "test_openmaic_admits_the_200_mb_its_upstream_accepts"
RECIPES = "test_recipe_book_admits_ten_25_mb_files_in_one_import"
SSE_CAP = "test_an_event_stream_request_body_is_capped_too"
SSE_STREAM = "test_an_event_stream_request_body_streams_through_intact"
DOWNLOAD = "test_a_200_mb_download_is_relayed_a_chunk_at_a_time_and_arrives_byte_exact"
ENCODING = "test_the_rails_encoding_and_length_describe_the_bytes_relayed"
HEAD = "test_a_head_relays_the_rails_length_and_no_body"
GONE = "test_a_client_that_goes_away_stops_the_relay_and_closes_the_rails_response"
CUT = "test_a_rail_that_breaks_off_mid_body_leaves_the_download_cut_short"
READ_TIMEOUT = "test_an_ordinary_request_streams_but_keeps_its_read_timeout"
BEHIND_UPLOADS = "test_with_101_slow_uploads_in_flight_a_page_load_and_a_save_still_answer"
BEHIND_STREAMS = "test_with_101_event_streams_open_a_page_load_still_answers"
LIFESPAN = "test_the_lifespan_builds_both_clients_and_closes_both"
NO_SLOT = "test_an_upload_that_cannot_get_a_slot_is_a_503_to_retry_and_reads_nothing"
IDLE_REUSE = "test_a_request_after_an_idle_spell_is_not_sent_on_a_connection_the_rail_is_closing"
IDLE_FIGURE = "test_both_pools_expire_idle_connections_well_inside_uvicorns_keep_alive"
SSE_BODY = "test_an_event_stream_that_carries_a_body_is_not_an_upload"
LOGIN_50MB = "test_a_declared_50_mb_login_is_a_413_before_a_byte_is_read"
LOGIN_CHUNKED = "test_a_chunked_login_past_the_ceiling_is_cut_off_one_chunk_later"
LOGIN_AT = "test_a_login_exactly_at_the_ceiling_reaches_login_and_one_byte_more_does_not"
EVERY_ROUTE = "test_every_body_route_refuses_a_declared_2_mb_body_unread"
RELAY_FREE = "test_the_rail_relay_is_not_held_to_the_platform_ceiling"
TRANSCRIBE_IN = "test_a_signed_in_transcribe_takes_its_15_mb_room"
TRANSCRIBE_ANON = "test_an_anonymous_transcribe_gets_the_ordinary_ceiling"
TRANSCRIBE_ROOM = "test_transcribes_room_holds_the_largest_audio_the_route_accepts_and_no_more"

MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "the Content-Length pre-check is dropped, so a declared 300 MB is read and relayed",
        MAIN,
        "    if declared is not None and declared > limit:\n"
        "        raise _too_large(app_name, limit)\n",
        "",
        [DECLARED_OVER, WATCH],
    ),
    (
        "the running count is dropped, so a chunked body is never stopped",
        MAIN,
        "        if seen > limit:\n            raise _BodyTooLarge\n",
        "",
        [CHUNKED_OVER, ONE_OVER, SSE_CAP],
    ),
    (
        "the relay reads the body whole again (request.body())",
        MAIN,
        '    body: bytes | AsyncIterator[bytes] = b""\n'
        '    chunked = "transfer-encoding" in request.headers\n'
        "    if declared:\n"
        '        headers["content-length"] = str(declared)\n'
        "        body = _capped_body(request, limit)\n"
        "    elif chunked:\n"
        "        body = _capped_body(request, limit)\n",
        '    chunked = "transfer-encoding" in request.headers\n'
        "    body = await request.body()\n",
        [STREAMED, CHUNKED_OVER, NO_BODY, SSE_CAP, SSE_STREAM],
    ),
    (
        "the manifest's figure is ignored, so every rail gets the platform default",
        MAIN,
        "    declared = MAX_REQUEST_BYTES.get(app_name)\n"
        "    if declared is not None:\n        return declared\n",
        "",
        [WATCH, OPENMAIC, RECIPES],
    ),
    (
        "a request with no body is sent an empty stream, labelled chunked",
        MAIN,
        "    elif chunked:\n"
        "        body = _capped_body(request, limit)\n",
        "    else:\n        body = _capped_body(request, limit)\n",
        [NO_BODY, "test_a_head_for_a_root_asset_sends_no_body_and_reads_none"],
    ),
    (
        "a known length is not passed on, so the rail sees every body chunked",
        MAIN,
        '        headers["content-length"] = str(declared)\n',
        "",
        [AT_CEILING, OPENMAIC],
    ),
    (
        "the event-stream branch reads the body whole",
        MAIN,
        "            request.method, target, content=body, headers=headers,\n"
        "            timeout=httpx.Timeout(600.0, read=None),",
        "            request.method, target, content=await request.body(), headers=headers,\n"
        "            timeout=httpx.Timeout(600.0, read=None),",
        [SSE_CAP, SSE_STREAM],
    ),
    (
        "a body cut off mid-stream escapes as an unhandled error instead of a 413",
        MAIN,
        "    except _BodyTooLarge:\n        raise _too_large(app_name, limit) from None\n"
        "    except httpx.HTTPError as exc:\n"
        '        raise HTTPException(status_code=502, detail=f"{app_name} backend unreachable: '
        '{exc}") from exc',
        "    except httpx.HTTPError as exc:\n"
        '        raise HTTPException(status_code=502, detail=f"{app_name} backend unreachable: '
        '{exc}") from exc',
        [CHUNKED_OVER],
    ),
    (
        "off by one: a body exactly at the ceiling is refused",
        MAIN,
        "        if seen > limit:",
        "        if seen >= limit:",
        [AT_CEILING],
    ),
    # --- the response ---------------------------------------------------------------------
    (
        "the relay reads the rail's answer whole before sending a byte",
        MAIN,
        "            async for chunk in self._upstream.aiter_raw():\n                yield chunk\n",
        "            yield await self._upstream.aread()\n",
        [DOWNLOAD, GONE],
    ),
    (
        "decoded bytes go out under the rail's Content-Encoding and length",
        MAIN,
        "            async for chunk in self._upstream.aiter_raw():",
        "            async for chunk in self._upstream.aiter_bytes():",
        [ENCODING],
    ),
    (
        "the rail's Content-Length is dropped again",
        MAIN,
        '_RELAY_DROP = (_HOP_BY_HOP - {"content-length"}) | {"content-type"}',
        '_RELAY_DROP = _HOP_BY_HOP | {"content-type"}',
        [DOWNLOAD, ENCODING, HEAD],
    ),
    (
        "the rail's response is left open when the client goes away",
        MAIN,
        "        try:\n            await super().__call__(scope, receive, send)\n"
        "        finally:\n            with anyio.CancelScope(shield=True):\n"
        "                await self._upstream.aclose()\n",
        "        await super().__call__(scope, receive, send)\n",
        [GONE, CUT],
    ),
    (
        "the close is not shielded, so the cancellation that ended the stream cancels it",
        MAIN,
        "            with anyio.CancelScope(shield=True):\n"
        "                await self._upstream.aclose()\n",
        "            await self._upstream.aclose()\n",
        [GONE],
    ),
    (
        "a rail that breaks off mid-body is never marked as cut",
        MAIN,
        "        except Exception as exc:\n            setattr(request.state, _RELAY_CUT,",
        "        except ZeroDivisionError as exc:\n            setattr(request.state, _RELAY_CUT,",
        [CUT],
    ),
    (
        "the guard passes on the final message of a cut response",
        MAIN,
        "                    and state.get(_RELAY_CUT)):",
        "                    and False):",
        [CUT],
    ),
    (
        "the guard is never installed",
        MAIN,
        "app.add_middleware(_CutShortGuard)\n",
        "",
        [CUT],
    ),
    (
        "ordinary traffic loses its read timeout too",
        MAIN,
        "            request.method, target, content=body, headers=headers,\n        )\n",
        "            request.method, target, content=body, headers=headers,\n"
        "            timeout=httpx.Timeout(600.0, read=None),\n        )\n",
        [READ_TIMEOUT],
    ),
    # --- the relay's two clients --------------------------------------------------------------
    (
        "the lifespan builds one shared client again, on httpx's default pool",
        MAIN,
        "    app.state.http, app.state.http_body = _relay_clients()",
        "    app.state.http = app.state.http_body = httpx.AsyncClient(timeout=600.0)",
        [BEHIND_UPLOADS, BEHIND_STREAMS, LIFESPAN],
    ),
    (
        "the relay's own client is capped again",
        MAIN,
        "_RELAY_LIMITS = httpx.Limits(max_connections=None,",
        "_RELAY_LIMITS = httpx.Limits(max_connections=100,",
        [BEHIND_STREAMS],
    ),
    # --- keep-alive: idle connections expire before the rail's (added 2026-10-14) -------------
    (
        "both pools keep an idle connection as long as the rail's uvicorn does again",
        MAIN,
        "_KEEPALIVE_EXPIRY = 2.0\n",
        "_KEEPALIVE_EXPIRY = 5.0\n",
        [IDLE_REUSE, IDLE_FIGURE],
    ),
    (
        "the relay's pool goes back to httpx's default expiry",
        MAIN,
        "max_keepalive_connections=20,\n                             keepalive_expiry=_KEEPALIVE_EXPIRY)",
        "max_keepalive_connections=20,\n                             keepalive_expiry=5.0)",
        [IDLE_REUSE, IDLE_FIGURE],
    ),
    (
        "the uploads' pool goes back to httpx's default expiry",
        MAIN,
        "max_keepalive_connections=4,\n                              keepalive_expiry=_KEEPALIVE_EXPIRY)",
        "max_keepalive_connections=4,\n                              keepalive_expiry=5.0)",
        [IDLE_REUSE, IDLE_FIGURE],
    ),
    (
        "uploads go out on the uncapped client too",
        MAIN,
        "    client: httpx.AsyncClient = app.state.http_body if upload else app.state.http",
        "    client: httpx.AsyncClient = app.state.http",
        [NO_SLOT],
    ),
    (
        "every body counts as an upload, so a small save queues behind them",
        MAIN,
        "(chunked or (declared or 0) > _UPLOAD_FROM)",
        "(chunked or (declared or 0) > 0)",
        [BEHIND_UPLOADS],
    ),
    (
        "a chunked upload rides the uncapped client",
        MAIN,
        "(chunked or (declared or 0) > _UPLOAD_FROM)",
        "((declared or 0) > _UPLOAD_FROM)",
        [NO_SLOT],
    ),
    (
        "an event stream that carries a body takes an upload slot",
        MAIN,
        "    upload = not event_stream and (chunked",
        "    upload = (chunked",
        [SSE_BODY],
    ),
    (
        "a full upload pool reads as an unreachable rail",
        MAIN,
        "    except httpx.PoolTimeout as exc:",
        "    except ZeroDivisionError as exc:",
        [NO_SLOT],
    ),
    (
        "the lifespan leaves the uploads' client open",
        MAIN,
        "        await app.state.http_body.aclose()\n",
        "",
        [LIFESPAN],
    ),
    # --- the platform's own routes ------------------------------------------------------------
    (
        "the platform ceiling's Content-Length pre-check is dropped",
        MAIN,
        "        if length is not None and length > ceiling:\n"
        "            await _platform_too_large(ceiling)(scope, receive, send)\n"
        "            return\n",
        "",
        [LOGIN_50MB, EVERY_ROUTE],
    ),
    (
        "the platform ceiling stops counting, so a chunked login is read whole",
        MAIN,
        "                if taken > ceiling:\n                    over = True\n",
        "                if False:\n                    over = True\n",
        [LOGIN_CHUNKED],
    ),
    (
        "off by one: a platform body exactly at the ceiling is refused",
        MAIN,
        "                if taken > ceiling:",
        "                if taken >= ceiling:",
        [LOGIN_AT],
    ),
    (
        "the route's own answer to a cut-off body goes out instead of the 413",
        MAIN,
        "            if over:\n"
        "                return          # the route's answer to a body it never got\n",
        "            if False:\n"
        "                return          # the route's answer to a body it never got\n",
        [LOGIN_CHUNKED],
    ),
    (
        "the platform ceiling holds the rail relay too",
        MAIN,
        '        if scope["type"] != "http" or not scope["path"].startswith("/api/platform/"):',
        '        if scope["type"] != "http":',
        [RELAY_FREE, STREAMED],
    ),
    (
        "transcribe's room is open to anyone",
        MAIN,
        "    if room is None or not _signed_in(scope):",
        "    if room is None:",
        [TRANSCRIBE_ANON],
    ),
    (
        "transcribe has no room of its own",
        MAIN,
        '_PLATFORM_BODY_ROOM = {"/api/platform/transcribe": _TRANSCRIBE_MAX_B64 + _PLATFORM_BODY_MAX}',
        "_PLATFORM_BODY_ROOM: dict[str, int] = {}",
        [TRANSCRIBE_IN, TRANSCRIBE_ROOM],
    ),
    (
        "transcribe's room is its audio bound alone, refusing the largest audio's own JSON",
        MAIN,
        '_PLATFORM_BODY_ROOM = {"/api/platform/transcribe": _TRANSCRIBE_MAX_B64 + _PLATFORM_BODY_MAX}',
        '_PLATFORM_BODY_ROOM = {"/api/platform/transcribe": _TRANSCRIBE_MAX_B64}',
        [TRANSCRIBE_ROOM],
    ),
    (
        "the platform ceiling is never added",
        MAIN,
        "app.add_middleware(_PlatformBodyCeiling)\n",
        "",
        [LOGIN_50MB, LOGIN_CHUNKED, EVERY_ROUTE, TRANSCRIBE_ANON],
    ),
    (
        "the default is exactly 25 MiB, refusing a 25 MiB file's own form",
        CONFIG,
        "DEFAULT_MAX_REQUEST_BYTES = 26 * 1024 * 1024",
        "DEFAULT_MAX_REQUEST_BYTES = 25 * 1024 * 1024",
        ["test_a_25_mb_file_and_its_multipart_framing_fit_the_default"],
    ),
    (
        "a blank or typo'd PLATFORM_MAX_REQUEST_BYTES reaches pydantic and kills the gateway",
        CONFIG,
        "        if isinstance(v, str):\n            try:\n"
        "                v = int(v.strip()) if v.strip() else DEFAULT_MAX_REQUEST_BYTES",
        "        return v\n        if isinstance(v, str):\n            try:\n"
        "                v = int(v.strip()) if v.strip() else DEFAULT_MAX_REQUEST_BYTES",
        ["test_a_blank_or_unusable_setting_is_the_default_not_a_dead_gateway"],
    ),
    (
        "the mirror loses watch-builder's figure",
        CONFIG,
        '    "watch-builder": 12648448,    # 12 MiB + 64 KiB\n',
        "",
        [WATCH, "test_the_real_tree_has_no_rc035_finding_and_some_rail_declares"],
    ),
    (
        "compose stops passing PLATFORM_MAX_REQUEST_BYTES to the gateway",
        COMPOSE,
        "      PLATFORM_MAX_REQUEST_BYTES: ${PLATFORM_MAX_REQUEST_BYTES:-}\n",
        "",
        ["test_every_offered_gateway_setting_is_passed_through"],
    ),
    (
        "RC035 stops checking the figure is a whole number",
        RC,
        "        if isinstance(raw, bool) or not isinstance(raw, int):",
        "        if False:",
        ["test_a_figure_that_is_not_a_whole_number_fails"],
    ),
    (
        "RC035 takes `true` for a figure",
        RC,
        "        if isinstance(raw, bool) or not isinstance(raw, int):",
        "        if not isinstance(raw, int):",
        ["test_a_figure_that_is_not_a_whole_number_fails"],
    ),
    (
        "RC035 stops checking the range",
        RC,
        "        elif not MIN_REQUEST_BYTES <= raw <= MAX_REQUEST_BYTES_CAP:",
        "        elif False:",
        ["test_a_figure_out_of_range_fails"],
    ),
    (
        "RC035 checks the rail's own id and not its also_serves tiles",
        RC,
        "    ids = m.catalog_ids()\n    if raw is None and not any(i in mirror for i in ids):",
        "    ids = [m.id]\n    if raw is None and not any(i in mirror for i in ids):",
        ["test_every_also_serves_tile_is_held_to_the_same_figure"],
    ),
    (
        "RC035 stops comparing the mirror with the manifest",
        RC,
        "        if got != held:\n            have = ",
        "        if False:\n            have = ",
        ["test_a_mirror_that_disagrees_fails_naming_both_figures",
         "test_a_declared_figure_with_no_mirror_entry_fails",
         "test_a_stale_mirror_entry_for_a_rail_that_declares_none_fails"],
    ),
    # --- the iep tile's ceiling (2026-10-14): the platform default, not the folder figure ------
    # Anchored on a line the public snapshot keeps. That the manifest's half (rail.json's
    # also_serves_default_ceiling) is tested is the withheld rail's own harness's business.
    (
        "the iep tile is held to a 256 MiB figure in the mirror again",
        CONFIG,
        '    "openmaic": 209715200,        # 200 MiB\n',
        '    "iep": 268435456,\n    "openmaic": 209715200,        # 200 MiB\n',
        [IEP_CEILING],
    ),
    (
        "RC035 holds a tile listed in also_serves_default_ceiling to the rail's figure",
        RC,
        "        held = None if app_id in also and app_id in defaulted else raw\n",
        "        held = raw\n",
        ["test_a_tile_held_to_the_default_has_no_mirror_entry"],
    ),
    (
        "RC035 takes any name in also_serves_default_ceiling",
        RC,
        "        if tile not in also:\n",
        "        if False:\n",
        ["test_a_default_ceiling_that_names_no_served_tile_fails"],
    ),
    (
        "RC035 lets the rail's own id take the default",
        RC,
        "        held = None if app_id in also and app_id in defaulted else raw\n",
        "        held = None if app_id in defaulted else raw\n",
        ["test_a_default_ceiling_that_names_no_served_tile_fails"],
    ),
    (
        "RC035 skips a rail that declares nothing even when the mirror holds a stale entry",
        RC,
        "    if raw is None and not any(i in mirror for i in ids):",
        "    if raw is None:",
        ["test_a_stale_mirror_entry_for_a_rail_that_declares_none_fails"],
    ),
    (
        "RC035 passes quietly when it cannot read the mirror",
        RC,
        "        if raw is not None:\n            out.append(F(\"RC035\", m, \"could not read",
        "        if False:\n            out.append(F(\"RC035\", m, \"could not read",
        ["test_an_unreadable_mirror_warns_never_passes"],
    ),
]

# Rewrites that keep the behaviour. Each must SURVIVE.
CONTROLS: list[tuple[str, str, str, str]] = [
    (
        "CONTROL the mirror lists two rails in the other order",
        CONFIG,
        '    "openmaic": 209715200,        # 200 MiB\n'
        '    "recipe-book": 263192576,     # 251 MiB\n',
        '    "recipe-book": 263192576,     # 251 MiB\n'
        '    "openmaic": 209715200,        # 200 MiB\n',
    ),
    (
        "CONTROL the pre-check is spelled the other way round",
        MAIN,
        "    if declared is not None and declared > limit:",
        "    if declared is not None and limit < declared:",
    ),
    (
        "CONTROL the 413 says the same thing in other words",
        MAIN,
        'f"this request is larger than the {limit / (1024 * 1024):.0f} MB {app_name} accepts"',
        'f"{app_name} accepts at most {limit / (1024 * 1024):.0f} MB in one request"',
    ),
    (
        "CONTROL RC035 reads the also_serves tiles straight from the manifest",
        RC,
        "    also = m.catalog_ids()[1:]\n",
        '    also = list(m.data.get("also_serves") or [])\n',
    ),
    (
        "CONTROL RC035 holds the tile ids in a tuple",
        RC,
        "    ids = m.catalog_ids()\n    if raw is None and not any(i in mirror for i in ids):",
        "    ids = tuple(m.catalog_ids())\n    if raw is None and not any(i in mirror for i in ids):",
    ),
    (
        "CONTROL the response drop set is spelled out",
        MAIN,
        '_RELAY_DROP = (_HOP_BY_HOP - {"content-length"}) | {"content-type"}',
        '_RELAY_DROP = {"host", "connection", "keep-alive", "transfer-encoding", "content-type"}',
    ),
    (
        "CONTROL the upload test is spelled with one byte more and >=",
        MAIN,
        "(chunked or (declared or 0) > _UPLOAD_FROM)",
        "(chunked or (declared or 0) >= _UPLOAD_FROM + 1)",
    ),
    (
        "CONTROL the relay pool's expiry is written as the literal it equals",
        MAIN,
        "max_keepalive_connections=20,\n                             keepalive_expiry=_KEEPALIVE_EXPIRY)",
        "max_keepalive_connections=20,\n                             keepalive_expiry=2.0)",
    ),
    (
        "CONTROL Retry-After is written as a literal",
        MAIN,
        'headers={"Retry-After": str(int(_UPLOAD_POOL_WAIT))}',
        'headers={"Retry-After": "30"}',
    ),
    (
        "CONTROL the platform's 413 says the same thing in other words",
        MAIN,
        'f"this request is larger than the {ceiling / (1024 * 1024):.0f} MB the platform "\n'
        '        f"accepts here")',
        'f"the platform takes at most {ceiling / (1024 * 1024):.0f} MB "\n'
        '        f"in one request here")',
    ),
    (
        "CONTROL the platform prefix is compared by slice",
        MAIN,
        'not scope["path"].startswith("/api/platform/"):',
        'scope["path"][:14] != "/api/platform/":',
    ),
    (
        "CONTROL the guard looks the flag up another way",
        MAIN,
        "                    and state.get(_RELAY_CUT)):",
        "                    and bool(state.get(_RELAY_CUT, \"\"))):",
    ),
]

RUN_TIMEOUT = 300


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # THIS tree first: the venv's editable installs point at one checkout.
    env["PYTHONPATH"] = os.pathsep.join([str(BACKEND), str(CORE)])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PLATFORM_MAX_REQUEST_BYTES", None)
    return env


def witness() -> str | None:
    """Prove the modules under test resolve to THIS tree before any verdict means anything."""
    p = mr.run_tree([PY, "-B", "-c", "import platform_gateway_app.main as m; print(m.__file__)"],
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
        if ln.startswith(("FAILED ", "ERROR ")) and "::" in ln:
            names.add(ln.split("::")[-1].split(" ", 1)[0].split("[", 1)[0])
    return names


def apply(relpath: str, old: str, new: str) -> tuple[str | None, int, str]:
    """(verdict-or-None, exit code, output) for one rewrite; the file is restored either way."""
    path = REPO / relpath
    original = path.read_bytes()
    n, mutated = mr.mutate_bytes(original, old, new)
    if n != 1:
        return f"PATTERN x{n}", 0, ""
    try:
        path.write_bytes(mutated)
        code, out = run_suite()
    finally:
        mr.restore_exact(path, original)
    return None, code, out


def main() -> int:
    mr.parse_harness_args(MUTATIONS, CONTROLS)
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
        pattern, code, out = apply(relpath, old, new)
        if pattern:
            verdict = pattern
        else:
            missing = [t for t in expected if t not in failed_names(out)]
            fired = code not in (0, -1)
            verdict = "TIMEOUT" if code == -1 else "FIRED" if fired and not missing else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            if verdict != "FIRED" and missing:
                print(f"      expected to fail but did not: {', '.join(missing)}")
        results.append((label, verdict))
        print(f"  {verdict:17} {label}")

    print("\ncontrols (each must be SURVIVED):")
    control_bad: list[tuple[str, str]] = []
    for label, relpath, old, new in CONTROLS:
        pattern, code, out = apply(relpath, old, new)
        verdict = pattern or ("TIMEOUT" if code == -1 else "SURVIVED" if code == 0 else "FIRED")
        if verdict != "SURVIVED":
            control_bad.append((label, verdict))
            tail = [ln for ln in out.splitlines() if ln.strip()][-8:]
            print("      " + "\n      ".join(tail))
        print(f"  {verdict:17} {label}")

    code, _ = run_suite()
    print(f"\nrestored tree: {'green' if code == 0 else 'STILL RED -- TREE IS DIRTY'}")
    bad = [r for r in results if r[1] != "FIRED"]
    print(f"{len(results) - len(bad)}/{len(results)} mutations fired the right test")
    print(f"{len(CONTROLS) - len(control_bad)}/{len(CONTROLS)} controls survived")
    for label, verdict in bad + control_bad:
        print(f"  {verdict}: {label}")
    return 0 if not bad and not control_bad and code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
