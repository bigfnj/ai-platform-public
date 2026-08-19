// The landing view: a synthesized executive brief instead of 147 raw cards.
//
// Renders inbox/brief.json (produced by the synthesis pass — see synthesize.py).
// Attention items carry an `id` pointing back at an inbox item, so Done/Dismiss
// hits the same PATCH endpoint the card grid uses and the two views stay in sync.
import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON, postJSON } from './api'
import { renderInline, renderMarkdown } from './markdown'
import {
  CATEGORY_META,
  SOURCES,
  URGENCY_META,
  attentionRank,
  type AttentionItem,
  type Brief,
  type BriefPass,
  type BriefStatus,
  type Source,
  type Status,
} from './types'
import { COWORK_JOBS } from './prompts.example'
// To use your own prompts: copy prompts.example.ts → prompts.ts (gitignored) and change this import.

function ageLabel(hours: number | undefined): { text: string; cls: string } {
  if (hours === undefined) return { text: 'never', cls: 'red' }
  if (hours < 1) return { text: 'just now', cls: 'green' }
  if (hours < 4) return { text: `${Math.round(hours)}h ago`, cls: 'green' }
  if (hours < 12) return { text: `${Math.round(hours)}h ago`, cls: 'amber' }
  if (hours < 48) return { text: `${Math.round(hours)}h ago`, cls: 'red' }
  return { text: `${Math.round(hours / 24)}d ago`, cls: 'red' }
}

function AttentionRow({
  a,
  onStatus,
  resolved,
  showSource,
}: {
  a: AttentionItem
  onStatus: (id: string, status: Status) => void
  resolved: boolean
  /** True on the Top 10 view. Switches the leading icon from category to source: on a lane
   *  view every row shares one source, so the lane glyph would be a column of identical
   *  icons — which is exactly the problem this swap exists to fix. */
  showSource?: boolean
}) {
  const cm = CATEGORY_META[a.category] ?? CATEGORY_META.other
  const um = URGENCY_META[a.urgency]
  const sm = a.source ? SOURCES.find((s) => s.id === a.source) : undefined

  // The leading icon shows whichever field actually VARIES in the current view.
  //
  // It used to always be the category icon, which made it a column of identical handshakes:
  // the synthesis prompt ranks client work above everything else and attentionRank breaks
  // urgency ties by category with 'client' first, so a top-10 of same-day items is
  // client-by-construction. Worse, the category was already spelled out in the chip on the
  // right of the same row — the icon and its "Client" tooltip said nothing the badge didn't.
  //
  // Source is the field that genuinely differs row to row once lanes are merged, and it was
  // buried inline before the headline. So on the Top 10 the lead is the lane; on a lane view
  // the lane is constant and the category is the discriminating field, so it takes the slot.
  // Category is never lost either way: the chip on the right always carries it as text.
  const lead = showSource && sm
    ? { icon: sm.icon, title: `Surfaced by the ${sm.label} pass` }
    : { icon: cm.icon, title: cm.label }

  return (
    <div className={`cw-att u-${a.urgency}${resolved ? ' resolved' : ''}`}>
      <span className="cw-att-icon" title={lead.title}>{lead.icon}</span>
      <div className="cw-att-main">
        <div className="cw-att-head">
          {renderInline(a.headline, `${a.id}-h`)}
        </div>
        {a.why && <div className="cw-att-why">{a.why}</div>}
      </div>
      <div className="cw-att-side">
        <span className={`cw-chip ${cm.tone}`}>{cm.label}</span>
        {um && <span className={`cw-urg ${a.urgency}`}>{um.label}</span>}
      </div>
      <div className="cw-att-acts">
        {a.unresolved_id ? (
          <span className="cw-att-noid" title={`No inbox item matches "${a.id}" — triage unavailable`}>
            ⚠
          </span>
        ) : resolved ? (
          <button className="cw-tri" onClick={() => onStatus(a.id, 'open')} title="Reopen">↩</button>
        ) : (
          <>
            <button className="cw-tri done" onClick={() => onStatus(a.id, 'done')} title="Mark done">✓</button>
            <button className="cw-tri" onClick={() => onStatus(a.id, 'dismissed')} title="Dismiss">✕</button>
          </>
        )}
      </div>
    </div>
  )
}

