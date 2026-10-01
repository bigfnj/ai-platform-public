"""Translation between the OpenAI shape OpenMAIC speaks and the Ollama shape the broker speaks.

This is the part of the rail with no visible failure mode. A wrong ``options`` key does not
raise — Ollama ignores it and silently uses the model's own default, so a request asking for 200
tokens quietly gets 2048. A list-shaped ``content`` that is not flattened reaches the model as
the repr of a Python list, which produces a real answer to the wrong question. Both look like
working calls, which is why they are tested rather than eyeballed.
"""
import asyncio
import json

import pytest

from openmaic_app.api import llm


# --- request translation ----------------------------------------------------------------------

def test_max_tokens_becomes_num_predict():
    assert llm._options({"max_tokens": 256})["num_predict"] == 256


def test_max_completion_tokens_wins_over_the_legacy_spelling():
    """Both are in the wild. The current spelling is checked first so a client sending both
    does not get the deprecated value."""
    opts = llm._options({"max_completion_tokens": 100, "max_tokens": 999})
    assert opts["num_predict"] == 100


def test_absent_sampling_keys_are_not_materialised():
    """An empty dict means 'use the Modelfile defaults'. Filling in our own would override the
    model's tuning on every single request."""
    assert llm._options({"model": "x", "messages": []}) == {}


def test_zero_temperature_survives():
    """0.0 is falsy and a truthiness check would drop it — which turns a deliberately
    deterministic request into a sampled one."""
    assert llm._options({"temperature": 0})["temperature"] == 0


def test_stop_string_is_wrapped_in_a_list():
    assert llm._options({"stop": "END"})["stop"] == ["END"]
    assert llm._options({"stop": ["A", "B"]})["stop"] == ["A", "B"]
    assert "stop" not in llm._options({"stop": []})


def test_list_content_is_flattened_to_text():
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "explain "},
        {"type": "image_url", "image_url": {"url": "..."}},
        {"type": "text", "text": "orbits"},
    ]}]}
    assert llm._messages(body) == [{"role": "user", "content": "explain orbits"}]


def test_plain_string_content_passes_through():
    body = {"messages": [{"role": "system", "content": "be brief"}]}
    assert llm._messages(body) == [{"role": "system", "content": "be brief"}]


def test_missing_content_becomes_empty_string_not_none():
    """Ollama rejects a null content; an assistant turn with tool calls and no text is the
    common way to produce one."""
    assert llm._messages({"messages": [{"role": "assistant"}]})[0]["content"] == ""


def test_model_falls_back_to_the_configured_role():
    from openmaic_app.config import settings

    assert llm._model({}) == settings.llm_model
    assert llm._model({"model": "@openmaic"}) == "@openmaic"


def test_role_reference_is_not_expanded_here():
    """Expansion is the broker's job. Resolving it in the shim would freeze whatever Admin ->
    Rails said at process start."""
    assert llm._model({"model": "@openmaic"}).startswith("@")


def test_usage_maps_ollamas_counters():
    u = llm._usage({"prompt_eval_count": 12, "eval_count": 30})
    assert u == {"prompt_tokens": 12, "completion_tokens": 30, "total_tokens": 42}


def test_usage_tolerates_a_frame_with_no_counters():
    assert llm._usage({})["total_tokens"] == 0


# --- streaming --------------------------------------------------------------------------------

def _collect(frames):
    """Run the SSE generator against a canned broker stream and return the raw events."""
    async def fake_stream(model, messages, *, options=None, fmt=None, think=None,
                          keep_alive="30m"):
        for f in frames:
            yield f

    async def run():
        from openmaic_app import broker

        original = broker.chat_stream
        broker.chat_stream = fake_stream
        try:
            return [e async for e in llm._sse("m", [], {}, "30m")]
        finally:
            broker.chat_stream = original

    return asyncio.run(run())


def _payloads(events):
    out = []
    for e in events:
        body = e[len("data: "):].strip()
        if body != "[DONE]":
            out.append(json.loads(body))
    return out


def test_stream_opens_with_an_assistant_role_delta():
    """OpenAI clients expect the role once, in the first chunk."""
    events = _collect([{"message": {"content": "hi"}, "done": True}])
    assert _payloads(events)[0]["choices"][0]["delta"]["role"] == "assistant"


