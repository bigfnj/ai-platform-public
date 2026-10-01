// Co-Worker item contract. Mirrors rails/co-worker/SCHEMA.md — keep both in sync.
// The harvest process (Claude co-work scheduled tasks) writes these; this rail renders them.
//
// When the schema changes: bump SCHEMA_VERSION here, update SCHEMA.md, update the four task
// prompts, and update tools/validate_inbox.py — all in the same change.

export const SCHEMA_VERSION = 2

// --- model status chips (mirrors GET /api/models) ---------------------------
// Four states, one visual language across every rail:
//   missing  RED     the model a role resolves to is not installed
//   cold     BLUE    installed but not resident in VRAM
//   warming  ORANGE  a broker job is queued/active for a model that is not resident yet
//   loaded   GREEN   resident right now
export type ModelState = 'missing' | 'cold' | 'warming' | 'loaded'

export interface ModelSlot {
  slot: string
  label: string
  role: string
  model: string
  state: ModelState
}

export interface ModelsStatus {
  broker: string
  models: ModelSlot[]
  items: number
}

export type Source = 'calendar' | 'email' | 'teams' | 'insights'

export type Status = 'open' | 'done' | 'dismissed'

export type ItemType =
  | 'meeting'
  | 'agenda-draft'
  | 'conflict'
  | 'prep'
  | 'email'
  | 'dangling'
  | 'follow-up'
  | 'reminder'
  | 'fyi'
  | 'noise'
  | 'insight'
  | 'recommendation'

export interface ItemLink {
  label: string
  url: string
}

/** How two items relate (schema v2.2). A bare string in `related` means 'relates-to'. */
export type RelType =
  | 'relates-to'
  | 'answers'
  | 'derives-from'
  | 'duplicates'
  /** That was true and has been overtaken. */
  | 'supersedes'
  /** That was wrong and is withdrawn — an admission, not routine staleness. */
  | 'retracts'
  | 'blocks'

export type RelatedRef = string | { id: string; rel?: RelType }

/** A quantity behind a claim. `prev` is the same measure last period — the delta is
 *  computed here rather than written into prose by the harvest. */
export interface Metric {
  label: string
  value: number
  unit?: string | null
  prev?: number | null
  /** Sample size — `median 36.5s` over 6 observations is a different claim than over 600. */
  n?: number | null
  /** Which way is better. Without it a trend arrow can't be coloured: a rising number is
   *  good for throughput and bad for latency, and nothing else in the item says which. */
  direction?: MetricDirection | null
  target?: number | null
  confidence?: Confidence | null
  /** Whether the number moved or the earlier count was wrong. A `correction` must never
   *  render as a trend arrow — that reports improvement where we simply measured badly. */
  kind?: MetricKind | null
  /** Per-measurement verification; a synthesis item mixes computed and inherited numbers. */
  verification?: Verification | null
}

export type MetricKind = 'movement' | 'correction'

export type MetricDirection = 'up-good' | 'down-good' | 'neutral'

export type Confidence = 'high' | 'medium' | 'low'

/** How the source was actually read. `summary`/`inferred` findings are worth publishing;
 *  ones that merely *look* verified are not. */
export type Verification = 'full-read' | 'summary' | 'inferred'

export interface Series {
  recurrence: 'daily' | 'weekly' | 'biweekly' | 'monthly' | 'irregular'
  series_end?: string | null
  occurrences?: number | null
}

/** One side of a `conflict`. Exactly one entry should carry verdict 'take'. */
export interface Competing {
  label: string
  ref?: string | null
  start?: string | null
  end?: string | null
  verdict?: 'take' | 'drop' | 'defer' | 'delegate' | null
}

/** Normalizes both `related` forms to objects so callers don't branch on type. */
export function relatedEdges(item: Item): { id: string; rel: RelType }[] {
  const raw = Array.isArray(item.related) ? item.related : []
  return raw
    .map((r) =>
      typeof r === 'string'
        ? { id: r, rel: 'relates-to' as RelType }
        : { id: r?.id, rel: (r?.rel ?? 'relates-to') as RelType },
    )
    .filter((e): e is { id: string; rel: RelType } => Boolean(e.id))
}

/** Signed delta vs the previous period, or null when there's no baseline.
 *  Null means "first period — this IS the baseline", not "missing". */
