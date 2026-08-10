"""Dashboard tests (run under the workspace venv):

    uv run python -m unittest discover -s apps/dashboard/tests

Covers the store, the library layout/bundling, and a full GPU-free job run
through the real runner using the 'echo' workflow.
"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from dashboard.store import Store
        self.store = Store(Path(self.tmp.name) / "lib.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_crud_and_events(self):
        from edu_media_core.jobs import Event
        self.store.create_job("j1", "First", "echo", "/tmp/j1", {"a": 1})
        self.store.create_job("j2", "Second", "just_translate", "/tmp/j2")
        self.assertEqual(len(self.store.list_jobs()), 2)
        self.assertEqual(len(self.store.list_jobs(workflow="echo")), 1)
        self.assertEqual(len(self.store.list_jobs(query="Sec")), 1)
        self.assertEqual(self.store.next_queued()["id"], "j1")  # oldest first

        self.store.add_event("j1", Event(kind="stage_started", ts=1.0, stage="x"))
        self.store.add_event("j1", Event(kind="stage_finished", ts=2.0, stage="x", status="done"))
        evs = self.store.get_events("j1")
        self.assertEqual(len(evs), 2)
        self.assertEqual(self.store.get_events("j1", after_id=evs[0]["id"])[0]["kind"], "stage_finished")

        self.store.set_status("j1", "done")
        self.assertEqual(self.store.get_job("j1")["status"], "done")
        self.store.rename_job("j1", "Renamed")
        self.assertEqual(self.store.get_job("j1")["name"], "Renamed")
        self.store.delete_job("j1")
        self.assertIsNone(self.store.get_job("j1"))
        self.assertEqual(self.store.get_events("j1"), [])


class LibraryTests(unittest.TestCase):
    def test_slug_dir_and_bundle(self):
        from dashboard import library
        self.assertEqual(library.slugify("Grade 7: Food & Water!"), "grade-7-food-water")
        with tempfile.TemporaryDirectory() as d:
            os.environ["EDU_LIBRARY_DIR"] = d
            jd = library.make_job_dir("echo", "My Job", "abcd1234")
            self.assertTrue((jd / "input").is_dir() and (jd / "output").is_dir())
            self.assertIn("__my-job__abcd1234", jd.name)
            (jd / "output" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
            (jd / "output" / "a.txt").write_text("x", encoding="utf-8")
            zp = library.bundle_zip(jd)
            self.assertTrue(zp.exists())
            self.assertEqual(set(zipfile.ZipFile(zp).namelist()), {"index.html", "a.txt"})


class EchoRunTests(unittest.TestCase):
    def test_execute_echo_job_end_to_end(self):
        import shutil
        from dashboard import library, runner
        from dashboard.store import Store
        d = tempfile.mkdtemp()
        os.environ["EDU_LIBRARY_DIR"] = d
        store = Store(Path(d) / "lib.db")
        try:
            job_id = "echotest"
            jd = library.make_job_dir("echo", "Echo Run", job_id)
            (jd / "input" / "a.txt").write_text("hello", encoding="utf-8")
            (jd / "input" / "b.txt").write_text("world", encoding="utf-8")
            store.create_job(job_id, "Echo Run", "echo", str(jd))
            runner.execute_job(store, store.get_job(job_id))
            self.assertEqual(store.get_job(job_id)["status"], "done")
            self.assertTrue((jd / "output.zip").exists())
            names = set(zipfile.ZipFile(jd / "output.zip").namelist())
            self.assertIn("a.txt", names)
            self.assertIn("b.txt", names)
            # the root HTML is renamed after the job (not "index.html")
            self.assertNotIn("index.html", names)
            self.assertTrue(any(n.endswith(".html") for n in names), names)
            kinds = [e["kind"] for e in store.get_events(job_id)]
            self.assertEqual(kinds[0], "job_started")
            self.assertEqual(kinds[-1], "job_finished")
            # job_finished is emitted only after the status flips + the bundle is packaged
            self.assertEqual(store.get_job(job_id)["status"], "done")
            msgs = " | ".join((e.get("message") or "") for e in store.get_events(job_id))
            self.assertIn("packaging", msgs)
        finally:
            store.close()
            shutil.rmtree(d, ignore_errors=True)


class UploadPathSafetyTests(unittest.TestCase):
    """safe_relpath must preserve folder structure but never escape input/."""

    def test_safe_relpath(self):
        import shutil
        # app has a module-level Store that stays open, so point it at a throwaway
        # dir and don't fail if the locked DB can't be removed on Windows.
        d = tempfile.mkdtemp()
        os.environ["EDU_LIBRARY_DIR"] = d
        try:
            from dashboard.app import safe_relpath
            self.assertEqual(safe_relpath("Great Expectations/Week 1/a.pdf").as_posix(),
                             "Great Expectations/Week 1/a.pdf")
            self.assertEqual(safe_relpath("../../etc/passwd").as_posix(), "etc/passwd")
            self.assertEqual(safe_relpath("C:/x/y.pdf").as_posix(), "x/y.pdf")
            self.assertEqual(safe_relpath("__MACOSX/x").as_posix(), "x")
            self.assertIsNone(safe_relpath(".DS_Store"))
            self.assertIsNone(safe_relpath(""))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ScopeBackstopTests(unittest.TestCase):
    """The deterministic guard forces known-unsupported asks out of the 'will do' side."""

    def _enforce(self):
        import shutil
        d = tempfile.mkdtemp()
        os.environ["EDU_LIBRARY_DIR"] = d
        os.environ["EDU_DB_PATH"] = str(Path(d) / "x.db")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        from dashboard.app import _enforce_scope
        return _enforce_scope

    def test_per_week_translation_is_refused(self):
        enforce = self._enforce()
        r = {"understanding": "I'll simplify activities and translate Week 1.",
             "applies": ["simpler activities", "translate only Week 1"], "ignored": [],
             "needs_clarification": False, "question": "OK?",
             "guidance": "prefer simpler activities, translate_weeks: [1]"}
        out = enforce("teachtown_builder", "make activities simpler, and only translate week 1", r)
        self.assertNotIn("translate_weeks", out["guidance"])
        self.assertIn("prefer simpler activities", out["guidance"])
        self.assertTrue(any("weeks" in i["reason"].lower() for i in out["ignored"]))
        self.assertNotIn("translate only Week 1", out["applies"])

    def test_font_change_is_refused(self):
        enforce = self._enforce()
        r = {"understanding": "I'll use a formal tone and make the font bigger.",
             "applies": ["formal tone", "bigger font"], "ignored": [],
             "needs_clarification": False, "question": "OK?",
             "guidance": "formality: usted; make the font bigger"}
        out = enforce("just_translate", "use usted and make the font bigger", r)
        self.assertNotIn("font", out["guidance"].lower())
        self.assertIn("usted", out["guidance"])
        self.assertTrue(out["ignored"])

    def test_legit_instruction_passes_through(self):
        enforce = self._enforce()
        r = {"understanding": "I'll keep it formal.", "applies": ["formal tone"], "ignored": [],
             "needs_clarification": False, "question": "OK?", "guidance": "formality: usted"}
        out = enforce("just_translate", "use a formal tone", r)
        self.assertEqual(out["guidance"], "formality: usted")
        self.assertEqual(out["ignored"], [])


class BuilderFolderTests(unittest.TestCase):
    """The Builder derives week/unit/worksheet-key from the uploaded folder tree."""

    def _builder(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "teachtown"))
        import builder
        return builder

    def test_week_and_worksheet_key(self):
        b = self._builder()
        root = Path("input")
        core = root / "Great Expectations" / "overview.pdf"
        w1 = root / "Great Expectations" / "Week 1" / "Reading.pdf"
        w2 = root / "Great Expectations" / "Week 2" / "Reading.pdf"
        w3 = root / "Great Expectations" / "Week 03" / "math.pdf"
        loose = root / "loose.pdf"
        self.assertEqual(b.week_of(core, root), 0)      # top-level core -> Overview (week 0)
        self.assertEqual(b.week_of(w1, root), 1)
        self.assertEqual(b.week_of(w3, root), 3)        # "Week 03" folder
        # same basename in different weeks -> distinct, collision-free keys
        self.assertEqual(b.rel_worksheet_name(w1, root), "Week 1/Reading.pdf")
        self.assertEqual(b.rel_worksheet_name(w2, root), "Week 2/Reading.pdf")
        self.assertEqual(b.rel_worksheet_name(loose, root), "loose.pdf")
        self.assertEqual(b.master_folder([core, w1], root), "Great Expectations")
        self.assertIsNone(b.master_folder([loose], root))

    def test_is_lesson_plan(self):
        b = self._builder()
        self.assertTrue(b.is_lesson_plan("ELA Lesson Plan.pdf"))
        self.assertTrue(b.is_lesson_plan("math lessonplan.pdf"))  # space-tolerant
        self.assertFalse(b.is_lesson_plan("Reading Comp.pdf"))
        self.assertFalse(b.is_lesson_plan("Math.pdf"))

    def _patch_builder(self, b, *, slides_text, chat, render=None):
        """Swap builder's PDF + model deps for fakes; returns a restore() callable."""
        import types

        orig_pdf, orig_tr = b.core_pdf, b.core_translate
        b.core_pdf = types.SimpleNamespace(
            read_slides=lambda path: [{"raw_text": slides_text}],
            render_page_b64=(render or (lambda *a, **k: "IMGB64")),
        )
        b.core_translate = types.SimpleNamespace(chat_json=chat)
        return lambda: setattr(b, "core_pdf", orig_pdf) or setattr(b, "core_translate", orig_tr)

    def test_draft_routes_vocab_and_infers_subject(self):
        """Lesson plans feed vocabulary (never a worksheet); worksheets take their
        subject from the model's content classification, not the filename."""
        b = self._builder()

        def chat(system, user, **kw):
            if user.startswith("Activities:"):
                return {"learn": "You practice new skills."}
            if "Lesson plan file:" in user:
                return {"vocab": [{"word": "nationalism", "def": "pride in your country"},
                                  {"word": "imperialism", "def": "taking control of areas"}]}
            return {"subject": "Science", "questions": 5,
                    "mission": {"title": "Quiz", "prompt": "Answer.",
                                "type": "choice", "options": ["a", "b"]}}

        restore = self._patch_builder(b, slides_text="Plenty of real words here so the text path is used today.", chat=chat)
        try:
            root = Path("input")
            files = [root / "U" / "Social Studies Lesson Plan.pdf",  # -> vocab (Social Studies)
                     root / "U" / "Reading Comp.pdf",                 # filename ELA, model says Science
                     root / "U" / "Math.pdf"]
            unit = b.draft_unit(files, "u", "U", input_root=root)
            self.assertEqual(len(unit["missions"]), 2)
            self.assertTrue(all("Lesson Plan" not in m[6] for m in unit["missions"]))
            self.assertTrue(all(m[1] == "Science" for m in unit["missions"]))  # model subject wins
            self.assertTrue(all(m[7] == 5 for m in unit["missions"]))  # model-provided answer count
            vocab = [v for wi in unit["weekInfo"].values() for v in wi["v"]]
            self.assertTrue(all(len(v) == 3 for v in vocab))
            self.assertTrue(any(v[0] == "nationalism" and v[2] == "Social Studies" for v in vocab))
        finally:
            restore()

    def test_image_worksheet_uses_vision(self):
        """A text-sparse worksheet is sent to the model as a rendered image."""
        b = self._builder()
        seen = {}

        def chat(system, user, **kw):
            seen["images"] = kw.get("images")
            return {"subject": "Social Studies", "questions": 3,
                    "mission": {"title": "WWI", "prompt": "Look.", "type": "choice", "options": ["a"]}}

        restore = self._patch_builder(b, slides_text="", chat=chat, render=lambda *a, **k: "PAGEB64")
        try:
            root = Path("input")
            unit = b.draft_unit([root / "U" / "World War Matching.pdf"], "u", "U", input_root=root)
            self.assertEqual(seen["images"], ["PAGEB64"])           # vision path taken
            self.assertEqual(unit["missions"][0][1], "Social Studies")
        finally:
            restore()

    def test_worksheet_never_dropped_on_failure(self):
        """If drafting fails entirely, the worksheet is still included as a picture
        worksheet (fallback subject + generic prompt) — never silently skipped."""
        b = self._builder()

        def chat(system, user, **kw):
            raise RuntimeError("model down")

        restore = self._patch_builder(b, slides_text="", chat=chat)
        try:
            root = Path("input")
            unit = b.draft_unit([root / "U" / "Mystery.pdf"], "u", "U", input_root=root)
            self.assertEqual(len(unit["missions"]), 1)
            m = unit["missions"][0]
            self.assertEqual(m[1], "ELA")                    # filename fallback subject
            self.assertEqual(m[3], "Complete the worksheet.")  # generic prompt
            self.assertTrue(m[6].endswith("Mystery.pdf"))
        finally:
            restore()

    def test_activity_extraction_and_validation(self):
        """Each interactive kind is extracted + validated into unit.activities; a
        malformed activity falls back (no entry -> annotate-on-image)."""
        b = self._builder()
        acts = {
            "match.pdf": {"kind": "match", "pairs": [{"left": "cycle", "right": "repeats"},
                                                     {"left": "recycle", "right": "reuse"}]},
            "drag.pdf": {"kind": "drag-drop", "items": ["A", "B"],
                         "targets": [{"label": "one", "answer": "A"}]},
            "hi.pdf": {"kind": "highlight", "questions": [{"prompt": "q", "options": ["x", "y"], "answer": "y"}]},
            "fill.pdf": {"kind": "fill-in", "questions": [{"prompt": "2+2", "answer": "4"}]},
            "bad.pdf": {"kind": "match", "pairs": [{"left": "lonely", "right": ""}]},  # invalid -> fallback
        }

        def chat(system, user, **kw):
            if "Lesson plan file:" in user:
                return {"vocab": []}
            for fn, act in acts.items():
                if fn in user:
                    return {"subject": "Science", "questions": 1,
                            "mission": {"title": fn, "prompt": "p"}, "activity": act}
            return {"subject": "Science", "mission": {"title": "x", "prompt": "p"}}

        restore = self._patch_builder(b, slides_text="enough real words here to use the text path today ok", chat=chat)
        try:
            root = Path("input")
            unit = b.draft_unit([root / "U" / fn for fn in acts], "u", "U", input_root=root)
            got = unit["activities"]
            self.assertEqual(got["u/match.pdf"]["kind"], "match")
            self.assertEqual(len(got["u/match.pdf"]["pairs"]), 2)
            self.assertEqual(got["u/drag.pdf"]["kind"], "drag-drop")
            self.assertEqual(got["u/hi.pdf"]["kind"], "highlight")
            self.assertEqual(got["u/fill.pdf"]["kind"], "fill-in")
            self.assertNotIn("u/bad.pdf", got)  # malformed -> fallback, no activity entry
            self.assertEqual(len(unit["missions"]), 5)  # but every worksheet still appears
        finally:
            restore()


