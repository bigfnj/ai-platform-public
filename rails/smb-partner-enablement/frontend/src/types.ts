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

export type Turn = {
  id: number
  question: string
  answer: string
  citations: Citation[]
  grounded: boolean
  streaming: boolean
  error?: string
}
