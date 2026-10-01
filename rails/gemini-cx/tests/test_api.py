"""The routes, end to end, with the broker faked and the GPU untouched.

Everything below the HTTP layer is real: the SQLite index, the numpy ranking, the deck lookup,
the context block and the citation payload. Only the broker is stubbed — a deterministic
one-hot "embedder" and a chat that echoes what it was asked, so the assertions can be about
*what the rail sends the model and what it tells the user*, not about model output.

The three behaviours worth the setup:

* **A deck click carries scoping that free prose cannot.** ``question_id`` wins over
  ``question`` for exactly that reason, and the scope has to actually narrow retrieval — a
  scoped ask that quietly searches everything is the deck's whole value gone.
* **Citations are the product.** Every hit becomes a numbered source the UI shows beside the
  answer, and the numbers have to line up with the ``[1]``/``[2]`` markers in the context
  block the model was given, or the assistant cites the wrong document convincingly.
* **The rail must degrade rather than lie.** With an empty corpus it still answers, on an
  explicit "(no matching context was retrieved)" — and when the broker is down it says 503
  rather than producing an ungrounded answer.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time

import httpx
import numpy as np
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from gemini_cx import broker, config, ingest, store, voice
from gemini_cx.api import create_api

USER = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}

# One axis per keyword; a text embeds to the axes it mentions. Deterministic and readable.
VOCAB = ["priced", "languages", "guardrails"]

ROLES = [{"role": "gemini-cx-rag", "resolved": "qwen3:4b"},
         {"role": "embed", "resolved": "bge-m3"}]
INSTALLED = [{"name": "qwen3:4b"}, {"name": "bge-m3:latest"}]


def fake_embed(texts, *, model):
    out = []
    for t in texts:
        v = [1.0 if w in t.lower() else 0.0 for w in VOCAB]
        out.append(v if any(v) else [0.0, 0.0, 1e-6])
    return out


def normalized(texts):
    arr = np.asarray(fake_embed(texts, model="x"), dtype=np.float32)
    return (arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)).astype(np.float32)


def receive_within(ws, seconds: float = 10.0):
    """`ws.receive_json()` with a deadline, raising AssertionError if nothing arrives.

    Starlette's test websocket has no receive timeout, and Starlette sends no close when an
    endpoint coroutine merely ends — so a handler that stops serving makes an ordinary
    `receive_json()` block forever, and the websocket tests at the bottom of this file would
    HANG on the regressions they exist to catch. Measured, not theorised: mutating away any of
    the /ws/ask guards blocked the suite for a mutation harness's full timeout until the reads
    went through here, and a test that hangs says nothing about what broke. The puller is a
    daemon thread, so one left blocked cannot keep the interpreter alive at exit.
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


@pytest.fixture(autouse=True)
def gpu(monkeypatch):
    """A broker that is up but never on the network. `sent` captures the chat payload."""
    sent: dict = {}

    def chat(model, messages, *, options=None, **kw):
        sent.update(model=model, messages=messages, options=options)
        return "GECX pricing is not published [1]."

    async def chat_stream(model, messages, *, options=None, **kw):
        sent.update(model=model, messages=messages, options=options, streamed=True)
        for tok in ("GECX ", "pricing ", "is not published [1]."):
            yield tok

    monkeypatch.setattr(broker, "chat", chat)
    monkeypatch.setattr(broker, "chat_stream", chat_stream)
    monkeypatch.setattr(broker, "embed", fake_embed)
    monkeypatch.setattr(broker, "roles", lambda: ROLES)
    monkeypatch.setattr(broker, "models", lambda: INSTALLED)
    monkeypatch.setattr(broker, "status", lambda: {"loaded": [{"name": "bge-m3:latest"}],
                                                   "jobs": []})
    monkeypatch.setattr(broker, "media_enabled", lambda: False)
    monkeypatch.setattr(broker, "warm", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr(voice, "_probe", None)
    return sent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STANDALONE", False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))
    # SEED_KB_DIR is left pointing at the real corpus: the deck validation the health and
    # questions routes report is only meaningful against the tree that actually ships.
    app = create_api()
    yield TestClient(app, headers=USER, raise_server_exceptions=False)
    store.reload_matrix()


@pytest.fixture()
def booted(tmp_path, monkeypatch):
    """A rail whose startup event ACTUALLY fires, so the boot-ingest task exists.

    The `client` fixture above skips startup deliberately — a real seed ingest (and, with
    WARM_ON_BOOT, two model warms) has no place in a route test. The boot-ingest tests below are
    about that task itself, so they pay for the context manager, with `ingest_seed` replaced by
    whatever the test needs it to do. `broker.warm` is already faked by the autouse `gpu` fixture.
    """
    monkeypatch.setattr(config, "STANDALONE", False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "var")
    monkeypatch.setattr(config, "UPLOADS_DIR", tmp_path / "var" / "uploads")
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "var" / "gemini_cx.db"))

    @contextlib.contextmanager
    def boot(seed):
        monkeypatch.setattr(ingest, "ingest_seed", seed)
        app = create_api()
        with TestClient(app, headers=USER, raise_server_exceptions=False) as started:
            yield app, started

    yield boot
    store.reload_matrix()


