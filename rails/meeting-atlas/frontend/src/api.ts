// Backend calls. The gateway serves this rail's API at /meeting-atlas/api/*; the vite
// dev server proxies the same prefix to 127.0.0.1:8740 with the prefix stripped, so one
// set of paths works in both places.

const BASE = '/meeting-atlas/api'

export type Keyword = { t: string; n: number }

export type SpeakerStat = {
  speaker: string
  seconds: number
  words: number
  turns: number
  share?: number | null
}

export type Action = {
  owner: string | null
  task: string
  due: string | null
  ref?: string | null
  quote?: string
  claimed_at?: number
  quote_at?: number
  ts_mismatch?: boolean
  quote_missing?: boolean
  quote_reused?: boolean
  due_uniform?: boolean
  due_suspect?: boolean
}

export type Meeting = {
  id: string
  title: string
  auto_title: string
  titled: boolean
  folder: string
  date: string
  week: string
  month: string
  dow: number
  start: string
  end: string
  start_min: number
  duration_s: number
  has_summary: boolean
  summary_model: string | null
  summary_source: string | null
  transcript_source: string | null
  transcript_model: string | null
  n_decisions: number
  n_actions: number
  n_highlights: number
  owners: string[]
  flags: number
  keywords: Keyword[]
  has_audio: boolean
  overview: string
  decisions: string[]
  actions: Action[]
  spoken_s: number
  words: number
  wpm: number
  density: number | null
  n_segments: number
  pauses: number
  pause_s: number
  longest_gap_s: number
  longest_seg_s: number
  questions: number
  activity: number[]
  speakers: SpeakerStat[]
}

export type Corpus = {
  generated_at: string
  recordings_root: string
  available: boolean
  n_meetings: number
  total_s: number
  total_words: number
  first: string | null
  last: string | null
  themes: Keyword[]
  n_flagged?: number
  n_summarised?: number
  n_enriched?: number
}

export type Highlight = { title: string | null; body: string }

export type Summary = {
  title: string | null
  overview: string
  decisions: string[]
  actions: Action[]
  highlights: Highlight[]
  raw: string
}

// [start, duration, text, speaker|null] — positional to keep a long transcript small.
export type Segment = [number, number, string, string | null]

export type Detail = {
  id: string
  segments: Segment[]
  summary: Summary | null
  folder: string
  audio: string | null
  devices: Record<string, string>
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: { accept: 'application/json' } })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export const fetchMeetings = () =>
  get<{ corpus: Corpus; meetings: Meeting[] }>('/meetings')

export const fetchMeeting = (id: string) =>
  get<{ meeting: Meeting; detail: Detail }>(`/meetings/${encodeURIComponent(id)}`)

export const audioUrl = (id: string) =>
  `${BASE}/meetings/${encodeURIComponent(id)}/audio`

export async function reindex(): Promise<{ n_meetings: number; seconds: number }> {
  const r = await fetch(`${BASE}/reindex`, { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}