export function metricDelta(m: Metric): number | null {
  return typeof m.prev === 'number' ? m.value - m.prev : null
}

/** Whether a delta is an improvement, given the metric's own sense of direction.
 *  Returns null when there's no baseline, no stated direction, or the change is a
 *  correction — the caller renders those neutral rather than guessing. */
export function deltaIsGood(m: Metric): boolean | null {
  if (isCorrection(m)) return null
  const d = metricDelta(m)
  if (d === null || d === 0 || !m.direction || m.direction === 'neutral') return null
  return m.direction === 'up-good' ? d > 0 : d < 0
}

/** A restatement of a bad count, not movement over time. Render as "was X", never as an
 *  arrow: an arrow here claims improvement that never happened. */
export function isCorrection(m: Metric): boolean {
  return m.kind === 'correction'
}

/** Backend-injected fields are prefixed with `_` and are always present on read. */
export interface Item {
  _id: string
  _file: string
  _mtime: number
  _status: Status

  schema?: number
  type?: ItemType
  source?: Source
  period?: string
  title?: string
  why?: string
  body?: string
  priority?: number
  client?: boolean
  when?: string | null
  due?: string | null
  from?: string | null
  run?: string
  doc?: string | null
  tags?: string[]
  links?: ItemLink[]
  related?: RelatedRef[]
  thread_id?: string | null
  evidence?: string | null
  metrics?: Metric[]
  confidence?: Confidence | null
  competing?: Competing[]
  verification?: Verification | null
  series?: Series | null

  /** Anything the harvest adds that this frontend doesn't know about yet. */
  [key: string]: unknown
}

/** A file the backend could not parse. Surfaced, never hidden. */
export interface SkippedFile {
  file: string
  error: string
}

export interface InboxResponse {
  items: Item[]
  skipped: SkippedFile[]
  /** Echo of the ?period= filter, null when unfiltered. */
  period?: string | null
  /** Every period present, newest first — drives a period picker without a second call. */
  periods?: string[]
  inbox_dir: string
  /** Present on /api/archive instead of inbox_dir. */
  archive_dir?: string
}

export interface DocResponse {
  path: string
  content: string
  mtime: number
}

// --- executive brief (the landing view) -------------------------------------

export type BriefCategory = 'client' | 'dangling' | 'missed' | 'agenda-gap' | 'other'

export type Urgency = 'today' | 'this-week' | 'soon'

/** One curated action. `id` points at an inbox item so triage round-trips. */
export interface AttentionItem {
  id: string
  category: BriefCategory
  headline: string
  urgency: Urgency
  why?: string | null
  /** The id didn't match an inbox item — triage would no-op, so it's not offered. */
  unresolved_id?: boolean
  /** Which per-source pass surfaced this. Set only on the merged brief. */
  source?: Source
}

/** Per-lane coverage on the merged brief: what each pass considered and read. */
export interface BriefPass {
  items_considered: number
  items_read: number
  attention: number
  truncated?: number
}

export interface Brief {
  exists: boolean
  stale: boolean
  generated?: string
  period?: string
  items_considered?: number
  items_triaged?: number
  attention: AttentionItem[]
  client_pulse?: string
  dangling?: string[]
  missed?: string[]
  agenda_gaps?: string[]
  suppressed?: number
  synthesis_note?: string | null
  age_hours?: number
  _mtime?: number
  message?: string
  /** How many items actually reached the model. */
  items_read?: number
  /** Survived the noise/FYI/staleness filters — the denominator for items_read. */
  items_eligible?: number
  /** Excluded on purpose (noise, FYI, stale non-client). Not a shortfall. */
  items_filtered?: number
  /** Eligible items the context budget dropped unread. This IS a shortfall. */
  truncated?: number
  /** True when the inbox has changed since the brief was synthesized (and auto_synthesize is on). */
  stale_source?: boolean
  /** Human-readable reason for staleness (item count changed, item rewritten, etc.). */
  stale_reason?: string | null
  /** Which lane this brief covers; null/undefined on the merged brief. */
  source?: Source | null
  /** Per-lane coverage, merged brief only — what each pass considered and read. */
  passes?: Record<string, BriefPass>
  /** Lanes whose pass errored and are therefore absent from the merge. */
  failed_sources?: string[]
}

