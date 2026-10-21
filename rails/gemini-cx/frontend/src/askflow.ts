// One question: streamed over /ws/ask with a watchdog that falls back to the buffered /api/ask,
// and ONE abort that ends all of it. The answer pane renders what this reports.
//
// Why it is its own module. The flow lived inline in the module's `run`, with the watchdog id in
// a ref that only `run` and the unmount cleanup cleared. The Stop button cancelled the socket
// and not the watchdog, so 12 s after a Stop the watchdog fired the buffered POST /api/ask: a
// second full generation of a question the user had cancelled, written into the pane. Every
// way out of a question now goes through the abort below, which clears the watchdog, closes the
// socket, and answers a buffered request already in flight into nothing.
//
// No runtime imports, so `node --test` loads it directly (tests/ask-flow.test.js).
import type { AskRequest } from './api'
import type { AskFrame, AskResponse, Citation } from './types'

export interface AskDeps {
  askStreaming: (req: AskRequest, onFrame: (f: AskFrame) => void,
                 onClose: (err?: string, fromServer?: boolean) => void) => () => void
  askBuffered: (req: AskRequest) => Promise<AskResponse>
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (id: unknown) => void
}

export interface AskHandlers {
  onCitations: (c: Citation[]) => void
  onToken: (text: string) => void
  /** Finished. `r` is present when the buffered route answered (the whole answer at once). */
  onDone: (r?: { answer: string; citations: Citation[] }) => void
  onError: (detail: string) => void
}

/** Warm first-token is ~4 s; past this the transport is taken to be swallowing the token
 *  frames (seen through Cloudflare) and the buffered route answers instead. */
export const WATCHDOG_MS = 12000

const text = (e: unknown) => String(e instanceof Error ? e.message : e)

export function startAsk(deps: AskDeps, req: AskRequest, h: AskHandlers,
                         watchdogMs: number = WATCHDOG_MS): () => void {
  let gotToken = false
  let settled = false
  let aborted = false
  let timer: unknown = null
  let cancelWs: (() => void) | null = null

  const clear = () => {
    if (timer !== null) { deps.clearTimeout(timer); timer = null }
  }

  const useBuffered = () => {
    if (settled || aborted) return
    settled = true
    clear()
    cancelWs?.()
    deps.askBuffered(req).then(
      (r) => { if (!aborted) h.onDone({ answer: r.answer, citations: r.citations }) },
      (e) => { if (!aborted) h.onError(text(e)) },
    )
  }

  cancelWs = deps.askStreaming(
    req,
    (frame) => {
      if (aborted || settled) return
      if (frame.type === 'retrieval') h.onCitations(frame.citations)
      else if (frame.type === 'token') {
        if (!gotToken) { gotToken = true; clear() }
        h.onToken(frame.text)
      }
    },
    (err, fromServer) => {
      if (aborted || settled) return
      // An explicit error FRAME is a reason, not a transport fault, so show it: retrying over
      // HTTP would pay for a second retrieval and broker call to fail the same way.
      if (err && fromServer) {
        settled = true
        clear()
        h.onError(err)
        return
      }
      // Ended with no answer at all: what the buffered fallback exists for.
      if (!gotToken) { useBuffered(); return }
      settled = true
      clear()
      if (err) h.onError(err)   // a partial answer: say so rather than present it as whole
      else h.onDone()
    },
  )

  timer = deps.setTimeout(() => { timer = null; if (!gotToken) useBuffered() }, watchdogMs)

  return () => {
    aborted = true
    settled = true
    clear()
    cancelWs?.()
  }
}
