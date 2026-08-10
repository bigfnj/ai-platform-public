"""IEP Present-Levels workflow — GPU-free unit tests.

Covers the OCR-independent split logic (ocr_pdf patched with canned text), the
extract-vs-generate step branching, and the generate step with a fake broker."""
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from edu_media_core import present_levels
from edu_media_core.jobs import JobContext
from dashboard import workflows
from dashboard.workflows import iep_present_levels as iep

CANNED_OCR = """EL DORADO COUNTY CHARTER SELPA
PRESENT LEVELS OF ACADEMIC ACHIEVEMENT AND FUNCTIONAL PERFORMANCE
Student Name: Doe, Jane Birthdate: 1/2/2011 IEP Date: 5/1/2026
Strengths/Preferences/Interests
Jane is kind and curious. She loves art.
Parent input and concerns relevant to educational progress
Parent wants reading growth.
Smarter Balanced Assessment Consortium (SBAC)
English/Language Arts
Not Applicable
Preacademic/Academic/Functional Skills
Reads at grade 2; iReady math grade 1.
Communication Development
Within normal limits.
Gross/Fine Motor Development
Age appropriate. Not a concern.
Social Emotional/Behavioral
Anxious at times.
Vocational
Attends consistently.
Adaptive/Daily Living Skills
Independent. Not a concern.
Health
No concerns.
Does this student have an Individual Health Plan? Yes No
For student to receive educational benefit, goals will be written to address the following areas of need:
Reading, Math
Page ___ of ___
"""


def _ctx(state: dict) -> JobContext:
    return JobContext("t", emit=lambda e: None, state=state)


class RegistrationTests(unittest.TestCase):
    def test_registered(self):
        self.assertIn("iep_present_levels", {w.key for w in workflows.all_workflows()})


class ExtractSplitTests(unittest.TestCase):
    def test_split_and_header(self):
        with mock.patch.object(present_levels, "ocr_pdf", return_value=CANNED_OCR):
            data = present_levels.extract("x.pdf")
        self.assertEqual(data["header"]["student_name"], "Doe, Jane")
        self.assertEqual(data["header"]["birthdate"], "1/2/2011")
        self.assertEqual(data["header"]["iep_date"], "5/1/2026")
        for k in present_levels.SECTION_KEYS:
            self.assertTrue(data["sections"][k].strip(), f"section {k} empty")
        self.assertIn("art", data["sections"]["strengths_preferences_interests"])
        # heading fragment must not bleed into the parent section body
        self.assertNotIn("relevant to educational progress",
                         data["sections"]["parent_input_concerns"])
        # SBAC assessment block is separated out, not in an 8-section field
        self.assertNotIn("Smarter Balanced",
                         data["sections"]["preacademic_academic_functional"])
        self.assertIn("Reading, Math", data["areas_of_need"])
        self.assertEqual(data["warnings"], [])


class BuildStepTests(unittest.TestCase):
    def test_extract_path_when_pdf_uploaded(self):
        steps = iep._build_steps(_ctx({"input_files": [Path("a.pdf")]}))
        self.assertEqual([s.key for s in steps], ["extract"])

    def test_generate_path_when_filled_provided(self):
        steps = iep._build_steps(_ctx({"input_files": [Path("filled.json")]}))
        self.assertEqual([s.key for s in steps], ["generate"])


class GenerateTests(unittest.TestCase):
    def test_generate_writes_artifact_and_builds_message(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "input").mkdir(); (d / "output").mkdir()
            filled = {"name": "Jane", "header": {"student_name": "Doe, Jane",
                                                 "iep_date": "5/1/2026"},
                      "sections": {k: {"current": f"cur-{k}", "input": f"inp-{k}"}
                                   for k, _ in iep.SECTIONS}}
            (d / "input" / "filled.json").write_text(json.dumps(filled))
            fake = {k: f"elaborated {k}" for k in iep._OUT_KEYS}
            ctx = _ctx({"input_files": [d / "input" / "filled.json"],
                        "output_dir": d / "output", "name": "Jane"})
            ctx.stages.append(types.SimpleNamespace(message=""))
            with mock.patch.object(iep.broker_media, "chat_json", return_value=fake) as m:
                iep._generate(ctx)
            html = (d / "output" / "index.html").read_text(encoding="utf-8")
            self.assertIn("elaborated strengths_preferences_interests", html)
            self.assertIn("Areas of Need", html)
            self.assertIn("Doe, Jane", html)
            final = json.loads((d / "output" / "present_levels_final.json").read_text(encoding="utf-8"))
            self.assertEqual(final["sections"]["vocational"], "elaborated vocational")
            # the model was asked with CURRENT + TEACHER INPUT per section
            _, user = m.call_args.args
            self.assertIn("CURRENT: cur-vocational", user)
            self.assertIn("TEACHER INPUT: inp-strengths_preferences_interests", user)
            self.assertEqual(m.call_args.kwargs["model"], iep.IEP_LLM_MODEL)

    def test_generate_coerces_list_areas_of_need_to_text(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "input").mkdir(); (d / "output").mkdir()
            filled = {"name": "Jane", "header": {},
                      "sections": {k: {"current": "c", "input": "i"} for k, _ in iep.SECTIONS}}
            (d / "input" / "filled.json").write_text(json.dumps(filled))
            # model returns areas_of_need as a JSON array (the real-world case we saw)
            fake = {k: f"elaborated {k}" for k, _ in iep.SECTIONS}
            fake["areas_of_need"] = ["Reading fluency", "Math operations", "Fine motor"]
            ctx = _ctx({"input_files": [d / "input" / "filled.json"],
                        "output_dir": d / "output", "name": "Jane"})
            ctx.stages.append(types.SimpleNamespace(message=""))
            with mock.patch.object(iep.broker_media, "chat_json", return_value=fake):
                iep._generate(ctx)
            final = json.loads((d / "output" / "present_levels_final.json").read_text(encoding="utf-8"))
            aon = final["sections"]["areas_of_need"]
            self.assertNotIn("[", aon)  # no python-list repr
            self.assertEqual(aon, "Reading fluency\nMath operations\nFine motor")


