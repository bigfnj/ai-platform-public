"""Mutation harness for the REAL UpstreamClient. NOT a pytest file -- it rewrites the tree.

    .venv\\Scripts\\python.exe services\\broker\\tests\\mutate_upstream_client.py

Run this, not just pytest, before trusting a change to `app/upstream.py`, or to the broker's
reads of a remote box in `app/broker.py` (/v1/models?upstream=X, /v1/upstreams, the roles
sweep's health probe, the per-box client cache).

Every way this client goes wrong is quiet on the box that runs it. An envelope handed back
unwrapped is iterated as its keys; an httpx exception that escapes a read view turns the Rails
tab into a 500; a health probe on the read timeout makes a dark box cost 30 s instead of 5; a
missing header spelling is a 401 only against the OTHER generation of broker; a client
rebuilt per call costs ~0.5 s each time while still answering correctly, so only a count sees
it; a page where JSON was expected reads as "not found"; a pool that keeps idle connections as
long as the remote does now and then sends a call into a connection being closed; and a client
replaced by an upstreams.json edit holds its pool open for as long as the broker runs. Each
mutation removes one of those guards and names the test in test_upstream_client.py that must
notice.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from _suite_runner import (SuiteRun, mutate_bytes, named_failed, parse_harness_args, restore_exact,
                           run_bounded)

BROKER = Path(__file__).resolve().parents[1]
PY = sys.executable
SUITE = ["tests/test_upstream_client.py"]

U = "app/upstream.py"
UNWRAP = "test_a_list_read_unwraps_the_envelope_and_tolerates_other_shapes"
ONE_CLIENT = "test_a_box_gets_one_http_client_however_many_sweeps_read_it"
EDITED = "test_an_edited_registry_reaches_the_new_address_and_token_on_the_next_call"
KEEPALIVE = "test_a_call_after_an_idle_spell_is_not_sent_on_a_connection_the_remote_is_closing"
RETIRED = "test_a_replaced_client_is_closed_when_its_last_stream_ends_not_at_shutdown"

MUTATIONS: list[tuple[str, str, str, str, str]] = [
    (
        "models() hands back the envelope, so roles_view iterates its keys",
        U,
        '        return resp.get("models", []) if isinstance(resp, dict) else (resp or [])',
        "        return resp",
        UNWRAP,
    ),
    (
        "roles() hands back the envelope",
        U,
        '        return resp.get("roles", []) if isinstance(resp, dict) else (resp or [])',
        "        return resp",
        UNWRAP,
    ),
    (
        "a bare-list inventory from an older broker is discarded",
        U,
        '        return resp.get("models", []) if isinstance(resp, dict) else (resp or [])',
        '        return resp.get("models", []) if isinstance(resp, dict) else []',
        UNWRAP,
    ),
    (
        "status() passes a non-object through, and roles_view calls .get on a list",
        U,
        "        return resp if isinstance(resp, dict) else {}",
        "        return resp",
        "test_status_passes_an_object_and_replaces_anything_else",
    ),
    (
        "a health answer that is not an object raises out of healthy()",
        U,
        "        except (UpstreamError, AttributeError):",
        "        except UpstreamError:",
        "test_healthy_is_a_plain_boolean_for_every_answer",
    ),
    (
        "the health probe waits the full read timeout",
        U,
        '            return bool((await self._get("/healthz", timeout=_HEALTH_TIMEOUT)).get("status")',
        '            return bool((await self._get("/healthz")).get("status")',
        "test_the_health_probe_carries_the_short_timeout",
    ),
    (
        "control-plane reads carry no timeout at all",
        U,
        "                                       timeout=_READ_TIMEOUT if timeout is None else timeout)",
        "                                       timeout=timeout)",
        "test_every_read_carries_the_read_timeout",
    ),
    (
        "a chat ignores the client's configured generation timeout",
        U,
        "                                        timeout=timeout or self.timeout)",
        "                                        timeout=timeout or 600.0)",
        "test_a_chat_carries_the_clients_own_generation_timeout",
    ),
    (
        "a pasted trailing slash doubles every path",
        U,
        '        self.url = url.rstrip("/")',
        "        self.url = url",
        "test_a_trailing_slash_in_the_registry_does_not_double_the_path",
    ),
    (
        "only the bearer spelling is sent, so an older remote answers 401",
        U,
        '        self._headers = ({"Authorization": f"Bearer {token}", "X-Broker-Token": token}',
        '        self._headers = ({"Authorization": f"Bearer {token}"}',
        "test_a_token_is_sent_in_both_header_spellings",
    ),
    (
        "no token still sends an empty bearer header",
        U,
        "                         if token else {})",
        "                         if True else {})",
        "test_no_token_sends_no_auth_header_at_all",
    ),
    (
        "unset chat fields are sent as null instead of being left to the remote's default",
        U,
        "                     (\"format\", format), (\"think\", think)):\n"
        "            if v is not None:\n"
        "                payload[k] = v\n"
        '        return await self._post("/v1/chat", payload)',
        "                     (\"format\", format), (\"think\", think)):\n"
        "            payload[k] = v\n"
        '        return await self._post("/v1/chat", payload)',
        "test_chat_sends_only_the_fields_that_were_set_and_returns_the_answer",
    ),
    (
        "a box that refuses us is reported as one that is down",
        U,
        "        except httpx.HTTPStatusError as exc:\n"
        '            raise self._fail(f"GET {path} -> {exc.response.status_code}", exc) from exc',
        "        except KeyError as exc:\n"
        '            raise self._fail(f"GET {path} -> {exc.response.status_code}", exc) from exc',
        "test_a_refused_read_names_the_box_the_path_and_the_status",
    ),
    (
        "a connection failure escapes as an httpx exception, out of a read view",
        U,
        "        except httpx.HTTPError as exc:\n"
        '            raise self._fail(f"GET {path} unreachable", exc) from exc',
        "        except KeyError as exc:\n"
        '            raise self._fail(f"GET {path} unreachable", exc) from exc',
        "test_an_unreachable_read_is_an_upstream_error_not_an_httpx_one",
    ),
    # --- the broker's /v1/models?upstream=X view -------------------------------------------
    (
        "a remote's inventory waits the client's 30 s read timeout, not the view bound",
        "app/broker.py",
        "            return await asyncio.wait_for(self._upstream(upstream).models(),\n"
        "                                          timeout=_VIEW_PROBE_TIMEOUT)",
        "            return await self._upstream(upstream).models()",
        "test_a_remote_inventory_read_is_bounded_by_the_view_timeout_not_the_read_timeout",
    ),
    (
        "a dark remote's inventory read escapes the route as a 500",
        "app/main.py",
        "    except UpstreamError as exc:\n"
        "        # The remote is down, refusing us, or too slow",
        "    except KeyError as exc:\n"
        "        # The remote is down, refusing us, or too slow",
        "test_the_route_answers_502_naming_the_box_not_a_500",
    ),
    # --- the broker's /v1/upstreams view ------------------------------------------------------
    (
        "the upstreams view probes the boxes one after another again",
        "app/broker.py",
        "        health = await asyncio.gather(*(_healthy(name) for name, _spec in registry))",
        "        health = [await _healthy(name) for name, _spec in registry]",
        "test_the_upstreams_view_probes_two_hung_boxes_at_once",
    ),
    (
        "a box that never answers holds the upstreams view, unbounded",
        "app/broker.py",
        "                return await asyncio.wait_for(self._upstream(name).healthy(),\n"
        "                                              timeout=_VIEW_PROBE_TIMEOUT)",
        "                return await self._upstream(name).healthy()",
        "test_a_box_that_never_answers_costs_the_upstreams_view_one_bound",
    ),
    (
        "the upstreams view lists the boxes in registry order, not by name",
        "app/broker.py",
        "        registry = sorted(self.settings.upstreams().items())\n\n"
        "        async def _healthy(name: str) -> bool:",
        "        registry = list(self.settings.upstreams().items())\n\n"
        "        async def _healthy(name: str) -> bool:",
        "test_a_box_that_never_answers_costs_the_upstreams_view_one_bound",
    ),
    # --- the roles sweep's health probe ---------------------------------------------------------
    (
        "the roles sweep's health probe waits on httpx alone, so a trickling box holds /v1/roles",
        "app/broker.py",
        "                if not await _bounded(client.healthy()):",
        "                if not await client.healthy():",
        "test_a_box_whose_health_never_answers_costs_the_roles_sweep_one_bound",
    ),
    # --- one HTTP client per box --------------------------------------------------------------
    (
        # Re-anchored 2026-10-14 when the client gained its pool limits. Same mutation.
        "the client opens a new HTTP client for every request again",
        U,
        "        if self._client is None:\n"
        "            self._client = httpx.AsyncClient(timeout=self.timeout, limits=",
        "        if True:\n"
        "            self._client = httpx.AsyncClient(timeout=self.timeout, limits=",
        ONE_CLIENT,
    ),
    (
        "the broker builds a new UpstreamClient for every call again",
        "app/broker.py",
        "        if client is None or not client.serves(url, token):\n",
        "        if True:\n",
        ONE_CLIENT,
    ),
    (
        "a held client outlives an edit to upstreams.json, dialling the old address",
        "app/broker.py",
        "        if client is None or not client.serves(url, token):\n",
        "        if client is None:\n",
        EDITED,
    ),
    (
        "a token edit is not noticed, so the old token goes on being sent",
        U,
        "        return self.url == url.rstrip(\"/\") and self._token == token",
        "        return self.url == url.rstrip(\"/\")",
        EDITED,
    ),
    (
        # Re-anchored 2026-10-14 when a replaced client began closing itself once idle
        # (UpstreamClient.retire) instead of waiting for the broker's close. Same mutation.
        "a replaced client is dropped unclosed, its connections left open",
        "app/broker.py",
        "                client.retire()\n",
        "                pass\n",
        EDITED,
    ),
    # --- a body that is not JSON, keep-alive, and replaced clients (audit 2026-10-14) ---------
    (
        "a read answered with a page escapes as JSONDecodeError, which the route answers 404",
        U,
        '            return self._json(r, f"GET {path}")',
        "            return r.json()",
        "test_a_read_answered_with_a_page_is_an_upstream_error_naming_the_box",
    ),
    (
        "a chat answered with a page escapes as JSONDecodeError",
        U,
        '            return self._json(r, f"POST {path}")',
        "            return r.json()",
        "test_a_chat_answered_with_a_page_is_an_upstream_error",
    ),
    (
        "the body check catches only a decoder too deep to finish, not a page",
        U,
        "        except (ValueError, RecursionError) as exc:\n"
        '            kind = r.headers.get("content-type")',
        "        except RecursionError as exc:\n"
        '            kind = r.headers.get("content-type")',
        "test_the_inventory_route_answers_502_for_a_box_that_answers_with_a_page",
    ),
    (
        "the pool keeps an idle connection as long as the remote does again",
        U,
        "                keepalive_expiry=_KEEPALIVE_EXPIRY))",
        "                keepalive_expiry=5.0))",
        KEEPALIVE,
    ),
    (
        "the shipped expiry equals uvicorn's keep-alive",
        U,
        "_KEEPALIVE_EXPIRY = 2.0",
        "_KEEPALIVE_EXPIRY = 5.0",
        "test_the_pool_expires_idle_connections_well_inside_uvicorn_s_keep_alive",
    ),
    (
        "keep-alive is turned off, so every call pays a new connection",
        U,
        "                max_connections=100, max_keepalive_connections=20,",
        "                max_connections=100, max_keepalive_connections=0,",
        KEEPALIVE,
    ),
    (
        "a replaced client is closed under the stream still reading it",
        U,
        "        self._retired = True\n"
        "        if not self._uses:\n",
        "        self._retired = True\n"
        "        if True:\n",
        RETIRED,
    ),
    (
        "a stream is not counted as a use, so the edit closes the client under it",
        U,
        "        # A use until the stream ends or is closed, so a retired client stays open "
        "under it.\n"
        "        self._uses += 1\n",
        "",
        RETIRED,
    ),
    (
        "the last use ending leaves a replaced client open until shutdown",
        U,
        "        if self._retired and not self._uses:\n"
        "            self._close_soon()",
        "        if False:\n"
        "            self._close_soon()",
        RETIRED,
    ),
    (
        "a reopened replaced client stays open: closing it forgets to drop the pool",
        U,
        "        client, self._client = self._client, None\n"
        "        if client is None:\n"
        "            return",
        "        client = self._client\n"
        "        if client is None:\n"
        "            return",
        "test_a_replaced_client_that_is_used_again_does_not_stay_open",
    ),
    (
        "the broker forgets a busy replaced client, so shutdown cannot close it",
        "app/broker.py",
        "                                          if c.busy]",
        "                                          if False]",
        "test_closing_the_broker_closes_a_replaced_client_a_stream_is_still_reading",
    ),
    (
        "closing the broker leaves its box clients open",
        "app/broker.py",
        "            for client in held:\n"
        "                try:\n"
        "                    await client.aclose()",
        "            for client in []:\n"
        "                try:\n"
        "                    await client.aclose()",
        "test_closing_the_broker_closes_its_box_clients",
    ),
    (
        "a client is carried into the next event loop, where its pooled connection fails",
        "app/broker.py",
        "        if loop is not self._upstream_loop:\n",
        "        if self._upstream_loop is None:\n",
        "test_a_client_is_never_carried_into_another_event_loop",
    ),
]


def run_suite() -> SuiteRun:
    env = dict(os.environ)
    # This tree's platform_core too, now that the suite builds a real Broker: the venv's editable
    # install points at whichever checkout installed it, which in a worktree is the wrong one.
    env["PYTHONPATH"] = os.pathsep.join([str(BROKER),
                                         str(BROKER.parents[1] / "packages" / "platform_core")])
    return run_bounded([PY, "-m", "pytest", *SUITE, "-q", "--no-header",
                        "-p", "no:cacheprovider"], cwd=BROKER, env=env)


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
            verdict = "TIMEOUT" if hung else "FIRED" if fired and named else (
                "FIRED-WRONG-TEST" if fired else "SURVIVED")
            results.append((label, verdict))
            print(f"  {verdict:17} {label}")
            if verdict != "FIRED":
                tail = [ln for ln in out.splitlines() if ln.strip()][-12:]
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
