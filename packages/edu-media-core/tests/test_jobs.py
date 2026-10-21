"""Tests for the staged job runner (GPU-free: no models, no broker, no GPU).

The ModelManager half of this file went with the manager itself (V-20). What is left
is the pipeline mechanics: order, timing, the event stream, and abort-on-failure.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_media_core.jobs import Step, StageResult, JobContext, JobFailed, run_workflow


class RunWorkflowTests(unittest.TestCase):
    def _ctx(self):
        events = []
        return JobContext("job1", emit=events.append), events

    def test_happy_path_runs_stages_in_order(self):
        ctx, events = self._ctx()
        order = []
        steps = [
            Step("extract", "Extract", lambda c: order.append("extract")),
            Step("translate", "Translate", lambda c: order.append("translate")),
        ]
        run_workflow(steps, ctx)
        self.assertEqual(order, ["extract", "translate"])
        self.assertEqual([s.status for s in ctx.stages], ["done", "done"])
        kinds = [e.kind for e in events]
        self.assertEqual(kinds[0], "job_started")
        self.assertEqual(kinds[-1], "job_finished")
        # both stages timed
        self.assertTrue(all(s.elapsed is not None for s in ctx.stages))

    def test_emit_finished_false_suppresses_event(self):
        # A caller with post-step work (the dashboard runner bundles output) suppresses the
        # premature job_finished and emits it itself only once the job is truly done.
        ctx, events = self._ctx()
        steps = [Step("only", "Only", lambda c: None)]
        run_workflow(steps, ctx, emit_finished=False)
        self.assertEqual([s.status for s in ctx.stages], ["done"])  # steps still ran
        self.assertNotIn("job_finished", [e.kind for e in events])

    def test_failure_aborts_the_rest_of_the_pipeline(self):
        ctx, events = self._ctx()

        def boom(c):
            raise ValueError("kaboom")

        steps = [
            Step("ok", "OK", lambda c: None),
            Step("bad", "Bad", boom),
            Step("never", "Never", lambda c: (_ for _ in ()).throw(AssertionError("ran after failure"))),
        ]
        with self.assertRaises(JobFailed):
            run_workflow(steps, ctx)
        self.assertEqual([s.status for s in ctx.stages], ["done", "failed"])  # 3rd never started
        kinds = [e.kind for e in events]
        self.assertIn("job_failed", kinds)
        self.assertNotIn("job_finished", kinds)

    def test_manager_argument_is_rejected_rather_than_swallowed(self):
        # run_workflow's third parameter used to be a ModelManager. emit_finished is
        # keyword-only so a stale positional caller fails loudly instead of binding its
        # manager to emit_finished (truthy) and appearing to work.
        ctx, _ = self._ctx()
        with self.assertRaises(TypeError):
            run_workflow([Step("only", "Only", lambda c: None)], ctx, object())

    def test_stage_dict_keeps_required_model_key(self):
        # WIRE CONTRACT. StageResult.required_model outlived Step.required_model because
        # the dashboard runner writes [s.to_dict() for s in ctx.stages] into every job's
        # job.json, so bundles already on disk carry the key. It is always None now; it
        # must still be emitted, or reading an old bundle beside a new one sees two shapes.
        ctx, _ = self._ctx()
        run_workflow([Step("only", "Only", lambda c: None)], ctx)
        d = ctx.stages[0].to_dict()
        self.assertIn("required_model", d)
        self.assertIsNone(d["required_model"])

    def test_stage_result_required_model_still_accepts_a_value(self):
        # Reading an old bundle back into a StageResult must not throw.
        sr = StageResult("s", "S", required_model="qwen")
        self.assertEqual(sr.to_dict()["required_model"], "qwen")


if __name__ == "__main__":
    unittest.main()
