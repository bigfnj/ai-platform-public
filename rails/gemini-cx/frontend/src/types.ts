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

export interface ModelStatus {
  broker: string
  rag_model: string
  embed_model: string
  rag_resident?: boolean
  embed_resident?: boolean
}

export interface Capabilities {
  retrieval: boolean
  answering: boolean
  streaming: boolean
  upload: boolean
  corpus: CorpusStats
  models: ModelStatus
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
