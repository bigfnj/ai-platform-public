// RAG-over-documents demo. Three columns: sample questions (left), ask + streamed
// cited answer (middle), retrieved sources (right). The answer streams token-by-token
// over the rail's WebSocket; generation flips between the local GPU (broker) and NVIDIA
// NIM, while retrieval always runs locally on bge-m3 through the broker.
import { useEffect, useRef, useState } from "react";
import { deleteCorpus, getJSON, postJSON, uploadCorpus, wsURL } from "../../api";

export type NimInfo = { available: boolean; endpoint: string; chat_model: string };
export type GenInfo = { role: string; model: string; label: string; is_nvidia: boolean; gpu: string };
type Corpus = { id: number; slug: string; name: string; kind: string; owner: string | null; chunks: number };
type Source = { n: number; source: string; score: number; text: string };
type Sw = { open: boolean; steps: string[]; done: boolean; ok: boolean; msg: string };

const SAMPLES = [
  "What is NVIDIA NIM and why is the OpenAI-compatible API useful?",
  "How do I serve a fine-tuned LLM in production on NVIDIA?",
  "Where does reranking fit in a RAG pipeline, and why does it matter?",
  "How can I share one GPU across several models for multiple tenants?",
  "What is NVIDIA Dynamo and how does it relate to Triton?",
  "How would a GSI take a RAG prototype to production on NVIDIA?",
  "What is physical AI, and what are Omniverse, Isaac, and Cosmos for?",
  "What does NVIDIA AI Enterprise include, and why do regulated clients care?",
  "Where do build.nvidia.com, NGC, and DLI fit for a new developer?",
  "What are NVIDIA AI Blueprints, and is there one for enterprise RAG?",
];

const SW0: Sw = { open: false, steps: [], done: false, ok: false, msg: "" };

// Light inline formatting: **bold** -> <strong>, everything else literal (newlines are
// preserved by white-space:pre-wrap on the container).
function fmt(s: string): React.ReactNode[] {
  return s.split(/\*\*/).map((p, i) => (i % 2 === 1 ? <strong key={i}>{p}</strong> : <span key={i}>{p}</span>));
}

// Render answer text with clickable citations. Models vary in bracket style
// ([2], 【2】, [1,4]), so normalize fullwidth brackets and accept comma lists.
function renderRich(text: string, onCite: (n: number) => void): React.ReactNode[] {
  const norm = text.replace(/【/g, "[").replace(/】/g, "]");
  const re = /\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]/g;
  const out: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(norm))) {
    if (m.index > last) out.push(<span key={key++}>{fmt(norm.slice(last, m.index))}</span>);
    for (const raw of m[1].split(",")) {
      const n = parseInt(raw.trim(), 10);
      out.push(
        <button key={key++} className="cite" onClick={() => onCite(n)} title={`jump to source [${n}]`}>
          [{n}]
        </button>
      );
    }
    last = re.lastIndex;
  }
  if (last < norm.length) out.push(<span key={key++}>{fmt(norm.slice(last))}</span>);
  return out;
}