def test_stream_emits_content_deltas_then_done():
    events = _collect([
        {"message": {"content": "Newton"}},
        {"message": {"content": " said"}},
        {"done": True},
    ])
    assert events[-1] == "data: [DONE]\n\n"
    text = "".join(p["choices"][0]["delta"].get("content", "") for p in _payloads(events))
    assert text == "Newton said"


def test_stream_finishes_with_a_stop_reason():
    events = _collect([{"message": {"content": "x"}, "done": True}])
    assert _payloads(events)[-1]["choices"][0]["finish_reason"] == "stop"


def test_empty_deltas_are_not_emitted():
    """Ollama sends empty-content frames; forwarding them is pure noise on the wire."""
    events = _collect([{"message": {"content": ""}}, {"message": {"content": "a"}},
                       {"done": True}])
    contents = [p["choices"][0]["delta"].get("content") for p in _payloads(events)]
    assert contents.count("") == 1  # only the opening role frame


def test_mid_stream_error_frame_is_surfaced_not_swallowed():
    """The broker reports a post-stream failure as a final frame with HTTP 200 long since sent.
    Dropping it would render a truncated lecture as a finished one."""
    events = _collect([{"message": {"content": "part"}},
                       {"error": "model crashed", "done": True}])
    payloads = _payloads(events)
    assert any("error" in p for p in payloads)
    assert events[-1] == "data: [DONE]\n\n"
    # and it must NOT claim a clean finish
    assert not any(c.get("finish_reason") == "stop"
                   for p in payloads for c in p.get("choices", []))


def test_stream_stops_at_done_even_if_more_frames_follow():
    """A frame after done is a protocol violation; forwarding it would append text after the
    client already closed the turn."""
    events = _collect([{"message": {"content": "a"}, "done": True},
                       {"message": {"content": "LEAKED"}}])
    assert "LEAKED" not in "".join(events)


def test_broker_error_before_any_frame_still_terminates_the_stream():
    """The response is already 200 by then, so the only way to tell the client is in-band."""
    from openmaic_app import broker

    async def boom(model, messages, *, options=None, fmt=None, think=None,
                   keep_alive="30m"):
        raise broker.BrokerError("broker down")
        yield  # pragma: no cover - makes this an async generator

    async def run():
        original = broker.chat_stream
        broker.chat_stream = boom
        try:
            return [e async for e in llm._sse("m", [], {}, "30m")]
        finally:
            broker.chat_stream = original

    events = asyncio.run(run())
    assert events[-1] == "data: [DONE]\n\n"
    assert any("broker down" in e for e in events)


# --- model listing ----------------------------------------------------------------------------

def test_model_list_offers_roles_before_concrete_names(monkeypatch):
    """Picking @openmaic is what keeps Admin -> Rails authoritative, so it must be the one a
    user sees first; a concrete name pins the rail and makes that panel decorative."""
    from openmaic_app import broker

    monkeypatch.setattr(broker, "roles", lambda: [{"role": "openmaic", "resolved": "gemma3:4b"}])
    monkeypatch.setattr(broker, "models", lambda: [{"name": "gemma3:4b"}])
    ids = [m["id"] for m in llm.list_models()["data"]]
    assert ids[0] == "@openmaic"
    assert "gemma3:4b" in ids


def test_disabled_models_are_hidden(monkeypatch):
    from openmaic_app import broker

    monkeypatch.setattr(broker, "roles", lambda: [])
    monkeypatch.setattr(broker, "models",
                        lambda: [{"name": "ok:1b"}, {"name": "banned:70b", "disabled": True}])
    ids = [m["id"] for m in llm.list_models()["data"]]
    assert ids == ["ok:1b"]


def test_model_list_survives_an_unreachable_broker(monkeypatch):
    """The selector must render something rather than 500 — a down broker is a normal state."""
    from openmaic_app import broker

    def boom():
        raise broker.BrokerError("down")

    monkeypatch.setattr(broker, "roles", boom)
    assert llm.list_models() == {"object": "list", "data": []}


# --- regressions found by audit, 2026-09-16 ---------------------------------------------------

def test_response_format_json_object_maps_to_ollama_json():
    assert llm._response_format({"response_format": {"type": "json_object"}}) == "json"


