import { useId } from "react";
import type { FlowerSummary } from "../lib/api";

// A flower-name field with type-ahead over the 50 KB flowers (a native datalist,
// so keyboard + a11y come for free). Correcting a name here re-links the line to a
// profile — the parent re-resolves it (alias-aware) to update the in-library flag.
export function FlowerLineInput({ value, onChange, options, placeholder }: {
  value: string;
  onChange: (v: string) => void;
  options: FlowerSummary[];
  placeholder?: string;
}) {
  const listId = useId();
  return (
    <>
      <input
        className="bq-input bq-fl-name"
        value={value}
        list={listId}
        placeholder={placeholder || "flower name"}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {options.map((o) => <option key={o.slug} value={o.title} />)}
      </datalist>
    </>
  );
}
