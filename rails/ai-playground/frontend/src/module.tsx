// AI Playground as a federated React module for the platform shell. Exposes ./module.
// No own top bar / theme — the shell provides those; this renders inside an
// `.ai-playground` wrapper and adopts the shell's data-theme via shared tokens.
import { useEffect, useState } from "react";
import { getJSON } from "./api";
import RagDemo, { type NimInfo, type GenInfo } from "./demos/rag/RagDemo";
import EmbedBenchDemo from "./demos/embed-bench/EmbedBenchDemo";
import "./theme.css";

type Demo = { id: string; title: string; icon: string; blurb: string; status: string };

export default function AiPlaygroundModule() {
  const [demos, setDemos] = useState<Demo[]>([]);
  const [active, setActive] = useState<string>("rag");
  const [nim, setNim] = useState<NimInfo>({ available: false, endpoint: "", chat_model: "" });
  const [gen, setGen] = useState<GenInfo | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  useEffect(() => {
    getJSON<{ demos: Demo[]; nim: NimInfo; gen: GenInfo }>("/api/demos")
      .then((d) => {
        setDemos(d.demos);
        setNim(d.nim);
        setGen(d.gen);
        if (d.demos[0]) setActive(d.demos[0].id);
      })
      .catch(() => {});
    // admin gates the Embedding Lab's model add/fetch/pull controls; null user (standalone) allowed.
    getJSON<{ user: string | null; is_admin: boolean }>("/api/whoami")
      .then((w) => setIsAdmin(w.is_admin || w.user === null))
      .catch(() => {});
  }, []);

  return (
    <div className="ai-playground">
      <header className="ap-head">
        <span className="ap-logo">
          <svg width={30} height={30} viewBox="0 0 32 32" aria-hidden="true" style={{ display: "block" }}>
            <rect x="1" y="26" width="30" height="5" rx="2.5" fill="#86cf68" />
            <path d="M18 14 C 11 16, 7 20, 6.5 26" fill="none" stroke="#ff9f43" strokeWidth="3" strokeLinecap="round" />
            <rect x="20" y="13" width="1.8" height="13" rx="0.9" fill="#2ab7c8" />
            <rect x="25.2" y="13" width="1.8" height="13" rx="0.9" fill="#2ab7c8" />
            <rect x="20" y="16" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="20" y="19.5" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="20" y="23" width="7" height="1.6" rx="0.8" fill="#2ab7c8" />
            <rect x="16" y="12" width="12" height="2.2" rx="1" fill="#e05572" />
            <path d="M15.5 12.5 L 22 6.5 L 28.5 12.5 Z" fill="#7b52c9" />
            <rect x="21.4" y="2.6" width="1.2" height="4.4" rx="0.6" fill="#5b6472" />
            <path d="M22.6 3 L 26.4 4.4 L 22.6 5.8 Z" fill="#e05572" />
          </svg>
        </span>
        <div className="ap-titles">
          <h1>AI Playground</h1>
          <span className="ap-sub">a home for AI demos on the shared GPU broker</span>
        </div>
        <span className="ap-spacer" />
        <nav className="ap-tabs" aria-label="demos">
          {demos.map((d) => (
            <button
              key={d.id}
              className={"ap-tab" + (active === d.id ? " active" : "")}
              onClick={() => setActive(d.id)}
              title={d.blurb}
              disabled={d.status !== "ready"}
            >
              <span className="ap-tab-ic">{d.icon}</span>
              {d.title}
            </button>
          ))}
        </nav>
      </header>

      {active === "rag" ? (
        <RagDemo nim={nim} gen={gen} />
      ) : active === "embed-bench" ? (
        <EmbedBenchDemo isAdmin={isAdmin} />
      ) : (
        <div className="ap-empty">This demo is coming soon.</div>
      )}
    </div>
  );
}