def test_response_format_json_schema_is_honoured():
    """The current OpenAI spelling. Matching only json_object let a structured-output request
    degrade to free text with a 200 — the model was never put in JSON mode."""
    schema = {"type": "object", "properties": {"title": {"type": "string"}}}
    body = {"response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
    assert llm._response_format(body) == schema


def test_response_format_json_schema_without_a_schema_still_asks_for_json():
    assert llm._response_format({"response_format": {"type": "json_schema"}}) == "json"


def test_response_format_tolerates_a_non_dict():
    """A bare string here used to raise AttributeError inside the route and escape the OpenAI
    error envelope as a 500."""
    assert llm._response_format({"response_format": "json"}) is None
    assert llm._response_format({}) is None


def test_images_are_extracted_not_dropped():
    """Dropping them is the same class of bug as not flattening text: the model answers from the
    surrounding prompt alone, confidently, with a 200, and nothing in the reply says it never saw
    the picture."""
    body = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "what is this?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}]}
    msg = llm._messages(body)[0]
    assert msg["content"] == "what is this?"
    assert msg["images"] == ["QUJD"]


def test_remote_image_urls_are_not_forwarded_as_if_they_were_bytes():
    """Ollama wants bytes. Passing a URL through would be silently answered from the prompt."""
    body = {"messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}]}]}
    assert "images" not in llm._messages(body)[0]


def test_text_only_messages_carry_no_images_key():
    assert "images" not in llm._messages({"messages": [{"role": "user", "content": "hi"}]})[0]


def test_finish_reason_reports_truncation():
    """A reply cut off at num_predict reports done_reason 'length'. Calling that 'stop' tells the
    client a truncated slide body is a finished one."""
    assert llm._finish_reason({"done_reason": "length"}) == "length"
    assert llm._finish_reason({"done_reason": "stop"}) == "stop"
    assert llm._finish_reason({}) == "stop"


def _collect_with(frames, **kw):
    """Run _sse over canned frames, returning (events, captured_kwargs_sent_to_the_broker)."""
    seen = {}

    async def fake_stream(model, messages, *, options=None, fmt=None, think=None,
                          keep_alive="30m"):
        seen.update({"model": model, "options": options, "fmt": fmt, "think": think,
                     "keep_alive": keep_alive})
        for f in frames:
            yield f

    async def run():
        from openmaic_app import broker

        original = broker.chat_stream
        broker.chat_stream = fake_stream
        try:
            return [e async for e in llm._sse("m", [], {}, "30m", **kw)]
        finally:
            broker.chat_stream = original

    return asyncio.run(run()), seen


def test_streaming_passes_the_response_format_through():
    """The bug this replaces: fmt was computed, used on the buffered branch, and silently not
    forwarded on the streaming one — so a streamed JSON request got prose and the identical
    non-streamed request worked."""
    _, seen = _collect_with([{"done": True}], fmt="json")
    assert seen["fmt"] == "json"


def test_streamed_finish_reason_comes_from_the_done_frame():
    events, _ = _collect_with([{"message": {"content": "x"}},
                               {"done": True, "done_reason": "length"}])
    assert _payloads(events)[-1]["choices"][0]["finish_reason"] == "length"


def test_streamed_usage_is_emitted_only_when_asked_for():
    frames = [{"done": True, "prompt_eval_count": 5, "eval_count": 7}]
    with_usage, _ = _collect_with(frames, want_usage=True)
    assert _payloads(with_usage)[-1]["usage"]["total_tokens"] == 12
    without, _ = _collect_with(frames)
    assert "usage" not in _payloads(without)[-1]


def test_streamed_chunks_report_the_resolved_model_not_the_role():
    """Otherwise the same request answers '@openmaic' streamed and 'mistral-small3.2:24b' not."""
    events, _ = _collect_with([{"model": "gemma3:4b", "message": {"content": "a"}},
                               {"done": True}])
    assert _payloads(events)[-1]["model"] == "gemma3:4b"


def test_thinking_is_disabled_for_structured_output():
    """A thinking model asked for JSON spends its whole budget reasoning and returns EMPTY
    content -- measured at ~33% on another rail here. The reasoning slot is admin-repointable and
    roles.json already ships a thinking model, so this is one panel click away from live."""
    _, seen = _collect_with([{"done": True}], fmt="json", think=False)
    assert seen["think"] is False


def test_thinking_is_left_alone_for_free_text():
    """None means the model's own default. Forcing it off everywhere would silently change the
    behaviour of a model chosen precisely because it reasons."""
    _, seen = _collect_with([{"done": True}])
    assert seen["think"] is None
