// API client for the edu-suite dashboard module. Calls the /api surface via the
// gateway (default) so it works when mounted in the shell; override with
// VITE_API_BASE for standalone dev.
const BASE = import.meta.env.VITE_API_BASE || '/edu-suite/api'

export interface Workflow {
  key: string
  label: string
  description: string
}

export interface JobEvent {
  kind: string
  stage?: string
  status?: string
  model?: string
  message?: string
  elapsed?: number | null
}

export interface Job {
  id: string
  name: string
  workflow: string
  status: string
  error?: string | null
  events?: JobEvent[]
  params?: string // JSON string from the store; e.g. {"kind":"generate"} for an IEP generate job
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init)
  if (!r.ok) throw new Error(`${url} -> ${r.status}`)
  return (await r.json()) as T
}

export const listWorkflows = () => req<Workflow[]>(`${BASE}/workflows`)

// Additional-instructions interpretation: the AI restates what it will do (only
// real levers), flags anything out of scope, and returns guidance to pass to drafting.
export interface Interp {
  understanding: string
  applies: string[]
  ignored: { text: string; reason: string }[]
  needs_clarification: boolean
  question: string
  guidance: string
  error?: string
}

export async function interpret(payload: {
  workflow: string
  instructions: string
  weeks: number[]
  worksheets: number
}): Promise<Interp> {
  const r = await fetch(`${BASE}/interpret`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return (await r.json()) as Interp
}
export const listJobs = (q = '', wf = '') =>
  req<Job[]>(`${BASE}/jobs?q=${encodeURIComponent(q)}&workflow=${encodeURIComponent(wf)}`)
export const getJob = (id: string) => req<Job>(`${BASE}/jobs/${id}`)
export const createJob = (fd: FormData) =>
  req<{ id?: string; error?: string }>(`${BASE}/jobs`, { method: 'POST', body: fd })
export const deleteJob = (id: string) => fetch(`${BASE}/jobs/${id}`, { method: 'DELETE' })
export const downloadUrl = (id: string) => `${BASE}/jobs/${id}/download`
// Live site served off the platform (trailing slash so relative fetch/asset paths resolve).
export const siteUrl = (id: string) => `${BASE}/jobs/${id}/site/`

// TeachTown Builder review/edit: a drafted unit the instructor can edit, then build.
export type Vocab = [string, string, string?] // [word, meaning, subject]
export type Mission = [number, string, string, string, string, string[], string, number?]
// [week, subject, title, prompt, type, options, pdfPath, questionCount?]
export interface Unit {
  label: string
  hero?: { h1?: string; p?: string }
  weekInfo: Record<string, { learn: string; v: Vocab[] }>
  missions: Mission[]
  // Per-worksheet interactive activities (kind + data), keyed by the mission's pdfPath.
  // Opaque here; the interactive site renders them. Preserved through review/edit.
  activities?: Record<string, unknown>
  error?: string
}

export async function getUnit(id: string): Promise<Unit> {
  // Returns {error} json on 404 (no draft), so don't throw on non-2xx here.
  const r = await fetch(`${BASE}/jobs/${id}/unit`)
  return (await r.json()) as Unit
}

export const finalize = (id: string, payload: unknown) =>
  req<{ id?: string; error?: string }>(`${BASE}/jobs/${id}/finalize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

// IEP Present Levels: the OCR-extracted 8 sections a teacher reviews and adds to.
export interface PresentLevels {
  header?: { student_name?: string; birthdate?: string; iep_date?: string }
  sections?: Record<string, string>
  assessment_data?: string
  areas_of_need?: string
  warnings?: string[]
  error?: string
}

export async function getPresentLevels(id: string): Promise<PresentLevels> {
  // Returns {error} json on 404 (not yet extracted), so don't throw on non-2xx.
  const r = await fetch(`${BASE}/jobs/${id}/present-levels`)
  return (await r.json()) as PresentLevels
}

export const generateIep = (id: string, payload: unknown) =>
  req<{ id?: string; error?: string }>(`${BASE}/jobs/${id}/generate-iep`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

// --- single-page IEP flow (the IEP-only app) -------------------------------
// Quick, jobless parse: upload a SEIS PDF, get the OCR-extracted sections back
// inline (nothing persisted server-side). Returns {error} json on failure.
export async function parseIep(file: File): Promise<PresentLevels> {
  const fd = new FormData()
  fd.append('file', file)
  const r = await fetch(`${BASE}/iep/parse`, { method: 'POST', body: fd })
  return (await r.json()) as PresentLevels
}

// Generate directly from the filled form (no prior extract job). Returns the new job id.
export const generateIepStandalone = (payload: unknown) =>
  req<{ id?: string; error?: string }>(`${BASE}/iep/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

// The generated 8 sections + Areas of Need for a finished generate job — powers the
// results preview and per-section copy. Returns {error} json on 404 (not ready yet).
export interface PresentLevelsFinal {
  header?: { student_name?: string; birthdate?: string; iep_date?: string }
  name?: string
  sections?: Record<string, string>
  error?: string
}
export async function getPresentLevelsFinal(id: string): Promise<PresentLevelsFinal> {
  const r = await fetch(`${BASE}/jobs/${id}/present-levels-final`)
  return (await r.json()) as PresentLevelsFinal
}
