import type { Answer, Capabilities, Collection } from './types'

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

export const getCollections = () =>
  json<{ collections: Collection[] }>('/api/collections').then((r) => r.collections)

export const reingest = (force = false) =>
  json<unknown>(`/api/ingest?force=${force}`, { method: 'POST' })

export const ask = (body: {
  question: string
  collections?: string[]
  speak?: boolean
  voice_backend?: string
}) => json<Answer>('/api/ask', { method: 'POST', body: JSON.stringify(body) })

export type StreamHandlers = {
  onCitations?: (c: Answer['citations'], grounded: boolean) => void
  onToken?: (t: string) => void
  onDone?: (a: { answer: string; voice?: Answer['voice'] }) => void
  onError?: (detail: string) => void
}

/**
 * Streamed ask over the gateway's WebSocket proxy. The gateway buffers plain HTTP, so this
 * is the only path that delivers tokens as they are generated — which is what makes the
 * voice agent feel responsive rather than sitting silent for eight seconds.
 */
export function askStream(
  body: { question: string; collections?: string[]; speak?: boolean; voice_backend?: string },
  handlers: StreamHandlers,
): () => void {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}${BASE}/ws/ask`)
  ws.onopen = () => ws.send(JSON.stringify(body))
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.type === 'citations') handlers.onCitations?.(msg.citations, msg.grounded)
    else if (msg.type === 'token') handlers.onToken?.(msg.token)
    else if (msg.type === 'done') {
      handlers.onDone?.({ answer: msg.answer, voice: msg.voice })
      ws.close()
    } else if (msg.type === 'error') {
      handlers.onError?.(msg.detail)
      ws.close()
    }
  }
  ws.onerror = () => handlers.onError?.('connection failed')
  return () => ws.close()
}