def ingest_state(client, want: str, seconds: float = 10.0) -> dict:
    """Poll /api/health until the boot ingest reaches `want`, then return its state block.

    Startup returns as soon as the task is SCHEDULED, so no finished state is observable on
    the way out of the context manager — and polling the route the state is served on is both
    the shortest wait available and the thing under test. Returns whatever it last saw on
    timeout, so the assertion that follows reports the wrong state rather than hanging.
    """
    deadline = time.monotonic() + seconds
    while True:
        state = client.get("/api/health").json()["ingest"]
        if state["state"] == want or time.monotonic() > deadline:
            return state
        time.sleep(0.02)


@pytest.fixture()
def corpus(client):
    """Two collections where the globally best match for "priced" is NOT in the collection
    the pricing deck question is scoped to — so a scope that does nothing is visible."""
    store.replace_collection(
        "pricing-and-licensing", "Pricing And Licensing", "seed",
        [{"source": "01-what-is-published.md", "title": "What is published",
          "text": "GECX is priced through your account team; languages are billed the same"}],
        normalized(["priced languages"]))
    store.replace_collection(
        "models-and-languages", "Models And Languages", "seed",
        [{"source": "02-language-coverage.md", "title": "Language coverage",
          "text": "Audio to audio is priced per minute"}],
        normalized(["priced"]))
    return client


# --- health -------------------------------------------------------------------------------

def test_health_reports_corpus_models_and_the_deck(corpus):
    body = corpus.get("/api/health").json()
    assert body["status"] == "ok"
    # `not_started`, not `done`: this fixture is not entered as a context manager, so no boot
    # ingest was ever scheduled. The two are kept distinct precisely so this cannot read as a
    # completed ingest. See the boot-ingest section below for the scheduled states.
    assert body["ingest"] == {"state": "not_started"}
    assert body["corpus"] == {"chunks": 2, "dims": 3, "collections": 2}
    assert body["models"]["broker"] == "ok"
    assert {m["slot"] for m in body["models"]["models"]} == {"reasoning", "retrieval"}
    assert body["deck"]["questions"] > 0
    assert body["deck"]["groups"] > 0


def test_health_reports_a_clean_deck_against_the_shipped_corpus(client):
    """The deck validation is surfaced here so a renamed collection fails loudly at boot
    instead of silently, in front of a user, on their first click."""
    assert client.get("/api/health").json()["deck"]["problems"] == []


