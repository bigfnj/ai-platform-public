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


class TtsCostBatchingTests(unittest.TestCase):
    """Sub-batches are packed by ESTIMATED COST, not by a fixed count.

    WHY. Measured on the live broker 2026-09-16: XTTS costs ~34s to load per request plus
    ~0.040s per character, so 48 clips of dialogue is ~90s and 48 clips of glossary text is
    ~480s. Only one of those fits a 480s read timeout, and a timeout costs the WHOLE
    sub-batch because _post is called with retry_on_timeout=False. A fixed count cannot be
    both safe for the long case and efficient for the short one.
    """

    def test_a_long_clip_closes_the_batch_before_the_count_cap(self):
        from edu_media_core import broker_media as bm

        # A budget that fits about four 500-character clips, with a count cap far above it.
        # 500 chars * 0.045 = 22.5s each; budget = 300*0.6 - 40 = 140s -> 6 per batch.
        items = [{"lang": "es", "text": "x" * 500} for _ in range(13)]
        batches = bm.tts_batches(items, timeout=300.0, count_cap=1000)
        self.assertTrue(all(len(b) <= 6 for b in batches), [len(b) for b in batches])
        self.assertEqual(sum(len(b) for b in batches), 13)

    def test_short_clips_fill_up_to_the_COUNT_cap(self):
        """The other half: the cost cap must not make every batch tiny, or the change is a
        slowdown rather than the point of it."""
        from edu_media_core import broker_media as bm

        items = [{"lang": "es", "text": "corto"} for _ in range(200)]
        batches = bm.tts_batches(items, timeout=1320.0, count_cap=96)
        self.assertEqual([len(b) for b in batches], [96, 96, 8])

    def test_one_clip_longer_than_the_whole_budget_still_goes_out(self):
        """Never an empty batch and never a dropped clip: refusing it would silently remove
        a sentence from a student's book. The broker's own ceiling is the backstop."""
        from edu_media_core import broker_media as bm

        items = [{"lang": "es", "text": "y" * 100000}, {"lang": "es", "text": "corto"}]
        batches = bm.tts_batches(items, timeout=60.0, count_cap=96)
        self.assertEqual([len(b) for b in batches], [1, 1])
        self.assertEqual(sum(len(b) for b in batches), 2)

    def test_the_budget_scales_with_the_read_timeout(self):
        """A bigger timeout must buy bigger batches, or the deployed 1320s is worth nothing
        over the 480s default."""
        from edu_media_core import broker_media as bm

        items = [{"lang": "es", "text": "z" * 200} for _ in range(400)]
        small = bm.tts_batches(items, timeout=480.0, count_cap=1000)
        large = bm.tts_batches(items, timeout=1320.0, count_cap=1000)
        self.assertGreater(len(large[0]), len(small[0]),
                           "a longer timeout did not buy a longer batch")

    def test_a_tiny_clip_costs_the_FLOOR_not_its_character_count(self):
        """Asserted against the floor constant, with literal batch sizes below, because the
        obvious version of this test computed its own expectation by calling
        _tts_clip_cost_s and therefore moved with the mutation it was meant to catch. A
        one-word clip is not free: it still pays XTTS's per-utterance overhead."""
        from edu_media_core import broker_media as bm

        self.assertEqual(bm._tts_clip_cost_s("a"), bm._TTS_CLIP_FLOOR_S)
        self.assertEqual(bm._tts_clip_cost_s(""), bm._TTS_CLIP_FLOOR_S)
        # 140s timeout -> a 44s budget. At the 1.0s floor that is ~44 clips a batch; with the
        # floor removed a one-character clip costs 0.045s and ~977 would be packed in.
        items = [{"lang": "es", "text": "a"} for _ in range(200)]
        batches = bm.tts_batches(items, timeout=140.0, count_cap=1000)
        self.assertGreater(len(batches), 3,
                           "200 tiny clips were packed into too few requests, so the "
                           "per-clip floor is not being applied")
        self.assertTrue(all(len(b) <= 50 for b in batches), [len(b) for b in batches])

    def test_no_batch_exceeds_its_time_budget(self):
        """The invariant the whole thing exists for, asserted over mixed lengths rather
        than one size: no request is estimated to outlast the read timeout."""
        from edu_media_core import broker_media as bm

        lengths = [49, 341, 12, 115, 230, 60, 300, 8, 175, 341, 341, 30]
        items = [{"lang": "es", "text": "q" * n} for n in lengths * 6]
        timeout = 480.0
        budget = timeout * bm._TTS_BUDGET_FRACTION - bm._TTS_LOAD_S
        for b in bm.tts_batches(items, timeout=timeout, count_cap=96):
            cost = sum(bm._tts_clip_cost_s(i["text"]) for i in b)
            if len(b) > 1:   # a lone over-budget clip is the documented exception
                self.assertLessEqual(cost, budget,
                                     f"a batch of {len(b)} is estimated at {cost:.0f}s "
                                     f"against a {budget:.0f}s budget")

    def test_every_clip_lands_on_ITS_OWN_path_across_uneven_batches(self):
        """The regression that packing by cost introduces. out_paths used to be indexed by a
        fixed stride, which is only correct while every batch is the same size. With uneven
        batches a stride writes every clip after the first short batch to the WRONG file,
        which is a book read aloud in the wrong order — and every path still exists, so
        nothing downstream notices."""
        import base64 as _b64

        from edu_media_core import broker_media as bm

        # Deliberately uneven: one huge clip, then short ones, then another huge one.
        texts = (["L" * 4000] + [f"s{i}" for i in range(9)] + ["M" * 4000]
                 + [f"t{i}" for i in range(4)])
        items = [{"lang": "es", "text": t} for t in texts]

        def fake_post(path, payload, **kw):
            # Echo each clip's OWN text back as its audio, so a mis-indexed write is visible.
            return {"audios": [_b64.b64encode(it["text"].encode()).decode()
                               for it in payload["items"]]}

        orig = bm._post
        bm._post = fake_post
        try:
            with tempfile.TemporaryDirectory() as d:
                paths = [Path(d) / f"c{i}.wav" for i in range(len(texts))]
                progress = []
                written = bm.synthesize_wavs(
                    items, paths, on_progress=lambda done, total: progress.append(done))
                self.assertEqual(len(written), len(texts))
                for p, t in zip(paths, texts):
                    self.assertEqual(p.read_bytes().decode(), t,
                                     f"{p.name} holds another clip's audio")
                # Progress is cumulative and ends at the total, even with uneven batches.
                self.assertEqual(progress[-1], len(texts))
                self.assertEqual(progress, sorted(progress))
        finally:
            bm._post = orig


