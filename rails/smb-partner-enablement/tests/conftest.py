"""Shared fixtures for the SMB Partner Enablement suite — offline, tmp-scoped, GPU-free.

Three things every test in this directory depends on, so they live here rather than being
re-derived in each file:

* **Never the real data volume.** ``config``'s paths are module constants snapshotted from the
  environment at import time, so setting an env var afterwards changes nothing — the attributes
  themselves have to be replaced. Without this the suite would write the SQLite index, the
  uploads and the audio cache into ``rails/smb-partner-enablement/data/``, and ingest would read
  the real 71-file seed corpus.
* **Never the broker, the GPU or the network.** ``smb_partner.broker``'s ``httpx`` handle is
  swapped for one that raises, so any model call this suite forgot to fake explodes here rather
  than dialling ``127.0.0.1:11500`` and hanging for the 600-second default timeout.
  ``fake_broker`` then supplies deterministic stand-ins for the calls that are meant to happen.
* **Never state from the previous test.** ``store`` holds the corpus in module globals and
  ``voice`` caches its media probe for five minutes; both outlive a test function.

The fake embedder is a hashing bag-of-words rather than a constant vector, so ranking is real:
a chunk that shares words with the query genuinely scores higher, and ``rag.rank`` is exercised
rather than asserted around.
"""
from __future__ import annotations

import re
import zlib

import numpy as np
import pytest

from smb_partner import broker, config, store, voice

#: Small enough to keep fixtures readable, wide enough that unrelated words rarely collide.
DIM = 64

USER = {"X-Platform-User": "admin", "X-Platform-Admin": "0"}
ADMIN = {"X-Platform-User": "admin", "X-Platform-Admin": "1"}


def hashed_vector(text: str) -> list[float]:
    """A deterministic bag-of-words vector. ``zlib.crc32`` rather than ``hash()``, because the
    latter is salted per interpreter and would make retrieval order vary from run to run."""
    vec = [0.0] * DIM
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        vec[zlib.crc32(word.encode("utf-8")) % DIM] += 1.0
    if not any(vec):
        vec[0] = 1.0  # an empty string still needs a usable unit vector
    return vec


def unit_matrix(texts: list[str]) -> np.ndarray:
    """The same L2-normalised matrix ``rag.embed_texts`` would produce, without a broker."""
    arr = np.asarray([hashed_vector(t) for t in texts], dtype=np.float32)
    return arr / (np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9)


class _NoNetwork:
    """Stands in for ``httpx`` inside the broker facade. Every entry point raises, so a missing
    fake surfaces as a loud AssertionError naming the call instead of a socket timeout."""

    @staticmethod
    def _boom(*_args, **_kwargs):
        raise AssertionError(
            "the test suite tried to reach the broker over HTTP — mock the broker call")

    post = _boom
    get = _boom
    stream = _boom
    AsyncClient = _boom

    # The httpx exception types the facade catches by name, so `except httpx.HTTPError` in
    # broker.py still resolves if a call ever gets that far.
    class HTTPError(Exception):
        pass

    class HTTPStatusError(HTTPError):
        pass


