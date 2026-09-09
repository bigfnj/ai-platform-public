// AI Playground as a federated React module for the platform shell. Exposes ./module.
// No own top bar / theme — the shell provides those; this renders inside an
// `.ai-playground` wrapper and adopts the shell's data-theme via shared tokens.
import { useEffect, useState } from "react";
import { RailHeader, ModelChips, type ModelChip } from "@web-core";
import { getJSON } from "./api";
import RagDemo, { type NimInfo, type GenInfo } from "./demos/rag/RagDemo";
import EmbedBenchDemo from "./demos/embed-bench/EmbedBenchDemo";
import "./theme.css";

type Demo = { id: string; title: string; icon: string; blurb: string; status: string };
type Caps = { broker: "ok" | "unreachable"; models: ModelChip[] };

export default function AiPlaygroundModule() {
  const [demos, setDemos] = useState<Demo[]>([]);
  const [active, setActive] = useState<string>("rag");
  const [nim, setNim] = useState<NimInfo>({ available: false, endpoint: "", chat_model: "" });
  const [gen, setGen] = useState<GenInfo | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [caps, setCaps] = useState<Caps | null>(null);

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

  // Header chips: poll capabilities so the four-state dots track residency (and the resolved
  // model tracks the admin Rails panel) without a reload. Same 6s cadence as the other rails.
  useEffect(() => {
    let live = true;
    const load = () =>
      getJSON<Caps>("/api/capabilities")
        .then((c) => { if (live) setCaps(c); })
        .catch(() => { /* keep last-known rather than blanking the header */ });
    load();
    const id = window.setInterval(load, 6000);
    return () => { live = false; window.clearInterval(id); };
  }, []);

  return (
    <div className="ai-playground">
      <RailHeader
        icon={
          <svg width={22} height={22} viewBox="0 0 32 32" aria-hidden="true" style={{ display: "block" }}>
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
        }
        title="AI Playground"
        subtitle="a home for AI demos on the shared GPU broker"
        chips={
          <ModelChips
            status={caps ? (caps.broker === "ok" ? "ok" : "unreachable") : "checking"}
            models={caps?.models}
          />
        }
        actions={
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
        }
      />

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
