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

/** The four-state chip contract, identical in meaning on every rail (see the rail's
 *  modelstate.py): missing = not installed at all (needs an `ollama pull`), cold = installed
 *  but not resident, warming = a broker job is waiting on it, loaded = resident in VRAM. */
export type ModelState = 'missing' | 'cold' | 'warming' | 'loaded'

export type ModelSlot = {
  slot: 'reasoning' | 'retrieval'
  label: string
  role: string
  model: string
  state: ModelState
}

export type Capabilities = {
  /** 'ok' | 'unreachable'. Was a boolean `broker_reachable`; renamed to the shared envelope
   *  key so this rail's payload matches every other rail's. */
  broker: string
  models: ModelSlot[]
  voice: {
    configured: string
    effective: 'browser' | 'broker' | 'off'
    broker_media: boolean
    /** Speech INPUT path. 'broker' = faster-whisper, and the only one that honours the
     *  selected microphone; 'browser' = Web Speech, which always uses the OS default. */
    stt: 'browser' | 'broker'
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
  /** Generation stages for THIS scenario — a practice self-assessment differs from a pre-call
   *  brief, so neither the checklist nor the tabs can be hardcoded. */
  stages: Stage[]
  tabs: { key: string; label: string }[]
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

/** Result of the deterministic first stage: what is open, and which hard rules fired. */
export type AnalysisEvent = {
  known: number
  unknown: string[]
  constraints: string[]
}

/** What a pass retrieved, and how well each piece matched. */
export type RetrievalEvent = {
  key: string
  query: string
  hits: { title: string; source: string; collection: string; score: number }[]
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
