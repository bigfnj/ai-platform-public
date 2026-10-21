// Watch Builder — federated React module for the platform shell. Exposes ./module.
//
// Three panes over one design document: an element editor, an AI panel, and a preview grid
// of real emulator screenshots. No own top bar or theme; the shell provides those, and this
// renders inside a `.watch-builder` wrapper adopting the shell's palette via shared tokens
// (see web/THEMING.md).
//
// The document is the single source of truth and the backend is its authority: every action
// here either edits the document or posts it somewhere that validates it. Nothing in this
// file knows how to generate C, which is why a model's output and a person's edits go
// through exactly the same path.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { RailHeader, ModelChips, usePoll } from '@web-core'
import { del, deleteJSON, downloadBase64, getJSON, postJSON, postForm, type ApiError } from './api'
import Canvas from './Canvas'
import {
  blankDesign,
  ELEMENT_LABEL,
  FIELDS,
  newElement,
  type AiJob,
  type AiJobSummary,
  type BuildResponse,
  type Capabilities,
  type Design,
  type Element,
  type ElementType,
  type Estimate,
  type LayoutReport,
  type PlatformName,
  type Preset,
  type SavedRow,
  type UploadedImage,
  type Variant,
} from './types'
import './theme.css'

type Tab = 'design' | 'presets' | 'ai' | 'preview'

const ELEMENT_TYPES = Object.keys(ELEMENT_LABEL) as ElementType[]

// The 64-colour palette as a swatch row: one entry per 2-bit level combination is 64
// swatches, which is too many to scan. These are the ones a face actually uses, and the hex
// field takes anything else. Every value here is already on the cube, so nothing is snapped
// behind the user's back.
const SWATCHES = [
  '#000000', '#555555', '#AAAAAA', '#FFFFFF',
  '#001E41', '#0000AA', '#0055FF', '#00AAFF',
  '#005500', '#00AA00', '#55FF55', '#AAFFAA',
  '#550000', '#AA0000', '#FF5555', '#FFAAAA',
  '#AA5500', '#FFAA00', '#FFFF00', '#FFFFAA',
  '#550055', '#AA00AA', '#FF55FF', '#FFAAFF',
]

// A swatch of one of the DESIGN's colours: a #RRGGBB the backend validates, never a UI or
// status colour. Every such swatch in this file goes through here, so the status-contrast
// test needs one allowance for them (../contrast-allow.json). It matches the exact
// expression `designColour`, which only this line uses; an inline colour the test cannot
// resolve anywhere else (a bare `c`, say) still fails.
function DesignChip({ designColour }: { designColour: string }) {
  return <span className="wb-chip-sq" style={{ background: designColour }} />
}

