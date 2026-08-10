"""Embedder providers — one ``embed(text, is_query)`` interface over two substrates.

A provider turns text into a unit-length vector. Two implementations:

* :class:`BrokerEmbedder` calls the platform broker's ``/v1/embed`` (Ollama-served, on the
  GPU). Batch-friendly (one broker round-trip embeds a whole corpus).
* :class:`OnnxEmbedder` runs an ONNX graph on the CPU via onnxruntime, tokenizing with the
  model's own ``tokenizer.json``. This reproduces the "beside-the-exe" path (int8 ONNX, no
  server) that recall.py and desktopPet ship.

Both share two knobs that make tonight's tests reproducible:

* **prompting** — ``none`` (embed raw text), ``bge-query`` (the BGE retrieval instruction on
  queries only), or ``model`` (the model's declared query/doc templates, e.g. EmbeddingGemma's
  ``task: search result | query: …`` or arctic's query prefix).
* **dim** — Matryoshka truncation: keep the first ``dim`` components and renormalise.
"""
from __future__ import annotations

import os

import numpy as np

from ai_playground import broker

# The classic BGE v1.5 retrieval instruction (applied to the query side only).
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def l2(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def apply_prompt(text: str, is_query: bool, prompting: str, spec: dict) -> str:
    """Wrap ``text`` per the prompting mode and the model's declared templates."""
    if prompting == "none":
        return text
    if prompting == "bge-query":
        return (BGE_QUERY_PREFIX + text) if is_query else text
    # prompting == "model": use the spec's own templates / prefixes (empty ⇒ raw text)
    tmpl = spec.get("query_template") if is_query else spec.get("doc_template")
    if tmpl:
        return tmpl.replace("{text}", text)
    prefix = spec.get("query_prefix", "") if is_query else spec.get("doc_prefix", "")
    return (prefix or "") + text


def _truncate(v: np.ndarray, dim: int | None) -> np.ndarray:
    return v[:dim] if dim else v


class BrokerEmbedder:
    """Embeds through the platform broker (any Ollama-served embedding model)."""

    provider = "broker"

    def __init__(self, spec: dict, prompting: str = "model", dim: int | None = None):
        self.spec = spec
        self.prompting = prompting
        self.dim = dim
        self.model = spec["broker_model"]

    def embed(self, text: str, is_query: bool = False) -> np.ndarray:
        return self.embed_batch([text], is_query)[0]

    def embed_batch(self, texts: list[str], is_query: bool = False) -> list[np.ndarray]:
        prompts = [apply_prompt(t, is_query, self.prompting, self.spec) for t in texts]
        vecs = broker.embed(prompts, model=self.model)  # one round-trip for the batch
        return [l2(_truncate(np.asarray(v, dtype=np.float64), self.dim)) for v in vecs]


class OnnxEmbedder:
    """Embeds on the CPU with onnxruntime + the model's own tokenizer.json.

    Handles the three pooling shapes we meet in practice:
      * ``graph``  — the export bakes pooling + any dense head into a ``sentence_embedding``
                     output (EmbeddingGemma, arctic, Xenova BGE). Preferred when present.
      * ``cls``    — take the [CLS] token of the last hidden state (classic BGE recipe).
      * ``mean``   — attention-masked mean of the last hidden state.

    ``pad_len`` pads/truncates every input to a fixed length. EmbeddingGemma's quantized
    RotaryEmbedding op cannot grow its cos/sin cache mid-session, so it needs a fixed length;
    masked pad tokens don't change the pooled vector.
    """

    provider = "onnx"

    def __init__(self, spec: dict, model_dir: str, prompting: str = "model",
                 dim: int | None = None):
        import onnxruntime as ort  # local import: only the onnx path needs it
        from tokenizers import Tokenizer

        self.spec = spec
        self.prompting = prompting
        self.dim = dim
        self.pool = spec.get("pooling", "graph")
        self.pad_len = spec.get("pad_len")
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.sess = ort.InferenceSession(
            os.path.join(model_dir, spec["onnx_file"]), providers=["CPUExecutionProvider"])
        self.in_names = [i.name for i in self.sess.get_inputs()]
        self.out_names = [o.name for o in self.sess.get_outputs()]

    def _forward(self, text: str) -> np.ndarray:
        ids = self.tok.encode(text or " ").ids
        if self.pad_len:
            ids = ids[:self.pad_len]
            mask = [1] * len(ids)
            pad = self.pad_len - len(ids)
            ids += [0] * pad
            mask += [0] * pad
        else:
            mask = [1] * len(ids)
        arr = np.array([ids], dtype=np.int64)
        marr = np.array([mask], dtype=np.int64)
        feeds: dict = {}
        for name in self.in_names:
            low = name.lower()
            feeds[name] = (marr if "mask" in low
                           else np.zeros_like(arr) if "type" in low
                           else arr)
        if self.pool == "graph" and "sentence_embedding" in self.out_names:
            out = self.sess.run(["sentence_embedding"], feeds)[0][0]
            return np.asarray(out, dtype=np.float64)
        outs = self.sess.run(None, feeds)
        tok = next((o for o in outs if o.ndim == 3), outs[0])
        if self.pool == "mean":
            m = np.asarray(mask, dtype=np.float64)[:, None]
            v = (tok[0] * m).sum(axis=0) / max(float(m.sum()), 1.0)
        else:  # cls
            v = tok[0, 0, :] if tok.ndim == 3 else tok[0, :]
        return np.asarray(v, dtype=np.float64)

    def embed(self, text: str, is_query: bool = False) -> np.ndarray:
        s = apply_prompt(text, is_query, self.prompting, self.spec)
        return l2(_truncate(self._forward(s), self.dim))

    def embed_batch(self, texts: list[str], is_query: bool = False) -> list[np.ndarray]:
        return [self.embed(t, is_query) for t in texts]


class RerankOnnx:
    """A cross-encoder reranker on the CPU (onnxruntime + the model's own tokenizer.json).

    A reranker is *not* an embedder: it reads the query and a candidate document **together** and
    emits a single relevance logit, so it can't precompute doc vectors. It's used as a second
    stage over a short candidate list (embed → take top-N by cosine → rerank those N). Xenova's
    int8 BGE / MS-MARCO exports are BERT-style graphs whose first output logit is the relevance
    score (higher = more relevant); we tokenise each ``(query, doc)`` as a sentence pair so the
    tokenizer.json's own template inserts the ``[SEP]`` and the token-type ids.
    """

    provider = "rerank-onnx"

    def __init__(self, spec: dict, model_dir: str):
        import onnxruntime as ort  # local import: only the onnx path needs it
        from tokenizers import Tokenizer

        self.spec = spec
        self.max_len = int(spec.get("max_len") or 512)
        self.tok = Tokenizer.from_file(os.path.join(model_dir, "tokenizer.json"))
        self.sess = ort.InferenceSession(
            os.path.join(model_dir, spec["onnx_file"]), providers=["CPUExecutionProvider"])
        self.in_names = [i.name for i in self.sess.get_inputs()]

    def score(self, query: str, docs: list[str]) -> list[float]:
        """Relevance score for each doc against the query (one forward pass per pair)."""
        out: list[float] = []
        for doc in docs:
            enc = self.tok.encode(query or " ", doc or " ")  # pair -> [CLS] q [SEP] doc [SEP]
            ids = enc.ids[:self.max_len]
            mask = enc.attention_mask[:self.max_len]
            types = (enc.type_ids or [0] * len(ids))[:self.max_len]
            arr = np.array([ids], dtype=np.int64)
            feeds: dict = {}
            for name in self.in_names:
                low = name.lower()
                feeds[name] = (np.array([mask], dtype=np.int64) if "mask" in low
                               else np.array([types], dtype=np.int64) if "type" in low
                               else arr)
            logits = np.asarray(self.sess.run(None, feeds)[0], dtype=np.float64).reshape(-1)
            out.append(float(logits[0]))
        return out
