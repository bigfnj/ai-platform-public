"""The benchmark engine: score (model, prompting, dim) run-configs over a corpus + query set.

A *run-config* is one column in the results table: a model id plus the prompting mode and the
Matryoshka dim to embed at. The engine re-embeds the whole corpus with each config (that's the
point — the corpus's stored bge-m3 vectors are irrelevant here), then for every labeled query
ranks the chunks by cosine and tallies the retrieval metrics.

Metrics (per config):
  * **R@1 / R@3** — share of queries whose top-1 / top-3 chunk belongs to a target source.
  * **MRR**       — mean reciprocal rank of the first chunk from a target source.
  * **sep**       — mean cosine margin: best target chunk minus best non-target chunk. On a
                    small query set this is the steadier signal than R@1.
  * **ms/query**  — single-item query embed latency (comparable across providers; broker doc
                    embedding is batched, so we time the per-query embeds, not the bulk docs).
  * **dim / footprint** — output dim after truncation; on-disk MB for onnx models.
"""
from __future__ import annotations

import time
from typing import Callable

import numpy as np

from ai_playground.bench import assets, registry


def _embed_corpus(emb, corpus_chunks: list[dict]):
    """Bulk-embed the corpus once (broker: a single round-trip). Returns texts, sources, the
    doc matrix, and the output dim — shared by the base and reranked scorers so a two-stage run
    doesn't re-embed the corpus twice."""
    texts = [c["text"] for c in corpus_chunks]
    sources = [c["source"] for c in corpus_chunks]
    doc_vecs = emb.embed_batch(texts, is_query=False)
    matrix = np.array([np.asarray(v, dtype=np.float64) for v in doc_vecs])
    dim_out = int(matrix.shape[1]) if matrix.ndim == 2 and matrix.shape[0] else 0
    return texts, sources, matrix, dim_out


def _metrics(r1, r3, mrr, sep, wall, cpu, nq, is_cpu, dim_out, n_docs, misses, extra=None) -> dict:
    q_ms = wall / max(nq, 1) * 1000.0
    cpu_ms = round(cpu / max(nq, 1) * 1000.0, 1) if is_cpu else None
    cores = round(cpu / wall, 1) if is_cpu and wall > 0 else None
    out = {
        "R@1": round(r1 / nq, 4), "R@3": round(r3 / nq, 4),
        "MRR": round(mrr / nq, 4), "sep": round(sep / nq, 4),
        "ms_per_query": round(q_ms, 1), "cpu_ms_per_query": cpu_ms, "cores": cores,
        "dim": dim_out, "n_docs": n_docs, "n_queries": nq, "misses": misses[:12],
    }
    if extra:
        out.update(extra)
    return out


def _score_core(emb, texts, sources, matrix, dim_out, queries: list[dict]) -> dict:
    r1 = r3 = mrr = sep = 0.0
    misses: list[dict] = []
    nq = len(queries)
    # Time the query embeds two ways: wall clock (latency to response) and attributable process
    # CPU time (the "CPU hit"). For a CPU/ONNX model these diverge when onnxruntime spreads work
    # across cores — cpu_ms can exceed ms, and cpu_ms/ms is roughly the number of cores it pinned.
    t0 = time.perf_counter()
    c0 = time.process_time()
    for item in queries:
        qv = np.asarray(emb.embed(item["q"], is_query=True), dtype=np.float64)
        sims = matrix @ qv
        order = np.argsort(-sims)
        ranked = [sources[i] for i in order]
        tgt = set(item.get("targets") or [])
        top1 = ranked[0] in tgt if ranked else False
        top3 = any(s in tgt for s in ranked[:3])
        rank = next((i + 1 for i, s in enumerate(ranked) if s in tgt), None)
        r1 += top1
        r3 += top3
        mrr += (1.0 / rank) if rank else 0.0
        tgt_idx = [i for i, s in enumerate(sources) if s in tgt]
        oth_idx = [i for i, s in enumerate(sources) if s not in tgt]
        if tgt_idx and oth_idx:
            sep += float(np.max(sims[tgt_idx]) - np.max(sims[oth_idx]))
        if not top1:
            misses.append({"q": item["q"][:80], "got": ranked[0] if ranked else "", "rank": rank})
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    # CPU cost is only meaningful for the in-process CPU/ONNX path. A broker call is network I/O
    # (the compute is on the GPU box), so process_time would read ~0 and mislead — report None.
    is_cpu = getattr(emb, "provider", "") == "onnx"
    return _metrics(r1, r3, mrr, sep, wall, cpu, nq, is_cpu, dim_out, len(texts), misses)


