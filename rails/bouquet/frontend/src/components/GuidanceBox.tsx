// Optional free-text direction for the writer ("For a wedding, tropical theme, keep
// it short."). Stays free-form; the chips just insert a starter phrase the florist
// can keep editing. The backend bounds this by the persona's factual + sensitivity
// rules, so a "sympathy" cue still trips the grief handling.

const CHIPS = ["Wedding", "Sympathy", "Anniversary", "Birthday", "Thank you", "Congratulations"];

export function GuidanceBox({ value, onChange }: {
  value: string;
  onChange: (v: string) => void;
}) {
  const insert = (label: string) => {
    const phrase = `For a ${label.toLowerCase()} arrangement. `;
    onChange(value ? value.replace(/\s*$/, " ") + phrase : phrase);
  };
  return (
    <div className="bq-guidance">
      <div className="bq-inv-label">Guidance for the description <span className="bq-muted">(optional)</span></div>
      <textarea
        className="bq-textarea"
        value={value}
        rows={3}
        placeholder="e.g. For a wedding, tropical theme, keep it short."
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="bq-chips bq-guidance-chips">
        {CHIPS.map((c) => (
          <button key={c} type="button" className="bq-chip bq-chip-link" onClick={() => insert(c)}>
            + {c}
          </button>
        ))}
      </div>
    </div>
  );
}
