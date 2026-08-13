// Same-origin API helpers. In production the shell serves this remote under /co-worker/
// and the gateway proxies /co-worker/api/*. In standalone dev, vite proxies the same path
// to the backend on :8860 (see vite.config.ts).
const BASE = '/co-worker'

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) throw new Error(`${path} -> ${r.status}`)
  return r.json()
}

export async function patchJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    // The inbox is a Windows bind mount; a read-only mount surfaces here as a 503 with
    // a useful detail. Pass it through rather than swallowing it.
    let detail = `${path} -> ${r.status}`
    try {
      const j = await r.json()
      if (j?.detail) detail = String(j.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return r.json()
}