def test_health_names_a_broken_deck_rather_than_hiding_it(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SEED_KB_DIR", tmp_path / "no-corpus-here")
    problems = client.get("/api/health").json()["deck"]["problems"]
    assert problems
    assert {"question", "missing_collections"} == set(problems[0])


def test_health_answers_with_the_broker_down(client, monkeypatch):
    """Liveness must not depend on the GPU layer, or a broker restart reads as a dead rail."""
    def down(*_a, **_kw):
        raise broker.BrokerError("connection refused")

    for name in ("roles", "models", "status"):
        monkeypatch.setattr(broker, name, down)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["models"]["broker"] == "unreachable"


# --- the boot ingest ------------------------------------------------------------------------

def test_the_boot_ingest_task_is_retained_and_health_says_so_until_it_finishes(booted):
    """Two halves of one defect. The handle is kept on `app.state`, because CPython holds only
    a WEAK reference to a running task and this one suspends inside `to_thread` for minutes on
    an 8 GB card; and `status` reads "starting" rather than "ok" for as long as it is
    unfinished, so a rail serving a half-embedded corpus can no longer answer healthy."""
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
        assert body["status"] == "starting"

        release.set()
        assert ingest_state(client, "done") == {"state": "done"}
        assert client.get("/api/health").json()["status"] == "ok"
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
        assert client.get("/api/health").json()["status"] == "degraded"
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
        assert client.get("/api/health").json()["status"] == "ok"


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
    caplog.set_level(logging.WARNING, logger="gemini_cx.api")
    leak = "/srv/platform/seed/knowledge-base/pricing-and-licensing/01-what-is-published.md"

    def boom():
        raise FileNotFoundError(2, "No such file or directory", leak)

    with booted(boom) as (app, client):
        assert ingest_state(client, "done")["state"] == "done"
        # A client with NO identity headers at all — the sibling-container request. The fixture
        # client sends `USER`, so the probe cannot be made through it. The gated routes prove
        # the premise: only health answers this caller.
        anon = TestClient(app, raise_server_exceptions=False)
        assert anon.get("/api/capabilities").status_code == 401
        assert anon.get("/api/questions").status_code == 401

        raw = anon.get("/api/health").text
        assert leak not in raw
        assert "01-what-is-published.md" not in raw
        assert "No such file or directory" not in raw
        body = json.loads(raw)
        assert body["ingest"] == {"state": "done", "degraded": True,
                                  "error": "FileNotFoundError"}
        assert body["status"] == "degraded"
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

    def warm_explodes(model):
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
        assert client.get("/api/health").json()["status"] == "degraded"


# --- capabilities ---------------------------------------------------------------------------

def test_capabilities_says_retrieval_is_off_until_something_is_indexed(client):
    """The state of the whole first boot. The UI renders honestly instead of offering a
    search that can only answer nothing."""
    body = client.get("/api/capabilities").json()
    assert body["retrieval"] is False
    assert body["corpus"]["chunks"] == 0


def test_capabilities_turns_retrieval_on_once_the_corpus_lands(corpus):
    body = corpus.get("/api/capabilities").json()
    assert body["retrieval"] is True
    assert body["answering"] is True
    assert body["streaming"] is True
    assert body["upload"] is True
    assert body["corpus"]["chunks"] == 2


def test_capabilities_reports_answering_off_when_the_broker_is_down(client, monkeypatch):
    def down(*_a, **_kw):
        raise broker.BrokerError("connection refused")

    for name in ("roles", "models", "status"):
        monkeypatch.setattr(broker, name, down)
    body = client.get("/api/capabilities").json()
    assert body["answering"] is False
    assert body["broker"] == "unreachable"


def test_capabilities_carries_the_voice_seam_for_the_read_aloud_button(client):
    voice_info = client.get("/api/capabilities").json()["voice"]
    assert voice_info["effective"] in voice.BACKENDS
    assert voice_info["broker_media"] is False   # the stub says the media worker is absent


# --- the deck and the corpus listing ------------------------------------------------------------

def test_questions_returns_the_deck_with_its_validation(client):
    body = client.get("/api/questions").json()
    assert [g["id"] for g in body["groups"]][:2] == ["basics", "traps"]
    assert body["problems"] == []


def test_collections_lists_what_is_indexed(corpus):
    body = corpus.get("/api/collections").json()
    assert [c["name"] for c in body["collections"]] == ["models-and-languages",
                                                        "pricing-and-licensing"]
    assert body["corpus"]["chunks"] == 2


# --- ask ------------------------------------------------------------------------------------

def test_ask_by_deck_id_answers_the_decks_question(corpus, gpu):
    body = corpus.post("/api/ask", json={"question_id": "pricing"}).json()
    assert body["question"] == "How is GECX priced?"
    assert body["answer"] == "GECX pricing is not published [1]."
    assert body["user"] == "admin"


def test_a_deck_click_narrows_retrieval_to_its_own_collections(corpus):
    """The better global match lives in models-and-languages. Scoping has to keep it out, or
    the pricing question is answered from the languages file."""
    body = corpus.post("/api/ask", json={"question_id": "pricing"}).json()
    assert body["collections"] == ["pricing-and-licensing"]
    assert [c["collection"] for c in body["citations"]] == ["pricing-and-licensing"]


def test_free_prose_is_never_scoped(corpus):
    """Design rule 3: scoping is a hint, not a cage. A typed question searches everything,
    so here the globally best match wins."""
    body = corpus.post("/api/ask", json={"question": "how is it priced?"}).json()
    assert body["collections"] == []
    assert body["citations"][0]["collection"] == "models-and-languages"


def test_a_deck_id_wins_over_prose_sent_alongside_it(corpus):
    body = corpus.post("/api/ask", json={"question_id": "pricing",
                                         "question": "tell me about guardrails"}).json()
    assert body["question"] == "How is GECX priced?"


def test_citations_are_numbered_from_one_and_name_their_source(corpus):
    cites = corpus.post("/api/ask", json={"question": "priced"}).json()["citations"]
    assert [c["n"] for c in cites] == list(range(1, len(cites) + 1))
    first = cites[0]
    assert first["source"] == "02-language-coverage.md"
    assert first["title"] == "Language coverage"
    assert isinstance(first["score"], float)


def test_the_context_the_model_sees_matches_the_citations_the_user_sees(corpus, gpu):
    """The numbering is the join between the two. If they drift, every [1] in the answer
    points at the wrong document and nothing about the reply looks wrong."""
    body = corpus.post("/api/ask", json={"question": "priced"}).json()
    user_msg = gpu["messages"][1]["content"]
    for cite in body["citations"]:
        assert f"[{cite['n']}] ({cite['source']})" in user_msg


def test_ask_sends_the_grounding_contract_as_the_system_prompt(corpus, gpu):
    corpus.post("/api/ask", json={"question": "priced"})
    assert gpu["messages"][0] == {"role": "system", "content": config.SYSTEM_PROMPT}
    assert gpu["model"] == config.RAG_MODEL


def test_ask_asks_for_a_low_temperature_and_a_bounded_answer(corpus, gpu):
    corpus.post("/api/ask", json={"question": "priced"})
    assert gpu["options"] == {"num_predict": config.MAX_TOKENS, "temperature": 0.2}


def test_ask_honours_an_explicit_top_k(corpus):
    body = corpus.post("/api/ask", json={"question": "priced", "top_k": 1}).json()
    assert len(body["citations"]) == 1


def test_ask_defaults_to_the_configured_top_k(corpus):
    body = corpus.post("/api/ask", json={"question": "priced"}).json()
    assert len(body["citations"]) == min(2, config.TOP_K)


def test_ask_on_an_empty_corpus_answers_ungrounded_and_says_so(client, gpu):
    """Not a 500 and not a silent empty answer: the model is told in as many words that
    nothing was retrieved, which is what makes it able to say the corpus does not cover it."""
    body = client.post("/api/ask", json={"question": "priced"}).json()
    assert body["citations"] == []
    assert "(no matching context was retrieved)" in gpu["messages"][1]["content"]


def test_ask_with_an_unknown_deck_id_is_a_404(corpus):
    r = corpus.post("/api/ask", json={"question_id": "no-such-question"})
    assert r.status_code == 404
    assert "no-such-question" in r.json()["detail"]


def test_ask_with_nothing_to_ask_is_a_422(corpus):
    assert corpus.post("/api/ask", json={}).status_code == 422
    assert corpus.post("/api/ask", json={"question": "   "}).status_code == 422


def test_ask_reports_a_broker_outage_as_503(corpus, monkeypatch):
    """503, not 500: the rail is fine, the GPU layer is not, and the UI can say to retry."""
    def boom(*_a, **_kw):
        raise broker.BrokerError("model failed to load")

    monkeypatch.setattr(broker, "chat", boom)
    r = corpus.post("/api/ask", json={"question": "priced"})
    assert r.status_code == 503
    assert "model failed to load" in r.json()["detail"]


def test_a_retrieval_outage_is_also_a_503(corpus, monkeypatch):
    def boom(_texts, *, model):
        raise broker.BrokerError("embedder down")

    monkeypatch.setattr(broker, "embed", boom)
    assert corpus.post("/api/ask", json={"question": "priced"}).status_code == 503


# --- upload --------------------------------------------------------------------------------
# /api/upload is admin-only, because it replaces a whole collection by name and can repoint a
# curated one. The shared `client` fixture is deliberately a NON-admin (the rest of this file
# needs that), so every upload call below passes ADMIN per request. The refusal side lives in
# test_auth.py, which is where the gate belongs.

def test_upload_indexes_a_document_and_reports_the_new_corpus(client):
    body = client.post("/api/upload", json={
        "name": "my notes",
        "text": "# Notes\n\n" + "GECX is priced through the account team. " * 6,
        "source": "notes.md"}, headers=ADMIN).json()
    assert body["collection"] == "my notes"
    assert body["chunks"] > 0
    assert body["corpus"]["chunks"] == body["chunks"]
    assert client.get("/api/collections").json()["collections"][0]["origin"] == "upload"


def test_an_uploaded_document_is_retrievable_immediately(client):
    client.post("/api/upload", json={
        "name": "my notes",
        "text": "# Notes\n\n" + "GECX guardrails block a response before it is sent. " * 6,
        "source": "notes.md"}, headers=ADMIN)
    cites = client.post("/api/ask", json={"question": "guardrails"}).json()["citations"]
    assert cites and cites[0]["collection"] == "my notes"


def test_upload_reports_an_embedder_outage_as_503(client, monkeypatch):
    def boom(_texts, *, model):
        raise broker.BrokerError("embedder down")

    monkeypatch.setattr(broker, "embed", boom)
    r = client.post("/api/upload", json={"name": "n", "text": "x" * 100}, headers=ADMIN)
    assert r.status_code == 503


def test_upload_validates_its_payload(client):
    """ADMIN headers on purpose: the gate runs BEFORE body validation, so a non-admin gets 403
    and this test would assert nothing about the schema. 422 here means the payload rules are
    real; 403 would only mean the gate is."""
    assert client.post("/api/upload", json={"name": "", "text": "x"},
                       headers=ADMIN).status_code == 422
    assert client.post("/api/upload", json={"name": "n"}, headers=ADMIN).status_code == 422
    # No maximum on `text` was an unbounded body fanning out to one embed call per 32 chunks.
    assert client.post("/api/upload", json={"name": "n", "text": "x" * 200_001},
                       headers=ADMIN).status_code == 422


# --- speak ---------------------------------------------------------------------------------

def test_speak_returns_a_cleaned_browser_payload(client):
    """The media worker is absent in this suite, so the seam degrades to the browser and the
    button keeps working without a byte of GPU."""
    body = client.post("/api/speak", json={"text": "It is metered per session [1]."}).json()
    assert body["mode"] == "browser"
    assert body["text"] == "It is metered per session."
    assert "audio_b64" not in body


def test_speak_requires_something_to_say(client):
    assert client.post("/api/speak", json={"text": ""}).status_code == 422


# --- ingest ---------------------------------------------------------------------------------

def test_admin_ingest_reports_per_collection_and_the_resulting_corpus(client, tmp_path,
                                                                     monkeypatch):
    seed = tmp_path / "kb"
    (seed / "discovery").mkdir(parents=True)
    (seed / "discovery" / "01-qualification.md").write_text(
        "# Qualification\n\n" + "Ask what the deflection target is and who owns it. " * 6,
        encoding="utf-8")
    monkeypatch.setattr(config, "SEED_KB_DIR", seed)
    body = client.post("/api/ingest", headers=ADMIN).json()
    assert body["report"]["found"] is True
    assert body["report"]["collections"][0]["status"] == "ingested"
    assert body["corpus"]["chunks"] > 0


# --- the streamed answer -----------------------------------------------------------------------

def test_the_websocket_streams_retrieval_then_tokens_then_done(corpus):
    """A cold 4B model takes ~20s to produce an 800-token answer, and a spinner for twenty
    seconds reads as a hang — so the sources arrive before the first token."""
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question_id": "pricing"}))
        first = ws.receive_json()
        assert first["type"] == "retrieval"
        assert first["question"] == "How is GECX priced?"
        assert [c["collection"] for c in first["citations"]] == ["pricing-and-licensing"]
        tokens = []
        frame = ws.receive_json()
        while frame["type"] == "token":
            tokens.append(frame["text"])
            frame = ws.receive_json()
        assert frame["type"] == "done"
        assert "".join(tokens) == "GECX pricing is not published [1]."


def test_the_streamed_answer_is_grounded_in_the_same_context(corpus, gpu):
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question": "priced"}))
        while ws.receive_json()["type"] != "done":
            pass
    assert gpu["streamed"] is True
    assert gpu["messages"][0]["content"] == config.SYSTEM_PROMPT


def test_a_bad_frame_is_an_error_the_socket_survives(corpus):
    """The client keeps one socket open for a session of asks. A typo must not end it."""
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text("not json at all")
        assert ws.receive_json()["type"] == "error"
        ws.send_text(json.dumps({"question_id": "no-such-question"}))
        err = ws.receive_json()
        assert err["type"] == "error"
        assert "no-such-question" in err["error"]
        ws.send_text(json.dumps({"question": "priced"}))
        assert ws.receive_json()["type"] == "retrieval"


def test_a_broker_outage_mid_stream_is_reported_as_an_error_frame(corpus, monkeypatch):
    async def boom(*_a, **_kw):
        raise broker.BrokerError("model failed to load")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(broker, "chat_stream", boom)
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question": "priced"}))
        assert ws.receive_json()["type"] == "retrieval"
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "model failed to load" in frame["error"]


def test_a_raw_httpx_error_mid_stream_is_still_an_error_frame_the_socket_survives(
        corpus, monkeypatch):
    """The sibling above raises the ALREADY-MAPPED BrokerError, which is exactly why V-16 stayed
    invisible for so long: the escapes the streaming facade actually had were httpx-typed, and
    `except broker.BrokerError` catches none of them. The broker 401ing mid-answer after a token
    rotation must still land as a frame — and must leave the socket usable, because the bug cost
    the whole conversation rather than the turn.

    Both asks are queued before anything is read, so the assertions need no live round trip, and
    every read goes through `receive_within` — see its docstring for why an unbounded
    `receive_json()` turns this exact regression into a hang instead of a failure.
    """
    async def boom(*_a, **_kw):
        raise httpx.HTTPStatusError(
            "Client error '401 Unauthorized'",
            request=httpx.Request("POST", "http://broker/v1/chat/stream"),
            response=httpx.Response(401))
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(broker, "chat_stream", boom)
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps({"question": "priced"}))
        ws.send_text(json.dumps({"question": "priced"}))

        assert receive_within(ws)["type"] == "retrieval"
        frame = receive_within(ws)
        assert frame["type"] == "error"
        assert "401" in frame["error"]
        # The catch-all is per turn, so the deck is still clickable: the second ask is served on
        # the same socket and fails the same way, rather than never being read at all.
        assert receive_within(ws)["type"] == "retrieval"
        assert receive_within(ws)["type"] == "error"


