"""Recorded edu-media-core faults, each pinned by the behaviour that was wrong.

1. The translation cache lost a concurrent job's result (each wrote back the snapshot it read
   before its model call) and a failed write discarded a translation already produced.
2. The clipart prompt existed twice, both live, nothing keeping them equal.
3. ``__all__`` omitted shipped modules.
4. tesseract's last-resort path was one developer's own toolbox directory.
5. ``images.generate_image`` loaded SDXL in-process, outside the broker's GPU gate.
6. ``search_clipart`` fetched any URL a search result named: any scheme, any address,
   redirects followed.
7. ``broker_media`` carried helpers nothing called (2026-10-14 audit).

No model, no network, no GPU: every external is a fake.
"""
from __future__ import annotations

import json
import socket
import sys
import types
from pathlib import Path

import pytest

from edu_media_stand_ins import stand_ins

# Heavy optional deps stand in for the imports alone, so the modules load on a bare Python, as in
# test_core.py. present_levels is withheld from the public snapshot (tools/publish.json), so its
# two tests skip there, saying so, and the rest of this file still runs.
with stand_ins(pil=False):
    from edu_media_core import broker_media, cache, images, translate  # noqa: E402
    try:
        from edu_media_core import present_levels  # noqa: E402
    except ImportError:
        present_levels = None
_NO_PRESENT_LEVELS = pytest.mark.skipif(
    present_levels is None, reason="edu_media_core/present_levels.py is withheld from the "
                                   "public snapshot; the other tests in this file still run.")


# --- 1. the shared cache --------------------------------------------------------------

def test_two_jobs_translating_at_once_both_keep_their_result(tmp_path, monkeypatch):
    """Job A misses, then job B misses and finishes first, then A finishes. A used to write
    back the store it read before its model call, which had no B in it."""
    path = tmp_path / "translations.json"
    order: list[str] = []

    def chat(system_prompt, user_message, **_kw):
        if user_message == "a":
            # B runs to completion while A is "inside the model call".
            translate.translate_cached(cache_path=path, system_prompt="s", user_message="b")
        order.append(user_message)
        return {"es": user_message.upper()}

    monkeypatch.setattr(translate, "chat_json", chat)
    translate.translate_cached(cache_path=path, system_prompt="s", user_message="a")
    store = json.loads(path.read_text(encoding="utf-8"))
    assert order == ["b", "a"]
    assert sorted(v["es"] for v in store.values()) == ["A", "B"], store


def test_a_failed_cache_write_still_returns_the_translation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(translate, "chat_json", lambda *a, **k: {"es": "hola"})

    def full_disk(_path, _data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cache, "save", full_disk)
    out = translate.translate_cached(cache_path=tmp_path / "t.json", system_prompt="s",
                                     user_message="hi")
    assert out == {"es": "hola"}
    assert "could not record a translation" in capsys.readouterr().err


def test_the_broker_path_keeps_a_concurrent_result_too(tmp_path, monkeypatch):
    path = tmp_path / "translations.json"

    def chat(system_prompt, user_message, **_kw):
        if user_message == "a":
            broker_media.translate_cached(cache_path=path, system_prompt="s", user_message="b")
        return {"es": user_message.upper()}

    monkeypatch.setattr(broker_media, "chat_json", chat)
    broker_media.translate_cached(cache_path=path, system_prompt="s", user_message="a")
    store = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(v["es"] for v in store.values()) == ["A", "B"], store


# --- 2. one clipart template ------------------------------------------------------------

def test_the_clipart_prompt_is_one_definition():
    assert broker_media._IMG_PROMPT is images._PROMPT_TMPL
    assert broker_media._IMG_NEGATIVE is images._NEGATIVE


# --- 3. __all__ names the package's own modules ------------------------------------------

def test_all_lists_every_shipped_module_but_the_private_one():
    import edu_media_core
    pkg = Path(edu_media_core.__file__).parent
    shipped = {p.stem for p in pkg.glob("*.py") if not p.stem.startswith("_")}
    # present_levels is withheld from the public snapshot (tools/publish.json), so naming it
    # in __all__ would break a star import there.
    assert set(edu_media_core.__all__) == shipped - {"present_levels"}


# --- 4. tesseract ------------------------------------------------------------------------

@_NO_PRESENT_LEVELS
def test_tesseract_has_no_per_user_fallback(monkeypatch):
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(present_levels.shutil, "which", lambda _n: None)
    assert present_levels._tesseract() == "tesseract"


