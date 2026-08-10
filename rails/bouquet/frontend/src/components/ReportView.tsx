import { Card, ConfidenceBadge, Markdown } from "./ui";
import type { Inventory } from "../lib/api";

// The shared rendering of one analysis — used both for a fresh result (Analyze)
// and a saved one (History). `matchedSlugs` are the flowers found in the KB;
// clicking one jumps to its Library profile.
export interface ReportData {
  title: string;
  mode: "analysis" | "florist";
  image_url: string | null;
  inventory: Inventory;
  matched_slugs: string[];
  unprofiled: string[];
  report_md: string;
  model?: string;
}

export function ReportView({ data, onOpenFlower }: {
  data: ReportData; onOpenFlower: (slug: string) => void;
}) {
  const inv = data.inventory || ({} as Inventory);
  const flowers = inv.flowers || [];
  return (
    <div className="bq-report">
      <div className="bq-report-grid">
        <div className="bq-report-side">
          {data.image_url && (
            <img className="bq-photo" src={data.image_url} alt="analyzed bouquet" />
          )}
          <Card className="bq-inv">
            <div className="bq-inv-head">
              <span className={"bq-mode bq-mode-" + data.mode}>
                {data.mode === "florist" ? "Florist copy" : "Analysis"}
              </span>
            </div>
            {(inv.palette || inv.arrangement) && (
              <div className="bq-inv-meta">
                {inv.palette && <div><b>Palette</b> · {inv.palette}</div>}
                {inv.arrangement && <div><b>Form</b> · {inv.arrangement}</div>}
                {inv.context && <div><b>Context</b> · {inv.context}</div>}
                {inv.greenery?.length ? <div><b>Greenery</b> · {inv.greenery.join(", ")}</div> : null}
              </div>
            )}
            {flowers.length > 0 && (
              <>
                <div className="bq-inv-label">Identified</div>
                <ul className="bq-flist">
                  {flowers.map((f, i) => (
                    <li key={i}>
                      <span className="bq-fname">{f.name}</span>
                      {f.colors?.length ? <span className="bq-fcolors"> · {f.colors.join(", ")}</span> : null}
                      {f.confidence ? <ConfidenceBadge level={f.confidence} /> : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {data.matched_slugs.length > 0 && (
              <>
                <div className="bq-inv-label">In the library</div>
                <div className="bq-chips">
                  {data.matched_slugs.map((s) => (
                    <button key={s} className="bq-chip bq-chip-link" onClick={() => onOpenFlower(s)}>
                      {s.replace(/-/g, " ")}
                    </button>
                  ))}
                </div>
              </>
            )}
            {data.unprofiled.length > 0 && (
              <>
                <div className="bq-inv-label">Not yet profiled</div>
                <div className="bq-chips">
                  {data.unprofiled.map((n, i) => <span key={i} className="bq-chip bq-chip-muted">{n}</span>)}
                </div>
              </>
            )}
          </Card>
        </div>
        <Card className="bq-report-body">
          <Markdown text={data.report_md} />
        </Card>
      </div>
    </div>
  );
}
