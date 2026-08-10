"""Benchmark engine tests: the retrieval metrics and the two-stage rerank scoring, driven by a
fake embedder + fake reranker (no ONNX graph, no broker) so the math is deterministic."""
import numpy as np

from ai_playground.bench import engine


class FakeEmb:
    """Returns a fixed vector per query text; provider decides the cpu-cost reporting path."""
    def __init__(self, qvecs, provider="onnx"):
        self.qvecs = qvecs
        self.provider = provider

    def embed(self, text, is_query=False):
        return np.array(self.qvecs[text], dtype=float)

    def embed_batch(self, texts, is_query=False):
        return [np.array(self.qvecs.get(t, [0, 0]), dtype=float) for t in texts]


class FakeReranker:
    def __init__(self, scores):
        self.scores = scores

    def score(self, query, docs):
        return [self.scores[d] for d in docs]


def test_score_core_perfect_retrieval():
    texts, sources = ["da", "db"], ["A.md", "B.md"]
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
    queries = [{"q": "qa", "targets": ["A.md"]}, {"q": "qb", "targets": ["B.md"]}]
    emb = FakeEmb({"qa": [1, 0], "qb": [0, 1]})
    m = engine._score_core(emb, texts, sources, matrix, 2, queries)
    assert m["R@1"] == 1.0 and m["R@3"] == 1.0 and m["MRR"] == 1.0
    assert m["sep"] > 0
    assert m["cpu_ms_per_query"] is not None      # onnx path reports CPU cost
    assert m["dim"] == 2 and m["n_docs"] == 2 and m["n_queries"] == 2


def test_score_core_records_miss():
    texts, sources = ["da", "db"], ["A.md", "B.md"]
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
    emb = FakeEmb({"qa": [0, 1]})                 # points at B — wrong
    m = engine._score_core(emb, texts, sources, matrix, 2, [{"q": "qa", "targets": ["A.md"]}])
    assert m["R@1"] == 0.0
    assert m["misses"] and m["misses"][0]["got"] == "B.md"


def test_score_core_broker_reports_no_cpu():
    emb = FakeEmb({"qa": [1, 0]}, provider="broker")
    m = engine._score_core(emb, ["d"], ["A"], np.array([[1.0, 0.0]]), 2,
                           [{"q": "qa", "targets": ["A"]}])
    assert m["cpu_ms_per_query"] is None and m["cores"] is None


def test_rerank_core_reorders_to_correct():
    # Cosine ranks B above A (wrong); the reranker scores A highest and fixes the top.
    texts, sources = ["a", "b", "c"], ["A", "B", "C"]
    matrix = np.array([[0.9, 0.0], [1.0, 0.0], [0.1, 0.0]])
    emb = FakeEmb({"q": [1, 0]})
    rr = FakeReranker({"a": 5.0, "b": 1.0, "c": 0.0})
    m = engine._rerank_core(emb, rr, texts, sources, matrix, 2, [{"q": "q", "targets": ["A"]}], depth=3)
    assert m["R@1"] == 1.0
    assert m["rerank_depth"] == 3
    assert m["cpu_ms_per_query"] is not None
    assert m["sep"] == 5.0 - 1.0                  # best target − best distractor (reranker scale)


def test_rerank_core_depth_limits_candidates():
    # depth=1 reranks only the embedder's top-1 (B), so A is never seen -> still a miss.
    texts, sources = ["a", "b", "c"], ["A", "B", "C"]
    matrix = np.array([[0.9, 0.0], [1.0, 0.0], [0.1, 0.0]])
    emb = FakeEmb({"q": [1, 0]})
    rr = FakeReranker({"a": 5.0, "b": 1.0, "c": 0.0})
    m = engine._rerank_core(emb, rr, texts, sources, matrix, 2, [{"q": "q", "targets": ["A"]}], depth=1)
    assert m["R@1"] == 0.0 and m["rerank_depth"] == 1


def test_config_label_variants():
    spec = {"label": "M", "native_dim": 768, "query_template": ""}
    assert engine.config_label(spec, "model", None) == "M"
    assert "@256d" in engine.config_label(spec, "model", 256)
    assert "no-prompt" in engine.config_label(spec, "none", None)
