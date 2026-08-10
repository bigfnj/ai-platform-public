import { useEffect, useState } from "react";
import { api, type AnalysisDetail, type AnalysisListItem } from "../lib/api";
import { Spinner } from "./ui";
import { ReportView } from "./ReportView";
import { DescriptionCard } from "./DescriptionCard";
import { ConfirmDialog } from "./ConfirmDialog";

export default function History({ reloadKey, onOpenFlower }: {
  reloadKey: number;
  onOpenFlower: (slug: string) => void;
}) {
  const [items, setItems] = useState<AnalysisListItem[] | null>(null);
  const [open, setOpen] = useState<AnalysisDetail | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const load = () => api.analyses().then((r) => setItems(r.analyses)).catch(() => setItems([]));
  useEffect(() => { load(); }, [reloadKey]);

  const doDelete = async () => {
    const id = confirmId;
    if (id == null) return;
    setConfirmId(null);
    await api.deleteAnalysis(id);
    if (open?.id === id) setOpen(null);
    load();
  };

  const confirmDialog = confirmId != null && (
    <ConfirmDialog
      title="Delete this saved piece?"
      body="This can't be undone."
      confirmLabel="Delete" danger
      onConfirm={doDelete} onCancel={() => setConfirmId(null)}
    />
  );

  if (items === null) return <div className="bq-empty"><Spinner /> &nbsp;Loading saved work…</div>;

  if (open) {
    return (
      <div>
        <div className="bq-result-bar">
          <button className="bq-btn" onClick={() => setOpen(null)}>← All saved</button>
          <span className="bq-result-title">{open.title}</span>
          <span className="bq-spacer" />
          <span className="bq-when">{new Date(open.created_at).toLocaleString()}</span>
          <button className="bq-btn" onClick={() => setConfirmId(open.id)}>Delete</button>
        </div>
        {open.mode === "florist"
          ? <DescriptionCard imageUrl={open.image_url} text={open.report_md} guidance={open.guidance} />
          : <ReportView data={{ ...open, matched_slugs: open.matched }} onOpenFlower={onOpenFlower} />}
        {confirmDialog}
      </div>
    );
  }

  if (items.length === 0) {
    return <div className="bq-empty">Nothing saved yet. Analyze a bouquet to start your library.</div>;
  }

  return (
    <div className="bq-history">
      {items.map((a) => (
        <div key={a.id} className="bq-hist-card" role="button" tabIndex={0}
          onClick={() => api.analysis(a.id).then(setOpen)}
          onKeyDown={(e) => { if (e.key === "Enter") api.analysis(a.id).then(setOpen); }}>
          {a.image_url ? <img src={a.image_url} alt="" loading="lazy" /> : <div className="bq-thumb-ph">💐</div>}
          <div className="bq-hist-body">
            <div className="bq-hist-title">{a.title}</div>
            <div className="bq-hist-meta">
              <span className={"bq-mode bq-mode-" + a.mode}>{a.mode === "florist" ? "Description" : "Analysis"}</span>
              <span className="bq-when">{new Date(a.created_at).toLocaleDateString()}</span>
              {a.matched.length > 0 && <span className="bq-muted">{a.matched.length} in library</span>}
            </div>
          </div>
          <button className="bq-hist-del" title="Delete"
            onClick={(e) => { e.stopPropagation(); setConfirmId(a.id); }}>🗑</button>
        </div>
      ))}
      {confirmDialog}
    </div>
  );
}
