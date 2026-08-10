// web-core shared types.

export type Theme = 'light' | 'dark' | 'system'
export type Tone = 'good' | 'warning' | 'critical' | 'accent' | 'neutral'

// An app in the platform's left rail.
export interface AppEntry {
  id: string
  label: string
  icon: string // emoji or short glyph for the rail
  status: 'ready' | 'soon'
}

// Platform GPU/model status, surfaced by the gateway's /api/platform/*.
export interface Gpu {
  total_mib: number
  used_mib: number
  free_mib: number
  gpu_name: string
}
export interface LoadedModel {
  name: string
  class: 'heavy' | 'embed'
  size_vram: number | null
}
// A job in the broker's GPU queue: the active one plus any waiting behind it.
export interface QueueJob {
  seq: number
  model: string | null
  source: string | null // rail name (from its @role), or null for a generic/manual job
  state: 'active' | 'waiting'
}
export interface PlatformStatus {
  broker_reachable: boolean
  ollama_reachable: boolean
  loaded: LoadedModel[]
  heavy_loaded: string[]
  gpu: Gpu | null
  queue: { active: number; waiting: number }
  jobs: QueueJob[]
}
export interface InstalledModel {
  name: string
  class: 'heavy' | 'embed'
}

// Admin -> Models: the workstation model pool. Every installed model, annotated with whether a
// rail role uses it (in_use + roles), whether it's resident in VRAM now (loaded), and the
// admin availability flag (enabled). Enable/Disable is reversible; Delete is not.
export interface ModelPoolEntry {
  name: string
  class: 'heavy' | 'embed' | string | null
  parameter_size?: string | null
  size?: number | null
  vision: boolean
  modified_at?: string | null
  loaded: boolean
  in_use: boolean
  roles: string[]
  enabled: boolean
}

// Admin -> Schedule: the central scheduler. Each rail owns maintenance tasks with an editable
// recurrence (Outlook-style, minus Duration). The gateway fires them and records next/last run.
export interface Recurrence {
  freq: 'daily' | 'weekly' | 'monthly'
  interval: number
  byweekday?: number[] // weekly, Mon=0 .. Sun=6
  bymonthday?: number // monthly, 1..31 or -1 (last day)
  at: string // 'HH:MM' wall-clock in tz
  tz: string // IANA zone
}
export interface ScheduleTask {
  task_id: string
  label: string
  description: string
  recurrence: Recurrence
  enabled: boolean
  next_run: string | null // ISO UTC
  last_run: string | null
  last_status: string | null
}
export interface RailSchedules {
  rail: string
  icon: string
  tasks: ScheduleTask[]
}

// Theming: a palette (color family) + mode, with a platform default and optional
// per-user override. Effective = override where set, else the platform default.
export interface ThemeState {
  palette: string
  mode: Theme
  default: { palette: string; mode: Theme }
  override: { palette: string | null; mode: string | null } | null
  palettes: string[]
  modes: string[]
}

// Auth / multi-tenant.
export interface Me {
  username: string
  is_admin: boolean
  theme: ThemeState
}
export interface AdminUser {
  id: number
  username: string
  is_admin: boolean
  is_superadmin: boolean
  apps: string[]
}

// Admin -> Rails: per-rail model slots. Each slot maps to a broker role that resolves to
// a concrete installed model; changing it repoints only that rail (per-rail roles).
export interface ModelOption {
  name: string
  class: 'heavy' | 'embed'
  parameter_size?: string | null
  vision: boolean // model accepts image input (a vision slot only offers these)
}
// A media (image) backend for an "image" slot — not an Ollama model.
export interface MediaOption {
  name: string
  label: string
  note?: string
}
export interface RailModelSlot {
  slot: string
  label: string
  role: string
  kind: 'chat' | 'vision' | 'image' // capability; filters which models are offered
  env: string
  default: string // the model/glob this slot shipped with (revert target)
  description: string
  model: string | null // concrete model the role resolves to (null if nothing installed matches)
  pattern: string // the role's stored name/glob
  installed: boolean
}
export interface RailModels {
  id: string
  label: string
  icon: string
  slots: RailModelSlot[]
}
export interface RailsSettings {
  rails: RailModels[]
  models: ModelOption[] // installed Ollama models (chat/vision slots)
  media: MediaOption[] // image backends (image slots)
}
