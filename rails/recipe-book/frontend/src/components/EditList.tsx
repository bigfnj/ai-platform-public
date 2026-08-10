// Editable list of lines (ingredients / method steps). Add, edit, remove rows.
// Shared by the Add-recipe preview editor and the on-card Edit mode.
export function EditList({ items, onChange, ordered, multiline, placeholder }: {
  items: string[];
  onChange: (v: string[]) => void;
  ordered?: boolean;
  multiline?: boolean;
  placeholder?: string;
}) {
  const rows = items.length ? items : [""];
  const set = (i: number, v: string) => onChange(rows.map((x, n) => (n === i ? v : x)));
  const add = () => onChange([...rows, ""]);
  const remove = (i: number) => { const next = rows.filter((_, n) => n !== i); onChange(next.length ? next : [""]); };
  return (
    <div className="edit-list">
      {rows.map((it, i) => (
        <div className="edit-row" key={i}>
          <span className="er-num">{ordered ? `${i + 1}.` : "•"}</span>
          {multiline
            ? <textarea rows={1} value={it} placeholder={placeholder}
                onChange={(e) => set(i, e.target.value)} />
            : <input value={it} placeholder={placeholder} onChange={(e) => set(i, e.target.value)} />}
          <button className="er-x" type="button" title="Remove line" onClick={() => remove(i)}>×</button>
        </div>
      ))}
      <button className="btn ghost sm er-add" type="button" onClick={add}>＋ Add line</button>
    </div>
  );
}