@_NO_PRESENT_LEVELS
def test_an_explicit_tesseract_cmd_still_wins(monkeypatch):
    monkeypatch.setenv("TESSERACT_CMD", "/opt/t/tesseract")
    monkeypatch.setattr(present_levels.shutil, "which", lambda _n: "/usr/bin/tesseract")
    assert present_levels._tesseract() == "/opt/t/tesseract"


# --- 5. generate_image goes through the broker ---------------------------------------------

def test_generate_image_goes_through_the_broker_not_this_process(tmp_path, monkeypatch):
    seen = []

    def via_broker(subject, out_path, **kw):
        seen.append((subject, kw))
        return Path(out_path)

    monkeypatch.setattr(broker_media, "generate_image", via_broker)
    monkeypatch.setattr(images, "get_sdxl",
                        lambda: pytest.fail("SDXL was loaded in-process, outside the GPU gate"))
    out = images.generate_image("cat", tmp_path / "cat.png", steps=2, size=256)
    assert out == tmp_path / "cat.png"
    assert seen == [("cat", {"force": False, "steps": 2, "size": 256})]


def test_generate_image_still_returns_none_when_the_broker_is_down(tmp_path, monkeypatch):
    def down(*_a, **_k):
        raise broker_media.BrokerUnavailable("connection refused")
    monkeypatch.setattr(broker_media, "generate_image", down)
    assert images.generate_image("cat", tmp_path / "cat.png") is None


# --- 5b. the header's reads are bounded like every other rail's --------------------------

@pytest.mark.parametrize("read", ["roles", "models", "status"])
def test_header_reads_use_the_short_read_timeout(monkeypatch, read):
    seen = []

    def get(url, timeout=None, headers=None):
        seen.append(timeout)
        return types.SimpleNamespace(status_code=200, json=lambda: {}, text="{}")

    monkeypatch.setattr(broker_media.requests, "get", get)
    getattr(broker_media, read)()
    assert seen == [30.0], f"{read}() waited up to {seen} s"


# --- 6. search_clipart fetches only public http(s), and follows no redirect -----------------

ADDRS = {"img.example.test": "93.184.216.34", "lan.example.test": "192.168.1.20",
         "meta.example.test": "169.254.169.254", "split.example.test": None}


def _fake_getaddrinfo(host, port, *a, **k):
    if host == "split.example.test":     # one public and one private record
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]
    if host in ("127.0.0.1",):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    addr = ADDRS.get(host)
    if addr is None:
        raise socket.gaierror("no such host")
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))]


@pytest.fixture
def search(monkeypatch):
    urls = ["file:///etc/passwd", "http://127.0.0.1/a.png", "http://lan.example.test/a.png",
            "http://meta.example.test/latest/meta-data", "http://split.example.test/a.png",
            "gopher://img.example.test/a", "https://img.example.test/cat.png"]

    class DDGS:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def images(self, query, max_results=8, type_image=None):
            return [{"image": u} for u in urls]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))
    fetched: list[tuple[str, dict]] = []

    def get(url, **kw):
        fetched.append((url, kw))
        return types.SimpleNamespace(status_code=200, headers={"Content-Type": "image/png"},
                                     content=b"PNG")

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))
    monkeypatch.setattr(images.socket, "getaddrinfo", _fake_getaddrinfo)
    return fetched


def test_only_a_public_https_result_is_fetched(search):
    assert images.search_clipart("cat") == b"PNG"
    assert [u for u, _ in search] == ["https://img.example.test/cat.png"]


def test_no_redirect_is_followed(search):
    images.search_clipart("cat")
    assert search, "nothing was fetched, so no fetch was checked"
    assert all(kw.get("allow_redirects") is False for _u, kw in search)


# --- 7. broker_media keeps no helper nothing calls (audit 2026-10-14) ----------------------

@pytest.mark.parametrize("gone", ["synthesize_wav", "load_cache", "save_cache", "content_hash"])
def test_broker_media_carries_no_uncalled_helper(gone):
    """synthesize_wav was the repo's only client of the broker's single-clip /v1/tts, and no
    workflow called it (they synthesize through synthesize_wavs and /v1/tts_batch); the three
    cache re-exports had no caller either. A helper nobody calls is a second contract to keep in
    step with the first."""
    assert not hasattr(broker_media, gone)


def test_the_cache_re_export_a_caller_uses_is_kept():
    """cvc-worksheets' translator calls broker_media.clear_cache."""
    assert broker_media.clear_cache is not None
    assert callable(broker_media.synthesize_wavs)