def _rerank_core(emb, reranker, texts, sources, matrix, dim_out, queries: list[dict],
                 depth: int) -> dict:
    """Two-stage scoring: retrieve the top-``depth`` chunks by cosine, then re-order them with the
    cross-encoder's joint relevance score. Metrics are computed over the reranked order; ``sep`` is
    the reranker's own score margin (best target − best distractor among the candidates). Latency
    covers **both** stages (query embed + reranking ``depth`` pairs) — the honest two-stage cost —
    and ``cpu_ms`` always reports, since the reranker is in-process CPU even behind a broker embed."""
    r1 = r3 = mrr = sep = 0.0
    misses: list[dict] = []
    nq = len(queries)
    depth = max(1, min(depth, len(texts)))
    t0 = time.perf_counter()
    c0 = time.process_time()
    for item in queries:
        qv = np.asarray(emb.embed(item["q"], is_query=True), dtype=np.float64)
        sims = matrix @ qv
        cand = [int(i) for i in np.argsort(-sims)[:depth]]          # stage 1: top-N by cosine
        scores = reranker.score(item["q"], [texts[i] for i in cand])  # stage 2: cross-encoder
        sc = np.asarray(scores, dtype=np.float64)
        order = [cand[j] for j in np.argsort(-sc)]
        ranked = [sources[i] for i in order]
        tgt = set(item.get("targets") or [])
        top1 = ranked[0] in tgt if ranked else False
        top3 = any(s in tgt for s in ranked[:3])
        rank = next((i + 1 for i, s in enumerate(ranked) if s in tgt), None)
        r1 += top1
        r3 += top3
        mrr += (1.0 / rank) if rank else 0.0
        t_j = [j for j, i in enumerate(cand) if sources[i] in tgt]
        o_j = [j for j, i in enumerate(cand) if sources[i] not in tgt]
        if t_j and o_j:
            sep += float(np.max(sc[t_j]) - np.max(sc[o_j]))
        if not top1:
            misses.append({"q": item["q"][:80], "got": ranked[0] if ranked else "", "rank": rank})
    wall = time.perf_counter() - t0
    cpu = time.process_time() - c0
    return _metrics(r1, r3, mrr, sep, wall, cpu, nq, True, dim_out, len(texts), misses,
                    extra={"rerank_depth": depth})


def _score(emb, corpus_chunks: list[dict], queries: list[dict], k: int) -> dict:
    texts, sources, matrix, dim_out = _embed_corpus(emb, corpus_chunks)
    return _score_core(emb, texts, sources, matrix, dim_out, queries)


def config_label(spec: dict, prompting: str, dim: int | None) -> str:
    parts = [spec["label"]]
    if dim and dim != spec.get("native_dim"):
        parts.append(f"@{dim}d")
    if prompting != "model" or spec.get("query_template"):
        parts.append({"none": "no-prompt", "bge-query": "+bge-prefix",
                      "model": "+model-prompt"}.get(prompting, prompting))
    return " ".join(parts)


def run(con, corpus_chunks: list[dict], queries: list[dict], run_configs: list[dict],
        k: int = 4, progress: Callable[[str, str], None] | None = None,
        reranker_id: str | None = None, rerank_depth: int = 10) -> list[dict]:
    """Execute every run-config, isolating failures so one bad model can't sink the run.

    ``run_configs`` = [{"model": id, "prompting": "none|bge-query|model", "dim": int|None}].
    ``progress(config_id, phase)`` is called as each config starts/finishes (for WS updates).

    When ``reranker_id`` is set, each successful embedder config also produces a paired **reranked**
    row (id ``…|+rerank``): the embedder's top-``rerank_depth`` chunks re-ordered by the cross-encoder,
    so the table shows the rerank lift side-by-side with the first-pass result.

    Returns [{"id", "label", "provider", ... , "metrics"|"error"}] in input order.
    """
    reranker = None
    reranker_label = reranker_id
    if reranker_id:
        try:
            reranker = registry.build_reranker(con, reranker_id)
            rspec = registry.get_spec(con, reranker_id)
            reranker_label = (rspec or {}).get("label", reranker_id)
        except Exception as exc:  # noqa: BLE001 — surface as a synthetic error row below
            reranker = None
            reranker_err = f"{type(exc).__name__}: {exc}"

    out: list[dict] = []
    for rc in run_configs:
        model_id = rc["model"]
        prompting = rc.get("prompting", "model")
        dim = rc.get("dim")
        spec = registry.get_spec(con, model_id)
        cfg_id = f"{model_id}|{prompting}|{dim or 'native'}"
        label = config_label(spec, prompting, dim) if spec else model_id
        footprint = (assets.footprint_mb(spec) if spec and spec["provider"] == "onnx" else None)
        row = {"id": cfg_id, "model": model_id, "label": label, "prompting": prompting,
               "dim_req": dim, "provider": spec["provider"] if spec else "?",
               "footprint_mb": footprint}
        if progress:
            progress(cfg_id, "start")
        shared = None
        try:
            emb = registry.build_embedder(con, model_id, prompting, dim)
            texts, sources, matrix, dim_out = _embed_corpus(emb, corpus_chunks)
            row["metrics"] = _score_core(emb, texts, sources, matrix, dim_out, queries)
            shared = (emb, texts, sources, matrix, dim_out)
        except Exception as exc:  # noqa: BLE001 — record and continue
            row["error"] = f"{type(exc).__name__}: {exc}"
        if progress:
            progress(cfg_id, "done")
        out.append(row)

        if reranker is not None and shared is not None:
            emb, texts, sources, matrix, dim_out = shared
            rid = cfg_id + "|+rerank"
            rrow = {"id": rid, "model": model_id, "prompting": prompting, "dim_req": dim,
                    "label": f"{label} + rerank ({reranker_label})", "provider": row["provider"],
                    "footprint_mb": footprint, "reranked": True, "reranker": reranker_id}
            if progress:
                progress(rid, "start")
            try:
                rrow["metrics"] = _rerank_core(emb, reranker, texts, sources, matrix, dim_out,
                                               queries, rerank_depth)
            except Exception as exc:  # noqa: BLE001
                rrow["error"] = f"{type(exc).__name__}: {exc}"
            if progress:
                progress(rid, "done")
            out.append(rrow)

    if reranker_id and reranker is None:
        out.append({"id": f"{reranker_id}|+rerank", "model": reranker_id, "prompting": None,
                    "dim_req": None, "label": f"reranker {reranker_id}", "provider": "rerank-onnx",
                    "footprint_mb": None, "reranked": True,
                    "error": locals().get("reranker_err", "reranker unavailable")})
    return out