function ListBlock({ title, icon, items }: { title: string; icon: string; items: string[] }) {
  if (!items.length) return null
  return (
    <section className="cw-block">
      <h2>
        <span>{icon}</span> {title} <span className="cw-block-n">{items.length}</span>
      </h2>
      <ul className="cw-block-list">
        {items.map((s, i) => (
          <li key={i}>{renderInline(s, `${title}-${i}`)}</li>
        ))}
      </ul>
    </section>
  )
}

// --- co-work prompts reference panel ----------------------------------------

function CoWorkPrompts() {
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <div className="cw-prompts">
      <div className="cw-prompts-grid">
        {COWORK_JOBS.map((job) => {
          const isOpen = expanded === job.id
          return (
            <div key={job.id} className={`cw-prompt-job${isOpen ? ' open' : ''}`}>
              <button
                className="cw-prompt-hd"
                onClick={() => setExpanded(isOpen ? null : job.id)}
              >
                <span className="cw-prompt-icon">{job.icon}</span>
                <span className="cw-prompt-meta">
                  <strong>{job.title}</strong>
                  <span className="cw-sub">{job.schedule}</span>
                </span>
                <span className="cw-prompt-chevron">{isOpen ? '▲' : '▼'}</span>
              </button>
              <p className="cw-prompt-desc">{job.description}</p>
              {isOpen && (
                <div className="cw-prompt-body">
                  {renderMarkdown(job.prompt)}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function BriefView({
  triaged,
  onStatus,
}: {
  triaged: Record<string, Status>
  onStatus: (id: string, status: Status) => void
}) {
  // 'all' reads the merged brief.json; a Source reads that lane's brief.<source>.json.
  const [lens, setLens] = useState<Source | 'all'>('all')
  const [brief, setBrief] = useState<Brief | null>(null)
  // Per-lane coverage lives only on the merged brief, but the tab counts have to stay
  // visible while a lane is selected — so it is kept separately from `brief`.
  const [passes, setPasses] = useState<Record<string, BriefPass>>({})
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [showPrompts, setShowPrompts] = useState(false)
  const poll = useRef<number | null>(null)
  const startPollingRef = useRef<(() => void) | undefined>(undefined)

  const qs = lens === 'all' ? '' : `?source=${lens}`
  const lensLabel = lens === 'all'
    ? 'Top 10'
    : SOURCES.find((s) => s.id === lens)?.label ?? lens

  const load = useCallback(() => {
    setLoading(true)
    setErr('')
    getJSON<Brief>(`/api/brief${qs}`)
      .then((b) => {
        setBrief(b)
        setLoading(false)
        if (b.passes) setPasses(b.passes)
        // Only the merged view auto-refreshes: a lane refresh rebuilds just that lane,
        // so letting three tabs each fire one would leave the merge inconsistent.
        if (b.stale_source && lens === 'all') {
          setNote('Harvest data changed since the last pass — re-synthesizing…')
          postJSON('/api/brief/refresh?auto=1')
            .then(() => { setBusy(true); startPollingRef.current?.() })
            .catch(() => { setNote('') }) // 409 (already running) or cooldown — leave stale brief
        }
      })
      .catch((e) => { setErr(String(e)); setLoading(false) })
  }, [qs, lens])

  useEffect(() => {
    load()
    return () => { if (poll.current) window.clearInterval(poll.current) }
  }, [load])

  // While a synthesis run is in flight, poll status; reload the brief when it lands.
  const startPolling = useCallback(() => {
    if (poll.current) window.clearInterval(poll.current)
    poll.current = window.setInterval(async () => {
      try {
        const s = await getJSON<BriefStatus>('/api/brief/status')
        if (!s.running) {
          if (poll.current) window.clearInterval(poll.current)
          poll.current = null
          setBusy(false)
          setNote(s.last_error ? `Synthesis failed: ${s.last_error}` : '')
          load()
        }
      } catch {
        if (poll.current) window.clearInterval(poll.current)
        poll.current = null
        setBusy(false)
      }
    }, 3000)
  }, [load])

  startPollingRef.current = startPolling

  const refresh = useCallback(async () => {
    setBusy(true)
    setNote(lens === 'all'
      ? 'Synthesizing — one pass per source, then merging. This takes a few minutes.'
      : `Synthesizing the ${lensLabel} pass…`)
    try {
      await postJSON(`/api/brief/refresh${qs}`)
      startPolling()
    } catch (e) {
      setBusy(false)
      setNote(String(e instanceof Error ? e.message : e))
    }
  }, [startPolling, qs, lens, lensLabel])

  const age = ageLabel(brief?.age_hours)
  const attention = (brief?.attention ?? [])
    .slice()
    .sort((a, b) => attentionRank(a) - attentionRank(b))

  const openCount = attention.filter(
    (a) => (triaged[a.id] ?? 'open') === 'open',
  ).length

  // How many attention items the lane passes surfaced in total. The merged view ranks those and
  // keeps ten, so without this number the page shows 10 while the lane tabs add up to 24 and
  // nothing accounts for the difference — the arithmetic that made "Merged" read as a lie.
  //
  // Deliberately phrased as "surfaced across lanes" rather than "10 of 24": the merge de-dupes
  // by item id before capping, so some of the gap is duplicates removed, not items ranked out.
  // Claiming 14 were outranked would be a more precise number and a less true statement.
  const laneTotal = Object.values(passes).reduce((n, p) => n + (p.attention ?? 0), 0)
  const heldBack = lens === 'all' && laneTotal > attention.length

  return (
    <div className="cw-brief">
      <div className="cw-brief-bar">
        <div className="cw-brief-meta">
          <strong>{lens === 'all' ? 'Executive brief' : `${lensLabel} brief`}</strong>
          <span className={`cw-age ${age.cls}`}>● synthesized {age.text}</span>
          {brief?.items_considered !== undefined && (
            <span className="cw-sub">
              {openCount} needing action · {brief.items_read ?? 0} of{' '}
              {brief.items_eligible ?? brief.items_read ?? 0} eligible items read ·{' '}
              {brief.items_filtered ?? 0} filtered as noise
            </span>
          )}
        </div>
        <span className="cw-spacer" />
        <button
          className={`cw-btn${showPrompts ? ' primary' : ''}`}
          onClick={() => setShowPrompts((v) => !v)}
          title="View the exact prompts for each scheduled co-work job"
        >
          📋 Co-Work Prompts
        </button>
        <button
          className="cw-btn primary"
          onClick={refresh}
          disabled={busy}
          title={lens === 'all'
            ? 'One pass per source, then re-rank them into the Top 10'
            : `Re-run only the ${lensLabel} pass`}
        >
          {busy ? 'Synthesizing…' : lens === 'all' ? '⟳ Re-synthesize all' : `⟳ Re-synthesize ${lensLabel}`}
        </button>
      </div>

      {/* One lens per synthesis pass, plus the cross-lane pick. That pick was labelled
          "Merged", which described how it is BUILT (fold the lane passes together) rather than
          what you get, and invited the reasonable arithmetic "four lanes of six, so twenty-four
          cards" — when it is hard-capped at ten by _merge_briefs. "Top 10" says the actual
          thing. A lane view is everything that lane's pass surfaced, which is where an item
          ranked 11th overall is still findable; the header says how many that is. */}
      <div className="cw-brief-lens">
        <button
          className={`cw-tab${lens === 'all' ? ' on' : ''}`}
          onClick={() => setLens('all')}
        >
          ★ Top 10
        </button>
        {SOURCES.map((s) => {
          const p = passes[s.id]
          return (
            <button
              key={s.id}
              className={`cw-tab${lens === s.id ? ' on' : ''}`}
              onClick={() => setLens(s.id)}
              title={p ? `${p.attention} surfaced from ${p.items_read} items read` : undefined}
            >
              {s.icon} {s.label}
              {p ? <span className="cw-tab-n">{p.attention}</span> : null}
            </button>
          )
        })}
      </div>

      {showPrompts && <CoWorkPrompts />}

      {note && <div className={busy ? 'cw-note' : 'cw-err'}>{note}</div>}
      {err && <div className="cw-err">Couldn't load the brief: {err}</div>}

      {brief?.truncated ? (
        <div className="cw-note">
          <strong>Partial pass.</strong> The context budget fit {brief.items_read} of{' '}
          {brief.items_eligible ?? (brief.items_read ?? 0) + brief.truncated} eligible items —{' '}
          {brief.truncated} were dropped unread, lowest-priority first. For a complete
          synthesis, have the co-work harvest task write the brief instead (see{' '}
          <code>BRIEF_SCHEMA.md</code>).
        </div>
      ) : null}

      {brief?.failed_sources?.length ? (
        <div className="cw-err">
          <strong>Incomplete merge.</strong> These passes failed and are not represented:{' '}
          {brief.failed_sources.join(', ')}. Re-synthesize to retry.
        </div>
      ) : null}

      {loading && <p className="cw-sub" style={{ padding: '0 4px' }}>Loading brief…</p>}

      {!loading && brief && !brief.exists && (
        <div className="cw-empty">
          <p><strong>No brief has been synthesized yet.</strong></p>
          <p>
            The synthesis pass reads every unresolved inbox item and reduces it to the
            handful that actually need your attention this week.
          </p>
          <p style={{ marginTop: 14 }}>
            <button className="cw-btn primary" onClick={refresh} disabled={busy}>
              {busy ? 'Synthesizing…' : 'Run the first synthesis'}
            </button>
          </p>
        </div>
      )}

      {!loading && brief?.exists && (
        <>
          {brief.client_pulse && (
            <section className="cw-pulse">
              <h2>🤝 Client pulse</h2>
              <p>{renderInline(brief.client_pulse, 'pulse')}</p>
            </section>
          )}

          <section className="cw-block">
            <h2>
              <span>🎯</span> Needs your attention{' '}
              <span className="cw-block-n">{openCount}</span>
              {heldBack && (
                <span className="cw-block-sub">
                  top {attention.length} of {laneTotal} surfaced across{' '}
                  {Object.keys(passes).length} lanes — open a lane for the rest
                </span>
              )}
            </h2>
            {attention.length === 0 ? (
              <p className="cw-sub">
                Nothing surfaced as needing action. {brief.suppressed ?? 0} items were
                assessed and suppressed.
              </p>
            ) : (() => {
              const open = attention.filter((a) => (triaged[a.id] ?? 'open') === 'open')
              const done = attention.filter((a) => (triaged[a.id] ?? 'open') !== 'open')
              return (
              <div className="cw-att-list">
                {open.map((a) => (
                  <AttentionRow key={a.id} a={a} onStatus={onStatus} resolved={false}
                                showSource={lens === 'all'} />
                ))}
                {done.length > 0 && (
                  <>
                    <div className="cw-att-divider">
                      <span>✓ cleared · {done.length}</span>
                    </div>
                    {done.map((a) => (
                      <AttentionRow key={a.id} a={a} onStatus={onStatus} resolved={true}
                                    showSource={lens === 'all'} />
                    ))}
                  </>
                )}
              </div>
              )
            })()}
          </section>

          <ListBlock title="You promised — no resolution yet" icon="🪢" items={brief.dangling ?? []} />
          <ListBlock title="Waiting on your response" icon="📬" items={brief.missed ?? []} />
          <ListBlock title="Your meetings with no agenda" icon="📋" items={brief.agenda_gaps ?? []} />

          {brief.synthesis_note && (
            <div className="cw-note">{renderInline(brief.synthesis_note, 'snote')}</div>
          )}

          <p className="cw-brief-foot">
            Synthesized from {brief.items_considered ?? '?'} harvested items
            {brief.period ? ` · period ${brief.period}` : ''}
            {brief.items_triaged ? ` · ${brief.items_triaged} already triaged` : ''}
            {lens === 'all' && Object.keys(passes).length > 0
              ? ` · ${Object.keys(passes).length} passes: ` +
                Object.entries(passes)
                  .map(([s, p]) => `${s} ${p.items_read}`)
                  .join(', ')
              : ''}
            . Full detail is in the <strong>All items</strong> tab.
          </p>
        </>
      )}
    </div>
  )
}
