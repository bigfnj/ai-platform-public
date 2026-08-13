// Co-Worker — federated React module for the platform shell. Exposes ./module.
// Renders the harvest inbox as a triage dashboard: KPI strip, filters, priority-ordered
// cards, drill-through to the full narrative brief. No own top bar / theme — the shell
// provides those; this renders inside a `.co-worker` wrapper and adopts the shell's
// palette via shared tokens (see web/THEMING.md).
//
// Item contract: rails/co-worker/SCHEMA.md. Unknown fields are ignored rather than fatal;
// a schema-version mismatch and any file the backend couldn't parse both surface as
// visible warnings, because silent degradation is the failure mode that matters here.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getJSON, patchJSON } from './api'
import { renderInline, renderMarkdown } from './markdown'
import BriefView from './BriefView'
import {
  SCHEMA_VERSION,
  SOURCES,
  PRIORITY_LABEL,
  typeMeta,
  priorityOf,
  periodLabel,
  relatedEdges,
  metricDelta,
  deltaIsGood,
  isCorrection,
  type Competing,
  type DocResponse,
  type InboxResponse,
  type Item,
  type Metric,
  type RelType,
  type SkippedFile,
  type Source,
  type Status,
} from './types'
import './theme.css'

type SortKey = 'priority' | 'newest' | 'when'

const REL_LABEL: Record<RelType, string> = {
  'relates-to': '↔',
  'answers': '→ answers',
  'derives-from': '← from',
  'duplicates': '≡ dup',
  'supersedes': '⇒ supersedes',
  'retracts': '✕ retracts',
  'blocks': '⛔ blocks',
}

function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function fmtMtime(mtime: number): string {
  return new Date(mtime * 1000).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function haystack(i: Item): string {
  return [i.title, i.why, i.body, i.from, (i.tags || []).join(' ')]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
}

// --- drill-through modal ----------------------------------------------------

function DocModal({ path, onClose }: { path: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DocResponse | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    let live = true
    getJSON<DocResponse>(`/api/doc/${path}`)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setErr(String(e)))
    return () => {
      live = false
    }
  }, [path])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="cw-backdrop" onClick={onClose} role="presentation">
      <div className="cw-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={path}>
        <div className="cw-modal-head">
          <strong>{path}</strong>
          {doc && <span className="cw-modal-sub">updated {fmtMtime(doc.mtime)}</span>}
          <span className="cw-spacer" />
          <button className="cw-btn" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="cw-modal-body">
          {err && <div className="cw-err">Couldn't load the brief: {err}</div>}
          {!doc && !err && <p className="cw-sub">Loading…</p>}
          {doc && <div className="cw-doc">{renderMarkdown(doc.content)}</div>}
        </div>
      </div>
    </div>
  )
}

// --- metrics strip ----------------------------------------------------------

function MetricsStrip({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="cw-metrics">
      {metrics.map((m, i) => {
        const delta = metricDelta(m)
        const good = deltaIsGood(m)
        const corr = isCorrection(m)
        const sign = delta !== null && delta > 0 ? '+' : ''
        return (
          <div key={i} className="cw-metric">
            <span className="cw-metric-lbl">{m.label}</span>
            <span className="cw-metric-val">
              {m.value}
              {m.unit ? <span className="cw-metric-unit">&thinsp;{m.unit}</span> : null}
            </span>
            {delta !== null && !corr && (
              <span className={`cw-metric-delta${good === true ? ' good' : good === false ? ' bad' : ''}`}>
                {sign}{delta}{m.unit || ''}
              </span>
            )}
            {corr && typeof m.prev === 'number' && (
              <span className="cw-metric-delta corr">was&nbsp;{m.prev}{m.unit || ''}</span>
            )}
          </div>
        )
      })}
    </div>
  )
}

// --- competing options (conflict type) -------------------------------------

