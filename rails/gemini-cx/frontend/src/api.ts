// Same-origin API helpers. In production the shell serves this remote under /gemini-cx/ and
// the gateway proxies /gemini-cx/api/* and /gemini-cx/ws/*. In standalone dev, vite proxies
// the same paths to the backend on :8880 (see vite.config.ts).
import type { AskFrame, AskResponse, Capabilities, DeckResponse, VoicePayload } from './types'

const BASE = '/gemini-cx'

/** What a stream that closes with no `done` or `error` frame reports. The gateway's relay
 *  swallows an upstream failure and closes with 1000, so a clean close is not a finished
 *  answer: reporting nothing showed a half-streamed answer as if it were complete. */
export const CLOSED_EARLY = 'the connection closed before the answer finished'

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

/** Synthesize an answer for Read aloud. Kokoro via the broker, or a browser-mode payload. */
export const speakText = (text: string) => postJSON<VoicePayload>('/api/speak', { text })

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
 *
 * `onClose(err, fromServer)`: fromServer is TRUE only for an explicit `error` FRAME, which is
 * the backend telling us why it failed. The two cases must not be conflated. A transport
 * fault (a refused upgrade, a dropped socket) is worth retrying over the buffered route; a
 * mapped broker error is not, because the retry pays for a second retrieval and a second
 * broker call to fail the same way, and the message the backend produced never reaches the
 * user. V-16 is what made that message worth showing.
 *
 * A close with neither `done` nor `error` reports CLOSED_EARLY (fromServer false), and `err`
 * is undefined only after `done`. It used to report nothing, which read as a finished answer.
 */
export function askStreaming(
  req: AskRequest,
  onFrame: (f: AskFrame) => void,
  onClose: (err?: string, fromServer?: boolean) => void,
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
    if (closed) return
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
      onClose(frame.type === 'error' ? frame.error : undefined, frame.type === 'error')
    }
  }
  ws.onerror = () => {
    if (!closed) {
      closed = true
      onClose('websocket failed', false)   // transport, not the server: fallback is right
    }
  }
  ws.onclose = () => {
    if (!closed) {
      closed = true
      onClose(CLOSED_EARLY, false)
    }
  }
  return () => {
    if (!closed) {
      closed = true
      ws.close()
    }
  }
}