export default function RagDemo({ nim, gen }: { nim: NimInfo; gen: GenInfo | null }) {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [corpusId, setCorpusId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [backend, setBackend] = useState<"local" | "nim">("local");
  const [highlight, setHighlight] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sw, setSw] = useState<Sw>(SW0);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadFiles, setUploadFiles] = useState<FileList | null>(null);
  const [uploading, setUploading] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const loadCorpora = () =>
    getJSON<{ corpora: Corpus[] }>("/api/rag/corpora").then((d) => {
      setCorpora(d.corpora);
      setCorpusId((prev) => prev ?? d.corpora[0]?.id ?? null);
    });

  useEffect(() => {
    loadCorpora().catch(() => setError("could not reach the AI Playground backend"));
    return () => wsRef.current?.close();
  }, []);

  const selected = corpora.find((c) => c.id === corpusId) || null;

  function ask(qArg?: string) {
    const q = (qArg ?? question).trim();
    if (!q || streaming || corpusId == null) return;
    setQuestion(q);
    setAnswer("");
    setSources([]);
    setError(null);
    setStreaming(true);
    const ws = new WebSocket(wsURL("/ws/rag"));
    wsRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ question: q, corpus: corpusId, backend }));
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "sources") setSources(m.sources);
      else if (m.type === "token") setAnswer((a) => a + m.text);
      else if (m.type === "done") { setStreaming(false); ws.close(); }
      else if (m.type === "error") { setError(m.message); setStreaming(false); ws.close(); }
    };
    ws.onerror = () => { setError("WebSocket connection error"); setStreaming(false); };
    ws.onclose = () => setStreaming(false);
  }

  function cite(n: number) {
    setHighlight(n);
    const el = document.querySelector(`.ai-playground .srccard[data-n="${n}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.setTimeout(() => setHighlight((h) => (h === n ? null : h)), 1600);
  }

  async function doUpload() {
    if (!uploadFiles || uploadFiles.length === 0 || !uploadName.trim()) return;
    setUploading(true);
    try {
      const res = await uploadCorpus(uploadName.trim(), uploadFiles);
      await loadCorpora();
      setCorpusId(res.id);
      setUploadOpen(false);
      setUploadName("");
      setUploadFiles(null);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setUploading(false);
    }
  }

  async function removeCorpus() {
    if (!selected || selected.kind !== "user") return;
    await deleteCorpus(selected.id).catch(() => {});
    setCorpusId(null);
    await loadCorpora().catch(() => {});
  }

  async function connectNim() {
    setSw({ open: true, steps: [], done: false, ok: false, msg: "" });
    const step = (s: string, d = 350) =>
      new Promise<void>((r) => {
        setSw((p) => ({ ...p, steps: [...p.steps, s] }));
        window.setTimeout(r, d);
      });
    try {
      await step("Connecting to the NVIDIA API Catalog (build.nvidia.com)…");
      await step("Passing your API key…");
      await postJSON("/api/nim/probe", {}); // real auth check against the hosted endpoint
      await step("Authenticated — key accepted ✓");
      await step(`Enabling NIM — generation → ${nim.chat_model}`, 250);
      setBackend("nim");
      setSw((p) => ({ ...p, done: true, ok: true, msg: "You are now connected to NVIDIA NIM" }));
    } catch (e: any) {
      setBackend("local");
      setSw((p) => ({
        ...p, done: true, ok: false,
        msg: String(e?.message || e).slice(0, 160) + "  (staying local)",
      }));
    }
  }

  function toggleNim() {
    if (backend === "nim") { setBackend("local"); return; }
    if (nim.available) connectNim();
  }

  return (
    <div className="rag">
      <div className="rag-controls">
        <label className="rag-corpus" title="The document set the answer is grounded in — retrieval searches these chunks for each question. Pick a seed corpus or upload your own docs.">
          <span>Corpus</span>
          <select
            value={corpusId ?? ""}
            onChange={(e) => setCorpusId(e.target.value ? Number(e.target.value) : null)}
          >
            {corpora.length === 0 && <option value="">indexing…</option>}
            {corpora.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} · {c.chunks} chunks{c.kind === "user" ? " (yours)" : ""}
              </option>
            ))}
          </select>
        </label>
        {selected?.kind === "user" && (
          <button className="rag-del" title="Delete this corpus" onClick={removeCorpus}>✕</button>
        )}
        <button className="rag-upload-btn" onClick={() => setUploadOpen(true)}
          title="Add .md or .txt files as a new corpus you can ask questions over.">＋ Upload docs</button>

        <span className="rag-spacer" />

        <span className="rag-endpoint">
          {backend === "nim" ? (
            <>generation <b>{nim.chat_model}</b> · <b>NVIDIA NIM</b> (cloud)</>
          ) : (
            <>generation <b>{gen?.label ?? "NVIDIA Nemotron"}</b>
              {gen?.model ? <span className="rag-genmodel"> {gen.model}</span> : null}
              {" "}on <b>{gen?.gpu ?? "NVIDIA RTX 4090"}</b> · broker</>
          )}{" "}
          · retrieval <b>bge-m3</b> (local)
        </span>
        <button
          className="rag-nim"
          onClick={toggleNim}
          disabled={backend === "local" && !nim.available}
          title={
            backend === "nim"
              ? "Connected to NVIDIA NIM — click to return to local"
              : nim.available
              ? "Connect generation to NVIDIA NIM"
              : "Add an NVIDIA key to deploy/.env to enable"
          }
        >
          <span className={"dot" + (backend === "nim" ? " on" : "")} />
          {backend === "nim" ? "Switch to local GPU" : "Switch to NVIDIA NIM"}
        </button>
        <span className={"rag-pill" + (backend === "nim" ? " enabled" : "")}>
          {backend === "nim" ? "NVIDIA NIM enabled" : "Local GPU"}
        </span>
      </div>

      <div className="rag-layout">
        <aside className="rag-side">
          <div className="side-h">Sample questions</div>
          <div className="side-list">
            {SAMPLES.map((s) => (
              <button className="chip" key={s} onClick={() => ask(s)} disabled={streaming}>
                {s}
              </button>
            ))}
          </div>
        </aside>

        <div className="rag-main">
          <div className="askbar">
            <input
              value={question}
              placeholder="Ask a question about the selected corpus…"
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") ask(); }}
            />
            <button onClick={() => ask()} disabled={streaming || corpusId == null}>
              {streaming ? "…" : "Ask"}
            </button>
          </div>

          {error && <div className="rag-error">{error}</div>}

          <div className="answer card">
            {answer ? (
              <>
                {renderRich(answer, cite)}
                {streaming && <span className="caret">▍</span>}
              </>
            ) : (
              <span className="ans-empty">
                {streaming
                  ? "Retrieving passages and generating a grounded answer…"
                  : "Ask a question to run the pipeline → embed → retrieve → generate."}
              </span>
            )}
          </div>
        </div>

        <aside className="rag-sources">
          <div className="side-h">Retrieved sources</div>
          <div className="src">
            {sources.length === 0 ? (
              <div className="src-empty">
                Ask a question — the passages the model used will appear here.
              </div>
            ) : (
              sources.map((s) => (
                <div key={s.n} className={"srccard" + (highlight === s.n ? " hot" : "")} data-n={s.n}>
                  <div className="srchead">
                    <span className="src-n">[{s.n}]</span>
                    <span className="src-name">{s.source}</span>
                    <span className="src-score">cosine {s.score.toFixed(3)}</span>
                  </div>
                  <div className="src-body">{s.text}</div>
                </div>
              ))
            )}
          </div>
        </aside>
      </div>

      {uploadOpen && (
        <div className="ap-modal-back" onClick={() => !uploading && setUploadOpen(false)}>
          <div className="ap-modal" onClick={(e) => e.stopPropagation()}>
            <h3>Upload documents</h3>
            <p className="hint">Add .md or .txt files to create a new corpus you can RAG over.</p>
            <input
              className="ap-input"
              placeholder="Corpus name (e.g. My Project Docs)"
              value={uploadName}
              onChange={(e) => setUploadName(e.target.value)}
            />
            <input
              type="file"
              accept=".md,.txt,text/plain,text/markdown"
              multiple
              onChange={(e) => setUploadFiles(e.target.files)}
            />
            <div className="ap-modal-row">
              <button onClick={() => setUploadOpen(false)} disabled={uploading}>Cancel</button>
              <button
                className="primary"
                disabled={uploading || !uploadName.trim() || !uploadFiles?.length}
                onClick={doUpload}
              >
                {uploading ? "Embedding…" : "Create corpus"}
              </button>
            </div>
          </div>
        </div>
      )}

      {sw.open && (
        <div className="ap-modal-back">
          <div className="ap-modal sw">
            <h3>{sw.done && sw.ok ? "Connected" : "Connecting to NVIDIA NIM"}</h3>
            <ul className="sw-steps">
              {sw.steps.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
            {sw.done && (
              <div className={"sw-final" + (sw.ok ? " ok" : " err")}>
                <span className="sw-dot" />
                {sw.msg}
              </div>
            )}
            {sw.done && (
              <div className="ap-modal-row">
                <button className="primary" onClick={() => setSw((p) => ({ ...p, open: false }))}>OK</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
