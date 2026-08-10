// Embedding Lab — benchmark embedding models head-to-head on a corpus + labeled query set.
// Broker (GPU) and CPU int8-ONNX models compete on the same footing: R@1/R@3, MRR, cosine
// separation, per-query latency, dim and footprint. Prompting (none / bge-prefix / model) and
// Matryoshka dims are per-model knobs, so tonight's tests (prefix vs not, @768 vs @256) are just
// multiple run-configs. The run streams progress over the rail's WebSocket.
import { Fragment, useEffect, useMemo, useState } from "react";
import { delJSON, getJSON, postAction, uploadQuerySet, wsURL } from "../../api";

type Model = {
  id: string; label: string; provider: "broker" | "onnx"; kind?: "embedder" | "reranker";
  family: string; params: string;
  native_dim: number; mrl_dims: number[]; ctx: number; about?: string; notes: string;
  has_query_prompt: boolean; available: boolean; footprint_mb: number | null; is_seed: boolean;
  broker_model?: string; hf_repo?: string; disabled_by_admin?: boolean;
};
type Corpus = { id: number; name: string; kind: string; chunks: number };
type QuerySet = { id: number; name: string; kind: string; queries: number };
type Metrics = {
  "R@1": number; "R@3": number; MRR: number; sep: number; ms_per_query: number;
  cpu_ms_per_query: number | null; cores: number | null;
  dim: number; n_docs: number; n_queries: number; rerank_depth?: number;
  misses: { q: string; got: string; rank: number | null }[];
};
type ResultRow = {
  id: string; model: string; label: string; prompting: string; provider: string;
  footprint_mb: number | null; metrics?: Metrics; error?: string; phase?: "start" | "done";
  reranked?: boolean; reranker?: string;
};
type Sel = { promptings: Set<string>; dims: Set<number | null> };

const PROMPTS = ["none", "bge-query", "model"] as const;
const PROMPT_LABEL: Record<string, string> = { none: "none", "bge-query": "bge-prefix", model: "model-default" };
const cfgId = (model: string, prompting: string, dim: number | null) =>
  `${model}|${prompting}|${dim ?? "native"}`;