class BrokerRetryTests(unittest.TestCase):
    """_post retries media/batch calls on a busy-broker timeout, and fails fast when
    retries=0 (interactive calls). Regression for the audio stage dying on a queued
    broker request."""

    def _run(self, *, fail_times, retries, retry_on_timeout=True, status=200):
        import requests as _rq

        from edu_media_core import broker_media as b

        calls = {"n": 0}

        class Resp:
            status_code = status
            text = "media worker died"

            def json(self):
                return {"ok": True}

        # **kw absorbs headers=: _post grew an Authorization header when the broker started
        # enforcing its control-plane token, and this stub was not updated, so all three of
        # these retry tests had been failing on a TypeError instead of exercising the retry.
        def flaky_post(url, json=None, timeout=None, **kw):
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise _rq.Timeout("broker busy")
            return Resp()

        orig_req, orig_time = b.requests, b.time
        b.requests = types.SimpleNamespace(post=flaky_post, Timeout=_rq.Timeout,
                                           RequestException=_rq.RequestException)
        b.time = types.SimpleNamespace(sleep=lambda *_a, **_k: None)  # no real waiting
        try:
            result = b._post("/v1/tts_batch", {"items": []}, retries=retries, backoff=0,
                             retry_on_timeout=retry_on_timeout)
            return calls["n"], result, None
        except Exception as e:
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

    def test_media_calls_do_not_retry_a_timeout(self):
        """A media timeout means the broker never answered, not that it was slow.

        The client ceiling now sits ABOVE the broker's own media_timeout, so a timeout
        implies the worker the broker spawned may still be holding the GPU. Retrying then
        queues a second identical job behind the first and doubles an already long wait, which
        at 1320s is ~44 minutes to learn the same thing twice.
        """
        n, result, err = self._run(fail_times=9, retries=2, retry_on_timeout=False)
        self.assertIsInstance(err, Exception)
        self.assertEqual(n, 1)  # tried once; the retries were NOT spent

    def test_a_connection_error_still_retries(self):
        """The case a retry genuinely fixes: a broker that is briefly restarting."""
        import requests as _rq

        from edu_media_core import broker_media as b

        calls = {"n": 0}

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"ok": True}

        def flaky(url, json=None, timeout=None, **kw):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _rq.RequestException("connection refused")
            return Resp()

        orig_req, orig_time = b.requests, b.time
        b.requests = types.SimpleNamespace(post=flaky, Timeout=_rq.Timeout,
                                           RequestException=_rq.RequestException)
        b.time = types.SimpleNamespace(sleep=lambda *_a, **_k: None)
        try:
            out = b._post("/v1/image", {}, retries=2, backoff=0, retry_on_timeout=False)
        finally:
            b.requests, b.time = orig_req, orig_time
        self.assertEqual(out, {"ok": True})
        self.assertEqual(calls["n"], 3)

    def test_an_http_error_is_a_broker_error(self):
        """A 502 must be catchable as BrokerError.

        It was a bare RuntimeError, and the broker turns EVERY backend fault into a 502 — a
        dead media worker, a model pull in flight, Ollama restarting, a 401 after a token
        rotation. So `except BrokerError`, written precisely to degrade on broker faults, did
        not fire for the most common broker fault there is.
        """
        from edu_media_core import broker_media as b

        n, result, err = self._run(fail_times=0, retries=2, status=502)
        self.assertIsInstance(err, b.BrokerError)
        self.assertIsInstance(err, b.BrokerHTTPError)
        self.assertEqual(err.status, 502)
        self.assertEqual(n, 1)  # an HTTP answer is the broker's verdict, never retried