class RecursiveInputTests(unittest.TestCase):
    """Runner must see files inside uploaded subfolders (rglob, not glob)."""

    def test_nested_input_is_processed(self):
        import shutil
        from dashboard import library, runner
        from dashboard.store import Store
        d = tempfile.mkdtemp()
        os.environ["EDU_LIBRARY_DIR"] = d
        store = Store(Path(d) / "lib.db")
        try:
            jid = "nested"
            jd = library.make_job_dir("echo", "Nested", jid)
            (jd / "input" / "top.txt").write_text("t", encoding="utf-8")
            wk = jd / "input" / "Unit" / "Week 1"
            wk.mkdir(parents=True)
            (wk / "deep.txt").write_text("d", encoding="utf-8")
            store.create_job(jid, "Nested", "echo", str(jd))
            runner.execute_job(store, store.get_job(jid))
            self.assertEqual(store.get_job(jid)["status"], "done")
            names = set(zipfile.ZipFile(jd / "output.zip").namelist())
            self.assertIn("deep.txt", names)  # nested file was picked up
            self.assertIn("top.txt", names)
        finally:
            store.close()
            shutil.rmtree(d, ignore_errors=True)


class JustTranslateReadAlongTests(unittest.TestCase):
    """Sentence splitting + WAV concatenation offsets that drive the read-along highlight."""

    def test_sentences_split(self):
        from dashboard.workflows import just_translate as jt
        self.assertEqual(
            jt._sentences("Hello there. How are you?  I am fine!"),
            ["Hello there.", "How are you?", "I am fine!"])
        self.assertEqual(jt._sentences("   "), [])
        self.assertEqual(jt._sentences("no end punctuation"), ["no end punctuation"])

    def test_concat_wavs_offsets(self):
        import shutil
        import wave
        from dashboard.workflows import just_translate as jt
        d = tempfile.mkdtemp()
        try:
            rate = 8000

            def mk(path, secs):
                with wave.open(str(path), "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
                    w.writeframes(b"\x00\x00" * int(rate * secs))

            p1, p2 = Path(d) / "a.wav", Path(d) / "b.wav"
            mk(p1, 1.0); mk(p2, 0.5)
            out = Path(d) / "track.wav"
            segs = jt._concat_wavs([p1, p2], out, gap_s=0.25)
            self.assertEqual(segs[0], (0.0, 1.0))          # first clip
            self.assertEqual(segs[1], (1.25, 1.75))        # after 1.0 + 0.25s gap
            with wave.open(str(out), "rb") as r:
                self.assertAlmostEqual(r.getnframes() / rate, 1.75, places=2)  # 1.0 + gap + 0.5
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
