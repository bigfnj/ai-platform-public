// Co-Worker — federated React module for the platform shell. Exposes ./module.
// Placeholder scaffold: displays inbox items dropped by the co-work harvest process.
// Schema is open — whatever JSON the harvest process writes, this renders.
import { useEffect, useState } from 'react'

interface InboxItem {
  _id: string
  _mtime: number
  [key: string]: unknown
}

const CSS = `
.cw { --i:var(--text-primary,#e6edf3); --mut:var(--text-secondary,#8b98a9);
  --s1:var(--surface-1,#0f141b); --s2:var(--surface-2,#161c26); --bd:var(--border,#263042);
  --ac:var(--accent,#2a78d6); color:var(--i);
  padding: 24px; min-height: calc(100vh - 132px); }
.cw h2 { margin: 0 0 6px; font-size: 20px; font-weight: 700; }
.cw .sub { color: var(--mut); font-size: 13.5px; margin-bottom: 24px; }
.cw .empty { color: var(--mut); font-size: 14px; border: 1px dashed var(--bd);
  border-radius: 12px; padding: 40px; text-align: center; }
.cw .empty code { font-size: 12px; background: var(--s2); padding: 2px 7px;
  border-radius: 5px; border: 1px solid var(--bd); }
.cw .grid { display: grid; gap: 14px;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.cw .card { background: var(--s2); border: 1px solid var(--bd); border-radius: 12px;
  padding: 16px 18px; display: flex; flex-direction: column; gap: 6px; }
.cw .card .ts { font-size: 11.5px; color: var(--mut); }
.cw .card pre { font: inherit; font-size: 12.5px; white-space: pre-wrap;
  word-break: break-word; margin: 0; color: var(--mut); }
`

function fmt(mtime: number) {
  return new Date(mtime * 1000).toLocaleString()
}

export default function CoWorkerModule() {
  const [items, setItems] = useState<InboxItem[]>([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')

  useEffect(() => {
    const el = document.createElement('style')
    el.textContent = CSS
    document.head.appendChild(el)
    return () => { document.head.removeChild(el) }
  }, [])

  useEffect(() => {
    fetch('/co-worker/api/inbox')
      .then((r) => r.json())
      .then((d) => { setItems(d.items ?? []); setLoading(false) })
      .catch((e) => { setErr(String(e)); setLoading(false) })
  }, [])

  const refresh = () => {
    setLoading(true); setErr('')
    fetch('/co-worker/api/inbox')
      .then((r) => r.json())
      .then((d) => { setItems(d.items ?? []); setLoading(false) })
      .catch((e) => { setErr(String(e)); setLoading(false) })
  }

  return (
    <div className="cw">
      <h2>💼 Co-Worker</h2>
      <div className="sub">
        Harvested email &amp; calendar items — reminders, follow-ups, FYIs.&nbsp;
        <button style={{ font: 'inherit', fontSize: 12.5, padding: '3px 10px', borderRadius: 7,
          border: '1px solid var(--bd)', background: 'transparent', color: 'var(--i)', cursor: 'pointer' }}
          onClick={refresh} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {err && <div style={{ color: 'var(--danger,#f85149)', marginBottom: 16 }}>{err}</div>}

      {!loading && items.length === 0 && (
        <div className="empty">
          No items yet. Drop a <code>.json</code> file into the inbox directory to get started.
        </div>
      )}

      {items.length > 0 && (
        <div className="grid">
          {items.map((item) => {
            const { _id, _mtime, _file: _f, ...rest } = item
            return (
              <div key={_id} className="card">
                <div className="ts">{fmt(_mtime)}</div>
                <pre>{JSON.stringify(rest, null, 2)}</pre>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
