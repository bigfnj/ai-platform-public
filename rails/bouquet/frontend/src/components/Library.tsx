import { useEffect, useState } from "react";
import { api, type FlowerDetail, type FlowerSummary } from "../lib/api";
import { Spinner, Markdown } from "./ui";

// Order the profile sections read naturally in the detail panel.
const SECTION_ORDER = [
  "Botanical identity", "Native region & origin", "Visual identification",
  "History & cultural background", "Symbolism & meaning",
  "Cultural meanings across the world", "Typical pairings",
  "Occasions & events", "Seasonality & availability", "Quick facts",
];

export default function Library({ openSlug, onClose }: {
  openSlug: string | null;
  onClose: () => void;
}) {
  const [flowers, setFlowers] = useState<FlowerSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(openSlug);

  useEffect(() => { api.flowers().then((r) => setFlowers(r.flowers)).catch(() => setFlowers([])); }, []);
  useEffect(() => { setSelected(openSlug); }, [openSlug]);

  if (flowers === null) return <div className="bq-empty"><Spinner /> &nbsp;Loading the flower library…</div>;

  const filtered = q.trim()
    ? flowers.filter((f) => (f.title + " " + f.oneliner).toLowerCase().includes(q.trim().toLowerCase()))
    : flowers;

  return (
    <div className="bq-library">
      <div className="bq-lib-bar">
        <input className="bq-input" placeholder={`Search ${flowers.length} flowers…`}
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      <div className="bq-grid">
        {filtered.map((f) => (
          <button key={f.slug} className="bq-flower-card" onClick={() => setSelected(f.slug)}>
            {f.thumb ? <img src={f.thumb} alt={f.title} loading="lazy" />
                     : <div className="bq-thumb-ph">💐</div>}
            <div className="bq-flower-cap">
              <div className="bq-flower-name">{f.title}</div>
              <div className="bq-flower-one">{f.oneliner}</div>
            </div>
          </button>
        ))}
        {filtered.length === 0 && <div className="bq-empty">No flowers match “{q}”.</div>}
      </div>

      {selected && <FlowerModal slug={selected} onClose={() => { setSelected(null); onClose(); }} />}
    </div>
  );
}

function FlowerModal({ slug, onClose }: { slug: string; onClose: () => void }) {
  const [detail, setDetail] = useState<FlowerDetail | null>(null);

  useEffect(() => {
    setDetail(null);
    api.flower(slug).then(setDetail).catch(() => setDetail(null));
  }, [slug]);

  return (
    <div className="bq-modal-backdrop" onClick={onClose}>
      <div className="bq-modal" onClick={(e) => e.stopPropagation()}>
        <button className="bq-modal-x" onClick={onClose} aria-label="Close">✕</button>
        {!detail ? (
          <div className="bq-empty"><Spinner /> &nbsp;Loading…</div>
        ) : (
          <>
            <h2 className="bq-modal-title">{detail.title}</h2>
            {detail.oneliner && <p className="bq-modal-one">{detail.oneliner}</p>}
            {detail.images.length > 0 && (
              <div className="bq-modal-imgs">
                {detail.images.map((im) => (
                  <figure key={im.file}>
                    <img src={im.url} alt={detail.title} loading="lazy" />
                    <figcaption>{im.author} · {im.license}</figcaption>
                  </figure>
                ))}
              </div>
            )}
            <div className="bq-sections">
              {SECTION_ORDER.filter((s) => detail.sections[s]).map((s) => (
                <section key={s}>
                  <h3>{s}</h3>
                  <Markdown text={detail.sections[s]} />
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
