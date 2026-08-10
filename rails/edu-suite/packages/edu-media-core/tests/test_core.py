"""Unit tests for the pure logic in edu-media-core.

Heavy optional deps (ollama, pdfplumber, fitz, pytesseract, PIL) are stubbed in
sys.modules so these tests run with a bare Python — they exercise the caching,
classification, and PDF-parsing logic, not the models. Run:

    python -m unittest discover -s packages/edu-media-core/tests
    # or: pytest packages/edu-media-core/tests
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

# --- stub heavy deps so the modules import without CUDA/native libs -----------
for _name in ("ollama", "pdfplumber", "fitz", "pytesseract"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
if "PIL" not in sys.modules:
    _pil = types.ModuleType("PIL")
    _img = types.ModuleType("PIL.Image")
    _pil.Image = _img
    sys.modules["PIL"] = _pil
    sys.modules["PIL.Image"] = _img

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_media_core import classify, pdf, translate  # noqa: E402


class _FakeOllama:
    """Stand-in for the ollama module; records calls and returns a fixed payload."""
    def __init__(self, payload: dict, *, boom: bool = False):
        self._payload = payload
        self.boom = boom
        self.calls = 0

    def Client(self, host=None):  # noqa: N802 (mimics ollama.Client)
        return self

    def chat(self, **kwargs):
        self.calls += 1
        if self.boom:
            raise AssertionError("ollama.chat should not have been called (cache hit expected)")
        return {"message": {"content": json.dumps(self._payload)}}


class TranslateTests(unittest.TestCase):
    def test_content_hash_stable_and_distinct(self):
        self.assertEqual(translate.content_hash("abc"), translate.content_hash("abc"))
        self.assertNotEqual(translate.content_hash("abc"), translate.content_hash("abd"))

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "cache.json"
            self.assertEqual(translate.load_cache(p), {})  # missing → {}
            translate.save_cache(p, {"k": {"word_es": "árbol"}})
            self.assertEqual(translate.load_cache(p), {"k": {"word_es": "árbol"}})
            self.assertTrue(translate.cache_has(p, "k"))
            translate.clear_cache(p)
            self.assertFalse(p.exists())

    def test_translate_cached_hit_skips_model(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            translate.save_cache(p, {"key1": {"word_es": "sol"}})
            translate.ollama = _FakeOllama({}, boom=True)  # must not be called
            out = translate.translate_cached(
                cache_path=p, cache_key="key1",
                system_prompt="s", user_message="u",
                required_keys=("word_es",),
            )
            self.assertEqual(out, {"word_es": "sol"})

    def test_translate_cached_miss_calls_model_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            fake = _FakeOllama({"word_es": "gato", "image_query": "cat"})
            translate.ollama = fake
            out = translate.translate_cached(
                cache_path=p, cache_key="k2",
                system_prompt="s", user_message="u",
                required_keys=("word_es", "image_query"),
            )
            self.assertEqual(out["word_es"], "gato")
            self.assertEqual(fake.calls, 1)
            self.assertEqual(translate.load_cache(p)["k2"]["word_es"], "gato")  # persisted

    def test_translate_cached_validates_required_keys(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "cache.json"
            translate.ollama = _FakeOllama({"word_es": "gato"})  # missing image_query
            with self.assertRaises(ValueError):
                translate.translate_cached(
                    cache_path=p, cache_key="k3",
                    system_prompt="s", user_message="u",
                    required_keys=("word_es", "image_query"),
                )


class ClassifyTests(unittest.TestCase):
    def _slide(self, title, bullets=(), paragraphs=(), raw=None):
        return {"title": title, "bullets": list(bullets),
                "paragraphs": list(paragraphs), "raw_text": raw if raw is not None else title}

    def test_types_weeks_and_dedup(self):
        slides = [
            self._slide("Week 1"),                                   # header, sets week
            self._slide("Anfitrion", bullets=["welcomes guests"]),   # content, week 1
            self._slide("Anfitrion", bullets=["welcomes guests"], raw="Anfitrion welcomes guests"),
            self._slide(""),                                         # empty
            self._slide("Math"),                                    # section header
        ]
        # make the two Anfitrion slides identical raw_text for the dedup check
        slides[1]["raw_text"] = "Anfitrion welcomes guests"
        out = classify.classify_slides(slides)
        types_ = [s["type"] for s in out]
        self.assertEqual(types_, ["header", "content", "duplicate", "empty", "header"])
        self.assertEqual(out[1]["week"], 1)
        self.assertIsNone(out[0]["week"])

    def test_headerless_content_without_bullets_is_header(self):
        out = classify.classify_slides([self._slide("Just a title", bullets=[], paragraphs=[])])
        self.assertEqual(out[0]["type"], "header")


class PdfParseTests(unittest.TestCase):
    def test_join_wrapped(self):
        lines = ["Needs are things you must have to be safe, healthy,", "and okay.", "Done."]
        self.assertEqual(pdf._join_wrapped(lines),
                         ["Needs are things you must have to be safe, healthy, and okay.", "Done."])

    def test_parse_page_splits_title_bullets_paragraphs(self):
        # Bullets ending in sentence punctuation stay separate.
        raw = "Host\n- welcomes guests.\n- takes reservations.\nA friendly greeter."
        page = pdf._parse_page(3, raw)
        self.assertEqual(page["slide_number"], 3)
        self.assertEqual(page["title"], "Host")
        self.assertEqual(page["bullets"], ["welcomes guests.", "takes reservations."])
        self.assertEqual(page["paragraphs"], ["A friendly greeter."])

    def test_parse_page_keeps_unpunctuated_bullets_separate(self):
        # Bullets are discrete list items: even without terminal punctuation they must
        # NOT fuse (wrap-joining is paragraph-only now).
        raw = "Host\n- welcomes guests\n- takes reservations"
        page = pdf._parse_page(3, raw)
        self.assertEqual(page["bullets"], ["welcomes guests", "takes reservations"])


class SynthesizeWavsBatchingTests(unittest.TestCase):
    """synthesize_wavs must sub-batch clips so no single broker request is unbounded
    — the cause of the 1200s read-timeout on large 'Just Translate' documents."""

    def test_subbatches_and_writes_every_clip(self):
        import base64 as _b64

        from edu_media_core import broker_media

        calls = []

        def fake_post(path, payload, **kw):
            self.assertEqual(path, "/v1/tts_batch")
            n = len(payload["items"])
            calls.append(n)
            return {"audios": [_b64.b64encode(b"wav").decode() for _ in range(n)]}

        orig_post, orig_batch = broker_media._post, broker_media._TTS_BATCH
        broker_media._post = fake_post
        broker_media._TTS_BATCH = 3
        try:
            with tempfile.TemporaryDirectory() as d:
                items = [{"lang": "en" if i % 2 == 0 else "es", "text": f"t{i}"} for i in range(7)]
                paths = [Path(d) / f"c{i}.wav" for i in range(7)]
                progress = []
                written = broker_media.synthesize_wavs(
                    items, paths, on_progress=lambda done, total: progress.append((done, total))
                )
                # 7 clips at batch size 3 -> three requests of 3, 3, 1
                self.assertEqual(calls, [3, 3, 1])
                self.assertEqual(len(written), 7)
                for p in paths:
                    self.assertTrue(p.exists())
                    self.assertEqual(p.read_bytes(), b"wav")
                self.assertEqual(progress, [(3, 7), (6, 7), (7, 7)])
        finally:
            broker_media._post, broker_media._TTS_BATCH = orig_post, orig_batch

    def test_rejects_misaligned_out_paths(self):
        from edu_media_core import broker_media

        with self.assertRaises(ValueError):
            broker_media.synthesize_wavs([{"lang": "en", "text": "a"}], [])


class BrokerRetryTests(unittest.TestCase):
    """_post retries media/batch calls on a busy-broker timeout, and fails fast when
    retries=0 (interactive calls). Regression for the audio stage dying on a queued
    broker request."""

    def _run(self, *, fail_times, retries):
        import requests as _rq

        from edu_media_core import broker_media as b

        calls = {"n": 0}

        class Resp:
            status_code = 200

            def json(self):
                return {"ok": True}

        def flaky_post(url, json=None, timeout=None):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise _rq.Timeout("broker busy")
            return Resp()

        orig_req, orig_time = b.requests, b.time
        b.requests = types.SimpleNamespace(post=flaky_post, Timeout=_rq.Timeout,
                                           RequestException=_rq.RequestException)
        b.time = types.SimpleNamespace(sleep=lambda *_a, **_k: None)  # no real waiting
        try:
            result = b._post("/v1/tts_batch", {"items": []}, retries=retries, backoff=0)
            return calls["n"], result, None
        except b.BrokerTimeout as e:
            return calls["n"], None, e
        finally:
            b.requests, b.time = orig_req, orig_time

    def test_retries_then_succeeds(self):
        n, result, err = self._run(fail_times=2, retries=2)
        self.assertIsNone(err)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(n, 3)  # 1 initial + 2 retries

    def test_no_retry_fails_fast(self):
        n, result, err = self._run(fail_times=1, retries=0)
        self.assertIsInstance(err, Exception)
        self.assertEqual(n, 1)  # tried once, no retry

    def test_gives_up_after_retries(self):
        n, result, err = self._run(fail_times=9, retries=2)
        self.assertIsInstance(err, Exception)
        self.assertEqual(n, 3)  # 1 + 2 retries, then raise


if __name__ == "__main__":
    unittest.main()