export interface BriefStatus {
  running: boolean
  last_started?: number | null
  last_finished?: number | null
  last_error?: string | null
}

export const CATEGORY_META: Record<BriefCategory, { label: string; icon: string; tone: string }> = {
  client: { label: 'Client', icon: '🤝', tone: 'critical' },
  dangling: { label: 'You promised', icon: '🪢', tone: 'critical' },
  missed: { label: 'Waiting on you', icon: '📬', tone: 'warning' },
  'agenda-gap': { label: 'No agenda', icon: '📋', tone: 'warning' },
  other: { label: 'Other', icon: '•', tone: 'neutral' },
}

export const URGENCY_META: Record<Urgency, { label: string; rank: number }> = {
  today: { label: 'Today', rank: 0 },
  'this-week': { label: 'This week', rank: 1 },
  soon: { label: 'Soon', rank: 2 },
}

/** Sort by urgency, then by the category priority the user actually cares about. */
export function attentionRank(a: AttentionItem): number {
  const u = URGENCY_META[a.urgency]?.rank ?? 3
  const catOrder: BriefCategory[] = ['client', 'dangling', 'missed', 'agenda-gap', 'other']
  const c = catOrder.indexOf(a.category)
  return u * 10 + (c === -1 ? 9 : c)
}

// --- display metadata -------------------------------------------------------

// Lens tabs, ordered by how FRESH each lane's data is rather than alphabetically, so the
// staleness gradient reads left to right instead of being invisible: email is harvested daily,
// calendar weekly, teams monthly, and insights is a weekly synthesis OF the other three (a
// separate scheduled task on the host) — two derivations from source data and never newer than
// the lanes it summarises. See the harvest-loop table in the rail README.
export const SOURCES: { id: Source; label: string; icon: string }[] = [
  { id: 'email', label: 'Email', icon: '✉️' },
  { id: 'calendar', label: 'Calendar', icon: '📅' },
  { id: 'teams', label: 'Teams', icon: '💬' },
  { id: 'insights', label: 'Insights', icon: '🧭' },
]

/** Label + semantic tone. Tone maps to --good / --warning / --critical only. */
export const TYPE_META: Record<ItemType, { label: string; tone: 'critical' | 'warning' | 'good' | 'neutral' }> = {
  conflict: { label: 'Conflict', tone: 'critical' },
  dangling: { label: 'Dangling', tone: 'critical' },
  meeting: { label: 'Meeting', tone: 'neutral' },
  'agenda-draft': { label: 'Agenda draft', tone: 'warning' },
  prep: { label: 'Prep', tone: 'good' },
  email: { label: 'Email', tone: 'neutral' },
  'follow-up': { label: 'Follow-up', tone: 'warning' },
  reminder: { label: 'Reminder', tone: 'warning' },
  fyi: { label: 'FYI', tone: 'neutral' },
  noise: { label: 'Noise', tone: 'neutral' },
  insight: { label: 'Insight', tone: 'good' },
  recommendation: { label: 'Recommendation', tone: 'good' },
}

export const PRIORITY_LABEL: Record<number, string> = {
  1: 'P1 · client blocking',
  2: 'P2 · client this week',
  3: 'P3 · internal action',
  4: 'P4 · informational',
  5: 'P5 · noise',
}

export function typeMeta(t: ItemType | undefined) {
  return (t && TYPE_META[t]) || { label: t || 'item', tone: 'neutral' as const }
}

/** Unknown/missing priority sorts last, not first. */
export function priorityOf(i: Item): number {
  const p = typeof i.priority === 'number' ? i.priority : NaN
  return Number.isFinite(p) ? Math.min(5, Math.max(1, p)) : 9
}

/**
 * Human label for a period. `2026W33` -> "week 33 · 2026", `20260811` -> "11 Aug 2026".
 * Periods group items reliably; `run` does not (two runs the same day collide).
 */
export function periodLabel(period: string | undefined): string {
  if (!period) return ''
  const w = /^(\d{4})W(\d{2})$/.exec(period)
  if (w) return `week ${Number(w[2])} · ${w[1]}`
  const d = /^(\d{4})(\d{2})(\d{2})$/.exec(period)
  if (d) {
    const date = new Date(Number(d[1]), Number(d[2]) - 1, Number(d[3]))
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
    }
  }
  return period
}
