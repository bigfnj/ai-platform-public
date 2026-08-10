"""Model classification tests — the basis of the VRAM policy."""

from app.registry import classify, is_embedding_model

HINTS = ["embed", "bge", "nomic-embed", "mxbai", "gte", "e5", "minilm"]


def test_embedders_are_light():
    assert is_embedding_model("bge-m3:latest", HINTS)
    assert is_embedding_model("nomic-embed-text", HINTS)
    assert classify("bge-m3:latest", HINTS) == "embed"


def test_generative_models_are_heavy():
    assert not is_embedding_model("llama3.1:8b", HINTS)
    assert classify("llama3.1:8b", HINTS) == "heavy"
    assert classify("qwen2.5:32b-instruct-q3_K_M", HINTS) == "heavy"
    assert classify("mistral-small3.1:24b", HINTS) == "heavy"
