// Same-origin API helpers. In production the shell serves this remote under /openmaic/ and the
// gateway proxies /openmaic/api/*. In standalone dev, vite proxies the same prefix to the backend
// on :8900 (see vite.config.ts).
//
// Same-origin is load-bearing here in a way it is not on other rails: everything this rail shows
// lives behind /openmaic/api/app/, inside an iframe, and the gateway identifies the caller by a
// session cookie. A cross-origin URL for the app would drop that cookie and the whole frame would
// 401, so both the status calls below and the iframe src stay on this one prefix.
import type { ModelChip } from '@web-core'

const BASE = '/openmaic'

/** Where the rail backend reverse-proxies the upstream OpenMAIC Next.js app (the iframe src).
 *  Ends in a slash on purpose: the app is served as a directory root and its own relative
 *  /_next/* asset URLs resolve against it. */
export const APP_PATH = `${BASE}/api/app/`

/** Whether the rail backend can reach the separate `openmaic-app` container it proxies.
 *
 *  This is a second container, not code in this repo, so it can be down while the rail itself is
 *  perfectly healthy — which looks to a user like a blank white iframe with no explanation. The
 *  status route reports it so the UI can say so in words instead. `url` is the upstream the
 *  backend tried, included for the operator who has to go fix it. */
export interface AppStatus {
  reachable: boolean
  url?: string
}

/** The /api/capabilities envelope. `models` is already the shared @web-core ModelChip shape
 *  (slot / label / model / four-valued state / role) because that is exactly what the backend's
 *  modelstate.resolve() emits — so <ModelChips> forwards it rather than re-deriving a state here,
 *  which is what keeps this rail's dots meaning the same thing as every other rail's. */
export interface Capabilities {
  broker: 'ok' | 'unreachable'
  models: ModelChip[]
  app: AppStatus
}

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

export const fetchCapabilities = () => getJSON<Capabilities>('/api/capabilities')
