export type Citation = {
  n: number
  source: string
  collection: string
  title: string
  score: number
}

export type VoicePayload = {
  mode: 'browser' | 'broker' | 'off'
  text: string
  lang: string
  audio_b64?: string
  sample_rate?: number
  degraded?: string
}

export type Answer = {
  answer: string
  citations: Citation[]
  grounded: boolean
  voice?: VoicePayload
}

export type ModelSlot = {
  slot: 'reasoning' | 'retrieval'
  role: string
  model: string
  resident: boolean
  class: 'heavy' | 'embed'
}

export type Capabilities = {
  broker_reachable: boolean
  gpu: { total_mib: number; used_mib: number; free_mib: number; gpu_name: string } | null
  models: ModelSlot[]
  voice: {
    configured: string
    effective: 'browser' | 'broker' | 'off'
    broker_media: boolean
    note: string
  }
  corpus: { chunks: number; dims: number; collections: number }
  user: string
  is_admin: boolean
}

export type Collection = {
  name: string
  label: string
  origin: 'seed' | 'upload'
  ingested_at: string | null
  chunks: number
}

// --- Scenario Builder -------------------------------------------------------
// These mirror `src/smb_partner/scenarios.py`, which is the source of truth. Option `signal`
// values are deliberately absent: they are generator input, not something a partner reads.

export type ScenarioQuestion = {
  id: string
  prompt: string
  /** Shown as "Why this matters" — the coaching, not the form. */
  why: string
  options: string[]
}

export type Scenario = {
  id: string
  icon: string
  title: string
  fit: string
  situation: string
  questions: ScenarioQuestion[]
}

/** One generation pass. Each is real grounded work, so the checklist is honest. */
export type Stage = { key: string; label: string }

export type StageState = 'pending' | 'active' | 'done' | 'error'

export type StageEvent = {
  key: string
  state: Exclude<StageState, 'pending'>
  /** Sentences dropped because they carried a figure absent from the retrieved context. */
  suppressed?: number
  detail?: string
  grounded?: boolean
  sources?: number
}

export type PackageCitation = { source: string; collection: string; title: string }

export type ScenarioPackage = {
  scenario: { id: string; title: string; icon: string; fit: string; situation: string }
  answers: { question: string; answer: string; signal: string }[]
  outputs: Record<string, string>
  citations: Record<string, PackageCitation[]>
  grounded: boolean
  suppressed?: Record<string, number>
  errors?: Record<string, string>
}

export type Turn = {
  id: number
  question: string
  answer: string
  citations: Citation[]
  grounded: boolean
  streaming: boolean
  error?: string
}
