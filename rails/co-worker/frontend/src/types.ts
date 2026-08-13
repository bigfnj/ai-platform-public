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
  related?: string[]
  thread_id?: string | null
  evidence?: string | null

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
  inbox_dir: string
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