class EndpointTests(unittest.TestCase):
    """Single-page IEP flow endpoints. Points the library/DB at a temp dir and imports
    the FastAPI app AFTER setting env so the module-level store + IEP_ONLY pick it up.
    The queue's lifespan worker is never started (we call the endpoint functions directly),
    so no job actually runs — we only assert the job/artifact plumbing the flow relies on."""

    @classmethod
    def setUpClass(cls):
        # ignore_cleanup_errors: the module-level Store keeps the SQLite connection open,
        # so Windows can't unlink library.db during teardown — harmless, don't fail on it.
        cls._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        lib = Path(cls._tmp.name) / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        os.environ["EDU_LIBRARY_DIR"] = str(lib)
        os.environ["EDU_DB_PATH"] = str(lib / "library.db")
        os.environ["IEP_ONLY"] = "1"
        from dashboard import app as appmod  # imported here so env is already set
        cls.app = appmod

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _filled(self):
        return {"name": "Jane", "header": {"student_name": "Doe, Jane", "iep_date": "5/1/2026"},
                "sections": {k: {"current": f"c-{k}", "input": f"i-{k}"} for k, _ in iep.SECTIONS}}

    def test_generate_creates_job_and_writes_filled(self):
        res = self.app.api_iep_generate({"filled": self._filled(), "name": "Jane"}, self.app.Identity("tester", True))
        self.assertIn("id", res)
        job = self.app.store.get_job(res["id"])
        self.assertIsNotNone(job)
        self.assertEqual(job["workflow"], "iep_present_levels")
        fp = Path(job["dir"]) / "input" / "filled.json"
        self.assertTrue(fp.exists(), "filled.json not written")
        written = json.loads(fp.read_text(encoding="utf-8"))
        self.assertEqual(written["sections"]["vocational"]["input"], "i-vocational")

    def test_generate_rejects_payload_without_sections(self):
        res = self.app.api_iep_generate({"filled": {"name": "x"}}, self.app.Identity("tester", True))
        self.assertEqual(getattr(res, "status_code", None), 400)

    def test_present_levels_final_roundtrip(self):
        jid = self.app.api_iep_generate({"filled": self._filled(), "name": "Jane"}, self.app.Identity("tester", True))["id"]
        out = Path(self.app.store.get_job(jid)["dir"]) / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "present_levels_final.json").write_text(
            json.dumps({"name": "Jane", "header": {}, "sections": {"vocational": "elaborated"}}),
            encoding="utf-8")
        got = self.app.api_present_levels_final(jid, self.app.Identity("tester", True))
        body = json.loads(bytes(got.body))
        self.assertEqual(body["sections"]["vocational"], "elaborated")

    def test_present_levels_final_404_before_generation(self):
        jid = self.app.api_iep_generate({"filled": self._filled(), "name": "Jane"}, self.app.Identity("tester", True))["id"]
        got = self.app.api_present_levels_final(jid, self.app.Identity("tester", True))
        self.assertEqual(getattr(got, "status_code", None), 404)

    def test_retention_sweep_deletes_old_done_jobs_only(self):
        old = self.app.api_iep_generate({"filled": self._filled(), "name": "Old"}, self.app.Identity("tester", True))["id"]
        self.app.store.set_status(old, "done")
        past = time.time() - (self.app.IEP_RETENTION_DAYS + 1) * 86400
        with self.app.store._lock:  # backdate created_at beyond the retention window
            self.app.store._conn.execute("UPDATE jobs SET created_at=? WHERE id=?", (past, old))
            self.app.store._conn.commit()
        fresh = self.app.api_iep_generate({"filled": self._filled(), "name": "New"}, self.app.Identity("tester", True))["id"]
        self.app.store.set_status(fresh, "done")
        removed = self.app._expire_old_iep_jobs()
        self.assertGreaterEqual(removed, 1)
        self.assertIsNone(self.app.store.get_job(old), "old job should be swept")
        self.assertIsNotNone(self.app.store.get_job(fresh), "fresh job should remain")


if __name__ == "__main__":
    unittest.main()