function CompetingSection({ items }: { items: Competing[] }) {
  return (
    <div className="cw-competing">
      {items.map((c, i) => (
        <div key={i} className={`cw-compete-item${c.verdict ? ' ' + c.verdict : ''}`}>
          <span className="cw-compete-lbl">{c.label}</span>
          {(c.start || c.end) && (
            <span className="cw-compete-time">
              {[c.start && fmtWhen(c.start), c.end && fmtWhen(c.end)].filter(Boolean).join(' – ')}
            </span>
          )}
          {c.verdict && (
            <span className={`cw-chip${c.verdict === 'take' ? ' good' : ''}`}>{c.verdict}</span>
          )}
        </div>
      ))}
    </div>
  )
}

// --- card -------------------------------------------------------------------

function Card({
  item,
  onStatus,
  onOpenDoc,
  archived = false,
}: {
  item: Item
  onStatus: (id: string, status: Status) => void
  onOpenDoc: (path: string) => void
  archived?: boolean
}) {
  const [open, setOpen] = useState(false)
  const p = priorityOf(item)
  const tm = typeMeta(item.type)
  const body = typeof item.body === 'string' ? item.body : ''
  const long = body.length > 260
  const links = Array.isArray(item.links) ? item.links : []
  const tags = Array.isArray(item.tags) ? item.tags : []
  const related = Array.isArray(item.related) ? item.related : []
  const metrics = Array.isArray(item.metrics) ? (item.metrics as Metric[]) : []
  const competing = Array.isArray(item.competing) ? (item.competing as Competing[]) : []
  const resolved = item._status === 'done' || item._status === 'dismissed'

  return (
    <div className={`cw-card p${p === 9 ? 4 : p}${resolved ? ' resolved' : ''}`}>
      <div className="cw-card-top">
        <span className={`cw-chip ${tm.tone}`}>{tm.label}</span>
        {item.client && <span className="cw-chip client">Client</span>}
        {item._status === 'done' && <span className="cw-chip done">Done</span>}
        {item._status === 'dismissed' && <span className="cw-chip">Dismissed</span>}
        <span className="cw-prio">{PRIORITY_LABEL[p] || 'unranked'}</span>
      </div>

      <h3>{item.title || item._id}</h3>

      {item.why && <div className="cw-why">{renderInline(item.why, `${item._id}-why`)}</div>}

      <div className="cw-meta">
        {item.when && <span>🕑 {fmtWhen(item.when)}</span>}
        {item.due && <span className="due">⏳ due {fmtWhen(item.due)}</span>}
        {item.from && <span>👤 {item.from}</span>}
        {item.period && <span title="grouping period">{periodLabel(item.period)}</span>}
      </div>

      {body && (
        <>
          <div className={`cw-body${long && !open ? ' clip' : ''}`}>{renderMarkdown(body)}</div>
          {long && (
            <div className="cw-acts">
              <button className="cw-more" onClick={() => setOpen((v) => !v)}>
                {open ? 'Show less' : 'Show more'}
              </button>
            </div>
          )}
        </>
      )}

      {item.evidence && <div className="cw-ev">Basis: {item.evidence}</div>}

      {tags.length > 0 && (
        <div className="cw-tags">
          {tags.map((t) => (
            <span className="cw-tag" key={t}>
              {t}
            </span>
          ))}
        </div>
      )}

      {related.length > 0 && (
        <div className="cw-rels">
          {relatedEdges(item).map((e, i) => (
            <span key={i} className="cw-rel-edge">
              <span className="cw-rel-type">{REL_LABEL[e.rel] ?? e.rel}</span>
              <code className="cw-rel-id">{e.id}</code>
            </span>
          ))}
        </div>
      )}

      {metrics.length > 0 && <MetricsStrip metrics={metrics} />}
      {competing.length > 0 && <CompetingSection items={competing} />}

      <div className="cw-acts">
        {links.map((l) => (
          <a className="cw-link" key={l.url} href={l.url} target="_blank" rel="noreferrer">
            {l.label} ↗
          </a>
        ))}
        {item.doc && (
          <button className="cw-more" onClick={() => onOpenDoc(String(item.doc))}>
            📄 Read the full brief
          </button>
        )}
        <span className="cw-spacer" />
        {!archived && (resolved ? (
          <button className="cw-tri" onClick={() => onStatus(item._id, 'open')} title="Move back to open">
            ↩ Reopen
          </button>
        ) : (
          <>
            <button className="cw-tri done" onClick={() => onStatus(item._id, 'done')}>
              ✓ Done
            </button>
            <button className="cw-tri" onClick={() => onStatus(item._id, 'dismissed')}>
              Dismiss
            </button>
          </>
        ))}
      </div>
    </div>
  )
}

