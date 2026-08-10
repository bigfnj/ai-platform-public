"""Provider helper tests: prompt wrapping, L2 normalisation, Matryoshka truncation (pure, no I/O)."""
import numpy as np

from ai_playground.bench import providers as P


def test_apply_prompt_none_is_identity():
    assert P.apply_prompt("x", True, "none", {}) == "x"


def test_apply_prompt_bge_query_wraps_query_only():
    q = P.apply_prompt("cats", True, "bge-query", {})
    assert q.endswith("cats") and "searching relevant passages" in q
    assert P.apply_prompt("cats", False, "bge-query", {}) == "cats"  # doc side untouched


def test_apply_prompt_model_templates():
    spec = {"query_template": "q: {text}", "doc_template": "d: {text}"}
    assert P.apply_prompt("hi", True, "model", spec) == "q: hi"
    assert P.apply_prompt("hi", False, "model", spec) == "d: hi"


def test_apply_prompt_model_prefix_fallback():
    spec = {"query_prefix": "Q ", "doc_prefix": ""}
    assert P.apply_prompt("hi", True, "model", spec) == "Q hi"
    assert P.apply_prompt("hi", False, "model", spec) == "hi"


def test_l2_makes_unit_vector():
    v = P.l2([3, 4])
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-9


def test_l2_zero_vector_stays_zero():
    assert list(P.l2([0, 0])) == [0.0, 0.0]


def test_truncate_keeps_first_n():
    assert list(P._truncate(np.array([1, 2, 3, 4]), 2)) == [1, 2]


def test_truncate_none_keeps_all():
    assert list(P._truncate(np.array([1, 2]), None)) == [1, 2]
