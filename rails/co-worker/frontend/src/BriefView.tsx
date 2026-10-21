// The landing view: a synthesized executive brief instead of 147 raw cards.
//
// Renders inbox/brief.json (produced by the synthesis pass — see synthesize.py).
// Attention items carry an `id` pointing back at an inbox item, so Done/Dismiss
// hits the same PATCH endpoint the card grid uses and the two views stay in sync.
import { useEffect, useRef, useState } from 'react'
import { SpeakButton } from '@web-core'
import { getJSON, postJSON } from './api'
import { BriefSync, initialBriefState, type BriefSyncState, type RefreshAnswer } from './briefsync'
import { renderInline, renderMarkdown } from './markdown'
import {
  CATEGORY_META,
  SOURCES,
  URGENCY_META,
  attentionRank,
  type AttentionItem,
  type Brief,
  type BriefStatus,
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

// The bracketed fields a prompt leaves for its reader, "[YOUR NAME]" and the like, read off the
// prompt text so the panel's note stays true of whichever prompts it shows. The tracked
// prompts.example.ts is templates; a local prompts.ts with real details has none, and then the
// panel stops calling them templates.
//
// The rail does not fill them in from CO_WORKER_USER_NAME. It knows that name only to drop
// self-sent items in synthesis (config.py `user_name`), no route hands it to the browser, and
// it knows no role or company at all. These are also examples, not the prompts that run: the
// real harvest prompts are machine-local SKILL.md files the rail never reads (README.md), so a
// filled-in example would read as the live prompt when it is not.
const FILL_IN = /\[[A-Z][A-Z ]*\]/g
const fillIns = (prompt: string): string[] => [...new Set(prompt.match(FILL_IN) ?? [])]
const PROMPTS_ARE_TEMPLATES = COWORK_JOBS.some((job) => fillIns(job.prompt).length > 0)

function CoWorkPrompts() {
  const [expanded, setExpanded] = useState<string | null>(null)

  return (
    <div className="cw-prompts">
      {PROMPTS_ARE_TEMPLATES && (
        <p className="cw-prompts-note">
          Example prompts for scheduled co-work jobs. They are templates: copy one into a Claude
          scheduled task and replace the bracketed fields with your own details. This rail does
          not run them; it reads what your harvest tasks write to its inbox.
        </p>
      )}
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
                  {fillIns(job.prompt).length > 0 && (
                    <p className="cw-prompt-fill">
                      Fill in before use:{' '}
                      {fillIns(job.prompt).map((f, i) => (
                        <span key={f}>{i > 0 && ', '}<code>{f}</code></span>
                      ))}
                    </p>
                  )}
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
  // The lens, its brief and the synthesis run being waited on are BriefSync's (briefsync.ts);
  // this component renders them. 'all' reads the merged brief.json; a Source reads that lane's
  // brief.<source>.json. The poll lives as long as the tab, so switching lens mid-synthesis
  // cannot strand `busy` (it used to: the lens effect's cleanup killed the poll).
  const [st, setSt] = useState<BriefSyncState>(initialBriefState)
  const [showPrompts, setShowPrompts] = useState(false)
  const sync = useRef<BriefSync | null>(null)

  useEffect(() => {
    // Created in the effect, not once per component: under StrictMode's dev double-mount a
    // controller made outside it would be disposed by the first cleanup and never come back.
    const s = new BriefSync({
      getBrief: (qs) => getJSON<Brief>(`/api/brief${qs}`),
      postRefresh: (path) => postJSON<RefreshAnswer>(path),
      getStatus: () => getJSON<BriefStatus>('/api/brief/status'),
      setInterval: (fn, ms) => window.setInterval(fn, ms),
      clearInterval: (id) => window.clearInterval(id as number),
    }, setSt)
    sync.current = s
    s.attach()
    s.load()
    return () => {
      s.dispose()
      if (sync.current === s) sync.current = null
    }
  }, [])

  const { lens, brief, passes, loading, err, busy, note } = st
  const lensLabel = lens === 'all'
    ? 'Top 10'
    : SOURCES.find((s) => s.id === lens)?.label ?? lens
  const setLens = (l: BriefSyncState['lens']) => sync.current?.setLens(l)
  const refresh = () => sync.current?.refresh(lensLabel)

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
          title={PROMPTS_ARE_TEMPLATES
            ? 'Example prompts for scheduled co-work jobs, as templates to fill in'
            : 'The prompts for the scheduled co-work jobs'}
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
              <h2>
                🤝 Client pulse
                {/* Read-aloud takes the text EXPLICITLY: this rail knows the pulse paragraph is
                    the thing worth hearing, which a shell-level "read the page" button could
                    only guess at — it would recite the nav and the lens tabs too. Markdown is
                    flattened by the shared speakable(). */}
                <SpeakButton text={brief.client_pulse} className="cw-speak" />
              </h2>
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
