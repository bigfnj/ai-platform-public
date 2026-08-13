// Co-Worker item contract. Mirrors rails/co-worker/SCHEMA.md — keep both in sync.
// The harvest process (Claude co-work scheduled tasks) writes these; this rail renders them.
//
// When the schema changes: bump SCHEMA_VERSION here, update SCHEMA.md, update the four task
// prompts, and update tools/validate_inbox.py — all in the same change.

export const SCHEMA_VERSION = 2

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
  | 'supersedes'
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
}

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
 *  Returns null when there's no baseline or no stated direction — the caller should
 *  render the arrow neutral rather than guess. */
export function deltaIsGood(m: Metric): boolean | null {
  const d = metricDelta(m)
  if (d === null || d === 0 || !m.direction || m.direction === 'neutral') return null
  return m.direction === 'up-good' ? d > 0 : d < 0
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

// --- display metadata -------------------------------------------------------

export const SOURCES: { id: Source; label: string; icon: string }[] = [
  { id: 'calendar', label: 'Calendar', icon: '📅' },
  { id: 'email', label: 'Email', icon: '✉️' },
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
