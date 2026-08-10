"""Tests for the ModelManager and staged job runner (GPU-free, stub handles)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_media_core.models import ModelManager, ModelValidationError, StubHandle
from edu_media_core import jobs
from edu_media_core.jobs import Step, JobContext, JobFailed, run_workflow


def _mgr():
    events = []
    m = ModelManager(
        {"qwen": StubHandle("qwen"), "xtts": StubHandle("xtts")},
        emit=lambda status, name, msg="": events.append((status, name)),
    )
    return m, events


class ModelManagerTests(unittest.TestCase):
    def test_ensure_loads_once_and_is_idempotent(self):
        m, events = _mgr()
        m.ensure("qwen")
        m.ensure("qwen")  # no reload
        self.assertEqual(m.current, "qwen")
        self.assertEqual(m._handles["qwen"].loads, 1)
        self.assertIn(("loaded", "qwen"), events)
        self.assertIn(("ready", "qwen"), events)  # second call = already loaded

    def test_ensure_swaps_models(self):
        m, events = _mgr()
        m.ensure("qwen")
        m.ensure("xtts")
        self.assertEqual(m.current, "xtts")
        self.assertEqual(m._handles["qwen"].unloads, 1)
        self.assertEqual(m._handles["xtts"].loads, 1)
        # order: qwen unloading happens before xtts loading
        self.assertLess(events.index(("unloading", "qwen")), events.index(("loading", "xtts")))

    def test_validate(self):
        m, _ = _mgr()
        m.ensure("qwen")
        m.validate("qwen")  # ok
        with self.assertRaises(ModelValidationError):
            m.validate("xtts")

    def test_unknown_model(self):
        m, _ = _mgr()
        with self.assertRaises(KeyError):
            m.ensure("nope")

    def test_unload_all(self):
        m, _ = _mgr()
        m.ensure("qwen")
        m.unload_all()
        self.assertIsNone(m.current)
        self.assertEqual(m._handles["qwen"].unloads, 1)


class RunWorkflowTests(unittest.TestCase):
    def _ctx(self):
        events = []
        return JobContext("job1", emit=events.append), events

    def test_happy_path_runs_stages_and_ensures_models(self):
        m, _ = _mgr()
        ctx, events = self._ctx()
        order = []
        steps = [
            Step("extract", "Extract", lambda c: order.append("extract")),
            Step("translate", "Translate", lambda c: order.append("translate"), required_model="qwen"),
        ]
        run_workflow(steps, ctx, m)
        self.assertEqual(order, ["extract", "translate"])
        self.assertEqual([s.status for s in ctx.stages], ["done", "done"])
        kinds = [e.kind for e in events]
        self.assertEqual(kinds[0], "job_started")
        self.assertEqual(kinds[-1], "job_finished")
        self.assertIn("model", kinds)  # qwen load emitted into the stream
        self.assertEqual(m.current, "qwen")
        # both stages timed
        self.assertTrue(all(s.elapsed is not None for s in ctx.stages))

    def test_emit_finished_false_suppresses_event(self):
        # A caller with post-step work (the dashboard runner bundles output) suppresses the
        # premature job_finished and emits it itself only once the job is truly done.
        m, _ = _mgr()
        ctx, events = self._ctx()
        steps = [Step("only", "Only", lambda c: None)]
        run_workflow(steps, ctx, m, emit_finished=False)
        self.assertEqual([s.status for s in ctx.stages], ["done"])  # steps still ran
        self.assertNotIn("job_finished", [e.kind for e in events])

    def test_failure_aborts_and_unloads(self):
        m, _ = _mgr()
        ctx, events = self._ctx()

        def boom(c):
            raise ValueError("kaboom")

        steps = [
            Step("ok", "OK", lambda c: None, required_model="qwen"),
            Step("bad", "Bad", boom, required_model="xtts"),
            Step("never", "Never", lambda c: (_ for _ in ()).throw(AssertionError("ran after failure"))),
        ]
        with self.assertRaises(JobFailed):
            run_workflow(steps, ctx, m)
        self.assertEqual([s.status for s in ctx.stages], ["done", "failed"])  # 3rd never started
        kinds = [e.kind for e in events]
        self.assertIn("job_failed", kinds)
        self.assertNotIn("job_finished", kinds)
        self.assertIsNone(m.current)  # unloaded on failure

    def test_wrong_model_resident_is_caught(self):
        # A manager whose ensure() silently no-ops would fail validate — prove validate guards.
        m, _ = _mgr()
        ctx, _ = self._ctx()
        # Pre-load the wrong model, then a step requires a different one; ensure() should swap it.
        m.ensure("xtts")
        steps = [Step("t", "Translate", lambda c: None, required_model="qwen")]
        run_workflow(steps, ctx, m)
        self.assertEqual(m.current, "qwen")  # ensure swapped xtts -> qwen, validate passed


if __name__ == "__main__":
    unittest.main()