// --- module -----------------------------------------------------------------

export default function CoWorkerModule() {
  const [items, setItems] = useState<Item[]>([])
  const [skipped, setSkipped] = useState<SkippedFile[]>([])
  const [inboxDir, setInboxDir] = useState('')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [patchErr, setPatchErr] = useState('')
  const [docPath, setDocPath] = useState<string | null>(null)

  const [view, setView] = useState<'brief' | 'inbox' | 'archive'>('brief')
  // Triage state lives here, not in the items array, so the brief and the card grid
  // stay in sync when you resolve something from either one.
  const [triaged, setTriaged] = useState<Record<string, Status>>({})
  const [period, setPeriod] = useState<string | null>(null)
  const [periods, setPeriods] = useState<string[]>([])
  const [source, setSource] = useState<Source | 'all'>('all')
  const [type, setType] = useState('all')
  const [clientOnly, setClientOnly] = useState(false)
  const [hideNoise, setHideNoise] = useState(true)
  const [hideResolved, setHideResolved] = useState(true)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('priority')

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    const endpoint = view === 'archive' ? '/api/archive' : '/api/inbox'
    getJSON<InboxResponse>(endpoint)
      .then((d) => {
        const list = Array.isArray(d.items) ? d.items : []
        setItems(list)
        setSkipped(Array.isArray(d.skipped) ? d.skipped : [])
        setInboxDir(d.inbox_dir || d.archive_dir || '')
        setPeriods(Array.isArray(d.periods) ? d.periods : [])
        // Server state wins on load, but keep any id we already know about so a
        // resolve made from the brief isn't forgotten when the grid loads later.
        setTriaged((cur) => {
          const next = { ...cur }
          for (const i of list) next[i._id] = i._status
          return next
        })
        setLoading(false)
      })
      .catch((e) => {
        setErr(String(e))
        setLoading(false)
      })
  }, [view])

  // The brief view needs triage state without loading the whole grid.
  useEffect(() => {
    if (view !== 'brief') {
      load()
      return
    }
    getJSON<InboxResponse>('/api/inbox')
      .then((d) => {
        const list = Array.isArray(d.items) ? d.items : []
        setTriaged((cur) => {
          const next = { ...cur }
          for (const i of list) next[i._id] = i._status
          return next
        })
      })
      .catch(() => { /* the brief still renders; triage marks just won't show */ })
  }, [view, load])

  const setStatus = useCallback((id: string, status: Status) => {
    setPatchErr('')
    // Optimistic: triage should feel instant. Roll back and explain if the write fails.
    const prevTriaged = triaged[id] ?? 'open'
    setTriaged((cur) => ({ ...cur, [id]: status }))
    setItems((cur) => cur.map((i) => (i._id === id ? { ...i, _status: status } : i)))
    patchJSON<{ _id: string; _status: Status }>(`/api/inbox/${id}`, { status }).catch((e) => {
      setTriaged((cur) => ({ ...cur, [id]: prevTriaged }))
      setItems((cur) => cur.map((i) => (i._id === id ? { ...i, _status: prevTriaged } : i)))
      setPatchErr(String(e instanceof Error ? e.message : e))
    })
  }, [triaged])

  const drift = useMemo(
    () => items.filter((i) => typeof i.schema === 'number' && i.schema !== SCHEMA_VERSION).length,
    [items],
  )

  const perSource = useMemo(() => {
    const m: Record<string, number> = {}
    for (const i of items) if (i.source) m[i.source] = (m[i.source] || 0) + 1
    return m
  }, [items])

  const types = useMemo(() => {
    const s = new Set<string>()
    for (const i of items) if (i.type) s.add(i.type)
    return Array.from(s).sort()
  }, [items])

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out = items.filter((i) => {
      if (source !== 'all' && i.source !== source) return false
      if (type !== 'all' && i.type !== type) return false
      if (period !== null && i.period !== period) return false
      if (clientOnly && !i.client) return false
      if (hideNoise && (i.type === 'noise' || priorityOf(i) === 5)) return false
      if (hideResolved && i._status !== 'open') return false
      if (needle && !haystack(i).includes(needle)) return false
      return true
    })
    const byWhen = (a: Item, b: Item) => {
      const av = a.when ? Date.parse(a.when) : Infinity
      const bv = b.when ? Date.parse(b.when) : Infinity
      return (Number.isNaN(av) ? Infinity : av) - (Number.isNaN(bv) ? Infinity : bv)
    }
    out.sort((a, b) => {
      if (sort === 'newest') return b._mtime - a._mtime
      if (sort === 'when') return byWhen(a, b) || b._mtime - a._mtime
      return priorityOf(a) - priorityOf(b) || byWhen(a, b) || b._mtime - a._mtime
    })
    return out
  }, [items, source, type, period, clientOnly, hideNoise, hideResolved, q, sort])

  const kpi = useMemo(() => {
    const open = items.filter((i) => i._status === 'open')
    return {
      p1: open.filter((i) => priorityOf(i) === 1).length,
      client: open.filter((i) => i.client).length,
      dangling: open.filter((i) => i.type === 'dangling').length,
      conflicts: open.filter((i) => i.type === 'conflict').length,
      periods: new Set(items.map((i) => i.period).filter(Boolean)).size,
      resolved: items.length - open.length,
    }
  }, [items])

  return (
    <div className="co-worker">
      <header className="cw-head">
        <span className="cw-logo">💼</span>
        <div className="cw-titles">
          <h1>Co-Worker</h1>
          <span className="cw-sub">
            Email, calendar &amp; Teams intelligence — harvested, ranked, client-first
          </span>
        </div>
        <span className="cw-spacer" />
        <div className="cw-view-toggle">
          <button
            className={'cw-tab' + (view === 'brief' ? ' on' : '')}
            onClick={() => setView('brief')}
          >
            🎯 Brief
          </button>
          <button
            className={'cw-tab' + (view === 'inbox' ? ' on' : '')}
            onClick={() => { setView('inbox'); setPeriod(null) }}
          >
            All items
          </button>
          <button
            className={'cw-tab' + (view === 'archive' ? ' on' : '')}
            onClick={() => { setView('archive'); setPeriod(null) }}
          >
            Archive
          </button>
        </div>
        {view !== 'brief' && (
          <button className="cw-btn primary" onClick={load} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        )}
      </header>

      {patchErr && <div className="cw-err">Triage change didn't save: {patchErr}</div>}

      {view === 'brief' && <BriefView triaged={triaged} onStatus={setStatus} />}

      {view !== 'brief' && <>

      {err && (
        <div className="cw-err">
          Couldn't reach the inbox API: {err}
          <br />
          Is the <code>co-worker</code> service up on :8860?
        </div>
      )}

      {skipped.length > 0 && (
        <div className="cw-err">
          <strong>
            {skipped.length} file{skipped.length > 1 ? 's' : ''} in the inbox couldn't be parsed
          </strong>{' '}
          and {skipped.length > 1 ? 'are' : 'is'} missing from this dashboard:
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {skipped.map((s) => (
              <li key={s.file}>
                <code>{s.file}</code> — {s.error}
              </li>
            ))}
          </ul>
          Run <code>python rails/co-worker/tools/validate_inbox.py</code> to diagnose.
        </div>
      )}

      {drift > 0 && (
        <div className="cw-note">
          {drift} item{drift > 1 ? 's' : ''} declare a schema version this dashboard doesn't know
          (expected {SCHEMA_VERSION}). They're still shown, but fields may not line up — check{' '}
          <code>rails/co-worker/SCHEMA.md</code> against the harvest prompts.
        </div>
      )}

      {!loading && !err && items.length > 0 && (
        <div className="cw-kpis">
          <div className="cw-kpi crit">
            <span className="n">{kpi.p1}</span>
            <span className="k">P1 · client blocking</span>
          </div>
          <div className="cw-kpi crit">
            <span className="n">{kpi.dangling}</span>
            <span className="k">Dangling promises</span>
          </div>
          <div className="cw-kpi warn">
            <span className="n">{kpi.conflicts}</span>
            <span className="k">Calendar conflicts</span>
          </div>
          <div className="cw-kpi good">
            <span className="n">{kpi.client}</span>
            <span className="k">Open client items</span>
          </div>
          <div className="cw-kpi">
            <span className="n">{items.length - kpi.resolved}</span>
            <span className="k">
              Open · {kpi.resolved} cleared · {kpi.periods} period{kpi.periods === 1 ? '' : 's'}
            </span>
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="cw-bar">
          <div className="cw-tabs">
            <button className={'cw-tab' + (source === 'all' ? ' on' : '')} onClick={() => setSource('all')}>
              All <span className="c">{items.length}</span>
            </button>
            {SOURCES.map((s) => (
              <button
                key={s.id}
                className={'cw-tab' + (source === s.id ? ' on' : '')}
                onClick={() => setSource(s.id)}
                disabled={!perSource[s.id]}
                title={perSource[s.id] ? undefined : 'No items from this loop yet'}
              >
                {s.icon} {s.label} <span className="c">{perSource[s.id] || 0}</span>
              </button>
            ))}
          </div>

          <span className="cw-spacer" />

          <input
            className="cw-search"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />

          <select className="cw-select" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="all">All types</option>
            {types.map((t) => (
              <option key={t} value={t}>
                {typeMeta(t as Item['type']).label}
              </option>
            ))}
          </select>

          <select className="cw-select" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            <option value="priority">Sort: priority</option>
            <option value="when">Sort: when it happens</option>
            <option value="newest">Sort: newest harvested</option>
          </select>

          {periods.length > 0 && (
            <select className="cw-select" value={period ?? ''} onChange={(e) => setPeriod(e.target.value || null)}>
              <option value="">All periods</option>
              {periods.map((p) => (
                <option key={p} value={p}>{periodLabel(p)}</option>
              ))}
            </select>
          )}

          <label>
            <input
              className="cw-check"
              type="checkbox"
              checked={clientOnly}
              onChange={(e) => setClientOnly(e.target.checked)}
            />
            Client only
          </label>
          <label>
            <input
              className="cw-check"
              type="checkbox"
              checked={hideNoise}
              onChange={(e) => setHideNoise(e.target.checked)}
            />
            Hide noise
          </label>
          <label>
            <input
              className="cw-check"
              type="checkbox"
              checked={hideResolved}
              onChange={(e) => setHideResolved(e.target.checked)}
            />
            Hide cleared
          </label>
        </div>
      )}

      {!loading && !err && items.length === 0 && (
        <div className="cw-empty">
          {view === 'archive' ? (
            <p>No archived items. Items move here after their active window expires.</p>
          ) : (
            <>
              <p>No harvested items yet.</p>
              <p>
                The scheduled co-work loops drop <code>.json</code> items into
                <br />
                <code>{inboxDir || '/data/inbox'}</code>
              </p>
              <p style={{ marginTop: 14 }}>
                Teams scan runs Mon 3a · Email daily 4a · Calendar Mon 5a · Insights Mon 6a
              </p>
            </>
          )}
        </div>
      )}

      {!loading && items.length > 0 && filtered.length === 0 && (
        <div className="cw-empty">
          Nothing matches these filters. {items.length} item{items.length > 1 ? 's' : ''} hidden.
        </div>
      )}

      {filtered.length > 0 && (
        <div className="cw-grid">
          {filtered.map((i) => (
            <Card key={i._id} item={i} onStatus={setStatus} onOpenDoc={setDocPath} archived={view === 'archive'} />
          ))}
        </div>
      )}

      </>}

      {docPath && <DocModal path={docPath} onClose={() => setDocPath(null)} />}
    </div>
  )
}