class FakeBroker:
    """A recording stand-in for the broker facade.

    Tests mutate the public attributes (``answer``, ``tokens``, ``installed`` ...) to shape a
    scenario, and read the recorded call lists to assert what the rail actually asked for —
    which prompt, which model, which token budget.
    """

    def __init__(self) -> None:
        self.answer = "Business Premium is the fit [1]."
        self.tokens = ["Business ", "Premium ", "is ", "the ", "fit [1]."]
        self.installed = ["qwen2.5:3b", "bge-m3:latest"]
        self.loaded = ["bge-m3:latest"]
        self.jobs: list[dict] = []
        self.role_table = [{"role": "smb-partner-rag", "resolved": "qwen2.5:3b"},
                           {"role": "embed", "resolved": "bge-m3"}]
        self.media = True
        self.chat_error: Exception | None = None
        self.stream_error: Exception | None = None
        self.embed_error: Exception | None = None
        self.tts_error: Exception | None = None
        self.transcribe_error: Exception | None = None
        # Recorded calls.
        self.chats: list[dict] = []
        self.streams: list[dict] = []
        self.embeds: list[list[str]] = []
        self.warmed: list[str] = []
        self.spoken: list[dict] = []
        self.transcribed: list[str] = []

    # --- model calls ---------------------------------------------------------
    def embed(self, text, *, model):
        if self.embed_error:
            raise self.embed_error
        texts = [text] if isinstance(text, str) else list(text)
        self.embeds.append(texts)
        return [hashed_vector(t) for t in texts]

    def chat(self, model, messages, *, options=None, fmt=None, keep_alive="30m"):
        self.chats.append({"model": model, "messages": messages, "options": options,
                           "keep_alive": keep_alive})
        if self.chat_error:
            raise self.chat_error
        return self.answer

    def chat_stream(self, model, messages, *, options=None, keep_alive="30m"):
        self.streams.append({"model": model, "messages": messages, "options": options})
        error = self.stream_error
        tokens = list(self.tokens)

        async def _gen():
            if error:
                raise error
            for tok in tokens:
                yield tok

        return _gen()

    # --- control plane -------------------------------------------------------
    def status(self):
        return {"loaded": [{"name": n} for n in self.loaded],
                "jobs": list(self.jobs),
                "media": {"enabled": self.media}}

    def models(self):
        return [{"name": n} for n in self.installed]

    def roles(self):
        return list(self.role_table)

    def warm(self, model, keep_alive="30m"):
        self.warmed.append(model)
        return {"ok": True}

    # --- media ---------------------------------------------------------------
    def tts_light(self, text, *, voice=None, lang_code=None, speed=None, timeout=None):
        if self.tts_error:
            raise self.tts_error
        self.spoken.append({"text": text, "voice": voice, "lang_code": lang_code})
        return {"audio_b64": "QUJD", "sample_rate": 24000}

    def transcribe(self, audio_b64, *, suffix=None, language=None, timeout=None):
        if self.transcribe_error:
            raise self.transcribe_error
        self.transcribed.append(audio_b64)
        return {"text": "  how does the copilot trial work  ", "language": "en", "duration": 2.5}


@pytest.fixture(autouse=True)
def isolated_rail(tmp_path, monkeypatch):
    """Point every writable path at ``tmp_path`` and clear the module-level caches."""
    data = tmp_path / "data"
    seed = tmp_path / "seed"
    seed.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", str(data / "smb_partner.db"))
    monkeypatch.setattr(config, "UPLOADS_DIR", data / "uploads")
    monkeypatch.setattr(config, "AUDIO_CACHE_DIR", data / "audio")
    monkeypatch.setattr(config, "SEED_KB_DIR", seed)
    # Read at request time by identity(), but snapshotted from the environment at import — so
    # the flag has to be set on the module, not the environment. See test_config.py.
    monkeypatch.setattr(config, "STANDALONE", False)
    monkeypatch.delenv("PLATFORM_STANDALONE", raising=False)
    # monkeypatch restores the value it saw at setattr time, which is this reset value — so the
    # probe cache is None at the start of every test regardless of what the last one left.
    monkeypatch.setattr(voice, "_probe", None)
    monkeypatch.setattr(broker, "httpx", _NoNetwork)
    store.init()
    return tmp_path


@pytest.fixture()
def fake_broker(monkeypatch):
    """Install the deterministic broker. ``media_enabled`` and ``resolved_model`` are left real:
    they are logic built on ``status()``/``roles()``, and faking them would skip it."""
    fake = FakeBroker()
    for name in ("embed", "chat", "chat_stream", "status", "models", "roles", "warm",
                 "tts_light", "transcribe"):
        monkeypatch.setattr(broker, name, getattr(fake, name))
    return fake


@pytest.fixture()
def seed_corpus():
    """Index a collection the way ingest would, using the fake embedder.

    Yields the callable rather than a fixed corpus so a test can seed several collections and
    control the text it will later query against.
    """
    def _seed(collection: str, rows: list[dict], *, origin: str = "seed",
              label: str = "") -> int:
        vectors = unit_matrix([r["text"] for r in rows])
        return store.replace_collection(collection, label or collection.title(), origin,
                                        rows, vectors)
    return _seed


def chunk(text: str, *, source: str = "doc.md", title: str = "",
          collection: str = "") -> dict:
    """A store row in the shape ``replace_collection`` expects."""
    return {"collection": collection, "source": source, "title": title, "text": text}
