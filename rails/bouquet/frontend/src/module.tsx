// Bouquet Builder as a federated React module for the platform shell. Exposes
// ./module. No own top bar / theme — the shell provides those; this renders inside
// a `.bouquet` wrapper and adopts the shell's data-theme via shared tokens.
import { useState } from "react";
import Analyze from "./components/Analyze";
import Library from "./components/Library";
import History from "./components/History";
import "./theme.css";

type Tab = "analyze" | "library" | "history";
const TABS: { id: Tab; label: string }[] = [
  { id: "analyze", label: "Analyze" },
  { id: "library", label: "Flower Library" },
  { id: "history", label: "History" },
];

export default function BouquetModule() {
  const [tab, setTab] = useState<Tab>("analyze");
  const [libSlug, setLibSlug] = useState<string | null>(null);
  const [historyKey, setHistoryKey] = useState(0);

  // Jump to a flower's Library profile (from an analysis's "in the library" chips).
  const openFlower = (slug: string) => { setLibSlug(slug); setTab("library"); };

  return (
    <div className="bouquet">
      <div className="bq-top">
        <span className="bq-title">💐 Bouquet Builder</span>
        <span className="bq-sub">photo → full flower report</span>
        <span className="bq-spacer" />
        <nav className="bq-tabs">
          {TABS.map((t) => (
            <button key={t.id} className={"bq-tab" + (tab === t.id ? " active" : "")}
              onClick={() => { if (t.id !== "library") setLibSlug(null); setTab(t.id); }}>
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      <main className="bq-main">
        {tab === "analyze" && (
          <Analyze onOpenFlower={openFlower} onSaved={() => setHistoryKey((k) => k + 1)} />
        )}
        {tab === "library" && (
          <Library openSlug={libSlug} onClose={() => setLibSlug(null)} />
        )}
        {tab === "history" && (
          <History reloadKey={historyKey} onOpenFlower={openFlower} />
        )}
      </main>
    </div>
  );
}