def test_a_json_array_is_a_bad_request_and_the_socket_survives(corpus):
    """A JSON ARRAY is a bad request, and must be treated as one.

    History, because this test has now asserted three different behaviours and each was an
    improvement on the last. `AskBody(**[1, 2])` raises TypeError. Originally that escaped the
    parse clause, escaped the outer `except WebSocketDisconnect`, and killed the socket with
    nothing sent, which is V-16's symptom reached without the broker being involved at all.
    V-16 added a per-turn catch-all, so it became an error frame and a clean close. The parse
    clause now lists TypeError, matching smb_partner/api.py:326 and :385, so it is handled
    where it belongs and the CONVERSATION SURVIVES.

    That last step is the point: a malformed body from one client message should cost that
    message, not the socket. The catch-all is still the net for errors nobody enumerated, and
    test_a_broken_turn_does_not_end_the_socket above is what holds it.
    """
    with corpus.websocket_connect("/ws/ask") as ws:
        ws.send_text(json.dumps([1, 2]))
        frame = receive_within(ws)
        assert frame["type"] == "error"
        assert frame["error"].startswith("bad request:"), (
            f"a malformed body should be reported as a bad request, not fall through to the "
            f"last-resort clause: {frame['error']!r}")

        # The socket is still serving. Under every earlier version this second ask was never
        # read, because the handler had already gone.
        ws.send_text(json.dumps({"question": "what is GECX?"}))
        assert receive_within(ws)["type"] == "retrieval"


def test_the_boot_warms_no_model_unless_asked(booted, monkeypatch):
    """The boot warm held about 3.5 GB of VRAM (and the runner's host RAM) for 30 minutes after
    every stack start on an 8 GB card, to save seconds on a first question that may not come.
    It is opt-in now (GEMINI_CX_WARM_ON_BOOT); off, the boot ingests and touches no model."""
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
