// Wire types for the Gemini Enterprise CX rail. Mirrors gemini_cx/api.py — keep in step.

export interface DeckQuestion {
  id: string
  text: string
  collections?: string[]
}

export interface DeckGroup {
  id: string
  label: string
  icon: string
  blurb: string
  questions: DeckQuestion[]
}

export interface DeckProblem {
  question: string
  missing_collections: string[]
}

export interface DeckResponse {
  groups: DeckGroup[]
  problems: DeckProblem[]
}

export interface Citation {
  n: number
  source: string
  collection: string
  title: string
  score: number
}

export interface AskResponse {
  question: string
  answer: string
  citations: Citation[]
  collections: string[]
  user: string
}

export interface CorpusStats {
  chunks: number
  dims: number
  collections: number
}

// Four states, one visual language across every rail's header chips:
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

export const STATE_TEXT: Record<ModelState, string> = {
  missing: 'not found',
  cold: 'cold',
  warming: 'warming up',
  loaded: 'GPU · ready',
}

// Mirrors voice.speak() — only `broker` mode carries audio; `browser` mode expects the client
// to synthesize `text` itself. `degraded` is set when a configured broker path failed and we
// silently fell back, so the UI can explain it rather than looking broken.
export interface VoicePayload {
  mode: 'browser' | 'broker' | 'off'
  text: string
  lang: string
  audio_b64?: string
  sample_rate?: number
  degraded?: string
}

export interface VoiceStatus {
  configured: string
  effective: string
  broker_media: boolean
  speaker: string
  note: string
}

export interface Capabilities {
  retrieval: boolean
  answering: boolean
  streaming: boolean
  upload: boolean
  corpus: CorpusStats
  broker: string
  models: ModelSlot[]
  voice: VoiceStatus
}

export interface CollectionRow {
  name: string
  label: string
  origin: string
  ingested_at: string | null
  chunks: number
}

// WebSocket frames from /ws/ask.
export type AskFrame =
  | { type: 'retrieval'; question: string; citations: Citation[] }
  | { type: 'token'; text: string }
  | { type: 'done' }
  | { type: 'error'; error: string }

export function titleCase(id: string): string {
  return id.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
