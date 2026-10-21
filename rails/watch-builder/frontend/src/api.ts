// Same-origin API helpers. In production the shell serves this remote under /watch-builder/
// and the gateway proxies /watch-builder/api/*. In standalone dev, vite proxies the same
// path to the backend on :8920 (see vite.config.ts).
const BASE = '/watch-builder'

async function unwrap<T>(r: Response, path: string): Promise<T> {
  if (r.ok) return r.json() as Promise<T>

  // The backend returns structured problems on 422 (invalid design), a plain string on a 404
  // or a 503, and {message, job_id} on a 409 (an AI job already running). All are worth
  // showing verbatim rather than as "request failed": a field-level validation message is
  // directly actionable. The parsed body rides along as `body`, so a caller can read the
  // 409's job_id.
  let detail = `${path} -> ${r.status}`
  let body: unknown
  try {
    const j = await r.json()
    body = j
    if (typeof j?.detail === 'string') detail = j.detail
    else if (j?.detail?.problems) {
      detail = j.detail.problems
        .map((p: { at: string; message: string }) => `${p.at}: ${p.message}`)
        .join('; ')
    } else if (j?.detail?.message) detail = String(j.detail.message)
    else if (j?.error) detail = String(j.error)
  } catch {
    /* non-JSON error body; the status line is all we have */
  }
  const err = new Error(detail) as ApiError
  err.status = r.status
  err.body = body
  throw err
}

export type ApiError = Error & { status?: number; body?: unknown }

/** A GET. `signal` aborts it, which is how a poll that is stopped drops its request. */
export async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  return unwrap<T>(await fetch(BASE + path, { signal }), path)
}

/** A DELETE whose answer is read, for a route that returns what it deleted or stopped. */
export async function deleteJSON<T>(path: string): Promise<T> {
  return unwrap<T>(await fetch(BASE + path, { method: 'DELETE' }), path)
}

export async function postJSON<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return unwrap<T>(r, path)
}

export async function del(path: string): Promise<void> {
  const r = await fetch(BASE + path, { method: 'DELETE' })
  if (!r.ok && r.status !== 204) await unwrap(r, path)
}

/** Turn a base64 PBW into a download, without a round trip through a blob URL leak. */
export function downloadBase64(b64: string, filename: string): void {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  // Revoked on the next tick rather than immediately: Safari cancels an in-flight download
  // if the URL is revoked synchronously after click().
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

/** A multipart POST, for image uploads.
 *
 * Deliberately does NOT set Content-Type: the browser has to add the multipart boundary
 * itself, and setting the header by hand produces a boundary-less request that FastAPI
 * rejects with a 422 about a missing field rather than anything about the header.
 */
export async function postForm<T>(path: string, body: FormData): Promise<T> {
  const r = await fetch(BASE + path, { method: 'POST', body })
  return unwrap<T>(r, path)
}
