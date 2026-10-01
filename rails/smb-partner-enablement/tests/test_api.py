"""The HTTP and WebSocket surface: grounded answers, citations, capabilities, uploads.

The rail's whole premise is that an answer is attributable. So the assertions here are mostly
about the join between what was retrieved and what the partner is shown: the numbering in the
context block the model is given has to be the numbering in the citation list, the grounded
flag has to mean something, and an empty corpus has to produce an honest ungrounded answer
rather than a confident invented one.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from smb_partner import broker, config, ingest, store
from smb_partner.api import create_api

from conftest import ADMIN, USER, chunk


def receive_within(ws, seconds: float = 10.0):
    """`ws.receive_json()` with a deadline, raising AssertionError if nothing arrives.

    Starlette's test websocket has no receive timeout, and Starlette sends no close when an
    endpoint coroutine merely ends — so a handler that stops serving makes an ordinary
    `receive_json()` block forever, and the two V-16 websocket tests below would HANG on the
    regressions they exist to catch. Measured, not theorised: mutating away either per-turn
    catch-all blocked the suite for a mutation harness's full timeout until the reads went
    through here. The puller is a daemon thread, so one left blocked cannot keep the interpreter
    alive at exit. Kept in step with the gemini-cx twin.
    """
    box: list = []

    def pull() -> None:
        try:
            box.append(("frame", ws.receive_json()))
        except BaseException as exc:  # noqa: BLE001 — re-raised on the calling thread below
            box.append(("raised", exc))

    puller = threading.Thread(target=pull, daemon=True)
    puller.start()
    puller.join(seconds)
    if not box:
        raise AssertionError(
            f"no frame and no close within {seconds}s — the handler stopped serving without "
            f"telling the client, which is the V-16 symptom")
    kind, payload = box[0]
    if kind == "raised":
        raise payload
    return payload

CORPUS = [
    chunk("Seat caps\n\nThe Business family caps at 300 seats pooled across the plans.",
          source="caps.md", title="Seat caps", collection="csp-licensing"),
    chunk("Copilot trial\n\nThe partner-led Copilot trial covers 25 seats.",
          source="copilot.md", title="Copilot trial", collection="csp-licensing"),
    chunk("Designations\n\nSolutions Partner designations rest on a capability score.",
          source="designations.md", title="Designations", collection="designations"),
]


@pytest.fixture()
def client(fake_broker):
    """Not entered as a context manager: the startup task ingests the corpus (and, with
    WARM_ON_BOOT, warms two models), neither of which belongs in a unit test."""
    return TestClient(create_api(), raise_server_exceptions=False)


@pytest.fixture()
def booted(fake_broker, monkeypatch):
    """A rail whose startup event ACTUALLY fires, so the boot-ingest task exists.

    The `client` fixture above skips startup deliberately, for the reason it states. The
    boot-ingest tests below are about that task itself, so they pay for the context manager,
    with `ingest_seed` replaced by whatever the test needs it to do and any opt-in warms
    answered by the fake broker. Paths are already tmp-scoped by the autouse `isolated_rail`.
    """
    @contextlib.contextmanager
    def boot(seed):
        monkeypatch.setattr(ingest, "ingest_seed", seed)
        app = create_api()
        with TestClient(app, raise_server_exceptions=False) as started:
            yield app, started

    return boot


def ingest_state(client, want: str, seconds: float = 10.0) -> dict:
    """Poll /api/health until the boot ingest reaches `want`, then return its state block.

    Startup returns as soon as the task is SCHEDULED, so no finished state is observable on
    the way out of the context manager — and polling the route the state is served on is both
    the shortest wait available and the thing under test. Returns whatever it last saw on
    timeout, so the assertion that follows reports the wrong state rather than hanging.
    Kept in step with the gemini-cx twin.
    """
    deadline = time.monotonic() + seconds
    while True:
        state = client.get("/api/health").json()["ingest"]
        if state["state"] == want or time.monotonic() > deadline:
            return state
        time.sleep(0.02)


@pytest.fixture()
def corpus(seed_corpus):
    seed_corpus("csp-licensing", CORPUS[:2])
    seed_corpus("designations", CORPUS[2:])
    return CORPUS


def ask(client, **body):
    body.setdefault("question", "how many seats does the Copilot trial cover")
    return client.post("/api/ask", json=body, headers=USER)


# --- health and capabilities -------------------------------------------------------------

def test_health_reports_the_corpus_it_can_actually_serve(client, corpus):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["app"] == "smb-partner-enablement"
    assert body == {**body, "chunks": 3, "dims": 64, "collections": 2}
    # `not_started`, not `done`: this fixture is not entered as a context manager, so no boot
    # ingest was ever scheduled. The two are kept distinct precisely so this cannot read as a
    # completed ingest. See the boot-ingest section below for the scheduled states.
    assert body["ingest"] == {"state": "not_started"}


def test_capabilities_describes_models_voice_and_corpus(client, corpus, fake_broker):
    body = client.get("/api/capabilities", headers=ADMIN).json()
    assert body["broker"] == "ok"
    assert [m["slot"] for m in body["models"]] == ["reasoning", "retrieval"]
    assert [m["label"] for m in body["models"]] == ["LLM", "Retrieval"]
    assert body["corpus"]["chunks"] == 3
    assert body["voice"]["effective"] in ("broker", "browser")
    assert body["user"] == "admin"
    assert body["is_admin"] is True


def test_capabilities_reports_a_non_admin_as_such(client):
    assert client.get("/api/capabilities", headers=USER).json()["is_admin"] is False


def test_capabilities_renders_when_the_broker_is_down(client, monkeypatch, fake_broker):
    """A header must render even when the GPU layer is down — the chips going red is the
    information, a 500 is not."""
    def down(*_a, **_kw):
        raise broker.BrokerError("connection refused")

    for name in ("roles", "models", "status"):
        monkeypatch.setattr(broker, name, down)
    body = client.get("/api/capabilities", headers=USER).json()
    assert body["broker"] == "unreachable"
    assert {m["state"] for m in body["models"]} == {"missing"}
    assert body["voice"]["effective"] == "browser"
    assert body["voice"]["broker_media"] is False


def test_collections_lists_the_indexed_corpus(client, corpus):
    listed = client.get("/api/collections", headers=USER).json()["collections"]
    assert [c["name"] for c in listed] == ["csp-licensing", "designations"]
    assert listed[0]["chunks"] == 2
    assert listed[0]["origin"] == "seed"


# --- the boot ingest ----------------------------------------------------------------------

def test_the_boot_ingest_task_is_retained_and_health_says_so_until_it_finishes(booted):
    """Two halves of one defect. The handle is kept on `app.state`, because CPython holds only
    a WEAK reference to a running task and this one suspends inside `to_thread` for minutes on
    an 8 GB card; and `ok` is False for as long as it is unfinished, so a rail serving a
    half-embedded corpus can no longer answer healthy."""
    running, release = threading.Event(), threading.Event()

    def slow_seed():
        running.set()
        assert release.wait(10), "the test never released the ingest"
        return {"found": True, "collections": []}

    with booted(slow_seed) as (app, client):
        assert running.wait(10), "startup never ran the ingest"
        assert isinstance(app.state.ingest_task, asyncio.Task)
        assert not app.state.ingest_task.done()
        body = client.get("/api/health").json()
        assert body["ingest"] == {"state": "running"}
        assert body["ok"] is False

        release.set()
        assert ingest_state(client, "done") == {"state": "done"}
        assert client.get("/api/health").json()["ok"] is True
        # The handle is released by the done-callback once the outcome has been retrieved; see
        # test_the_finished_ingest_handle_is_released_rather_than_pinned below.
        assert app.state.ingest_task is None


def test_a_failing_boot_ingest_is_named_rather_than_swallowed(booted):
    """`prepare()` catching and logging is right — boot must not die on a bad seed mount — but
    a log line is not an answer to "is this rail serving a corpus". The reason has to reach the
    payload, or the only symptom is degraded answers.

    `done, degraded` rather than `failed`: `prepare()` ran on past it to its last statement, so
    the preparation DID complete — over a stale corpus. And the reason
    that reaches the wire is the exception TYPE only; the message stays server-side.
    """
    def boom():
        raise RuntimeError("seed volume is not mounted")

    with booted(boom) as (app, client):
        state = ingest_state(client, "done")
        assert state == {"state": "done", "degraded": True, "error": "RuntimeError"}
        assert client.get("/api/health").json()["ok"] is False
        # Still fully diagnosable in-process and in the container log — just not over the wire.
        assert app.state.ingest_error == "RuntimeError: seed volume is not mounted"
        assert app.state.ingest_task is None


def test_a_transient_ingest_failure_does_not_pin_the_rail_to_failed(booted):
    """The stickiness itself, which is the operational half of the defect. `failed` is reserved
    for a preparation that did NOT complete; a completed one reports the `done` that
    tools/rail_smoke.py pins, so a rail that recovers on its next boot can go green again
    instead of needing a process restart to stop being red."""
    def boom():
        raise RuntimeError("the embedder was restarting")

    with booted(boom) as (_app, client):
        assert ingest_state(client, "done")["state"] == "done"

    # Same rail, next boot, clean seed: nothing carries over and the block is bare `done` —
    # exactly the value rail_smoke's `expect={"state": "done"}` compares against.
    with booted(lambda: {"found": True, "collections": []}) as (_app, client):
        assert ingest_state(client, "done") == {"state": "done"}
        assert client.get("/api/health").json()["ok"] is True


def test_the_finished_ingest_handle_is_released_rather_than_pinned(booted):
    """The Task also pins `prepare()`'s closure over app, config, store and broker. The weak-ref
    hazard that justifies retaining it only exists while the task RUNS, and the outcome has been
    copied onto app.state by then — so the done-callback drops the handle. The state machine has
    to survive that: a released handle must still read `done`, never `not_started`."""
    with booted(lambda: {"found": True, "collections": []}) as (app, client):
        assert ingest_state(client, "done") == {"state": "done"}
        assert app.state.ingest_task is None
        assert client.get("/api/health").json()["ingest"] == {"state": "done"}


def test_the_unauthenticated_health_route_does_not_leak_a_filesystem_path(booted, caplog):
    """/api/health is the ONLY route on this rail with no `Depends(identity)`, so whatever it
    returns is readable by every sibling container on the compose network. The seed walk raises
    FileNotFoundError / PermissionError / UnicodeDecodeError whose messages name the absolute
    host path and the corpus filename, and those used to be echoed verbatim. The TYPE is on the
    wire, the message is in the log."""
    caplog.set_level(logging.WARNING, logger="smb_partner.api")
    leak = "/srv/platform/seed/knowledge-base/csp-licensing/seat-caps.md"

    def boom():
        raise FileNotFoundError(2, "No such file or directory", leak)

    with booted(boom) as (app, client):
        # The `booted` client carries no identity headers, so this IS the sibling-container
        # request. The gated routes prove the premise: only health answers it.
        assert client.get("/api/capabilities").status_code == 401
        assert client.get("/api/collections").status_code == 401
        assert ingest_state(client, "done")["state"] == "done"

        raw = client.get("/api/health").text
        assert leak not in raw
        assert "seat-caps.md" not in raw
        assert "No such file or directory" not in raw
        body = json.loads(raw)
        assert body["ingest"] == {"state": "done", "degraded": True,
                                  "error": "FileNotFoundError"}
        assert body["ok"] is False
        # The operator keeps everything: full detail on app.state and in the log.
        assert leak in app.state.ingest_error
        assert leak in caplog.text


def test_an_error_wider_than_prepare_catches_is_retrieved_from_the_task(booted, monkeypatch):
    """The done-callback's own job. Each step inside `prepare()` is guarded by `except
    Exception`, so anything wider escapes to the task — and with nothing calling
    `.exception()`, that is reported only as an "exception was never retrieved" warning at
    interpreter shutdown, long after the corpus went out half-built."""
    class WiderThanException(BaseException):
        pass

    def warm_explodes(model, keep_alive="30m"):
        raise WiderThanException("the broker refused the warm")

    monkeypatch.setattr(broker, "warm", warm_explodes)
    # The boot warm is opt-in now, so turn it on: it is the step that raises here.
    monkeypatch.setattr(config, "WARM_ON_BOOT", True)
    with booted(lambda: {"found": True, "collections": []}) as (app, client):
        state = ingest_state(client, "failed")
        # `failed`, not `done, degraded`: this raise is wider than `prepare()`'s guards, so it
        # never reached the completion flag — the preparation genuinely did not finish.
        assert state == {"state": "failed", "error": "WiderThanException"}
        assert "the broker refused the warm" not in json.dumps(state)
        # Retrieved by the callback — which is what turned it into that payload at all — and
        # kept in full on the server side before the handle was released.
        assert app.state.ingest_error == "WiderThanException: the broker refused the warm"
        assert app.state.ingest_task is None


def test_a_cancelled_boot_ingest_does_not_read_as_healthy(booted):
    """What a container stop does to it: cancelled mid-`to_thread`, so `prepare()` never
    reaches its completion flag and no `except Exception` ever sees it. Retaining the handle is
    what makes this state knowable at all."""
    running, release = threading.Event(), threading.Event()

    def slow_seed():
        running.set()
        release.wait(10)
        return {"found": True, "collections": []}

    with booted(slow_seed) as (app, client):
        assert running.wait(10), "startup never ran the ingest"
        task = app.state.ingest_task
        # The task belongs to the TestClient's loop, not this thread.
        task.get_loop().call_soon_threadsafe(task.cancel)
        state = ingest_state(client, "failed")
        release.set()
        # `failed` survives the precedence change: a cancellation never reaches `prepare()`'s
        # completion flag, so this is the state that means "did not complete". The literal is
        # the exception TYPE — the sentence explaining it is in the log, not on the wire.
        assert state == {"state": "failed", "error": "CancelledError"}
        assert client.get("/api/health").json()["ok"] is False


# --- ask ----------------------------------------------------------------------------------

def test_ask_cites_the_material_it_answered_from(client, corpus, fake_broker):
    body = ask(client).json()
    assert body["grounded"] is True
    assert body["answer"] == fake_broker.answer
    assert [c["n"] for c in body["citations"]] == [1, 2, 3]
    assert body["citations"][0]["source"] == "copilot.md"
    assert body["citations"][0]["collection"] == "csp-licensing"
    assert body["citations"][0]["title"] == "Copilot trial"
    assert isinstance(body["citations"][0]["score"], float)


def test_the_citation_numbers_are_the_numbers_in_the_model_context(client, corpus,
                                                                    fake_broker):
    """The system prompt tells the model to cite as [1], [2]. If the context block and the
    citation list numbered differently, every inline marker would point at the wrong source."""
    body = ask(client).json()
    context = fake_broker.chats[0]["messages"][1]["content"]
    for cite in body["citations"]:
        assert f"[{cite['n']}] ({cite['source']})" in context


def test_ask_uses_the_grounded_system_prompt_and_the_screen_budget(client, corpus,
                                                                    fake_broker):
    ask(client)
    call = fake_broker.chats[0]
    assert call["model"] == config.RAG_MODEL
    assert call["messages"][0]["content"] == config.SYSTEM_PROMPT
    assert call["options"] == {"num_predict": config.MAX_TOKENS}
    assert "voice" not in ask(client).json()


def test_ask_scopes_to_the_requested_collections(client, corpus):
    body = ask(client, collections=["designations"]).json()
    assert {c["collection"] for c in body["citations"]} == {"designations"}


def test_ask_honours_top_k(client, corpus):
    assert len(ask(client, top_k=1).json()["citations"]) == 1


def test_ask_on_an_empty_corpus_answers_but_says_it_is_ungrounded(client, fake_broker):
    """The honest degradation: the model is told plainly that nothing was retrieved, and the
    UI is told not to present the answer as sourced."""
    body = ask(client).json()
    assert body["grounded"] is False
    assert body["citations"] == []
    assert body["answer"] == fake_broker.answer
    assert "(no matching context was retrieved)" in fake_broker.chats[0]["messages"][1]["content"]


def test_ask_on_an_unknown_collection_is_ungrounded_rather_than_wrong(client, corpus):
    body = ask(client, collections=["not-indexed"]).json()
    assert body["grounded"] is False
    assert body["citations"] == []


def test_a_spoken_turn_gets_the_ear_shaped_prompt_and_a_tighter_budget(client, corpus,
                                                                        fake_broker):
    """A partner on a phone between meetings will not listen to 700 tokens."""
    body = ask(client, speak=True).json()
    call = fake_broker.chats[0]
    assert call["messages"][0]["content"] == config.VOICE_SYSTEM_PROMPT
    assert call["options"] == {"num_predict": config.VOICE_MAX_TOKENS}
    assert config.VOICE_MAX_TOKENS < config.MAX_TOKENS
    assert body["voice"]["mode"] == "broker"
    assert body["voice"]["audio_b64"] == "QUJD"
    # The inline citation marker is stripped before synthesis — nobody wants "bracket one".
    assert "[1]" not in body["voice"]["text"]


def test_a_broker_failure_on_ask_is_a_502_not_a_500(client, corpus, fake_broker):
    fake_broker.chat_error = broker.BrokerError("model evicted")
    resp = ask(client)
    assert resp.status_code == 502
    assert "model evicted" in resp.json()["detail"]


def test_an_embedder_failure_on_ask_is_a_502(client, corpus, fake_broker):
    fake_broker.embed_error = broker.BrokerError("embedder unreachable")
    assert ask(client).status_code == 502


# --- upload ---------------------------------------------------------------------------------

def test_an_uploaded_document_becomes_citable(client, corpus, fake_broker):
    text = ("# Client tender\n\nThe tender closes in March and requires ISO 27001 evidence "
            "from every supplier.")
    resp = client.post("/api/upload", json={"name": "tender", "text": text,
                                            "source": "tender.md"}, headers=ADMIN)
    assert resp.json() == {"collection": "tender", "chunks": 1}
    assert "tender" in {c["name"] for c in store.collections()}

    body = client.post("/api/ask", json={"question": "what does the tender require",
                                         "collections": ["tender"]}, headers=USER).json()
    assert [c["source"] for c in body["citations"]] == ["tender.md"]
    assert body["grounded"] is True


def test_an_upload_the_embedder_cannot_index_is_a_502(client, fake_broker):
    fake_broker.embed_error = broker.BrokerError("embedder unreachable")
    resp = client.post("/api/upload",
                       json={"name": "tender",
                             "text": "# Tender\n\nContent long enough to clear the chunker's "
                                     "forty character floor."},
                       headers=ADMIN)
    assert resp.status_code == 502


def test_an_oversized_upload_name_is_rejected_by_validation(client):
    resp = client.post("/api/upload", json={"name": "x" * 200, "text": "content"},
                       headers=ADMIN)
    assert resp.status_code == 422


def test_an_oversized_upload_body_is_rejected_by_validation(client):
    """`text` had no maximum, so one request could fan out to an arbitrary number of embed
    calls (32 chunks each). The bound is the same 200k as gemini-cx's UploadBody — if one rail
    moves it, this pair of tests is where the drift shows up."""
    resp = client.post("/api/upload", json={"name": "tender", "text": "x" * 200_001},
                       headers=ADMIN)
    assert resp.status_code == 422


# --- scenarios over HTTP ----------------------------------------------------------------------

def test_scenarios_route_publishes_the_builder(client):
    body = client.get("/api/scenarios", headers=USER).json()
    assert len(body["scenarios"]) == 6
    assert body["scenarios"][0]["questions"][0]["options"][-1] == "Not sure yet"
    assert [s["key"] for s in body["stages"]][:2] == ["analyze", "ground"]


def test_generate_over_http_returns_the_whole_package(client, corpus, fake_broker):
    resp = client.post("/api/scenario/generate",
                       json={"scenario_id": "retail-chain",
                             "answers": {"headcount": "25–100"}}, headers=USER)
    assert resp.status_code == 200
    package = resp.json()
    assert package["scenario"]["id"] == "retail-chain"
    assert set(package["outputs"]) == {"scenario_card", "next_move", "discovery",
                                       "customer_qa", "roi"}
    assert package["grounded"] is True


def test_generate_for_an_unknown_scenario_is_a_404(client, fake_broker):
    resp = client.post("/api/scenario/generate",
                       json={"scenario_id": "nope", "answers": {}}, headers=USER)
    assert resp.status_code == 404
    assert "unknown scenario" in resp.json()["detail"]


def test_generate_surfaces_a_broker_failure_in_the_grounding_probe_as_502(client, corpus,
                                                                          fake_broker):
    """A failed *pass* is absorbed into the package. A failed grounding probe happens before
    any output exists, so there is nothing partial to return."""
    fake_broker.embed_error = broker.BrokerError("embedder unreachable")
    resp = client.post("/api/scenario/generate",
                       json={"scenario_id": "retail-chain", "answers": {}}, headers=USER)
    assert resp.status_code == 502


# --- voice routes -----------------------------------------------------------------------------

def test_speak_synthesizes_through_the_non_evicting_path(client, fake_broker):
    body = client.post("/api/speak", json={"text": "**Lead** with frontline [1]."},
                       headers=USER).json()
    assert body["mode"] == "broker"
    assert body["audio_b64"] == "QUJD"
    # A synthesizer will happily read "asterisk asterisk" and "bracket one" out loud.
    spoken = fake_broker.spoken[0]["text"]
    assert spoken.startswith("Lead with frontline")
    assert "**" not in spoken and "[1]" not in spoken


def test_transcribe_returns_the_utterance(client, fake_broker):
    body = client.post("/api/transcribe", json={"audio_b64": "QUJD", "language": "en"},
                       headers=USER).json()
    assert body == {"text": "how does the copilot trial work", "language": "en",
                    "duration": 2.5}


def test_transcribe_without_a_media_worker_is_a_503(client, fake_broker):
    fake_broker.transcribe_error = broker.BrokerError("media disabled")
    resp = client.post("/api/transcribe", json={"audio_b64": "QUJD"}, headers=USER)
    assert resp.status_code == 503
    assert "speech-to-text unavailable" in resp.json()["detail"]


# --- websockets -------------------------------------------------------------------------------

def test_ws_ask_streams_citations_then_tokens_then_the_answer(client, corpus, fake_broker):
    """Citations go out FIRST, before the model has produced anything: a cold load can take
    minutes, and the sources are what the partner reads while they wait."""
    with client.websocket_connect("/ws/ask", headers=USER) as ws:
        ws.send_text(json.dumps({"question": "what does the Copilot trial cover"}))
        first = ws.receive_json()
        assert first["type"] == "citations"
        assert first["grounded"] is True
        assert [c["n"] for c in first["citations"]] == [1, 2, 3]

        tokens = []
        frame = ws.receive_json()
        while frame["type"] == "token":
            tokens.append(frame["token"])
            frame = ws.receive_json()
        assert tokens == fake_broker.tokens
        assert frame == {"type": "done", "answer": "".join(fake_broker.tokens)}


def test_ws_ask_attaches_a_voice_payload_to_a_spoken_turn(client, corpus, fake_broker):
    with client.websocket_connect("/ws/ask", headers=USER) as ws:
        ws.send_text(json.dumps({"question": "seats?", "speak": True}))
        frame = ws.receive_json()
        while frame["type"] != "done":
            frame = ws.receive_json()
        assert frame["voice"]["mode"] == "broker"
        assert frame["voice"]["audio_b64"] == "QUJD"


def test_ws_ask_reports_a_bad_frame_without_dropping_the_socket(client, corpus, fake_broker):
    """A malformed frame must not cost the connection — the client would have to re-establish
    and re-show the whole conversation."""
    with client.websocket_connect("/ws/ask", headers=USER) as ws:
        ws.send_text("{not json at all")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "bad request" in error["detail"]

        ws.send_text(json.dumps({"question": "seats?"}))
        assert ws.receive_json()["type"] == "citations"


def test_ws_ask_reports_a_broker_failure_as_an_error_frame(client, corpus, fake_broker):
    fake_broker.stream_error = broker.BrokerError("model evicted")
    with client.websocket_connect("/ws/ask", headers=USER) as ws:
        ws.send_text(json.dumps({"question": "seats?"}))
        assert ws.receive_json()["type"] == "citations"
        assert ws.receive_json() == {"type": "error", "detail": "model evicted"}


def test_ws_ask_survives_a_raw_httpx_error_mid_stream(client, corpus, fake_broker):
    """V-16. The test above raises the ALREADY-MAPPED BrokerError, so it never exercised the
    escapes the streaming path actually had. A raw httpx error — the broker 401ing mid-answer
    after a token rotation — used to pass straight through `except broker.BrokerError` AND the
    outer `except WebSocketDisconnect`, so the socket died silently and the partner lost the
    conversation rather than the answer. It must be a frame, and the next question must work.

    Both asks are queued before anything is read, so the assertions need no live round trip, and
    every read goes through `receive_within` — see its docstring for why an unbounded
    `receive_json()` turns this exact regression into a hang instead of a failure.
    """
    fake_broker.stream_error = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=httpx.Request("POST", "http://broker/v1/chat/stream"),
        response=httpx.Response(401))
    with client.websocket_connect("/ws/ask", headers=USER) as ws:
        ws.send_text(json.dumps({"question": "seats?"}))
        ws.send_text(json.dumps({"question": "seats?"}))

        assert receive_within(ws)["type"] == "citations"
        error = receive_within(ws)
        assert error["type"] == "error"
        assert "401" in error["detail"]
        # Per turn, not per socket: the second question is served on the same connection.
        assert receive_within(ws)["type"] == "citations"
        assert receive_within(ws)["type"] == "error"


def test_ws_scenario_streams_the_package_build(client, corpus, fake_broker):
    with client.websocket_connect("/ws/scenario", headers=USER) as ws:
        ws.send_text(json.dumps({"scenario_id": "retail-chain",
                                 "answers": {"headcount": "More than 300"}}))
        seen: list[dict] = []
        while True:
            frame = ws.receive_json()
            seen.append(frame)
            if frame["type"] == "package":
                break

    kinds = [f["type"] for f in seen]
    assert kinds[0] == "stage"
    assert "analysis" in kinds and "retrieval" in kinds and "token" in kinds
    analysis = next(f for f in seen if f["type"] == "analysis")
    assert any("300-seat" in c for c in analysis["constraints"])
    assert seen[-1]["package"]["outputs"]["scenario_card"].startswith("## Customer profile")


def test_ws_scenario_reports_an_unknown_scenario_as_an_error_frame(client, fake_broker):
    with client.websocket_connect("/ws/scenario", headers=USER) as ws:
        ws.send_text(json.dumps({"scenario_id": "nope", "answers": {}}))
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "unknown scenario" in frame["detail"]


def test_ws_scenario_survives_a_raw_httpx_error_mid_pass(client, corpus, fake_broker):
    """V-16 on the other socket. This one reaches chat_stream through generate.py, which never
    goes near `_stream_with_heartbeat` — so the wrapper could not have carried the fix, and this
    handler had nothing at all between a raw httpx error and a dead socket. generate.py's own
    per-pass `except broker.BrokerError` degrades one pass and keeps the package; an httpx error
    slipped past that too, abandoning the whole build.

    Both builds are queued before anything is read, and every read goes through
    `receive_within`, so a socket that ended fails by name instead of blocking the suite.
    """
    fake_broker.stream_error = httpx.HTTPStatusError(
        "Client error '401 Unauthorized'",
        request=httpx.Request("POST", "http://broker/v1/chat/stream"),
        response=httpx.Response(401))
    request = json.dumps({"scenario_id": "retail-chain",
                          "answers": {"headcount": "More than 300"}})
    with client.websocket_connect("/ws/scenario", headers=USER) as ws:
        ws.send_text(request)
        ws.send_text(request)

        def until_error() -> dict:
            frame = receive_within(ws)
            while frame["type"] != "error":
                assert frame["type"] != "package", "the package completed despite a failed pass"
                frame = receive_within(ws)
            return frame

        assert "401" in until_error()["detail"]
        # Per turn, not per socket: the next scenario the partner clicks is still served.
        assert "401" in until_error()["detail"]


def test_the_boot_warms_no_model_unless_asked(booted, monkeypatch):
    """The boot warm held about 3.5 GB of VRAM (and the runner's host RAM) for 30 minutes after
    every stack start on an 8 GB card, to save seconds on a first question that may not come.
    It is opt-in now (SMB_PARTNER_WARM_ON_BOOT); off, the boot ingests and touches no model."""
    calls = []
    monkeypatch.setattr(broker, "warm", lambda model, *a, **kw: calls.append(model) or {"ok": True})
    monkeypatch.setattr(config, "WARM_ON_BOOT", False)
    with booted(lambda: {"found": True, "collections": []}) as (_app, client):
        assert ingest_state(client, "done")["state"] == "done"
    assert calls == []

    monkeypatch.setattr(config, "WARM_ON_BOOT", True)
    with booted(lambda: {"found": True, "collections": []}) as (_app, client):
        assert ingest_state(client, "done")["state"] == "done"
    assert calls == [config.EMBED_MODEL, config.RAG_MODEL]
