"""Vision eval harness — the broker-free scoring logic (no GPU)."""
from __future__ import annotations

from pathlib import Path

from bouquet import eval_vision as ev


def test_load_reference_samples_one_per_flower():
    samples = ev.load_reference_samples(per_flower=1)
    assert len(samples) == 50                       # one photo per KB flower
    assert all(s.path.is_file() for s in samples)
    assert "rose" in {s.expected for s in samples}


def test_evaluate_scores_hits_extras_and_confusion():
    samples = [
        ev.Sample(expected="rose", path=Path("a.jpg")),
        ev.Sample(expected="tulip", path=Path("b.jpg")),
    ]

    # A fake vision model that always says "garden rose" (-> resolves to rose).
    def fake_identify(_path: Path) -> dict:
        return {"flowers": [{"name": "garden rose", "colors": ["red"]}]}

    results = ev.evaluate(samples, identify_fn=fake_identify)
    summary = ev.summarize(results)

    assert summary["n"] == 2
    assert summary["hits"] == 1 and summary["recall"] == 0.5      # rose hit, tulip missed
    assert summary["mean_extras"] == 0.5                          # tulip got a stray "rose"
    assert summary["confusion"]["tulip"]["rose"] == 1            # tulip -> called rose


def test_evaluate_handles_nothing_identified():
    samples = [ev.Sample(expected="rose", path=Path("a.jpg"))]
    results = ev.evaluate(samples, identify_fn=lambda _p: {"flowers": []})
    summary = ev.summarize(results)
    assert summary["recall"] == 0.0
    assert summary["confusion"]["rose"]["(nothing)"] == 1


def test_evaluate_credits_greenery():
    # Foliage is reported in the separate greenery list, not flowers — it must still count.
    samples = [ev.Sample(expected="eucalyptus", path=Path("e.jpg"))]
    results = ev.evaluate(samples, identify_fn=lambda _p: {
        "flowers": [], "greenery": ["silver dollar eucalyptus"]})
    assert results[0].hit


def test_evaluate_records_errors_without_aborting():
    samples = [ev.Sample("rose", Path("a")), ev.Sample("tulip", Path("b"))]

    def flaky(path: Path) -> dict:
        if path.name == "b":
            raise RuntimeError("boom")
        return {"flowers": [{"name": "garden rose"}]}

    results = ev.evaluate(samples, identify_fn=flaky)
    summary = ev.summarize(results)
    assert results[1].error == "boom"
    assert summary["n"] == 2 and summary["errors"] == 1
    assert summary["hits"] == 1 and summary["recall"] == 1.0   # 1 hit / 1 scored sample


def test_score_corrections_precision_recall_f1():
    S = ev.CorrectionSample
    samples = [
        S(truth={"rose", "tulip"}, predicted={"rose"}),        # tulip missed (fn)
        S(truth={"rose"}, predicted={"rose", "aster"}),        # aster hallucinated (fp)
    ]
    r = ev.score_corrections(samples)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 1, 1)
    assert r["precision"] == round(2 / 3, 3) and r["recall"] == round(2 / 3, 3)
    assert r["f1"] == round(2 / 3, 3)


def test_load_correction_samples_reads_db(tmp_path, monkeypatch):
    from bouquet import config as cfg
    from bouquet import db
    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "b.db"))
    db.init()
    db.insert(mode="florist", title="t", image_file="x.jpg", model="m",
              inventory={"flowers": [{"name": "rose"}, {"name": "tulip"}]},
              matched=["rose", "tulip"], unprofiled=[], report_md="r",
              vision_draft={"flowers": [{"name": "garden rose"}, {"name": "aster"}]})
    samples = ev.load_correction_samples()
    assert len(samples) == 1
    assert samples[0].truth == {"rose", "tulip"}               # corrected inventory
    assert samples[0].predicted == {"rose", "aster"}           # draft (garden rose -> rose)
