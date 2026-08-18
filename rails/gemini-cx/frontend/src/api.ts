// Same-origin API helpers. In production the shell serves this remote under /gemini-cx/ and
// the gateway proxies /gemini-cx/api/* and /gemini-cx/ws/*. In standalone dev, vite proxies
// the same paths to the backend on :8880 (see vite.config.ts).
import type { AskFrame, AskResponse, Capabilities, DeckResponse } from './types'

const BASE = '/gemini-cx'

async function unwrap<T>(r: Response, path: string): Promise<T> {
  if (!r.ok) {
    let detail = `${path} -> ${r.status}`
    try {
      const j = await r.json()
      if (j?.detail) detail = String(j.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export async function getJSON<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(BASE + path), path)
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return unwrap<T>(r, path)
}

export const fetchDeck = () => getJSON<DeckResponse>('/api/questions')
export const fetchCapabilities = () => getJSON<Capabilities>('/api/capabilities')

export interface AskRequest {
  question?: string
  question_id?: string
  collections?: string[]
}

/** Buffered ask — the fallback when a WebSocket cannot be established. */
export const askBuffered = (req: AskRequest) => postJSON<AskResponse>('/api/ask', req)

/**
 * Streamed ask over /ws/ask. Returns a cancel function.
 *
 * A corporate proxy can silently refuse the upgrade, so the caller is expected to fall back to
 * askBuffered on error rather than leaving the user with a dead panel.
 */
export function askStreaming(
  req: AskRequest,
  onFrame: (f: AskFrame) => void,
  onClose: (err?: string) => void,
): () => void {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  let closed = false
  let ws: WebSocket
  try {
    ws = new WebSocket(`${proto}//${location.host}${BASE}/ws/ask`)
  } catch (e) {
    onClose(String(e))
    return () => {}
  }
  ws.onopen = () => ws.send(JSON.stringify(req))
  ws.onmessage = (ev) => {
    let frame: AskFrame
    try {
      frame = JSON.parse(ev.data as string) as AskFrame
    } catch {
      return
    }
    onFrame(frame)
    if (frame.type === 'done' || frame.type === 'error') {
      closed = true
      ws.close()
      onClose(frame.type === 'error' ? frame.error : undefined)
    }
  }
  ws.onerror = () => {
    if (!closed) {
      closed = true
      onClose('websocket failed')
    }
  }
  ws.onclose = () => {
    if (!closed) {
      closed = true
      onClose()
    }
  }
  return () => {
    if (!closed) {
      closed = true
      ws.close()
    }
  }
}
