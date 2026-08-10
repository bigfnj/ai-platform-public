import { useEffect } from "react";
import { api, type FlowerSummary } from "../lib/api";
import { Card } from "./ui";
import { FlowerLineInput } from "./FlowerLineInput";

// The human-in-the-loop step: review + correct the vision draft before writing.
// Each flower is an editable line (name type-ahead + colors) with an in-library
// indicator; palette + arrangement (which shape the copy) are editable too.

export interface EditFlower {
  id: string;
  name: string;
  colors: string;          // comma-separated while editing; split at generate
  slug: string | null;
  in_library: boolean;
}

export interface EditState {
  flowers: EditFlower[];
  palette: string;
  arrangement: string;
}

let _seq = 0;
export function newFlower(name = "", colors = ""): EditFlower {
  return { id: `f${Date.now()}-${_seq++}`, name, colors, slug: null, in_library: false };
}

// One editable flower line. Debounce-resolves the (possibly edited/added) name
// against the KB — alias-aware, matching the server — to refresh the in-library flag.
function Row({ f, options, onChange, onRemove }: {
  f: EditFlower;
  options: FlowerSummary[];
  onChange: (f: EditFlower) => void;
  onRemove: () => void;
}) {
  useEffect(() => {
    const name = f.name.trim();
    if (!name) {
      if (f.in_library || f.slug) onChange({ ...f, slug: null, in_library: false });
      return;
    }
    let alive = true;
    const t = setTimeout(async () => {
      try {
        const r = await api.resolve(name);
        if (alive && (r.in_library !== f.in_library || r.slug !== f.slug)) {
          onChange({ ...f, slug: r.slug, in_library: r.in_library });
        }
      } catch { /* leave the flag as-is on a transient failure */ }
    }, 350);
    return () => { alive = false; clearTimeout(t); };
    // Only re-resolve when the name changes; onChange/f identity churn must not refire.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.name]);

  const typed = f.name.trim().length > 0;
  return (
    <div className="bq-fl-row">
      <FlowerLineInput value={f.name} options={options}
        onChange={(name) => onChange({ ...f, name })} />
      <input className="bq-input bq-fl-colors" value={f.colors} placeholder="colors"
        autoComplete="off" onChange={(e) => onChange({ ...f, colors: e.target.value })} />
      <span className={"bq-fl-status " + (typed ? (f.in_library ? "in" : "out") : "none")}>
        {typed ? (f.in_library ? "✓ in library" : "not profiled") : ""}
      </span>
      <button className="bq-fl-x" title="Remove flower" onClick={onRemove}>✕</button>
    </div>
  );
}

export function InventoryEditor({ value, onChange, options }: {
  value: EditState;
  onChange: (s: EditState) => void;
  options: FlowerSummary[];
}) {
  const setFlower = (id: string, nf: EditFlower) =>
    onChange({ ...value, flowers: value.flowers.map((x) => (x.id === id ? nf : x)) });
  const removeFlower = (id: string) =>
    onChange({ ...value, flowers: value.flowers.filter((x) => x.id !== id) });
  const addFlower = () =>
    onChange({ ...value, flowers: [...value.flowers, newFlower()] });

  return (
    <Card className="bq-editor">
      <div className="bq-inv-label">Flowers in this bouquet</div>
      {value.flowers.length === 0 && (
        <p className="bq-note">No flowers yet — add the ones you see below.</p>
      )}
      <div className="bq-fl-list">
        {value.flowers.map((f) => (
          <Row key={f.id} f={f} options={options}
            onChange={(nf) => setFlower(f.id, nf)} onRemove={() => removeFlower(f.id)} />
        ))}
      </div>
      <button className="bq-btn bq-add-flower" onClick={addFlower}>+ Add flower</button>

      <div className="bq-editor-grid">
        <label className="bq-field">
          <span>Palette</span>
          <input className="bq-input" value={value.palette} placeholder="the overall color story"
            onChange={(e) => onChange({ ...value, palette: e.target.value })} />
        </label>
        <label className="bq-field">
          <span>Arrangement / form</span>
          <input className="bq-input" value={value.arrangement} placeholder="e.g. loose hand-tied posy"
            onChange={(e) => onChange({ ...value, arrangement: e.target.value })} />
        </label>
      </div>
    </Card>
  );
}
