"""Retrieval-grounding — the baked index + nearest-neighbour shortlist, offline.

The broker embed is faked; the real baked reference index (in seed) is used, so the
numpy nearest-neighbour path is exercised end to end without a GPU."""
from __future__ import annotations

import pytest

from bouquet import broker, config, prompts, retrieval


def test_index_loads():
    idx = retrieval._index()
    assert idx is not None, "baked reference-index.npz should ship in seed"
    vectors, slugs = idx
    assert vectors.shape[0] == len(slugs) == 200
    assert vectors.shape[1] == 768


def test_shortlist_returns_nearest_flower(monkeypatch):
    vectors, slugs = retrieval._index()
    ri = slugs.index("rose")
    # the broker "embeds" the query to rose's own reference vector -> rose is nearest
    monkeypatch.setattr(broker, "embed_image", lambda _b64: vectors[ri].tolist())
    sl = retrieval.shortlist("b64", k=8, max_flowers=5)
    assert "Rose" in sl
    assert 1 <= len(sl) <= 5
    assert len(sl) == len(set(sl))          # de-duplicated titles


def test_shortlist_degrades_on_broker_error(monkeypatch):
    def boom(_b64):
        raise broker.BrokerError("embed down")
    monkeypatch.setattr(broker, "embed_image", boom)
    assert retrieval.shortlist("b64") == []   # identify still runs, ungrounded


def test_grounding_block_names_the_shortlist():
    block = prompts.grounding_block(["Rose", "Ranunculus", "Peony"])
    assert "Rose, Ranunculus, Peony" in block
    assert "do not list a flower you cannot actually see" in block


def test_identify_injects_grounding(monkeypatch):
    seen = {}

    def fake_json(model, messages, **kw):
        seen["system"] = messages[0]["content"]
        return {"flowers": [{"name": "rose"}], "palette": "", "arrangement": ""}

    monkeypatch.setattr(broker, "chat_json", fake_json)
    from bouquet import analyze as analyze_mod
    analyze_mod.identify("b64", shortlist=["Rose", "Peony"])
    assert "closest matches to THIS photo are, in order: Rose, Peony" in seen["system"]
