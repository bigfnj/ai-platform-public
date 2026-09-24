import type { ModelChip } from '@web-core'

const BASE = '/course-builder'

export interface IndexStats {
  present: boolean
  chunks: number
  corpus: string
  embed_role: string
}

export interface Capabilities {
  broker: 'ok' | 'unreachable'
  models: ModelChip[]
  index: IndexStats
}

export interface Blueprint {
  id: string
  name: string
  domains: { name: string; weight: string; skills: string[] }[]
}

async function unwrap<T>(r: Response, path: string): Promise<T> {
  if (!r.ok) {
    let detail = `${path} -> ${r.status}`
    try {
      const j = await r.json()
      if (j?.detail) detail = String(j.detail)
    } catch { /* non-JSON body */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

export async function getJSON<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(BASE + path), path)
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  return unwrap<T>(
    await fetch(BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
    path,
  )
}

export async function deleteJSON<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(BASE + path, { method: 'DELETE' }), path)
}

export const fetchCapabilities = () => getJSON<Capabilities>('/api/capabilities')
export const fetchBlueprints = () => getJSON<Blueprint[]>('/api/blueprints')

/** Stream SSE events. Returns a cleanup function. */
export function streamEvents(
  path: string,
  onMessage: (type: string, data: string) => void,
  onEnd: () => void,
): () => void {
  const ctrl = new AbortController()
  ;(async () => {
    try {
      const resp = await fetch(BASE + path, { signal: ctrl.signal })
      if (!resp.ok || !resp.body) { onEnd(); return }
      const reader = resp.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        let eventType = 'progress'
        for (const line of lines) {
          if (line.startsWith('event:')) eventType = line.slice(6).trim()
          else if (line.startsWith('data:')) {
            onMessage(eventType, line.slice(5).trim())
            if (eventType === 'done' || eventType === 'error') { onEnd(); return }
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') onEnd()
    }
  })()
  return () => ctrl.abort()
}