export default function EmbedBenchDemo({ isAdmin }: { isAdmin: boolean }) {
  const [models, setModels] = useState<Model[]>([]);
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [querysets, setQuerysets] = useState<QuerySet[]>([]);
  const [corpusId, setCorpusId] = useState<number | null>(null);
  const [querysetId, setQuerysetId] = useState<number | null>(null);
  const [topK, setTopK] = useState(4);
  const [rerankerId, setRerankerId] = useState<string | null>(null);
  const [rerankDepth, setRerankDepth] = useState(10);
  const [sel, setSel] = useState<Record<string, Sel>>({});
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [running, setRunning] = useState(false);
  const [meta, setMeta] = useState<string>("");
  const [sortKey, setSortKey] = useState<keyof Metrics | "label">("sep");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, string>>({}); // modelId -> action label
  const [expand, setExpand] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [upOpen, setUpOpen] = useState(false);
  const [refreshOpen, setRefreshOpen] = useState(false);
  const [histOpen, setHistOpen] = useState(false);

  async function loadRun(id: number) {
    try {
      const run = await getJSON<any>(`/api/bench/runs/${id}`);
      setRows(run.results || []);
      const n = run.results?.find((r: any) => r.metrics)?.metrics?.n_docs ?? "?";
      setMeta(`${run.corpus_name} · ${n} docs · ${run.queryset} · loaded from history (${new Date(run.created_at).toLocaleString()})`);
      setHistOpen(false);
    } catch (e: any) { setError(String(e?.message || e)); }
  }

  function exportCsv() {
    const done = rows.filter((r) => r.metrics);
    if (!done.length) return;
    const esc = (v: any) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const head = ["model_config", "provider", "dim", "R@1", "R@3", "MRR", "sep", "ms_per_query", "cpu_ms_per_query", "footprint_mb"];
    const lines = [head.join(",")];
    for (const r of done) {
      const m = r.metrics!;
      lines.push([r.label, r.provider, m.dim, m["R@1"], m["R@3"], m.MRR, m.sep, m.ms_per_query,
        m.cpu_ms_per_query ?? "", r.footprint_mb ?? ""].map(esc).join(","));
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
    a.download = "embedding-lab-results.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const loadModels = () => getJSON<{ models: Model[] }>("/api/bench/models").then((d) => setModels(d.models));
  const loadCorpora = () =>
    getJSON<{ corpora: Corpus[] }>("/api/bench/corpora").then((d) => {
      setCorpora(d.corpora);
      setCorpusId((p) => p ?? d.corpora.find((c) => c.name === "Embedding Concepts")?.id ?? d.corpora[0]?.id ?? null);
    });
  const loadQuerysets = () =>
    getJSON<{ querysets: QuerySet[] }>("/api/bench/querysets").then((d) => {
      setQuerysets(d.querysets);
      setQuerysetId((p) => p ?? d.querysets[0]?.id ?? null);
    });

  useEffect(() => {
    loadModels().catch(() => setError("could not reach the Embedding Lab backend"));
    loadCorpora().catch(() => {});
    loadQuerysets().catch(() => {});
  }, []);

  function dimChips(m: Model): (number | null)[] {
    return [null, ...m.mrl_dims.filter((d) => d < m.native_dim)];
  }

  function toggleModel(m: Model) {
    setSel((prev) => {
      const next = { ...prev };
      if (next[m.id]) delete next[m.id];
      else next[m.id] = { promptings: new Set(["model"]), dims: new Set<number | null>([null]) };
      return next;
    });
  }
  function toggleIn(id: string, key: "promptings" | "dims", val: any) {
    setSel((prev) => {
      const cur = prev[id];
      if (!cur) return prev;
      const set = new Set(cur[key] as Set<any>);
      set.has(val) ? set.delete(val) : set.add(val);
      if (set.size === 0) set.add(key === "promptings" ? "model" : null);
      return { ...prev, [id]: { ...cur, [key]: set } };
    });
  }

  const runConfigs = useMemo(() => {
    const cfgs: { model: string; prompting: string; dim: number | null }[] = [];
    for (const [model, s] of Object.entries(sel))
      for (const p of s.promptings) for (const d of s.dims) cfgs.push({ model, prompting: p, dim: d });
    return cfgs;
  }, [sel]);

  function run() {
    if (!corpusId || !querysetId || runConfigs.length === 0 || running) return;
    setError(null);
    setRunning(true);
    setMeta("");
    const rrLabel = models.find((m) => m.id === activeReranker)?.label ?? activeReranker;
    // seed placeholder rows so the table shows the plan immediately, then fills in on `done`.
    // A selected reranker adds a paired "+ rerank" row per config (matching the engine's output).
    const placeholders: ResultRow[] = [];
    for (const c of runConfigs) {
      const m = models.find((x) => x.id === c.model);
      const label =
        (m?.label ?? c.model) +
        (c.dim && c.dim !== m?.native_dim ? ` @${c.dim}d` : "") +
        (c.prompting !== "model" || m?.has_query_prompt ? ` +${PROMPT_LABEL[c.prompting]}` : "");
      const base: ResultRow = { id: cfgId(c.model, c.prompting, c.dim), model: c.model, label,
        prompting: c.prompting, provider: m?.provider ?? "?", footprint_mb: m?.footprint_mb ?? null };
      placeholders.push(base);
      if (activeReranker)
        placeholders.push({ ...base, id: base.id + "|+rerank",
          label: `${label} + rerank (${rrLabel})`, reranked: true, reranker: activeReranker });
    }
    setRows(placeholders);
    const ws = new WebSocket(wsURL("/ws/bench"));
    ws.onopen = () => ws.send(JSON.stringify({ corpus: corpusId, queryset: querysetId,
      configs: runConfigs, k: topK, reranker: activeReranker, rerank_depth: rerankDepth }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "meta")
        setMeta(`${m.corpus} · ${m.n_docs} docs · ${m.queryset} · ${m.n_queries} queries · ${m.configs} run-configs`);
      else if (m.type === "progress")
        setRows((rs) => rs.map((r) => (r.id === m.config ? { ...r, phase: m.phase } : r)));
      else if (m.type === "done") {
        const byId: Record<string, ResultRow> = {};
        for (const r of m.results) byId[r.id] = r;
        setRows((rs) => rs.map((r) => byId[r.id] ?? r));
        setRunning(false);
        ws.close();
      } else if (m.type === "error") {
        setError(m.message);
        setRunning(false);
        ws.close();
      }
    };
    ws.onerror = () => { setError("WebSocket error during run"); setRunning(false); };
  }

  async function act(m: Model, kind: "fetch" | "pull") {
    setBusy((b) => ({ ...b, [m.id]: kind === "fetch" ? "Fetching…" : "Pulling…" }));
    setError(null);
    try {
      await postAction(`/api/bench/models/${encodeURIComponent(m.id)}/${kind}`);
      await loadModels();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setBusy((b) => { const n = { ...b }; delete n[m.id]; return n; });
    }
  }
  async function removeModel(m: Model) {
    if (!confirm(`Remove ${m.label} from the registry${m.provider === "onnx" ? " and delete its files" : ""}?`)) return;
    try {
      await delJSON(`/api/bench/models/${encodeURIComponent(m.id)}?purge=true`);
      await loadModels();
    } catch (e: any) { setError(String(e?.message || e)); }
  }

  const sorted = useMemo(() => {
    const done = rows.filter((r) => r.metrics);
    const rest = rows.filter((r) => !r.metrics);
    const val = (r: ResultRow) =>
      sortKey === "label" ? r.label : (r.metrics ? ((r.metrics as any)[sortKey] ?? -Infinity) : -Infinity);
    const asc = sortKey === "ms_per_query" || sortKey === "label";
    done.sort((a, b) => {
      const x = val(a), y = val(b);
      if (typeof x === "string") return asc ? x.localeCompare(y as string) : (y as string).localeCompare(x);
      return asc ? x - y : y - x;
    });
    return [...done, ...rest];
  }, [rows, sortKey]);

  const verd = useMemo(() => verdict(rows, models), [rows, models]);
  const plain = useMemo(() => plainTerms(rows, models), [rows, models]);
  const lift = useMemo(() => rerankLift(rows), [rows]);
  const isReranker = (m: Model) => m.kind === "reranker";
  const grouped = {
    broker: models.filter((m) => m.provider === "broker" && !isReranker(m)),
    onnx: models.filter((m) => m.provider === "onnx" && !isReranker(m)),
  };
  const rerankers = models.filter(isReranker);
  const activeReranker = rerankers.find((m) => m.id === rerankerId && m.available) ? rerankerId : null;

  return (
    <div className="lab">
      <div className="lab-setup">
        <label className="lab-field" title="The document set every model searches over. Each doc is split into chunks; pick a seed corpus or upload your own with the RAG demo.">
          <span>Corpus</span>
          <select value={corpusId ?? ""} onChange={(e) => setCorpusId(Number(e.target.value) || null)}>
            {corpora.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.chunks} chunks</option>)}
          </select>
        </label>
        <label className="lab-field" title="The labeled questions used to score retrieval — each question is tagged with the corpus source(s) that are correct answers.">
          <span>Query set</span>
          <select value={querysetId ?? ""} onChange={(e) => setQuerysetId(Number(e.target.value) || null)}>
            {querysets.map((q) => <option key={q.id} value={q.id}>{q.name} · {q.queries} queries</option>)}
          </select>
        </label>
        <button className="lab-mini" onClick={() => setUpOpen(true)} title="Upload your own query set (JSON): {name, queries:[{q, targets:[source]}]}">＋ query set</button>
        <label className="lab-field lab-k" title="How many top results count as a hit for Recall@3 and MRR. Recall@1 always looks at just the first result.">
          <span>top-K</span>
          <input type="number" min={1} max={10} value={topK} onChange={(e) => setTopK(Math.max(1, Number(e.target.value) || 4))} />
        </label>
        <button className="lab-mini" onClick={() => setHistOpen(true)} title="Browse and reload past benchmark runs">⟲ history</button>
        <span className="lab-spacer" />
        {activeReranker && (
          <span className="lab-rr-chip" title={`Two-stage: each config's top ${rerankDepth} are re-ranked by ${models.find((m) => m.id === activeReranker)?.label}. Adds a "+ rerank" row per config.`}>
            🔁 + rerank ×{rerankDepth}
          </span>
        )}
        <span className="lab-count" title="One column per (model × prompting × dim). Each selected model contributes all combinations of its ticked prompt and dim chips. A selected reranker doubles the rows (base + reranked).">{runConfigs.length} run-config{runConfigs.length === 1 ? "" : "s"}</span>
        <button className="lab-run" onClick={run} disabled={running || runConfigs.length === 0 || !corpusId || !querysetId}
          title="Re-embed the corpus with every selected model/config and score them all on the query set.">
          {running ? "Running…" : "▶ Run benchmark"}
        </button>
      </div>

      {error && <div className="lab-error">{error}</div>}
      {meta && <div className="lab-meta">{meta}</div>}

      <div className="lab-body">
        <aside className="lab-models">
          <div className="side-h">
            Models
            {isAdmin && (
              <span className="side-h-btns">
                <button className="lab-add" onClick={() => setRefreshOpen(true)}
                  title="Scan each family for newer releases (Hugging Face + Ollama) and update in place.">⟳ refresh</button>
                <button className="lab-add" onClick={() => setAddOpen(true)}
                  title="Register a new model to benchmark — a broker (Ollama) tag, or an ONNX repo to fetch from Hugging Face.">＋ add</button>
              </span>
            )}
          </div>
          {(["broker", "onnx"] as const).map((prov) => (
            <div key={prov} className="lab-group">
              <div className="lab-group-h">{prov === "broker" ? "🖥️ broker · GPU" : "💾 ONNX · CPU (beside-the-exe)"}</div>
              {grouped[prov].map((m) => {
                const s = sel[m.id];
                const on = !!s;
                return (
                  <div key={m.id} className={"mrow" + (on ? " on" : "")}>
                    <div className="mrow-top">
                      <label className="mrow-pick">
                        <input type="checkbox" checked={on} disabled={!m.available} onChange={() => toggleModel(m)} />
                        <span className="mrow-label">{m.label}</span>
                      </label>
                      <span className={"mrow-badge" + (m.available ? " ok" : "")}>
                        {m.available ? (m.footprint_mb ? `${m.footprint_mb} MB` : "ready")
                          : m.disabled_by_admin ? "disabled" : "not fetched"}
                      </span>
                    </div>
                    <div className="mrow-meta">
                      {m.family} · {m.params} · {m.native_dim}d · {m.ctx} ctx
                    </div>
                    {m.about && (
                      <div className="mrow-about" title={m.notes || undefined}>{m.about}</div>
                    )}
                    {!m.available && isAdmin && (
                      <div className="mrow-actions">
                        {busy[m.id] ? <span className="mrow-busy">{busy[m.id]}</span> :
                          m.provider === "onnx"
                            ? <button onClick={() => act(m, "fetch")}>Fetch from HF</button>
                            : <button onClick={() => act(m, "pull")}>Pull via Ollama</button>}
                      </div>
                    )}
                    {!m.available && !isAdmin && (
                      <div className="mrow-hint">
                        {m.provider === "onnx" ? "ask an admin to fetch this model" : `run: ollama pull ${m.broker_model}`}
                      </div>
                    )}
                    {on && (
                      <div className="mrow-knobs">
                        <div className="knob">
                          <span>prompt</span>
                          {PROMPTS.map((p) => (
                            <button key={p} className={"chip2" + (s.promptings.has(p) ? " sel" : "")}
                              onClick={() => toggleIn(m.id, "promptings", p)}
                              title={p === "bge-query" ? "BGE retrieval instruction on the query" :
                                     p === "model" ? "the model's own query/doc templates" : "embed raw text"}>
                              {PROMPT_LABEL[p]}
                            </button>
                          ))}
                        </div>
                        {dimChips(m).length > 1 && (
                          <div className="knob">
                            <span>dim</span>
                            {dimChips(m).map((d) => (
                              <button key={String(d)} className={"chip2" + (s.dims.has(d) ? " sel" : "")}
                                onClick={() => toggleIn(m.id, "dims", d)}
                                title={d === null
                                  ? `Full ${m.native_dim}-number vectors (native — best quality, largest to store)`
                                  : `Matryoshka: keep the first ${d} of ${m.native_dim} numbers — smaller and cheaper to store/compare, usually a small quality cost`}>
                                {d === null ? `${m.native_dim} (native)` : d}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                    {isAdmin && !m.is_seed && (
                      <button className="mrow-del" title="Remove from registry" onClick={() => removeModel(m)}>✕</button>
                    )}
                  </div>
                );
              })}
            </div>
          ))}

          {rerankers.length > 0 && (
            <div className="lab-group lab-group-rr">
              <div className="lab-group-h">🔁 reranker · CPU (2-stage, optional)</div>
              <div className="mrow-hint rr-lead">
                A second pass: re-orders each embedder's top results with a cross-encoder that reads the
                question and a candidate together. Pick one to add a “+ rerank” row per config.
              </div>
              {rerankers.map((m) => {
                const on = rerankerId === m.id;
                return (
                  <div key={m.id} className={"mrow" + (on ? " on" : "")}>
                    <div className="mrow-top">
                      <label className="mrow-pick">
                        <input type="radio" name="reranker" checked={on} disabled={!m.available}
                          onChange={() => setRerankerId(on ? null : m.id)} />
                        <span className="mrow-label">{m.label}</span>
                      </label>
                      <span className={"mrow-badge" + (m.available ? " ok" : "")}>
                        {m.available ? (m.footprint_mb ? `${m.footprint_mb} MB` : "ready")
                          : m.disabled_by_admin ? "disabled" : "not fetched"}
                      </span>
                    </div>
                    <div className="mrow-meta">{m.family} · {m.params} · cross-encoder · {m.ctx} ctx</div>
                    {m.about && <div className="mrow-about" title={m.notes || undefined}>{m.about}</div>}
                    {!m.available && isAdmin && (
                      <div className="mrow-actions">
                        {busy[m.id] ? <span className="mrow-busy">{busy[m.id]}</span>
                          : <button onClick={() => act(m, "fetch")}>Fetch from HF</button>}
                      </div>
                    )}
                    {!m.available && !isAdmin && (
                      <div className="mrow-hint">ask an admin to fetch this reranker</div>
                    )}
                  </div>
                );
              })}
              {activeReranker && (
                <label className="knob rr-depth" title="How many of the embedder's top chunks (by first-pass cosine) the reranker re-scores per query. Larger is more thorough but slower.">
                  <span>rerank depth</span>
                  <input type="number" min={2} max={50} value={rerankDepth}
                    onChange={(e) => setRerankDepth(Math.max(2, Math.min(50, Number(e.target.value) || 10)))} />
                </label>
              )}
              {rerankerId && (
                <button className="rr-clear" onClick={() => setRerankerId(null)}>clear reranker</button>
              )}
            </div>
          )}
        </aside>

        <main className="lab-results">
          {rows.length === 0 ? (
            <div className="lab-empty">
              Pick a corpus + query set, check the models to compare (toggle prompting and Matryoshka dims per model),
              then <b>Run benchmark</b>. Every model re-embeds the corpus and is scored on the same queries.
            </div>
          ) : (
            <table className="lab-table">
              <thead>
                <tr>
                  <Th k="label" cur={sortKey} set={setSortKey} tip="The model plus its prompting mode and output dimension">model · config</Th>
                  <th title="Where it ran: broker = Ollama on the GPU; onnx = int8 on the CPU, no server">prov</th>
                  <Th k="dim" cur={sortKey} set={setSortKey} tip="Numbers per vector — smaller is cheaper to store/compare">dim</Th>
                  <Th k="R@1" cur={sortKey} set={setSortKey} tip="How often the very top result was a correct one (higher is better)">R@1</Th>
                  <Th k="R@3" cur={sortKey} set={setSortKey} tip="How often a correct result was in the top 3 (higher is better)">R@3</Th>
                  <Th k="MRR" cur={sortKey} set={setSortKey} tip="Mean reciprocal rank — how near the top the right answer landed on average">MRR</Th>
                  <Th k="sep" cur={sortKey} set={setSortKey} tip="Cosine margin between the best right and best wrong answer — how confidently it separates them; the steadiest signal on small sets">sep</Th>
                  <Th k="ms_per_query" cur={sortKey} set={setSortKey} tip="Latency to respond to one query (wall-clock)">ms/q</Th>
                  <Th k="cpu_ms_per_query" cur={sortKey} set={setSortKey} tip="CPU compute burned per query, with cores pinned in parentheses. 'gpu' = broker-served, so the cost is on the GPU box">cpu ms/q</Th>
                  <th title="On-disk size of the model file (ONNX/CPU models only)">MB</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <Fragment key={r.id}>
                    <tr className={r.error ? "err" : ""}
                        onClick={() => r.metrics?.misses?.length && setExpand(expand === r.id ? null : r.id)}>
                      <td className="c-label">
                        <span className={"pdot " + r.provider} /> {r.label}
                        {r.error && <span className="c-errtag"> — {r.error}</span>}
                      </td>
                      <td className="c-prov">{r.provider}</td>
                      <td>{r.metrics?.dim ?? (r.phase === "start" ? "…" : r.phase === "done" ? "" : "·")}</td>
                      <td className="c-m">{fmt(r.metrics?.["R@1"])}</td>
                      <td className="c-m">{fmt(r.metrics?.["R@3"])}</td>
                      <td className="c-m">{fmt(r.metrics?.MRR, 3)}</td>
                      <td className="c-m strong">{r.metrics ? sgn(r.metrics.sep) : run_dot(r)}</td>
                      <td className="c-m">{r.metrics ? r.metrics.ms_per_query.toFixed(0) : ""}</td>
                      <td className="c-m" title={r.metrics?.cores ? `~${r.metrics.cores} cores busy` : "GPU-served (broker)"}>
                        {r.metrics ? (r.metrics.cpu_ms_per_query != null
                          ? r.metrics.cpu_ms_per_query.toFixed(0) + (r.metrics.cores ? ` (${r.metrics.cores}×)` : "")
                          : "gpu") : ""}
                      </td>
                      <td className="c-m">{r.footprint_mb ?? ""}</td>
                    </tr>
                    {expand === r.id && r.metrics?.misses?.length ? (
                      <tr className="miss-row"><td colSpan={10}>
                        <div className="miss-h">missed at rank 1 ({r.metrics.misses.length}):</div>
                        {r.metrics.misses.map((mi, i) => (
                          <div key={i} className="miss">“{mi.q}…” → <code>{mi.got}</code> (target at rank {mi.rank ?? "NR"})</div>
                        ))}
                      </td></tr>
                    ) : null}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
          {rows.some((r) => r.metrics) && (
            <div className="lab-results-bar">
              <button className="lab-mini" onClick={exportCsv} title="Download the current results table as CSV">⭳ Export CSV</button>
            </div>
          )}
          {rows.some((r) => r.metrics) && (
            <div className="lab-legend">
              R@1/R@3 = top-1/top-3 hit rate · MRR = mean reciprocal rank · <b>sep</b> = cosine margin (target − best distractor;
              the steadier signal on small sets) · ms/q = latency to response · <b>cpu ms/q</b> = CPU compute burned per query
              (with ~cores pinned; “gpu” = broker-served, cost is on the GPU box) · MB = on-disk footprint (ONNX).
              A <b>+ rerank</b> row is the same embedder's top-N re-ordered by a CPU cross-encoder (ms/q + cpu covers both stages;
              its <b>sep</b> is the reranker's own score margin, a different scale from cosine sep — compare rerank rows on R@1/MRR).
              Click a row for misses.
            </div>
          )}

          {verd && !running && (
            <section className="lab-verdict">
              <div className="lv-head"><span className="lv-badge">🏆 Winner</span><span className="lv-name">{verd.winnerLabel}</span></div>
              <p className="lv-why">{md(verd.winnerWhy)}</p>
              {verd.runnerLabel && (
                <p className="lv-runner"><span className="lv-ru">Runner-up · {verd.runnerLabel}</span> {md(verd.runnerLine!)}</p>
              )}
              {verd.valueLine && (
                <p className="lv-value"><span className="lv-ru">💡 Value</span> {md(verd.valueLine)}</p>
              )}
              {verd.note && <p className="lv-note">{md(verd.note)}</p>}
              {verd.single && <p className="lv-note">Only one setup in this run — add more models or configs to see a head-to-head.</p>}
            </section>
          )}

          {lift && !running && (
            <section className="lab-lift">
              <div className="lab-lift-h">🔁 Reranking</div>
              <p className="lab-lift-lead">{md(lift.lead)}</p>
              <ul className="lab-plain-list">{lift.lines.map((b, i) => <li key={i}>{md(b)}</li>)}</ul>
            </section>
          )}

          {plain && !running && (
            <section className="lab-plain">
              <div className="lab-plain-h">🧑‍🏫 In plain terms</div>
              <p className="lab-plain-lead">{plain.head}</p>
              <ul className="lab-plain-list">{plain.bullets.map((b, i) => <li key={i}>{md(b)}</li>)}</ul>
              <p className="lab-plain-foot">{plain.foot}</p>
            </section>
          )}
        </main>
      </div>

      {addOpen && <AddModelModal onClose={() => setAddOpen(false)} onSaved={() => { setAddOpen(false); loadModels(); }} />}
      {upOpen && <UploadQsModal onClose={() => setUpOpen(false)} onSaved={() => { setUpOpen(false); loadQuerysets(); }} />}
      {refreshOpen && <RefreshModal onClose={() => setRefreshOpen(false)} onChanged={loadModels} />}
      {histOpen && <HistoryModal onClose={() => setHistOpen(false)} onLoad={loadRun} />}
    </div>
  );
}

function Th({ k, cur, set, children, tip }: { k: any; cur: string; set: (k: any) => void; children: any; tip?: string }) {
  return (
    <th className={"sortable" + (cur === k ? " active" : "")} onClick={() => set(k)}
        title={(tip ? tip + " · " : "") + "click to sort"}>
      {children}{cur === k ? " ↓" : ""}
    </th>
  );
}
const fmt = (v?: number, d = 2) => (v == null ? "" : v.toFixed(d));
const sgn = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(3);
const run_dot = (r: ResultRow) => (r.phase === "start" ? "running…" : r.phase === "done" ? "…" : "");

// tiny **bold** renderer for the plain-terms copy
function md(s: string) {
  return s.split(/\*\*/).map((p, i) => (i % 2 === 1 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>));
}
const promptWord = (p: string) =>
  p === "none" ? "no prompt" : p === "bge-query" ? "the bge search prefix" : "the model's own prompt";
// Cost-per-query proxy: CPU compute time for onnx models, else wall latency (broker round-trip).
const costOf = (r: DoneRow) => r.metrics.cpu_ms_per_query ?? r.metrics.ms_per_query;
const fmtCost = (r: DoneRow) =>
  r.metrics.cpu_ms_per_query != null ? `${r.metrics.cpu_ms_per_query} cpu-ms` : `${r.metrics.ms_per_query} ms`;
// The cheapest config whose quality is within `tol` of the winner's — the "why pay more?" pick.
function valuePick(done: DoneRow[], winner: DoneRow, tol = 0.2): DoneRow | null {
  const wSep = winner.metrics.sep;
  const close = done.filter(
    (r) => r.id !== winner.id
      && (wSep - r.metrics.sep) / Math.max(wSep, 0.001) <= tol
      && costOf(winner) / Math.max(costOf(r), 0.01) >= 1.5
  );
  if (!close.length) return null;
  return close.sort((a, b) => costOf(a) - costOf(b))[0];
}

// Build a layman-friendly narrative from whatever configs actually ran. Every claim is derived
// from the result numbers, so it can't drift from the table above it.
type DoneRow = ResultRow & { metrics: Metrics };
function plainTerms(rows: ResultRow[], models: Model[]) {
  // Reranked rows use a cross-encoder logit margin for `sep` — a different scale from cosine sep —
  // so they're excluded from the sep-ranked narrative (the rerank story is told by rerankLift).
  const done = rows.filter((r): r is DoneRow => !!r.metrics && !r.reranked);
  if (done.length === 0) return null;
  const pct = (v: number) => Math.round(v * 100) + "%";
  const labelOf = (id: string) => models.find((m) => m.id === id)?.label ?? id;
  const bySep = [...done].sort((a, b) => b.metrics.sep - a.metrics.sep);
  const best = bySep[0];
  const bullets: string[] = [];

  bullets.push(
    `The strongest performer was **${best.label}**. It put a correct document in first place ${pct(best.metrics["R@1"])} of the time and told right answers from wrong ones by the widest margin (sep ${best.metrics.sep.toFixed(3)}). A bigger margin means the model is more sure of itself, so it is the most dependable of the ones you ran.`
  );

  const cpu = done.filter((r) => r.provider === "onnx");
  const gpu = done.filter((r) => r.provider === "broker");
  if (cpu.length && gpu.length) {
    const bc = [...cpu].sort((a, b) => b.metrics.sep - a.metrics.sep)[0];
    const bg = [...gpu].sort((a, b) => b.metrics.sep - a.metrics.sep)[0];
    const verb = bc.metrics.sep >= bg.metrics.sep ? "matched or beat" : "came within a hair of";
    bullets.push(
      `You may not need the graphics card. The best CPU model (**${bc.label}**, ${bc.footprint_mb} MB on disk, no GPU) ${verb} the best GPU model (**${bg.label}**) on accuracy. CPU models are the kind you can ship as one small file next to an app, offline.`
    );
  }

  const fast = [...done].sort((a, b) => a.metrics.ms_per_query - b.metrics.ms_per_query)[0];
  const slow = [...done].sort((a, b) => b.metrics.ms_per_query - a.metrics.ms_per_query)[0];
  if (fast.id !== slow.id) {
    const caveat = cpu.length && gpu.length
      ? " (the GPU numbers include the network trip to the broker, so they aren't a pure model-speed measure)"
      : "";
    bullets.push(
      `On speed, **${fast.label}** was quickest at about ${fast.metrics.ms_per_query} ms per question, next to about ${slow.metrics.ms_per_query} ms for **${slow.label}**${caveat}. All are fast enough for interactive use; speed mainly bites when indexing millions of items.`
    );
  }

  const byModel: Record<string, DoneRow[]> = {};
  for (const r of done) (byModel[r.model] ||= []).push(r);

  for (const rs of Object.values(byModel)) {
    if (new Set(rs.map((r) => r.prompting)).size > 1) {
      const s = [...rs].sort((a, b) => b.metrics.sep - a.metrics.sep);
      const hi = s[0], lo = s[s.length - 1];
      if (Math.abs(hi.metrics.sep - lo.metrics.sep) >= 0.005)
        bullets.push(
          `How you phrase the input matters. For **${labelOf(hi.model)}**, using ${promptWord(hi.prompting)} clearly beat ${promptWord(lo.prompting)} (margin ${hi.metrics.sep.toFixed(3)} vs ${lo.metrics.sep.toFixed(3)}). Prompting can matter as much as which model you pick.`
        );
      break;
    }
  }

  for (const rs of Object.values(byModel)) {
    if (new Set(rs.map((r) => r.metrics.dim)).size > 1) {
      const s = [...rs].sort((a, b) => b.metrics.dim - a.metrics.dim);
      const big = s[0], small = s[s.length - 1];
      const drop = big.metrics.sep - small.metrics.sep;
      bullets.push(
        drop > 0.01
          ? `Shrinking the vectors has a price. Cutting **${labelOf(big.model)}** from ${big.metrics.dim} to ${small.metrics.dim} numbers per item lowered its margin (${big.metrics.sep.toFixed(3)} to ${small.metrics.sep.toFixed(3)}). Smaller vectors save memory and storage at a modest accuracy cost.`
          : `Shrinking the vectors was nearly free here. Cutting **${labelOf(big.model)}** from ${big.metrics.dim} to ${small.metrics.dim} numbers barely moved accuracy, so you can store smaller vectors to save memory.`
      );
      break;
    }
  }

  const bigOnnx = cpu.filter((r) => r.footprint_mb).sort((a, b) => b.footprint_mb! - a.footprint_mb!)[0];
  if (bigOnnx && bigOnnx.id !== best.id && bigOnnx.metrics.sep < best.metrics.sep)
    bullets.push(
      `Bigger is not automatically better. **${bigOnnx.label}** is the largest CPU model you ran (${bigOnnx.footprint_mb} MB) yet did not top **${best.label}** on this data. That is exactly why it pays to test on your own content instead of trusting a leaderboard.`
    );

  // cost/benefit: is the top model worth its compute?
  const vp = valuePick(done, best);
  if (vp) {
    const gap = Math.round(((best.metrics.sep - vp.metrics.sep) / Math.max(best.metrics.sep, 0.001)) * 100);
    const ratio = costOf(best) / Math.max(costOf(vp), 0.01);
    const times = ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1);
    bullets.push(
      `Is the best worth its cost? **${vp.label}** lands within ${gap}% of **${best.label}**'s quality but costs roughly ${times}x less to run per query (${fmtCost(vp)} vs ${fmtCost(best)}). Unless that last few percent really matters, the cheaper one is the smarter default.`
    );
  }

  return {
    head: `You compared ${done.length} setup${done.length === 1 ? "" : "s"} over the same ${done[0].metrics.n_docs} documents and ${done[0].metrics.n_queries} questions. In everyday terms:`,
    foot: "Quick key — R@1: how often the very first result was right. MRR: how near the top the right answer landed on average. sep: how confidently it separated right from wrong (higher = better). ms/q: time per question. MB: size on disk.",
    bullets,
  };
}

// Declare a winner for the current run and explain, in plain terms, why it won and where the
// runner-up fell short. Winner is decided on cosine separation (the steadier signal), MRR breaks ties.
function verdict(rows: ResultRow[], models: Model[]) {
  // See plainTerms: reranked rows are scored on a different sep scale, so they don't compete in
  // the cosine-sep winner ranking (their story is the separate rerank-lift callout).
  const done = rows.filter((r): r is DoneRow => !!r.metrics && !r.reranked);
  if (done.length === 0) return null;
  const pct = (v: number) => Math.round(v * 100) + "%";
  const bySep = [...done].sort((a, b) => b.metrics.sep - a.metrics.sep || b.metrics.MRR - a.metrics.MRR);
  const win = bySep[0];
  const run = bySep[1];

  const cheap: string[] = [];
  if (win.provider === "onnx") cheap.push("runs on the CPU with no GPU");
  if (win.footprint_mb) cheap.push(`is only ${win.footprint_mb} MB on disk`);
  const cheapBit = cheap.length ? ` It also ${cheap.join(" and ")}.` : "";
  const winnerWhy =
    `It found a correct document first ${pct(win.metrics["R@1"])} of the time (R@1) and, more tellingly, held the widest confidence gap between right and wrong answers (sep ${win.metrics.sep.toFixed(3)}) — the steadier signal on a small question set.${cheapBit}`;

  let runnerLabel: string | undefined, runnerLine: string | undefined;
  if (run) {
    const short: string[] = [];
    if (run.metrics.sep < win.metrics.sep)
      short.push(`a narrower confidence margin (sep ${run.metrics.sep.toFixed(3)} vs ${win.metrics.sep.toFixed(3)})`);
    if (run.metrics["R@1"] < win.metrics["R@1"])
      short.push(`a lower first-place hit rate (R@1 ${pct(run.metrics["R@1"])} vs ${pct(win.metrics["R@1"])})`);
    const edge: string[] = [];
    if (run.metrics.ms_per_query < win.metrics.ms_per_query * 0.85)
      edge.push(`quicker (${run.metrics.ms_per_query} vs ${win.metrics.ms_per_query} ms/q)`);
    if (run.footprint_mb && win.footprint_mb && run.footprint_mb < win.footprint_mb * 0.85)
      edge.push(`smaller (${run.footprint_mb} vs ${win.footprint_mb} MB)`);
    if (run.metrics["R@1"] >= win.metrics["R@1"] && run.metrics.sep < win.metrics.sep)
      edge.push("it actually tied on hit rate");
    const shortText = short.length ? short.join(" and ") : "a touch less on every measure";
    const edgeText = edge.length ? ` In its favour, it was ${edge.join(" and ")}, so it's a fair pick if that matters more to you.` : "";
    runnerLabel = run.label;
    runnerLine = `Fell just short on ${shortText}.${edgeText}`;
  }

  const r1 = [...done].sort((a, b) => b.metrics["R@1"] - a.metrics["R@1"])[0];
  const note = r1.id !== win.id && r1.metrics["R@1"] > win.metrics["R@1"]
    ? `Worth knowing: **${r1.label}** had the highest raw hit rate (R@1 ${pct(r1.metrics["R@1"])}) but a slimmer confidence margin, so the crown goes to ${win.label} on the steadier metric. If you only care about top-1 hits, ${r1.label} is the pick.`
    : undefined;

  // The cost/benefit call: is the winner's edge worth its compute?
  let valueLine: string | undefined;
  const vp = valuePick(done, win);
  if (vp) {
    const gap = Math.round(((win.metrics.sep - vp.metrics.sep) / Math.max(win.metrics.sep, 0.001)) * 100);
    const ratio = costOf(win) / Math.max(costOf(vp), 0.01);
    const times = ratio >= 10 ? Math.round(ratio) : ratio.toFixed(1);
    const kind = vp.metrics.cpu_ms_per_query != null && win.metrics.cpu_ms_per_query != null ? "CPU compute" : "time";
    const mb = vp.footprint_mb && win.footprint_mb && vp.footprint_mb < win.footprint_mb * 0.85
      ? ` and ${vp.footprint_mb} vs ${win.footprint_mb} MB on disk` : "";
    valueLine = `**${vp.label}** is the value pick. It comes within ${gap}% of the winner's confidence margin (sep ${vp.metrics.sep.toFixed(3)} vs ${win.metrics.sep.toFixed(3)}) while costing about ${times}x less ${kind} per query (${fmtCost(vp)} vs ${fmtCost(win)})${mb}. If that small quality edge isn't worth the extra compute, take it.`;
  }

  return { winnerLabel: win.label, winnerWhy, runnerLabel, runnerLine, note, valueLine, single: done.length === 1 };
}

// Pair each embedder config with its "+ rerank" variant and describe, in comparable terms, whether
// the second pass helped. Ranking metrics (R@1/MRR) ARE comparable across the two stages (unlike
// sep), so the lift story is honest: it can just as easily show "no change" or a regression + cost.
function rerankLift(rows: ResultRow[]) {
  const done = rows.filter((r): r is DoneRow => !!r.metrics);
  if (!done.some((r) => r.reranked)) return null;
  const base = new Map(done.filter((r) => !r.reranked).map((r) => [r.id, r] as const));
  const pairs: { base: DoneRow; rer: DoneRow }[] = [];
  for (const r of done) {
    if (!r.reranked) continue;
    const b = base.get(r.id.replace(/\|\+rerank$/, ""));
    if (b) pairs.push({ base: b, rer: r });
  }
  if (!pairs.length) return null;
  const rrName = pairs[0].rer.label.replace(/^.*\+ rerank \(/, "").replace(/\)\s*$/, "");
  const depth = pairs[0].rer.metrics.rerank_depth;
  const lines = pairs.map(({ base: b, rer: r }) => {
    const d1 = r.metrics["R@1"] - b.metrics["R@1"];
    const dm = r.metrics.MRR - b.metrics.MRR;
    const dir = d1 > 0.001 || (Math.abs(d1) <= 0.001 && dm > 0.001) ? "helped"
      : d1 < -0.001 || dm < -0.001 ? "hurt" : "no change";
    const pp = (v: number) => (v >= 0 ? "+" : "") + Math.round(v * 100);
    const costMs = Math.round(r.metrics.ms_per_query - b.metrics.ms_per_query);
    const verb = dir === "helped" ? "lifted" : dir === "hurt" ? "lowered" : "held";
    return `**${b.label}**: reranking ${verb} R@1 ${Math.round(b.metrics["R@1"] * 100)}%→${Math.round(r.metrics["R@1"] * 100)}% (${pp(d1)} pts) and MRR ${b.metrics.MRR.toFixed(2)}→${r.metrics.MRR.toFixed(2)}, at about +${costMs} ms/query of second-pass cost.`;
  });
  const helped = pairs.filter((p) => p.rer.metrics["R@1"] > p.base.metrics["R@1"] + 0.001).length;
  const lead = `A cross-encoder (**${rrName}**) re-scored each embedder's top ${depth} results by reading the question and candidate together. Reranking helped on ${helped} of ${pairs.length} setup${pairs.length === 1 ? "" : "s"} here — on an easy query set the first pass is often already right, so the gain shows most on harder, ambiguous queries.`;
  return { lead, lines };
}

function AddModelModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [provider, setProvider] = useState<"broker" | "onnx">("broker");
  const [f, setF] = useState<any>({ id: "", label: "", broker_model: "", hf_repo: "", onnx_file: "onnx/model_quantized.onnx",
    files: "onnx/model_quantized.onnx, tokenizer.json", pooling: "cls", native_dim: 768, query_template: "", doc_template: "", about: "" });
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggested, setSuggested] = useState<string | null>(null); // repo it grounded on

  async function suggest() {
    const name = (f.id || f.broker_model || f.hf_repo || "").trim();
    if (!name) { setErr("enter a model id or name first"); return; }
    setSuggesting(true); setErr(null); setSuggested(null);
    try {
      const r = await fetch("/ai-playground/api/bench/models/describe", { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, family: f.label, hf_repo: f.hf_repo || undefined, broker_model: f.broker_model || undefined }) });
      if (!r.ok) throw new Error((await r.text()) || "describe failed");
      const d = await r.json();
      setF((p: any) => ({ ...p, about: d.about || p.about }));
      setSuggested(d.grounded_from || "the model name");
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setSuggesting(false); }
  }

  async function save() {
    setSaving(true); setErr(null);
    const spec: any = { id: f.id.trim(), label: f.label.trim() || f.id.trim(), provider,
      native_dim: Number(f.native_dim) || 768, mrl_dims: [], query_template: f.query_template,
      doc_template: f.doc_template, about: f.about.trim() };
    if (provider === "broker") spec.broker_model = f.broker_model.trim();
    else { spec.hf_repo = f.hf_repo.trim(); spec.onnx_file = f.onnx_file.trim(); spec.pooling = f.pooling;
      spec.files = f.files.split(",").map((s: string) => s.trim()).filter(Boolean); }
    try {
      const r = await fetch("/ai-playground/api/bench/models", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(spec) });
      if (!r.ok) throw new Error((await r.text()) || "save failed");
      onSaved();
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setSaving(false); }
  }
  return (
    <div className="ap-modal-back" onClick={onClose}>
      <div className="ap-modal lab-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add a model to the registry</h3>
        <div className="seg">
          {(["broker", "onnx"] as const).map((p) => (
            <button key={p} className={provider === p ? "on" : ""} onClick={() => setProvider(p)}>{p === "broker" ? "broker (Ollama/GPU)" : "onnx (CPU)"}</button>
          ))}
        </div>
        <input className="ap-input" placeholder="id (e.g. nomic-embed-text)" value={f.id} onChange={(e) => setF({ ...f, id: e.target.value })} />
        <input className="ap-input" placeholder="label (display name)" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
        {provider === "broker" ? (
          <input className="ap-input" placeholder="ollama tag (e.g. nomic-embed-text:latest)" value={f.broker_model} onChange={(e) => setF({ ...f, broker_model: e.target.value })} />
        ) : (
          <>
            <input className="ap-input" placeholder="hf_repo (e.g. Xenova/gte-small)" value={f.hf_repo} onChange={(e) => setF({ ...f, hf_repo: e.target.value })} />
            <input className="ap-input" placeholder="onnx_file (repo path)" value={f.onnx_file} onChange={(e) => setF({ ...f, onnx_file: e.target.value })} />
            <input className="ap-input" placeholder="files to fetch (comma-separated)" value={f.files} onChange={(e) => setF({ ...f, files: e.target.value })} />
            <label className="lab-inline"><span>pooling</span>
              <select value={f.pooling} onChange={(e) => setF({ ...f, pooling: e.target.value })}>
                <option value="cls">cls</option><option value="mean">mean</option><option value="graph">graph (sentence_embedding)</option>
              </select>
            </label>
          </>
        )}
        <label className="lab-inline"><span>native dim</span>
          <input type="number" value={f.native_dim} onChange={(e) => setF({ ...f, native_dim: e.target.value })} /></label>
        <input className="ap-input" placeholder="query_template (optional, use {text})" value={f.query_template} onChange={(e) => setF({ ...f, query_template: e.target.value })} />
        <div className="lab-about-field">
          <div className="lab-about-head">
            <span>About (plain-English, shown on the card)</span>
            <button type="button" className="lab-suggest" onClick={suggest} disabled={suggesting}
              title="Look the model up on Hugging Face and draft a one-line description for you to review">
              {suggesting ? "Looking it up…" : "✨ Suggest"}
            </button>
          </div>
          <textarea className="ap-input lab-about-ta" rows={2}
            placeholder="One plain sentence: why it exists and what it's good at"
            value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} />
          {suggested && <div className="lab-about-note">Drafted from <b>{suggested}</b> — please review and edit before saving.</div>}
        </div>
        {err && <div className="lab-error">{err}</div>}
        <div className="ap-modal-row">
          <button onClick={onClose} disabled={saving}>Cancel</button>
          <button className="primary" onClick={save} disabled={saving || !f.id.trim()}>{saving ? "Saving…" : "Add model"}</button>
        </div>
      </div>
    </div>
  );
}

function HistoryModal({ onClose, onLoad }: { onClose: () => void; onLoad: (id: number) => void }) {
  const [runs, setRuns] = useState<{ id: number; corpus_name: string; queryset: string; k: number; created_at: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    getJSON<{ runs: any[] }>("/api/bench/runs")
      .then((d) => setRuns(d.runs)).catch((e) => setErr(String(e?.message || e))).finally(() => setLoaded(true));
  }, []);
  return (
    <div className="ap-modal-back" onClick={onClose}>
      <div className="ap-modal lab-modal lab-refresh" onClick={(e) => e.stopPropagation()}>
        <h3>⟲ Run history</h3>
        <p className="hint">Past benchmark runs (most recent first). Load one to view its results table again.</p>
        {err && <div className="lab-error">{err}</div>}
        {!loaded ? <div className="lab-empty">Loading…</div>
          : runs.length === 0 ? <div className="lab-empty">No past runs yet — run a benchmark to build history.</div>
          : (
            <div className="rf-list">
              {runs.map((r) => (
                <div key={r.id} className="rf-row">
                  <div className="rf-name">{r.corpus_name} · {r.queryset}
                    <div className="rf-sub">top-K {r.k} · {new Date(r.created_at).toLocaleString()}</div>
                  </div>
                  <button className="rf-btn" onClick={() => onLoad(r.id)}>Load</button>
                </div>
              ))}
            </div>
          )}
        <div className="ap-modal-row"><button className="primary" onClick={onClose}>Done</button></div>
      </div>
    </div>
  );
}

function RefreshModal({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<{ updates: any[]; broker: any[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [done, setDone] = useState<Record<string, string>>({});

  useEffect(() => {
    getJSON<{ updates: any[]; broker: any[] }>("/api/bench/refresh")
      .then(setData).catch((e) => setErr(String(e?.message || e)));
  }, []);

  async function act(id: string, kind: "adopt" | "pull", label: string) {
    setBusy((b) => ({ ...b, [id]: kind === "adopt" ? "Fetching…" : "Pulling…" }));
    setErr(null);
    try {
      const path = kind === "adopt"
        ? `/api/bench/refresh/adopt/${encodeURIComponent(id)}`
        : `/api/bench/models/${encodeURIComponent(id)}/pull`;
      const r = await postAction<any>(path);
      setDone((d) => ({ ...d, [id]: kind === "adopt" ? `added ${r.label} · ${r.footprint_mb} MB` : "re-pulled latest" }));
      onChanged();
    } catch (e: any) { setErr(String(e?.message || e)); }
    finally { setBusy((b) => { const n = { ...b }; delete n[id]; return n; }); }
  }

  return (
    <div className="ap-modal-back" onClick={onClose}>
      <div className="ap-modal lab-modal lab-refresh" onClick={(e) => e.stopPropagation()}>
        <h3>⟳ Refresh models</h3>
        <p className="hint">Best-effort scan of each family's Hugging Face publisher and the Ollama library.
          A newer version is added as a <b>new</b> entry, so you keep the old one for comparison.</p>
        {err && <div className="lab-error">{err}</div>}
        {!data ? (
          <div className="lab-empty">Scanning Hugging Face…</div>
        ) : (
          <div className="rf-list">
            <div className="rf-sec">CPU · ONNX families</div>
            {data.updates.map((u) => (
              <div key={u.id} className="rf-row">
                <div className="rf-name">{u.label}
                  <div className="rf-sub">{u.family} · you have v{u.current}{u.status === "newer" ? ` · latest v${u.latest}` : ""}</div>
                </div>
                {done[u.id] ? <span className="rf-ok">✓ {done[u.id]}</span>
                  : busy[u.id] ? <span className="mrow-busy">{busy[u.id]}</span>
                  : u.status === "newer" ? <button className="rf-btn" onClick={() => act(u.id, "adopt", u.label)}>Update to v{u.latest}</button>
                  : <span className="rf-uptodate">up to date</span>}
              </div>
            ))}
            <div className="rf-sec">Broker · Ollama models</div>
            {data.broker.map((b) => (
              <div key={b.id} className="rf-row">
                <div className="rf-name">{b.label}
                  <div className="rf-sub">{b.family} · {b.current}
                    {b.status === "update" ? " · update available" : b.status === "up_to_date" ? " · digest matches" : ""}</div>
                </div>
                {done[b.id] ? <span className="rf-ok">✓ {done[b.id]}</span>
                  : busy[b.id] ? <span className="mrow-busy">{busy[b.id]}</span>
                  : b.status === "up_to_date" ? <span className="rf-uptodate">up to date</span>
                  : <button className="rf-btn" onClick={() => act(b.id, "pull", b.label)}>
                      {b.status === "update" ? "Update available — re-pull" : "Re-pull latest"}
                    </button>}
              </div>
            ))}
          </div>
        )}
        <div className="ap-modal-row"><button className="primary" onClick={onClose}>Done</button></div>
      </div>
    </div>
  );
}

function UploadQsModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function save() {
    if (!file || !name.trim()) return;
    setSaving(true); setErr(null);
    try { await uploadQuerySet(name.trim(), file); onSaved(); }
    catch (e: any) { setErr(String(e?.message || e)); } finally { setSaving(false); }
  }
  return (
    <div className="ap-modal-back" onClick={onClose}>
      <div className="ap-modal lab-modal" onClick={(e) => e.stopPropagation()}>
        <h3>Upload a query set</h3>
        <p className="hint">JSON: {`{"name": "...", "queries": [{"q": "...", "targets": ["source.md"]}]}`}. Targets are corpus source names.</p>
        <input className="ap-input" placeholder="Query set name" value={name} onChange={(e) => setName(e.target.value)} />
        <input type="file" accept=".json,application/json" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        {err && <div className="lab-error">{err}</div>}
        <div className="ap-modal-row">
          <button onClick={onClose} disabled={saving}>Cancel</button>
          <button className="primary" onClick={save} disabled={saving || !file || !name.trim()}>{saving ? "Uploading…" : "Create"}</button>
        </div>
      </div>
    </div>
  );
}