function fmtAge(seconds: number): string {
  const d = new Date(seconds * 1000)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined,
    { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

// --- findings ---------------------------------------------------------------------------

function Findings({ report }: { report: LayoutReport | null }) {
  if (!report) return null
  const { errors, warnings, findings } = report
  const semantics = report.semantics || []
  if (!errors && !warnings && !semantics.length) {
    return <div className="wb-ok">No layout problems on any target platform.</div>
  }
  return (
    <div className="wb-findings">
      <div className="wb-findings-head">
        {errors > 0 && <span className="wb-badge crit">{errors} error{errors > 1 ? 's' : ''}</span>}
        {warnings > 0 && <span className="wb-badge warn">{warnings} warning{warnings > 1 ? 's' : ''}</span>}
        <span className="wb-sub">measured per platform, in pixels</span>
      </div>
      {semantics.map((s, i) => (
        <div key={`s${i}`} className="wb-finding warn"><code>design</code> {s}</div>
      ))}
      {findings.map((f, i) => (
        <div key={i} className={`wb-finding ${f.severity === 'error' ? 'crit' : 'warn'}`}>
          <code>{f.platform}</code> <span className="wb-code">{f.code}</span> {f.message}
        </div>
      ))}
    </div>
  )
}

// --- colour shift -----------------------------------------------------------------------

function ColourShift({ report }: { report: LayoutReport | null }) {
  const rows = report?.colour_shift || []
  if (!rows.length) return null
  return (
    <div className="wb-shift">
      <div className="wb-sub">
        What the reflective display actually shows. Pebble&apos;s screens are e-paper and
        desaturate hard, so a saturated colour reaches the eye markedly paler than its value.
        Left is what the design asks for, right is what a wrist sees.
      </div>
      <div className="wb-shift-row">
        {rows.map((r) => (
          <div key={r.specified} className="wb-shift-pair" title={`${r.specified} → ${r.on_watch}`}>
            <DesignChip designColour={r.specified} />
            <span className="wb-arrow">→</span>
            <DesignChip designColour={r.on_watch} />
          </div>
        ))}
      </div>
    </div>
  )
}

// --- inspector --------------------------------------------------------------------------

function Field({ name, value, onChange }: {
  name: string
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (name === 'filled' || name === 'second_hand') {
    return (
      <label className="wb-field">
        <span>{name}</span>
        <input type="checkbox" checked={Boolean(value)}
               onChange={(e) => onChange(e.target.checked)} />
      </label>
    )
  }
  if (name === 'align') {
    return (
      <label className="wb-field">
        <span>align</span>
        <select value={String(value ?? 'center')} onChange={(e) => onChange(e.target.value)}>
          <option value="left">left</option>
          <option value="center">center</option>
          <option value="right">right</option>
        </select>
      </label>
    )
  }
  if (name === 'font') {
    return (
      <label className="wb-field">
        <span>font</span>
        <select value={String(value ?? 'auto')} onChange={(e) => onChange(e.target.value)}>
          <option value="auto">auto (largest that fits)</option>
          <option value="auto-small">auto-small</option>
          <option value="auto-tiny">auto-tiny</option>
        </select>
      </label>
    )
  }
  if (name === 'color' || name === 'hand_color') {
    return (
      <label className="wb-field">
        <span>{name}</span>
        <span className="wb-colour">
          <input value={String(value ?? '#FFFFFF')} spellCheck={false}
                 onChange={(e) => onChange(e.target.value.toUpperCase())} />
          <DesignChip designColour={String(value ?? '#FFFFFF')} />
        </span>
      </label>
    )
  }
  if (name === 'radius' || name === 'stroke' || name === 'thickness') {
    return (
      <label className="wb-field">
        <span>{name}</span>
        <input type="number" value={Number(value ?? 0)}
               onChange={(e) => onChange(Number(e.target.value))} />
      </label>
    )
  }
  // Lengths and free text. Kept as text so "40%" and "center" are typeable; the backend
  // validates the form and says which forms are legal.
  return (
    <label className="wb-field">
      <span>{name}</span>
      <input value={value === null || value === undefined ? '' : String(value)}
             spellCheck={false}
             placeholder={name === 'format' ? '%a %d %b' : '40% | center | 12'}
             onChange={(e) => {
               const raw = e.target.value
               if (raw === '') { onChange(null); return }
               // An all-digit entry becomes a number so the document carries absolute pixels
               // as ints rather than numeric strings, which the schema would reject.
               onChange(/^-?\d+$/.test(raw) ? Number(raw) : raw)
             }} />
    </label>
  )
}

function Inspector({ element, onChange }: {
  element: Element
  onChange: (patch: Partial<Element>) => void
}) {
  const fields = FIELDS[element.type] || []
  return (
    <div className="wb-inspector">
      <div className="wb-inspector-head">{ELEMENT_LABEL[element.type]}</div>
      {fields.map((f) => (
        // Element has no index signature, so the field lookup goes via unknown. The
        // inspector is driven by FIELDS, which is keyed off the same type union, so a name
        // reaching here is always a real property.
        <Field key={f} name={f} value={(element as unknown as Record<string, unknown>)[f]}
               onChange={(v) => onChange({ [f]: v } as Partial<Element>)} />
      ))}
      <div className="wb-hint">
        Percentages scale across all seven screens. Absolute pixels are correct only on the
        screen you had in mind.
      </div>
    </div>
  )
}

// --- preview ----------------------------------------------------------------------------

function PreviewGrid({ build, showRaw }: { build: BuildResponse | null; showRaw: boolean }) {
  const shots = build?.render?.shots || {}
  const errors = build?.render?.errors || {}
  const names = Object.keys(shots)
  if (!names.length && !Object.keys(errors).length) return null
  return (
    <div className="wb-shots">
      {names.map((p) => {
        // Falls back to on_watch when the raw capture is missing rather than rendering a
        // broken image: the second screenshot is best-effort.
        const b64 = showRaw ? (shots[p].raw ?? shots[p].on_watch) : shots[p].on_watch
        return (
        <figure key={p} className="wb-shot">
          <img alt={`${p} preview`} src={`data:image/png;base64,${b64}`} />
          <figcaption>{p}</figcaption>
        </figure>
        )
      })}
      {Object.entries(errors).map(([p, msg]) => (
        <figure key={p} className="wb-shot failed">
          <figcaption>{p}: {msg}</figcaption>
        </figure>
      ))}
    </div>
  )
}

// --- AI ---------------------------------------------------------------------------------

function CostNotice({ estimate }: { estimate: Estimate | null }) {
  if (!estimate) return null
  return (
    <div className="wb-cost">
      <strong>{estimate.calls} call{estimate.calls > 1 ? 's' : ''}</strong> to{' '}
      <code>{estimate.model_ref}</code>, roughly {estimate.approx_seconds}s
      {estimate.holds_gpu ? ' of GPU' : ' on Claude, with no GPU unless a call falls back'}.
      <div className="wb-sub">{estimate.note}</div>
    </div>
  )
}

// --- AI jobs ----------------------------------------------------------------------------
//
// Both AI actions run as background jobs on the rail (jobs.py): the POST answers at once with
// the job, and the page polls it until it is done, failed or cancelled. Each model call then
// waits the rail's whole per-call timeout, so every one of them can reach Claude for a viewer
// the broker serves from there, and no gateway or Cloudflare timeout cuts the page off mid-pass.

// The states a page keeps polling. done, failed and cancelled are final.
const ACTIVE_JOB_STATES: readonly string[] = ['queued', 'running']
const isActiveJob = (state: string) => ACTIVE_JOB_STATES.includes(state)

// Progress moves by whole model calls, each several seconds at least.
const JOB_POLL_MS = 1500
// Failed reads in a row (a 502 while the gateway restarts, a dropped connection) before the page
// says it has lost touch: about 90 s at the poll's rate. It does not stop asking. The poll stands
// down and the keep-alive reads the job every KEEPALIVE_MS instead, visible or hidden, until a
// read is answered (the poll resumes), the job is gone (a 404) or it is final, so an outage that
// ends inside the rail's lease costs the job nothing. A 404 is final at once.
const MAX_FAILED_POLLS = 60
// A job nobody reads for its lease (jobs.LEASE_S, 5 minutes) starts no new call. The keep-alive
// below reads it on this timer whenever the poll is not reading it. Browsers clamp a hidden
// tab's timers to about once a minute (Chrome's intensive throttling), so it fires every 30 to
// 60 s, and the lease outlasts several of those lost in a row. It is also each read's deadline.
const KEEPALIVE_MS = 30000

const LOST_JOB = 'This AI job is gone: the rail restarted, which loses every job in flight, '
  + 'or it finished long enough ago to be cleared. Nothing is running for it now; run it again.'
const OUT_OF_TOUCH = 'Lost contact with the rail while waiting on the AI job. It may still be '
  + `running: this page asks again every ${KEEPALIVE_MS / 1000} seconds and carries on as soon as `
  + 'the rail answers.'
// A page that mounted with a job the keep-alive is keeping, and whose read of it failed: the
// rail is not answering. Until a read of that job is answered the page shows nothing of it,
// since it may be someone else's (a sign-out does not reload the tab), so this names no job.
const UNREACHED = 'Lost contact with the rail, so this page cannot show yet whether an AI job '
  + `of yours is running. It asks again every ${KEEPALIVE_MS / 1000} seconds and carries on as `
  + 'soon as the rail answers.'

// The platform answered a read 401 or 403: this page is signed out, or its viewer may no longer
// open Watch Builder, so no read it sends reaches the rail as the job's owner.
function signedOutLine(leaseS: number | undefined): string {
  const when = leaseS ? `within about ${Math.round(leaseS / 60)} minutes` : 'once its lease lapses'
  return 'The platform stopped answering this page as you (signed out, or no longer allowed into '
    + `Watch Builder), so nothing here reads the AI job any more and the rail stops it ${when}. `
    + 'Sign in and open Watch Builder again to pick it up.'
}

function jobLine(job: AiJob): string {
  if (job.state === 'queued') {
    return 'Queued: it starts once no earlier job of yours is still in a model call.'
  }
  if (job.kind === 'design') {
    return `Designing: model call ${Math.max(1, job.calls)} of at most 3 (the design, a reformat, a repair).`
  }
  return `Exploring variations: ${job.progress.done} of ${job.progress.total} done.`
}

// What the page tells the viewer about leaving: the job's lease (jobs.LEASE_S) is renewed by
// the reads of every tab that has shown the job (this one, or the tab that started it): its
// poll, and its keep-alive while the tab is hidden or on another rail. A browser that freezes
// or discards a background tab (Energy Saver, Memory Saver, a phone locking) stops that tab's
// timers, so its reads stop as if it were closed: that is the exception the line names.
function leaseLine(job: AiJob): string {
  return `It keeps going while a tab that has shown it stays open, on another rail or in the background, unless the browser puts that tab to sleep; with none open, it stops within about ${Math.round(job.lease_s / 60)} minutes.`
}

// The cancel does not reach the broker (jobs.cancel): the rail starts no new call, but one it
// had already sent runs there to the end, and the rail drops its answer.
function cancelledLine(job: AiJob): string {
  const done = job.kind === 'variations'
    ? ` ${job.progress.done} of ${job.progress.total} variations finished first.` : ''
  return `Cancelled.${done} No new call starts after a cancel; a call already sent finishes and its answer is dropped.`
}

// The variants a pass keeps: the ones that finished as a design. Not progress.done, which counts
// a variant whose calls failed too (jobs.Run.variant_done is handed every finished entry).
function keptVariants(job: AiJob): number {
  return (job.result?.variants ?? job.partial ?? []).filter((v) => v.ok).length
}

// A job the rail stopped on its own (jobs.Run.gate): nobody read it for its lease (its tab was
// closed, or the browser froze it), or it reached its ceiling. Not a failure of the model, so
// it is said here rather than as an error, with what finished first, which the rail keeps.
function stoppedLine(job: AiJob): string {
  const why = job.stopped === 'unwatched'
    ? `Stopped: nobody was watching it for ${Math.round(job.lease_s / 60)} minutes, so no new call started.`
    : `Stopped at its ${Math.round(job.ceiling_s / 60)}-minute limit, so no new call started.`
  const kept = job.kind === 'variations'
    ? ` ${keptVariants(job)} of ${job.progress.total} variations finished first and are kept.`
    : ' The design was not finished; run it again.'
  return why + kept
}

// --- the keep-alive, outside React -------------------------------------------------------
//
// A job nobody reads for its lease (jobs.LEASE_S) starts no new call. While this page is
// mounted, visible and in touch with the rail, its poll (usePoll, JOB_POLL_MS) reads the job.
// The keep-alive reads it every KEEPALIVE_MS at every other moment:
//   * the tab is hidden, so usePoll skips its ticks;
//   * the poll has lost touch (MAX_FAILED_POLLS failed reads: a gateway restart longer than
//     90 s), and the keep-alive asks on at its own pace until the rail answers;
//   * the viewer moved to another rail in this platform tab. The shell renders only the active
//     rail (apps/platform/frontend/src/App.tsx), so WatchBuilderModule unmounts, but this module
//     stays loaded for as long as the tab is open, and the keep-alive lives here, not in the
//     component.
// It is keyed on the job the page is showing: started when one is submitted, attached to after a
// 409 or picked up after a reload (applyJob), and stopped once that job is final, gone (a 404),
// cancelled, or refused (401/403), with a page mounted or not. One read at a time, each with its
// own deadline. Closing the tab or the browser ends it with the page, and the job then stops
// within the lease.
//
// It holds the job's id and nothing any read answered. The shell signs out and back in without a
// reload (App.tsx), so this module outlives a sign-out, and the next person to open Watch Builder
// in the tab may be someone else. So a page shows only what the rail answered a read made as that
// page's viewer: its own poll, its read of the kept id as it mounts, the listing it picks a job
// up from, or a keep-alive read sent while that same page was mounted. And it says how the kept
// job ended only once a read has shown it that job. Before that, a 404 is what another viewer's
// job gets as well as a lost one, so it ends the keep-alive and the page says nothing
// (JobPage.heard in the mount effect). A keep-alive read sent with no page mounted carries
// ?keepalive=1, which renews the lease without marking a finished job delivered (jobs.get):
// nobody is shown its answer, so a pass that finishes then is still offered by the next page's
// listing. A read that a mounted page will show (a hidden tab's, or one out of touch) is a plain
// read, and delivers a finished job as the poll's read does.

/** One read of a job: its view, a 404 (gone), a 401/403 (refused: the platform no longer lets
 *  this page read it as its owner), or any other failure. */
type JobRead = { job: AiJob } | { gone: true } | { refused: true } | { failed: true }

/** What the keep-alive needs from a mounted page. */
interface JobPage {
  /** True while the page's own poll is reading `id`: it shows the job, is visible, in touch. */
  polling(id: string): boolean
  /** A read of `id` made while this page was mounted (its own as it mounts, or the
   *  keep-alive's), for it to show or not. */
  heard(id: string, read: JobRead): void
}

const kept: {
  id: string | null              // the job the keep-alive renews: its id, never what a read said
  foreign: boolean               // that job is one this page did not submit (a 409's)
  stop: AbortController | null
  timer: number | undefined
  failures: number               // failed reads in a row, by the poll and the keep-alive alike
  page: JobPage | null
} = { id: null, foreign: false, stop: null, timer: undefined, failures: 0, page: null }

/** One GET of the job, by the poll, by a page as it mounts, or by the keep-alive (`keepalive`,
 *  for its read with no page mounted: renew the lease and leave a finished job undelivered), or
 *  null once `stop` has ended it. Its deadline is its own: a read still unanswered after
 *  KEEPALIVE_MS (a connection that stalled after a sleep or a network change, a rail too busy to
 *  answer) is abandoned and counts as one failure, so it never holds up the next read. The
 *  deadline is a timer, cleared once the read settles, rather than AbortSignal.timeout, which
 *  Safari before 16 and Chrome before 103 lack. */
async function readJobOnce(id: string, stop: AbortSignal, keepalive = false): Promise<JobRead | null> {
  if (stop.aborted) return null
  const read = new AbortController()
  const abort = () => read.abort()
  const deadline = window.setTimeout(abort, KEEPALIVE_MS)
  stop.addEventListener('abort', abort)
  try {
    const marker = keepalive ? '?keepalive=1' : ''
    return { job: await getJSON<AiJob>(`/api/ai/jobs/${id}${marker}`, read.signal) }
  } catch (e) {
    if (stop.aborted) return null
    const status = (e as ApiError).status
    if (status === 404) return { gone: true }
    if (status === 401 || status === 403) return { refused: true }
    return { failed: true }
  } finally {
    window.clearTimeout(deadline)
    stop.removeEventListener('abort', abort)
  }
}

/** Keep job `id` alive: start the keep-alive for it, or carry on if it is the one already kept. */
function keepAlive(id: string, foreign: boolean): void {
  if (kept.id === id) return
  stopKeepAlive()
  const stop = new AbortController()
  kept.id = id
  kept.foreign = foreign
  kept.stop = stop
  kept.timer = window.setTimeout(() => void keepTick(stop), KEEPALIVE_MS)
}

/** Stop the keep-alive, abort its read in flight, and forget the job. */
function stopKeepAlive(): void {
  window.clearTimeout(kept.timer)
  kept.timer = undefined
  kept.stop?.abort()
  kept.stop = null
  kept.id = null
  kept.foreign = false
  kept.failures = 0
}

// The next read is scheduled once this one has settled, like usePoll's, so two never overlap.
// It is scheduled whatever the tick threw, too: a keep-alive that ended on an exception would
// leave its job to stop a lease later, and nothing on the page would say why.
async function keepTick(stop: AbortController): Promise<void> {
  kept.timer = undefined
  const id = kept.id
  if (kept.stop !== stop || !id) return
  try {
    // The page mounted as this read is sent. Only that page may show the answer: one mounted
    // since (after a sign-out, perhaps by someone else) has read the job for itself. The marker
    // goes only on a read no page will show. A mounted page shows the answer (hidden, or out
    // of touch), so its read delivers a finished job, as the poll's does.
    const page = kept.page
    if (!page?.polling(id)) {
      const read = await readJobOnce(id, stop.signal, !page)
      if (read !== null && kept.stop === stop) {
        if (page && kept.page === page) page.heard(id, read)
        else keptAlone(read)
      }
    }
  } catch (e) {
    console.error('watch-builder: a keep-alive tick threw; the next one is scheduled all the same', e)
  } finally {
    if (kept.stop === stop) kept.timer = window.setTimeout(() => void keepTick(stop), KEEPALIVE_MS)
  }
}

/** A keep-alive read no page will show: none was mounted when it was sent, or another one is
 *  now. An outage asks on, at the keep-alive's pace, for as long as the tab is open. A final
 *  job, a 404 or a refusal (401/403) ends the keep-alive and forgets the id. Nothing of the
 *  answer is kept: the next page to mount reads for itself, as its own viewer, and finds a pass
 *  that finished meanwhile in the listing, where a read sent with no page mounted left it
 *  undelivered. (A read sent for a page that unmounted before it was answered was a plain
 *  read, and delivered it.) */
function keptAlone(read: JobRead): void {
  if ('failed' in read) {
    kept.failures += 1
    return
  }
  kept.failures = 0
  if ('job' in read && isActiveJob(read.job.state)) return
  stopKeepAlive()
}

/** A page mounting: from now on the keep-alive's reads go to it. Answers the job the keep-alive
 *  renews (its id, and whether this page submitted it), or null. The page reads that job as its
 *  own viewer before it shows anything of it. */
function attachPage(page: JobPage): { id: string; foreign: boolean } | null {
  kept.page = page
  return kept.id ? { id: kept.id, foreign: kept.foreign } : null
}

function VariantGrid({ variants, onPick }: {
  variants: Variant[]
  onPick: (d: Design) => void
}) {
  if (!variants.length) return null
  return (
    <div className="wb-variants">
      {variants.map((v, i) => (
        <div key={i} className={`wb-variant${v.ok ? '' : ' failed'}`}>
          <div className="wb-variant-axis">{v.axis}</div>
          {v.ok && v.design ? (
            <>
              <div className="wb-variant-name">{v.design.name}</div>
              {v.design.note && <div className="wb-sub">{v.design.note}</div>}
              <div className="wb-variant-swatches">
                {[v.design.background, ...v.design.elements.map((e) => e.color || '#FFFFFF')]
                  .filter((c, idx, arr) => arr.indexOf(c) === idx)
                  .slice(0, 6)
                  .map((c) => <DesignChip key={c} designColour={c} />)}
              </div>
              <div className="wb-sub">{v.design.elements.length} elements</div>
              <button className="wb-btn" onClick={() => onPick(v.design!)}>Open this one</button>
            </>
          ) : (
            <div className="wb-sub">{v.error}</div>
          )}
        </div>
      ))}
    </div>
  )
}

/** Starting points, each with what it reports and what it only depicts.
 *
 * The `live` / `limits` split is the reason this is a gallery with prose rather than a
 * dropdown of names. Every preset here is derived from a watchface whose every mark reported
 * something real, and most of that wiring (a calendar, a task list, a work timer) is not
 * something this rail has. A ring that looks like a countdown and counts nothing is worth
 * shipping, and is only worth shipping if it says so where you choose it.
 */
function PresetGrid({ presets, onPick }: {
  presets: Preset[]
  onPick: (p: Preset) => void
}) {
  if (!presets.length) return <div className="wb-sub">Loading presets…</div>
  return (
    <div className="wb-presets">
      {presets.map((p) => (
        <div key={p.id} className="wb-preset">
          <div className="wb-preset-head">
            <span className="wb-preset-name">{p.label}</span>
            <span className="wb-sub">{p.design.platforms.join(' · ')}</span>
          </div>
          <div className="wb-preset-swatches">
            {[...new Set(p.design.elements.map((e) => e.color || '#FFFFFF'))]
              .slice(0, 8)
              .map((c) => <DesignChip key={c} designColour={c} />)}
          </div>
          <div className="wb-preset-why">{p.why}</div>
          <div className="wb-preset-live">
            {p.live.map((k) => <span key={k} className="wb-tag live">{k}</span>)}
            {!p.live.length && <span className="wb-tag">nothing live</span>}
          </div>
          {p.limits.length > 0 && (
            <ul className="wb-preset-limits">
              {p.limits.map((l, i) => <li key={i}>{l}</li>)}
            </ul>
          )}
          <div className="wb-preset-foot">
            <span className="wb-sub">{p.after} · {p.design.elements.length} elements</span>
            <button className="wb-btn primary" onClick={() => onPick(p)}>Use this</button>
          </div>
        </div>
      ))}
    </div>
  )
}

// --- module -----------------------------------------------------------------------------

export default function WatchBuilderModule() {
  const [caps, setCaps] = useState<Capabilities | null>(null)
  const [design, setDesign] = useState<Design>(blankDesign)
  const [selected, setSelected] = useState(0)
  const [tab, setTab] = useState<Tab>('design')
  const [report, setReport] = useState<LayoutReport | null>(null)
  const [build, setBuild] = useState<BuildResponse | null>(null)
  // Default shows what the watch shows. The toggle opts IN to the raw
  // framebuffer, which is the unusual thing to want.
  const [showRaw, setShowRaw] = useState(false)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const [prompt, setPrompt] = useState('')
  const [variants, setVariants] = useState<Variant[]>([])
  const [estimate, setEstimate] = useState<Estimate | null>(null)
  const [pending, setPending] = useState<null | { kind: 'design' | 'variations'; count: number }>(null)
  // The AI job this page is showing, as the last poll answered it, and why one stopped being
  // shown when that was not its own doing (a restart lost it, the rail stopped answering).
  const [job, setJob] = useState<AiJob | null>(null)
  const [jobGone, setJobGone] = useState('')
  // The lease of the job last shown, for the line a refusal leaves once the job is cleared.
  const shownLease = useRef<number | undefined>(undefined)
  // The job a read has shown this page (applyJob). The keep-alive's job is said to be gone or
  // refused only if it is this one.
  const shownJob = useRef<string | null>(null)
  // MAX_FAILED_POLLS reads in a row failed: the poll stands down, the keep-alive asks on.
  const [outOfTouch, setOutOfTouch] = useState(false)
  // The kept job this page mounted with, while a read of it has failed and none has been
  // answered since: the page shows UNREACHED, which names no job.
  const [unreached, setUnreached] = useState<string | null>(null)
  // A job this page did not submit: the one a 409 named, started in another tab or earlier.
  // Its progress is shown, but a design it finishes is OFFERED rather than put in the editor,
  // which may hold edits this tab has not saved. busyLine is the 409's message, shown until
  // that job is final.
  const foreignJob = useRef<string | null>(null)
  const [busyLine, setBusyLine] = useState('')
  const [offer, setOffer] = useState<null | { design: Design; layout: LayoutReport | null }>(null)

  const [saved, setSaved] = useState<SavedRow[]>([])
  const [presets, setPresets] = useState<Preset[]>([])
  const [designId, setDesignId] = useState<string | null>(null)

  const [uploads, setUploads] = useState<UploadedImage[]>([])
  // Which platform the canvas is showing. The design may target several and they have
  // genuinely different layouts, so the preview has to name the one it is drawing.
  const [canvasPlatform, setCanvasPlatform] = useState<PlatformName | null>(null)
  const [zoom, setZoom] = useState(2)
  // Default ON, matching the Preview tab. Pebble's screens desaturate hard, so the
  // question a preview answers is "what will this look like", and the colour field
  // already answers "what did I type".
  const [onWatchColours, setOnWatchColours] = useState(true)
  const fileRef = useRef<HTMLInputElement>(null)

  // Capabilities, polled. Model residency changes without anyone touching the UI: the broker
  // evicts on a keep_alive expiry and a design pass warms the model back up. A one-shot
  // fetch renders a state that is correct for about a second, and because loading a model is
  // something you CAUSE by using the rail, the chips would be stale exactly when you look.
  useEffect(() => {
    let live = true
    const tick = () => {
      getJSON<Capabilities>('/api/capabilities')
        .then((d) => live && setCaps(d))
        .catch(() => live && setCaps(null))
    }
    tick()
    const id = window.setInterval(tick, 6000)
    return () => { live = false; window.clearInterval(id) }
  }, [])

  const refreshSaved = useCallback(() => {
    getJSON<{ designs: SavedRow[] }>('/api/designs')
      .then((d) => setSaved(d.designs || []))
      .catch(() => setSaved([]))
  }, [])

  useEffect(() => { refreshSaved() }, [refreshSaved])

  // Fetched once, with no identity and no polling: presets are static and identical for
  // every user, unlike saved designs.
  useEffect(() => {
    getJSON<{ presets: Preset[] }>('/api/presets')
      .then((d) => setPresets(d.presets || []))
      .catch(() => setPresets([]))
  }, [])

  const refreshUploads = useCallback(() => {
    getJSON<{ images: UploadedImage[] }>('/api/images')
      .then((d) => setUploads(d.images || []))
      .catch(() => setUploads([]))
  }, [])

  useEffect(() => { refreshUploads() }, [refreshUploads])

  // The canvas platform follows the design unless the user has picked one that is still a
  // target. Without this, unticking a platform would leave the canvas drawing a screen the
  // design no longer builds for.
  useEffect(() => {
    setCanvasPlatform((cur) =>
      cur && design.platforms.includes(cur) ? cur : (design.platforms[0] ?? null))
  }, [design.platforms])

  // Validate on every edit, debounced. Cheap (no GPU, no compiler) and it is the feedback
  // that makes the percentage-vs-pixel distinction learnable: move an element and the
  // overlap finding appears or clears immediately, naming the platform.
  const timer = useRef<number | undefined>(undefined)
  useEffect(() => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      postJSON<LayoutReport>('/api/validate', design)
        .then(setReport)
        .catch((e) => { setReport(null); setErr(String(e.message || e)) })
    }, 250)
    return () => window.clearTimeout(timer.current)
  }, [design])

  const patch = useCallback((i: number, p: Partial<Element>) => {
    setDesign((d) => ({
      ...d,
      elements: d.elements.map((el, idx) => (idx === i ? { ...el, ...p } : el)),
    }))
  }, [])

  const addElement = (type: ElementType) => {
    setDesign((d) => ({ ...d, elements: [...d.elements, newElement(type)] }))
    setSelected(design.elements.length)
  }

  const removeElement = (i: number) => {
    setDesign((d) => ({ ...d, elements: d.elements.filter((_, idx) => idx !== i) }))
    setSelected((s) => Math.max(0, s > i ? s - 1 : s))
  }

  const move = (i: number, delta: number) => {
    setDesign((d) => {
      const next = [...d.elements]
      const j = i + delta
      if (j < 0 || j >= next.length) return d
      ;[next[i], next[j]] = [next[j], next[i]]
      return { ...d, elements: next }
    })
    setSelected(i + delta)
  }

  const togglePlatform = (name: PlatformName) => {
    setDesign((d) => {
      const on = d.platforms.includes(name)
      // Never allow zero platforms: the document would be invalid and every subsequent
      // validate would 422 with a message about a field the user did not knowingly edit.
      if (on && d.platforms.length === 1) return d
      return {
        ...d,
        platforms: on ? d.platforms.filter((p) => p !== name) : [...d.platforms, name],
      }
    })
  }

  const upload = async (files: FileList | null) => {
    if (!files || !files.length) return
    setErr(''); setBusy('Uploading…')
    try {
      let last: UploadedImage | null = null
      for (const f of Array.from(files)) {
        const fd = new FormData()
        fd.append('file', f)
        last = await postForm<UploadedImage>('/api/images', fd)
      }
      refreshUploads()
      // Land the upload on the canvas immediately. Uploading and then having to find an
      // "add image" menu is a step nobody wants, and an image in the list that is not in
      // the design looks like the upload failed.
      if (last) {
        const el = newElement('image')
        el.image_id = last.id
        setDesign((d) => ({ ...d, elements: [...d.elements, el] }))
        setSelected(design.elements.length)
        setTab('design')
      }
    } catch (e) { setErr(String((e as Error).message || e)) } finally { setBusy('') }
  }

  const imageUrl = useCallback((id: string) => `/watch-builder/api/images/${id}/raw`, [])

  const dropUpload = (e: React.DragEvent) => {
    e.preventDefault()
    upload(e.dataTransfer?.files ?? null)
  }

  const doBuild = async () => {
    setErr(''); setBusy('Building and rendering…'); setBuild(null)
    try {
      const res = await postJSON<BuildResponse>('/api/build', { design, render: true })
      setBuild(res); setTab('preview')
    } catch (e) {
      const status = (e as { status?: number }).status
      setErr(status === 503
        ? 'No Pebble SDK in this environment. Export the source instead; the SDK has no Windows build.'
        : String((e as Error).message || e))
    } finally { setBusy('') }
  }

  const exportSource = async () => {
    setErr(''); setBusy('Generating source…')
    try {
      const res = await postJSON<{ files: Record<string, string> }>('/api/source', design)
      // One file at a time rather than a zip: adding a zip writer to this rail for three
      // files is not a trade worth making, and the SDK layout is two directories deep.
      for (const [path, content] of Object.entries(res.files)) {
        const b64 = btoa(unescape(encodeURIComponent(content)))
        downloadBase64(b64, path.split('/').pop() || 'file.txt')
      }
    } catch (e) { setErr(String((e as Error).message || e)) } finally { setBusy('') }
  }

  // Every AI action is a two-step: ask what it costs, show that, then run it on a second
  // click. The GPU may be shared with other work and the broker evicts
  // rather than queues, so spending inference on a single click is not this rail's call; for
  // a viewer the broker answers from Claude, the cost shown is their own subscription instead.
  const askFirst = async (kind: 'design' | 'variations') => {
    setErr('')
    const count = kind === 'variations' ? (caps?.ai.variation_default ?? 9) : 1
    try {
      setEstimate(await getJSON<Estimate>(`/api/ai/estimate?kind=${kind}&count=${count}`))
      setPending({ kind, count })
    } catch (e) { setErr(String((e as Error).message || e)) }
  }

  // One poll's answer, or the job the POST, a cancel or a reattach handed back. Partial
  // variants show as they arrive; a done job's result lands exactly as the old synchronous
  // routes' did (a design replaces the editor's, variants fill the grid), except that a design
  // from a job this page did not submit is only offered (foreignJob).
  const applyJob = useCallback((j: AiJob) => {
    setJob(j)
    shownLease.current = j.lease_s
    shownJob.current = j.job_id
    setUnreached(null)
    const foreign = foreignJob.current === j.job_id
    // The keep-alive follows the job this page shows, from submit, attach or pickup to its end.
    if (isActiveJob(j.state)) keepAlive(j.job_id, foreign)
    else if (kept.id === j.job_id) stopKeepAlive()
    if (foreign && !isActiveJob(j.state)) setBusyLine('')
    if (j.kind === 'variations' && j.state !== 'done') setVariants(j.partial || [])
    if (j.state === 'done') {
      if (j.kind === 'design' && j.result?.design && foreign) {
        setOffer({ design: j.result.design, layout: j.result.layout ?? null })
      } else if (j.kind === 'design' && j.result?.design) {
        setDesign(j.result.design)
        setReport(j.result.layout ?? null)
        setSelected(0)
        setTab('design')
      } else if (j.kind === 'variations') {
        setVariants(j.result?.variants || [])
      }
    } else if (j.state === 'failed' && !j.stopped) {
      // A design the rail stopped is not a model failure; stoppedLine says why instead.
      setErr(j.error || 'The AI job failed.')
    }
  }, [])

  // `signal` is the mounted page's, so a pickup answered after it unmounted shows nothing.
  const attach = useCallback(async (jobId: string, signal?: AbortSignal) => {
    try {
      const j = await getJSON<AiJob>(`/api/ai/jobs/${jobId}`, signal)
      if (!signal?.aborted) applyJob(j)
    } catch (e) {
      if (signal?.aborted) return
      // The 409's line says the job is shown here; once it is gone, that is no longer true.
      if ((e as ApiError).status === 404) { setJobGone(LOST_JOB); setBusyLine('') }
    }
  }, [applyJob])

  // What one read of a job this page has shown means for it, from its poll or from the
  // keep-alive, or a 200 for the job it mounted with (JobPage.heard in the mount effect). Every
  // read that reaches the rail renews the job's lease there (jobs.get).
  const heard = useCallback((id: string, read: JobRead): 'stop' | undefined => {
    if ('job' in read) {
      kept.failures = 0
      setOutOfTouch(false)
      applyJob(read.job)
      return isActiveJob(read.job.state) ? undefined : 'stop'
    }
    // A 404 is final: the job is not on the rail any more, so asking on would wait forever. A
    // 401/403 is too: no read from this page reaches the rail as the job's owner.
    if ('gone' in read || 'refused' in read) {
      const why = 'gone' in read ? LOST_JOB : signedOutLine(shownLease.current)
      if (kept.id === id) stopKeepAlive()
      setJob(null); setOutOfTouch(false); setJobGone(why); setBusyLine('')
      return 'stop'
    }
    kept.failures += 1
    if (kept.failures < MAX_FAILED_POLLS) return undefined
    // Out of touch. The poll stands down, and the keep-alive asks on at its own pace.
    setOutOfTouch(true)
    return 'stop'
  }, [applyJob])

  const pollingId = job && isActiveJob(job.state) && !outOfTouch ? job.job_id : null
  const pollingRef = useRef<string | null>(null)
  pollingRef.current = pollingId
  usePoll(pollingId ? async (signal) => {
    const read = await readJobOnce(pollingId, signal)
    return read === null ? 'stop' : heard(pollingId, read)
  } : null, JOB_POLL_MS, [pollingId])

  // On mount. The page takes the keep-alive's reads from now on (the keep-alive is not this
  // component's, see "the keep-alive, outside React", and runs on when the page unmounts). If
  // it is keeping a job (the viewer left for another rail in this tab), the page reads that job
  // as its own viewer before it shows anything of it: the viewer's own job carries on here, and
  // anyone else's is a 404, which ends the keep-alive and shows nothing. Otherwise, or after
  // that 404, the page picks up the viewer's newest job still running or not yet shown (GET
  // /api/ai/jobs): after a reload, in a new tab, or a pass that finished while no page was
  // mounted (a pass that finished in a hidden tab was delivered there, so it is not offered).
  // Until a read of the kept job is answered 200, the page judges the keep-alive's reads of it
  // as it judges its own: a failure shows UNREACHED, and a 404 or a refusal says nothing, since
  // this page cannot tell a job the rail lost from someone else's.
  useEffect(() => {
    const stop = new AbortController()
    let pickedUp = false
    const pickUp = () => {
      if (pickedUp) return
      pickedUp = true
      getJSON<{ jobs: AiJobSummary[] }>('/api/ai/jobs', stop.signal)
        .then((d) => {
          if (stop.signal.aborted) return
          const pick = (d.jobs || []).find((j) => isActiveJob(j.state) || !j.delivered)
          if (pick) attach(pick.job_id, stop.signal)
        })
        .catch(() => { /* no listing, so nothing to pick up; the page works as it did */ })
    }
    const page: JobPage = {
      polling: (id) => pollingRef.current === id && document.visibilityState !== 'hidden',
      heard: (id, read) => {
        if ('job' in read || shownJob.current === id) {
          // A job this page has shown, or the rail's 200 for one it has not: the read was
          // made as this page's viewer, so the job is theirs.
          heard(id, read)
        } else if ('failed' in read) {
          // Out of touch: the keep-alive asks on, and its first answer is shown here.
          kept.failures += 1
          if (kept.id === id) setUnreached(id)
        } else {
          // Not this viewer's to read: someone else's job, one a restart lost, or a refusal.
          if (kept.id === id) stopKeepAlive()
          setUnreached(null)
          pickUp()
        }
      },
    }
    const ours = attachPage(page)
    if (!ours) {
      pickUp()
    } else {
      foreignJob.current = ours.foreign ? ours.id : null
      void readJobOnce(ours.id, stop.signal).then((read) => {
        if (read !== null && !stop.signal.aborted) page.heard(ours.id, read)
      })
    }
    return () => {
      stop.abort()
      if (kept.page === page) kept.page = null
    }
  }, [attach, heard])

  const runPending = async () => {
    if (!pending) return
    const { kind, count } = pending
    setPending(null); setEstimate(null); setErr(''); setJobGone(''); setBusyLine(''); setOffer(null)
    setOutOfTouch(false)
    foreignJob.current = null
    if (kind === 'variations') setVariants([])
    try {
      const started = kind === 'design'
        ? await postJSON<AiJob>('/api/ai/design', { prompt, platforms: design.platforms })
        : await postJSON<AiJob>('/api/ai/variations', { design, count })
      applyJob(started)
    } catch (e) {
      const err = e as ApiError
      const detail = (err.body as { detail?: { error?: string; job_id?: string } } | undefined)?.detail
      if (err.status === 409 && detail?.error === 'busy' && detail.job_id) {
        // This viewer already has a job running, from another tab or from before. Show that
        // one rather than nothing, as a job this page did not ask for (foreignJob). A 409
        // 'finishing' names a cancelled job, which has nothing to show; its message is all.
        foreignJob.current = detail.job_id
        setBusyLine(String(err.message || e))
        attach(detail.job_id)
      } else {
        setErr(String(err.message || e))
      }
    }
  }

  const openOffer = () => {
    if (!offer) return
    setDesign(offer.design); setReport(offer.layout); setSelected(0); setTab('design')
    setOffer(null)
  }

  const cancelJob = async () => {
    if (!job) return
    try {
      applyJob(await deleteJSON<AiJob>(`/api/ai/jobs/${job.job_id}`))
    } catch (e) { setErr(String((e as Error).message || e)) }
  }

  const jobActive = !!job && isActiveJob(job.state)

  const save = async () => {
    setErr(''); setBusy('Saving…')
    try {
      const res = await postJSON<{ id: string }>('/api/designs', { id: designId, design })
      setDesignId(res.id); refreshSaved()
    } catch (e) { setErr(String((e as Error).message || e)) } finally { setBusy('') }
  }

  const open = async (id: string) => {
    setErr(''); setBusy('Opening…')
    try {
      const rec = await getJSON<{ id: string; design: Design }>(`/api/designs/${id}`)
      setDesign(rec.design); setDesignId(rec.id); setSelected(0); setBuild(null); setTab('design')
    } catch (e) { setErr(String((e as Error).message || e)) } finally { setBusy('') }
  }

  const drop = async (id: string) => {
    try { await del(`/api/designs/${id}`); if (id === designId) setDesignId(null); refreshSaved() }
    catch (e) { setErr(String((e as Error).message || e)) }
  }

  const current = design.elements[selected]
  const buildAvailable = caps?.build_available ?? false

  const platformList = useMemo(() => caps?.platforms ?? [], [caps])

  return (
    <div className="watch-builder">
      <RailHeader
        icon="⌚"
        title="Watch Builder"
        subtitle="Design a Pebble watchface, compile it for all seven platforms, see it rendered"
        chips={
          <ModelChips
            status={caps ? (caps.broker === 'ok' ? 'ok' : 'unreachable') : 'checking'}
            models={caps?.models}
            trailing={caps
              ? (caps.build_available ? `SDK ${caps.sdk_version ?? 'ready'}` : 'no SDK here')
              : undefined}
          />
        }
        actions={
          <>
            <div className="wb-tabs">
              {(['design', 'presets', 'ai', 'preview'] as Tab[]).map((t) => (
                <button key={t} className={'wb-tab' + (tab === t ? ' on' : '')}
                        onClick={() => setTab(t)}>
                  {t === 'design' ? 'Design' : t === 'presets' ? 'Presets'
                    : t === 'ai' ? 'AI' : 'Preview'}
                </button>
              ))}
            </div>
            <button className="wb-btn" onClick={save} disabled={!!busy}>Save</button>
            <button className="wb-btn" onClick={exportSource} disabled={!!busy}>Export C</button>
            <button className="wb-btn primary" onClick={doBuild}
                    disabled={!!busy || !buildAvailable}
                    title={buildAvailable ? undefined
                      : 'The Pebble SDK has no Windows build; run the rail container to compile'}>
              Build &amp; render
            </button>
          </>
        }
      />

      {busy && <div className="wb-note">{busy}</div>}
      {job && jobActive && (
        <div className="wb-job" role="status">
          <span className="wb-job-line">
            {jobLine(job)} <span className="wb-sub wb-job-lease">{leaseLine(job)}</span>
          </span>
          <button className="wb-btn" onClick={cancelJob}>Cancel job</button>
        </div>
      )}
      {job?.state === 'cancelled' && <div className="wb-note">{cancelledLine(job)}</div>}
      {job?.stopped && job.state !== 'cancelled' && (
        <div className="wb-note wb-stopped" role="status">{stoppedLine(job)}</div>
      )}
      {offer && (
        <div className="wb-offer" role="status">
          <span className="wb-offer-line">
            Your other AI job finished a design: {offer.design.name}. Opening it replaces the
            design in the editor.
          </span>
          <button className="wb-btn" onClick={openOffer}>Open this design</button>
        </div>
      )}
      {jobGone && <div className="wb-err">{jobGone}</div>}
      {outOfTouch && job && isActiveJob(job.state) && (
        <div className="wb-err wb-out-of-touch" role="status">{OUT_OF_TOUCH}</div>
      )}
      {unreached && unreached === kept.id && (
        <div className="wb-err wb-unreached" role="status">{UNREACHED}</div>
      )}
      {busyLine && <div className="wb-err wb-busy">{busyLine}</div>}
      {err && <div className="wb-err">{err}</div>}

      {tab === 'design' && (
        <div className="wb-cols">
          <div className="wb-pane">
            <div className="wb-pane-head">
              <input className="wb-name" value={design.name}
                     onChange={(e) => setDesign((d) => ({ ...d, name: e.target.value }))} />
            </div>

            <div className="wb-row">
              <span className="wb-label">Background</span>
              <span className="wb-colour">
                <input value={design.background} spellCheck={false}
                       onChange={(e) => setDesign((d) => ({
                         ...d, background: e.target.value.toUpperCase() }))} />
                <DesignChip designColour={design.background} />
              </span>
            </div>
            <div className="wb-swatches">
              {/* Indexed, not `(c) =>`, so the status checks resolve every swatch and the
                  colour it applies to the literals in SWATCHES instead of failing on `c`. */}
              {SWATCHES.map((_, i) => (
                <button key={SWATCHES[i]} className="wb-sw" style={{ background: SWATCHES[i] }}
                        title={SWATCHES[i]}
                        onClick={() => {
                          if (current) patch(selected, { color: SWATCHES[i] })
                          else setDesign((d) => ({ ...d, background: SWATCHES[i] }))
                        }} />
              ))}
            </div>

            <div className="wb-pane-head">Platforms</div>
            <div className="wb-platforms">
              {platformList.map((p) => (
                <label key={p.name} className={'wb-plat' + (design.platforms.includes(p.name) ? ' on' : '')}>
                  <input type="checkbox" checked={design.platforms.includes(p.name)}
                         onChange={() => togglePlatform(p.name)} />
                  <span className="wb-plat-name">{p.name}</span>
                  <span className="wb-sub">{p.width}x{p.height}{p.round ? ' round' : ''}
                    {p.colors === 2 ? ' B/W' : ''}</span>
                </label>
              ))}
            </div>

            <div className="wb-pane-head">Elements</div>
            <ul className="wb-list">
              {design.elements.map((el, i) => (
                <li key={i} className={'wb-item' + (i === selected ? ' on' : '')}
                    onClick={() => setSelected(i)}>
                  <DesignChip designColour={el.color || '#FFFFFF'} />
                  <span className="wb-item-name">{ELEMENT_LABEL[el.type]}</span>
                  <span className="wb-spacer" />
                  <button className="wb-mini" title="Move back (drawn earlier)"
                          onClick={(e) => { e.stopPropagation(); move(i, -1) }}>↑</button>
                  <button className="wb-mini" title="Move forward (drawn later)"
                          onClick={(e) => { e.stopPropagation(); move(i, 1) }}>↓</button>
                  <button className="wb-mini" title="Remove"
                          onClick={(e) => { e.stopPropagation(); removeElement(i) }}>✕</button>
                </li>
              ))}
            </ul>
            <div className="wb-sub">Drawn top to bottom, so later elements paint over earlier.</div>

            <div className="wb-add">
              <select defaultValue="" onChange={(e) => {
                if (e.target.value) { addElement(e.target.value as ElementType); e.target.value = '' }
              }}>
                <option value="">Add an element…</option>
                {ELEMENT_TYPES.map((t) => (
                  <option key={t} value={t}>{ELEMENT_LABEL[t]}</option>
                ))}
              </select>
            </div>
          </div>

          <div className="wb-pane">
            {current
              ? <Inspector element={current} onChange={(p) => patch(selected, p)} />
              : <div className="wb-sub">No elements yet. Add one on the left.</div>}
            <Findings report={report} />
            <ColourShift report={report} />
          </div>

          <div className="wb-pane wb-canvas-pane" onDragOver={(e) => e.preventDefault()}
               onDrop={dropUpload}>
            <div className="wb-canvas-bar">
              <select value={canvasPlatform ?? ''}
                      onChange={(e) => setCanvasPlatform(e.target.value as PlatformName)}>
                {design.platforms.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <span className="wb-spacer" />
              {[1, 2, 3].map((z) => (
                <button key={z} className={'wb-tab' + (zoom === z ? ' on' : '')}
                        onClick={() => setZoom(z)}>{z}x</button>
              ))}
            </div>

            <Canvas
              design={design}
              boxes={(canvasPlatform && report?.boxes?.[canvasPlatform]) || []}
              screen={(canvasPlatform && report?.screens?.[canvasPlatform]) || null}
              platform={(canvasPlatform ?? 'emery') as PlatformName}
              selected={selected}
              zoom={zoom}
              imageUrl={imageUrl}
              onSelect={setSelected}
              onCommit={patch}
              onWatchColours={onWatchColours}
              onToggleColours={setOnWatchColours}
            />

            <div className="wb-pane-head">Images</div>
            <div className="wb-upload">
              <input ref={fileRef} type="file" accept="image/*" multiple hidden
                     onChange={(e) => { upload(e.target.files); e.target.value = '' }} />
              <button className="wb-btn" onClick={() => fileRef.current?.click()}>
                Upload an image
              </button>
              <span className="wb-sub">or drop one on the canvas</span>
            </div>
            <ul className="wb-list wb-uploads">
              {uploads.map((u) => (
                <li key={u.id} className="wb-item">
                  <img className="wb-thumb" src={imageUrl(u.id)} alt="" />
                  <span className="wb-item-name" title={u.name}>{u.name}</span>
                  <span className="wb-spacer" />
                  <span className="wb-sub">{u.width}x{u.height} · {Math.round(u.bytes / 1024)}KB</span>
                  {current?.type === 'image' && (
                    <button className="wb-mini" title="Use for the selected image element"
                            onClick={() => patch(selected, { image_id: u.id })}>use</button>
                  )}
                  <button className="wb-mini" title="Delete upload"
                          onClick={() => del(`/api/images/${u.id}`).then(refreshUploads)
                            .catch((e) => setErr(String(e.message || e)))}>✕</button>
                </li>
              ))}
            </ul>
            {uploads.length === 0 && (
              <div className="wb-sub">
                Nothing uploaded. Images are downscaled to 320px on upload and then resampled
                per platform at build time, because the SDK cannot scale a bitmap at runtime.
              </div>
            )}

            <div className="wb-pane-head">Saved</div>
            {saved.length === 0 && <div className="wb-sub">Nothing saved yet.</div>}
            <ul className="wb-list">
              {saved.map((s) => (
                <li key={s.id} className={'wb-item' + (s.id === designId ? ' on' : '')}>
                  <span className="wb-item-name" onClick={() => open(s.id)}>{s.name}</span>
                  <span className="wb-spacer" />
                  <span className="wb-sub">{fmtAge(s.updated)}</span>
                  <button className="wb-mini" onClick={() => drop(s.id)} title="Delete">✕</button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {tab === 'presets' && (
        <div className="wb-presets-wrap">
          <div className="wb-note">
            Eight starting points read off Jonas Hietala&apos;s write-up of designing a
            personal Pebble Time 2 face, including the two designs he rejected. His geometry
            came out of 49 inline SVG prototypes drawn at 200x228, which is emery exactly, so
            these are measured rather than eyeballed. Choosing one replaces the current
            design.
          </div>
          <PresetGrid
            presets={presets}
            onPick={(p) => {
              // A preset is a whole document, so it replaces the design and clears the saved
              // id: saving afterwards should create a new record rather than overwrite the
              // design that happened to be open.
              setDesign(p.design)
              setDesignId(null)
              setSelected(0)
              setBuild(null)
              setTab('design')
            }}
          />
        </div>
      )}

      {tab === 'ai' && (
        <div className="wb-ai">
          <div className="wb-pane">
            <div className="wb-pane-head">Describe a face</div>
            <textarea className="wb-prompt" rows={4} value={prompt}
                      placeholder="A minimal dark face with a big time, the date underneath, and a battery ring around the edge."
                      onChange={(e) => setPrompt(e.target.value)} />
            <button className="wb-btn primary" disabled={!!busy || jobActive || !prompt.trim()}
                    onClick={() => askFirst('design')}>
              Design it
            </button>
            <div className="wb-hint">
              The model emits a design document, never code. A document that would render
              cropped or overlapping is rejected and repaired once before you see it.
            </div>
          </div>

          <div className="wb-pane">
            <div className="wb-pane-head">Explore variations</div>
            <div className="wb-sub">
              Each variant changes one named thing about the current design, so the grid is
              something you can reason about rather than nine near-identical faces.
            </div>
            <button className="wb-btn primary"
                    disabled={!!busy || jobActive || !design.elements.length}
                    onClick={() => askFirst('variations')}>
              Explore {caps?.ai.variation_default ?? 9} variations
            </button>
            <VariantGrid variants={variants} onPick={(d) => {
              setDesign(d); setSelected(0); setTab('design')
            }} />
          </div>

          {pending && (
            <div className="wb-confirm">
              <CostNotice estimate={estimate} />
              <div className="wb-confirm-acts">
                <button className="wb-btn primary" onClick={runPending} disabled={jobActive}>
                  Run it
                </button>
                <button className="wb-btn" onClick={() => { setPending(null); setEstimate(null) }}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === 'preview' && (
        <div className="wb-preview">
          {!build && (
            <div className="wb-sub">
              {buildAvailable
                ? 'Press Build & render to compile this design and screenshot it on every target emulator.'
                : 'No Pebble SDK in this environment, so there is nothing to render. Export the C project instead.'}
            </div>
          )}
          {build && (
            <>
              <div className="wb-row">
                <span className="wb-label">
                  {build.ok
                    ? `Built ${build.platforms.length} platform(s) in ${build.duration_s}s`
                    : `Build failed: ${build.error}`}
                </span>
                <span className="wb-spacer" />
                <label className="wb-field inline">
                  <input type="checkbox" checked={showRaw}
                         onChange={(e) => setShowRaw(e.target.checked)} />
                  <span>Show raw framebuffer</span>
                </label>
                {build.pbw_base64 && (
                  <button className="wb-btn" onClick={() =>
                    downloadBase64(build.pbw_base64!, build.pbw_name || 'watchface.pbw')}>
                    Download .pbw
                  </button>
                )}
              </div>
              <div className="wb-sub">
                {showRaw
                  ? 'Raw framebuffer: the literal palette colours this design specified. '
                    + 'No wrist shows these.'
                  : 'What the watch shows. The SDK applies the reflective display’s colour '
                    + 'correction itself, so this is the on-wrist appearance. Judge colour here.'}
              </div>
              {build.image_warnings?.map((w, i) => (
                <div key={i} className="wb-note">{w}</div>
              ))}
              {!!build.images?.length && (
                <div className="wb-sub">
                  Image resources:{' '}
                  {build.images.map((r) => `${r.platform} ${r.pixels[0]}x${r.pixels[1]} `
                    + `${Math.round(r.bytes / 1024 * 10) / 10}KB`).join(' · ')}
                </div>
              )}
              <PreviewGrid build={build} showRaw={showRaw} />
              <Findings report={build.layout} />
              {build.log_tail && <pre className="wb-log">{build.log_tail}</pre>}
            </>
          )}
        </div>
      )}
    </div>
  )
}
