// One grounded question: streamed over /ws/ask, with a watchdog that falls back to the buffered
// /api/ask, and ONE abort that ends all of it. Shared by the desktop Voice Chat and the mobile
// Chat so the two cannot drift again.
//
// Why this is its own module. The streamed-with-fallback logic lived inline in VoiceChat.submit,
// with the cancel handle and the 12 s watchdog as closure locals nothing outside could reach.
// Leaving the tab mid-answer therefore fired a fresh POST /api/ask 12 s later and read the answer
// aloud with no panel on screen. The mobile Chat had the stream only: it discarded the cancel
// handle and had no watchdog, on the surface most likely to cross Cloudflare.
//
// No runtime imports, so `node --test` loads it directly (tests/askflow.test.js).
import type { StreamHandlers } from './api'
import type { Answer } from './types'

export type AskBody = { question: string; speak?: boolean; collections?: string[] }

export interface AskDeps {
  askStream: (body: AskBody, handlers: StreamHandlers) => () => void
  ask: (body: AskBody) => Promise<Answer>
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (id: unknown) => void
}

export interface AskHandlers {
  onCitations?: (c: Answer['citations'], grounded: boolean) => void
  onToken?: (t: string) => void
  /** The finished answer. `citations`/`grounded` are present when the buffered route
   *  answered, since it delivers them with the answer rather than ahead of it. */
  onDone: (r: { answer: string; voice?: Answer['voice']; citations?: Answer['citations'];
                grounded?: boolean }) => void
  onError: (detail: string) => void
}

/** Warm first-token is a few seconds; past this the transport is taken to be swallowing the
 *  token frames (seen through Cloudflare with a slower model) and the buffered route answers. */
export const WATCHDOG_MS = 12000

const text = (e: unknown) => String(e instanceof Error ? e.message : e)

/** Start one question. Returns the abort: after it, nothing below calls a handler again, the
 *  socket is closed, the watchdog is cleared, and a buffered request already in flight is
 *  answered into nothing. */
export function startAsk(deps: AskDeps, body: AskBody, h: AskHandlers,
                         watchdogMs: number = WATCHDOG_MS): () => void {
  let gotToken = false
  let settled = false    // the stream has finished one way or another
  let aborted = false    // the caller has gone away
  let timer: unknown = null
  let cancelWs: (() => void) | null = null

  const clear = () => {
    if (timer !== null) { deps.clearTimeout(timer); timer = null }
  }

  const useBuffered = () => {
    if (settled || aborted) return
    settled = true
    clear()
    cancelWs?.()       // close the stalled socket if it is still open
    deps.ask(body).then(
      (r) => { if (!aborted) h.onDone({ answer: r.answer, voice: r.voice,
                                        citations: r.citations, grounded: r.grounded }) },
      (e) => { if (!aborted) h.onError(text(e)) },
    )
  }

  cancelWs = deps.askStream(body, {
    onCitations: (c, g) => { if (!aborted && !settled) h.onCitations?.(c, g) },
    onToken: (t) => {
      if (aborted || settled) return
      if (!gotToken) { gotToken = true; clear() }
      h.onToken?.(t)
    },
    onDone: ({ answer, voice }) => {
      if (aborted || settled) return
      settled = true
      clear()
      h.onDone({ answer, voice })
    },
    onError: (detail) => {
      if (aborted || settled) return
      if (!gotToken) { useBuffered(); return }   // failed before any answer: try plain HTTP
      settled = true
      clear()
      h.onError(detail)
    },
  })

  timer = deps.setTimeout(() => { timer = null; if (!gotToken) useBuffered() }, watchdogMs)

  return () => {
    aborted = true
    settled = true
    clear()
    cancelWs?.()
  }
}
