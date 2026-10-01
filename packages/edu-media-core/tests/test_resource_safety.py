"""Regression tests for resource safety in edu-media-core.

Three defects, all on the FAILURE path where nothing was watching:

* ``pdf.read_slides`` and ``present_levels.ocr_pdf`` leaked the PyMuPDF document handle
  when extraction raised (encrypted/corrupt upload, missing tesseract) — a memory-mapped
  file per upload inside the long-lived dashboard container, and on Windows a pinned file
  that the upload can no longer replace. ``ocr_pdf`` never closed its document at all.
* ``tts.get_tts`` restored the ``weights_only=False`` monkey-patch of ``torch.load`` only
  on the success path, so a download failure or a CUDA OOM left the whole process patched
  — and since ``_tts`` stays None, the next attempt nested another wrapper.
* ``cache.save`` rewrote the shared translations.json in place, so a kill or a full disk
  mid-write truncated it — and ``load`` reads a truncated file as NO cache at all.

Hermetic: fitz, torch, TTS, pdfplumber and tesseract are all fakes. No GPU, no network,
no model load, no real PDF. Run:

    .venv/Scripts/python.exe -m pytest packages/edu-media-core/tests -q -p no:cacheprovider
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path

# --- stub heavy deps so the modules import without CUDA/native libs (see test_core.py) ---
for _name in ("ollama", "pdfplumber", "fitz", "pytesseract"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
if "PIL" not in sys.modules:
    _pil = types.ModuleType("PIL")
    _img = types.ModuleType("PIL.Image")
    _pil.Image = _img
    sys.modules["PIL"] = _pil
    sys.modules["PIL.Image"] = _img

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_media_core import cache, pdf, tts  # noqa: E402

# present_levels.py is withheld from the public snapshot together with its only callers (see
# publish.json paths.exclude). Imported unconditionally, it made pytest abort collection there,
# and with it every other test in the same run, including the three classes in this file that
# test code which does ship. Guarded on the FILE, as tools/tests/test_rail_smoke_roles.py is, so
# a real import error in the private repo still fails instead of skipping.
_PRESENT_LEVELS = Path(__file__).resolve().parents[1] / "src" / "edu_media_core" / "present_levels.py"
if _PRESENT_LEVELS.is_file():
    from edu_media_core import present_levels  # noqa: E402
else:
    present_levels = None


# --------------------------------------------------------------------------- fakes ---

class _FakePage:
    """A pymupdf page. ``boom`` makes rendering fail the way a corrupt page does."""

    def __init__(self, boom: bool = False):
        self._boom = boom

    def get_pixmap(self, **_kw):
        if self._boom:
            raise RuntimeError("cannot render page: damaged xref")
        return types.SimpleNamespace(save=lambda _p: None)


class _FakeDoc:
    """A pymupdf document that records whether it was closed.

    It implements the context-manager protocol because the fix relies on it; a regression
    back to a bare ``fitz.open(...)`` assignment simply never closes it, which is what
    these tests assert on.
    """

    def __init__(self, pages: int = 1, *, render_boom: bool = False):
        self.closed = False
        self._pages = pages
        self._render_boom = render_boom

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False

    def __len__(self):
        return self._pages

    def __iter__(self):
        return iter([_FakePage(self._render_boom) for _ in range(self._pages)])

    def __getitem__(self, _i):
        return _FakePage(self._render_boom)

    def close(self):
        self.closed = True


class _FakePlumberPage:
    def __init__(self, text: str):
        self._text = text
        self.chars = []

    def dedupe_chars(self, tolerance=1):
        return self

    def extract_text(self):
        return self._text


class _FakePlumberPdf:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# ------------------------------------------------------- FIX 1a: pdf.read_slides ---

class PdfHandleTests(unittest.TestCase):
    """read_slides must release the fitz document on EVERY path. Its live callers
    (just_translate, teachtown/builder) hand it a user-uploaded PDF, so any leak is one
    memory-mapped handle per upload in a container that never restarts."""

    def setUp(self):
        self._orig = (pdf.fitz, pdf.pdfplumber, pdf._ocr_page)
        self.doc = _FakeDoc(pages=1)
        pdf.fitz = types.SimpleNamespace(open=lambda *_a, **_k: self.doc)

    def tearDown(self):
        pdf.fitz, pdf.pdfplumber, pdf._ocr_page = self._orig

    def _plumber(self, pages=None, *, boom=None):
        def _open(*_a, **_k):
            if boom is not None:
                raise boom
            return _FakePlumberPdf(pages or [])
        return types.SimpleNamespace(open=_open)

    def test_closes_doc_when_pdfplumber_raises(self):
        # An encrypted or corrupt upload: pdfplumber.open raises before any page is read.
        pdf.pdfplumber = self._plumber(boom=ValueError("file has not been decrypted"))
        with self.assertRaises(ValueError):
            pdf.read_slides("upload.pdf")
        self.assertTrue(self.doc.closed,
                        "fitz document leaked when pdfplumber.open raised")

    def test_closes_doc_when_ocr_raises(self):
        # An image-only page with no tesseract on PATH: TesseractNotFoundError from the
        # OCR fallback, midway through the loop.
        pdf.pdfplumber = self._plumber([_FakePlumberPage("")])  # no text -> OCR fallback

        def _boom(_page):
            raise RuntimeError("tesseract is not installed or it's not in your PATH")

        pdf._ocr_page = _boom
        with self.assertRaises(RuntimeError):
            pdf.read_slides("upload.pdf")
        self.assertTrue(self.doc.closed, "fitz document leaked when the OCR fallback raised")

    def test_closes_doc_and_still_parses_on_the_success_path(self):
        pdf.pdfplumber = self._plumber([_FakePlumberPage("Title\n- one\n- two")])
        slides = pdf.read_slides("upload.pdf")
        self.assertEqual([s["title"] for s in slides], ["Title"])
        self.assertEqual(slides[0]["bullets"], ["one", "two"])
        self.assertTrue(self.doc.closed, "fitz document left open after a clean read")


# ------------------------------------------------ FIX 1b: present_levels.ocr_pdf ---

@unittest.skipIf(present_levels is None,
                 "edu_media_core/present_levels.py is withheld from the public snapshot, so "
                 "ocr_pdf is not part of this artifact; the other tests in this file still run.")
class PresentLevelsHandleTests(unittest.TestCase):
    """ocr_pdf never closed its document — not on success, not on failure. Reached from
    the dashboard's present-levels upload."""

    def setUp(self):
        self._orig_fitz = sys.modules.get("fitz")
        self._orig_subprocess = present_levels.subprocess

    def tearDown(self):
        if self._orig_fitz is None:
            sys.modules.pop("fitz", None)
        else:
            sys.modules["fitz"] = self._orig_fitz
        present_levels.subprocess = self._orig_subprocess

    def _install(self, doc):
        # ocr_pdf does `import fitz` lazily inside the function, so the fake goes into
        # sys.modules rather than onto the module object.
        sys.modules["fitz"] = types.SimpleNamespace(open=lambda *_a, **_k: doc)
        # returncode and stderr are part of what subprocess.run always returns, and ocr_pdf
        # now reads them: a non-zero exit is how a missing tesseract announces itself under
        # shell=True, and ignoring it made "the tool is not installed" indistinguishable from
        # "these pages are blank". A fake that omits them is a fake of an API that does not
        # exist, so it would fail the caller for a reason the real world never produces.
        present_levels.subprocess = types.SimpleNamespace(
            run=lambda *_a, **_k: types.SimpleNamespace(
                stdout="OCR TEXT", stderr="", returncode=0))

    def test_every_page_failing_ocr_raises_instead_of_returning_silence(self):
        """A broken tesseract must not look like a blank document.

        Under shell=True a missing binary is not an exception: cmd.exe exits non-zero with
        the message on stderr and nothing on stdout. ocr_pdf used to append that empty stdout
        and report success, so the IEP workflow produced eight empty sections and the teacher
        got an empty review form with no error anywhere.
        """
        doc = _FakeDoc(pages=3)
        self._install(doc)
        present_levels.subprocess = types.SimpleNamespace(
            run=lambda *_a, **_k: types.SimpleNamespace(
                stdout="", stderr="'tesseract' is not recognized", returncode=1))
        with self.assertRaises(RuntimeError) as ctx:
            present_levels.ocr_pdf("upload.pdf")
        self.assertIn("tesseract", str(ctx.exception))
        self.assertTrue(doc.closed, "the document must still be closed on this path")

    def test_one_bad_page_still_returns_the_rest(self):
        """A single unreadable page is a bad page, not a broken tool: keep going."""
        doc = _FakeDoc(pages=2)
        self._install(doc)
        calls = {"n": 0}

        def run(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return types.SimpleNamespace(stdout="", stderr="bad page", returncode=1)
            return types.SimpleNamespace(stdout="OCR TEXT", stderr="", returncode=0)

        present_levels.subprocess = types.SimpleNamespace(run=run)
        self.assertEqual(present_levels.ocr_pdf("upload.pdf"), "\nOCR TEXT")

    def test_closes_doc_when_the_page_render_raises(self):
        doc = _FakeDoc(pages=1, render_boom=True)
        self._install(doc)
        with self.assertRaises(RuntimeError):
            present_levels.ocr_pdf("upload.pdf")
        self.assertTrue(doc.closed, "fitz document leaked when the page render raised")

    def test_closes_doc_on_the_success_path(self):
        doc = _FakeDoc(pages=2)
        self._install(doc)
        self.assertEqual(present_levels.ocr_pdf("upload.pdf"), "OCR TEXT\nOCR TEXT")
        self.assertTrue(doc.closed, "fitz document was never closed after a clean OCR run")


# ------------------------------------------------------- FIX 2: tts.get_tts ---

class TorchLoadRestoreTests(unittest.TestCase):
    """get_tts swaps torch.load for a weights_only=False wrapper. It must be restored even
    when the model load blows up, or the whole long-lived process (slide-audio,
    xtts_synth_cli) keeps weights_only=False for good."""

    def setUp(self):
        self._saved = {n: sys.modules.get(n) for n in ("torch", "TTS", "TTS.api")}
        tts._tts = None

    def tearDown(self):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        tts._tts = None

    def _install(self, tts_factory):
        """Install fake torch + TTS modules. Returns (torch, original_load, load_calls)."""
        load_calls = []

        def original_load(*_a, **kw):
            load_calls.append(kw)
            return "checkpoint"

        torch = types.SimpleNamespace(
            load=original_load,
            cuda=types.SimpleNamespace(is_available=lambda: False))
        api = types.ModuleType("TTS.api")
        api.TTS = tts_factory
        pkg = types.ModuleType("TTS")
        pkg.api = api
        sys.modules["torch"], sys.modules["TTS"], sys.modules["TTS.api"] = torch, pkg, api
        return torch, original_load, load_calls

    @staticmethod
    def _oom(_name):
        raise RuntimeError("CUDA out of memory")

    def test_restores_torch_load_when_the_model_fails_to_load(self):
        torch, original_load, load_calls = self._install(self._oom)

        with self.assertRaises(RuntimeError):
            tts.get_tts()

        self.assertIs(torch.load, original_load,
                      "torch.load left globally patched after a failed model load")
        self.assertIsNone(tts._tts, "a failed load must not cache a model")
        # The concrete harm: unrelated later loads silently get weights_only=False.
        torch.load("some_other_checkpoint.pth")
        self.assertEqual(load_calls[-1], {},
                         "torch.load still forces weights_only=False process-wide")

    def test_repeated_failures_do_not_nest_wrappers(self):
        torch, original_load, _ = self._install(self._oom)

        for _ in range(3):
            with self.assertRaises(RuntimeError):
                tts.get_tts()

        self.assertIs(torch.load, original_load,
                      "each failed get_tts() captured the patched load and nested another wrapper")

    def test_success_path_still_restores_and_caches(self):
        loaded = object()
        seen = {"calls": 0}

        class _FakeTTS:
            def __init__(self, name):
                seen["name"] = name
                seen["calls"] += 1

            def to(self, device):
                seen["device"] = device
                return loaded

        torch, original_load, _ = self._install(_FakeTTS)

        self.assertIs(tts.get_tts(), loaded)
        self.assertIs(tts.get_tts(), loaded)  # cached in memory, not re-loaded
        self.assertEqual(seen["calls"], 1)
        self.assertEqual(seen["name"], tts.MODEL_NAME)
        self.assertEqual(seen["device"], "cpu")
        self.assertIs(torch.load, original_load)


# ------------------------------------------------------- FIX 3: cache.save ---

class CacheAtomicWriteTests(unittest.TestCase):
    """save() rewrites the WHOLE shared translation store on every miss. A kill or a full
    disk mid-write used to truncate translations.json in place, and load() reads a
    truncated file as no cache at all — silently discarding every cached translation."""

    def test_a_torn_write_leaves_the_previous_cache_intact(self):
        real_write_text = Path.write_text

        def truncating_write_text(self, data, encoding=None, errors=None, newline=None):
            """Half the bytes land, then the write dies — a container kill or a full disk."""
            real_write_text(self, data[:len(data) // 2], encoding=encoding)
            raise OSError(28, "No space left on device")

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "translations.json"
            cache.save(p, {"k1": {"text_es": "hola"}})
            self.assertEqual(cache.load(p), {"k1": {"text_es": "hola"}})

            Path.write_text = truncating_write_text
            try:
                with self.assertRaises(OSError):
                    cache.save(p, {"k1": {"text_es": "hola"},
                                   "k2": {"text_es": "adios"}})
            finally:
                Path.write_text = real_write_text

            # A temp file took the damage; the live store is still readable.
            self.assertEqual(cache.load(p), {"k1": {"text_es": "hola"}},
                             "a torn write destroyed the whole translation cache")

    def test_save_replaces_cleanly_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "translations.json"
            cache.save(p, {"k": 1})
            cache.save(p, {"k": 2})
            self.assertEqual(cache.load(p), {"k": 2})
            self.assertEqual(sorted(x.name for x in p.parent.iterdir()),
                             ["translations.json"])


if __name__ == "__main__":
    unittest.main()