if __name__ == "__main__":
    unittest.main()


# --- A-8: the profile registry was WRITE-ONLY ----------------------------------------------
# Five register() calls insert into _REGISTRY and nothing read it: every caller keeps the
# object register() returned and reads .system_prompt / .options / .required_keys off it
# directly, so get() and all_profiles() had no caller and Profile.key / .label were never
# read either.
#
# Covered rather than deleted. They are the READ half of a registry whose docstring states
# its purpose ("a catalog for a future audience picker"), and removing the only way to read a
# registry does not improve the fact that nothing reads it. A test makes the API verifiable
# and stops the accessors drifting from the register() they belong to.

def test_a_registered_profile_can_be_fetched_by_key():
    from edu_media_core import profiles

    p = profiles.register(profiles.Profile(
        key="test_fetch", label="Test", system_prompt="sp",
        options={"temperature": 0.1}, required_keys=("es",)))
    got = profiles.get("test_fetch")
    assert got is p, "get() must return the registered object, not a copy"
    assert got.label == "Test"
    assert got.required_keys == ("es",)


def test_an_unknown_key_names_what_IS_registered():
    """The error is the useful part: a typo'd key should say what the options were."""
    import pytest

    from edu_media_core import profiles

    profiles.register(profiles.Profile(
        key="test_known", label="Known", system_prompt="sp", options={},
        required_keys=("es",)))
    with pytest.raises(KeyError) as caught:
        profiles.get("test_nope")
    msg = str(caught.value)
    assert "test_nope" in msg
    assert "test_known" in msg, f"the error must list what IS registered: {msg}"


def test_all_profiles_sees_everything_registered():
    from edu_media_core import profiles

    p = profiles.register(profiles.Profile(
        key="test_catalog", label="Catalog", system_prompt="sp", options={},
        required_keys=("es",)))
    everything = profiles.all_profiles()
    assert p in everything
    # The real profiles register at import time, so the catalog is not just this test's.
    assert len(everything) > 1, "all_profiles returned only the test's own entry"
