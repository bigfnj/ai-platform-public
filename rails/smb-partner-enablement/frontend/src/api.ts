import type {
  AnalysisEvent,
  Answer,
  Capabilities,
  RetrievalEvent,
  Scenario,
  ScenarioPackage,
  Stage,
  StageEvent,
  VoicePayload,
} from './types'

// Both surfaces are served under the rail id by the gateway (the desktop remote at
// /smb-partner-enablement/, the mobile SPA at /smb-partner-enablement/m/), so one absolute
// base works for both and keeps the session cookie in scope.
const BASE = '/smb-partner-enablement'

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const getCapabilities = () => json<Capabilities>('/api/capabilities')

export const ask = (body: {
  question: string
  collections?: string[]
  speak?: boolean
  voice_backend?: string
}) => json<Answer>('/api/ask', { method: 'POST', body: JSON.stringify(body) })

export const getScenarios = () =>
  json<{ scenarios: Scenario[]; stages: Stage[] }>('/api/scenarios')

export const transcribe = (audio_b64: string, suffix: string) =>
  json<{ text: string; language: string; duration: number }>(
    '/api/transcribe', { method: 'POST', body: JSON.stringify({ audio_b64, suffix }) }
  )

export const speakText = (text: string) =>
  json<VoicePayload>('/api/speak', { method: 'POST', body: JSON.stringify({ text }) })

export type PackageHandlers = {
  onStage?: (e: StageEvent) => void
  /** The deterministic analysis: open questions and the hard constraints that fired. */
  onAnalysis?: (e: AnalysisEvent) => void
  /** What a pass is standing on — sourced material and match scores. */
  onRetrieval?: (e: RetrievalEvent) => void
  /** Generation deltas for a pass, so the reasoning is visible as it happens. */
  onToken?: (key: string, token: string) => void
  onPackage?: (p: ScenarioPackage) => void
  onError?: (detail: string) => void
}

/** What a socket that closes with no terminal frame tells the caller. The gateway's relay
 *  swallows an upstream failure and closes with 1000, and these streams used to act only on a
 *  frame or `onerror`, so a clean close left the UI busy until reload. */
export const CLOSED_EARLY = 'the connection closed before the answer finished'

/**
 * Streamed package generation. Five grounded passes run server-side and each reports a stage
 * event, which is what lets the checklist show real progress instead of a spinner — the whole
 * run is ~20s, long enough that an unexplained wait reads as broken.
 *
 * This is the only route that builds a package. The buffered `POST /api/scenario/generate` was
 * retired on 2026-10-14: four passes in one request could outlast the gateway's 600 s read
 * timeout once a pass waited on the Claude CLI, while this socket pings through a silent pass.
 */
export function generatePackage(
  body: { scenario_id: string; answers: Record<string, string> },
  handlers: PackageHandlers,
): () => void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/ws/scenario`)
  const end = terminal(ws)
  ws.onopen = () => ws.send(JSON.stringify(body))
  ws.onmessage = (ev) => {
    const msg = parse(ev.data)
    if (!msg || end.over()) return
    if (msg.type === 'stage') handlers.onStage?.(msg as StageEvent)
    else if (msg.type === 'analysis') handlers.onAnalysis?.(msg as AnalysisEvent)
    else if (msg.type === 'retrieval') handlers.onRetrieval?.(msg as RetrievalEvent)
    else if (msg.type === 'token') handlers.onToken?.(msg.key, msg.token)
    else if (msg.type === 'package') end(() => handlers.onPackage?.(msg.package))
    else if (msg.type === 'error') end(() => handlers.onError?.(msg.detail))
  }
  ws.onerror = () => end(() => handlers.onError?.('connection failed'))
  ws.onclose = () => end(() => handlers.onError?.(CLOSED_EARLY))
  return end.cancel
}

/**
 * Exactly one terminal callback per socket: a result frame, an error frame, a transport error
 * or a close, whichever comes first, and none at all after the caller cancels. The socket is
 * closed once the stream is over. Both streams share it so they cannot disagree.
 */
function terminal(ws: WebSocket) {
  let over = false
  const end = (fn: () => void) => {
    if (over) return
    over = true
    try { ws.close() } catch { /* already closed */ }
    fn()
  }
  end.over = () => over
  end.cancel = () => { if (!over) { over = true; try { ws.close() } catch { /* closed */ } } }
  return end
}

/** A frame that is not JSON is skipped, not thrown out of onmessage. */
function parse(data: unknown): any {
  try {
    return JSON.parse(String(data))
  } catch {
    return null
  }
}

export type StreamHandlers = {
  onCitations?: (c: Answer['citations'], grounded: boolean) => void
  onToken?: (t: string) => void
  onDone?: (a: { answer: string; voice?: Answer['voice'] }) => void
  onError?: (detail: string) => void
}

/**
 * Streamed ask over the gateway's WebSocket proxy, the path this rail delivers its tokens on as
 * they are generated (the gateway relays plain HTTP unbuffered too, since 2026-10-13) — which is
 * what makes the voice agent feel responsive rather than sitting silent for eight seconds.
 */
export function askStream(
  body: { question: string; collections?: string[]; speak?: boolean; voice_backend?: string },
  handlers: StreamHandlers,
): () => void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/ws/ask`)
  const end = terminal(ws)
  ws.onopen = () => ws.send(JSON.stringify(body))
  ws.onmessage = (ev) => {
    const msg = parse(ev.data)
    if (!msg || end.over()) return
    if (msg.type === 'citations') handlers.onCitations?.(msg.citations, msg.grounded)
    else if (msg.type === 'token') handlers.onToken?.(msg.token)
    else if (msg.type === 'done') end(() => handlers.onDone?.({ answer: msg.answer, voice: msg.voice }))
    else if (msg.type === 'error') end(() => handlers.onError?.(msg.detail))
  }
  ws.onerror = () => end(() => handlers.onError?.('connection failed'))
  ws.onclose = () => end(() => handlers.onError?.(CLOSED_EARLY))
  return end.cancel
}
