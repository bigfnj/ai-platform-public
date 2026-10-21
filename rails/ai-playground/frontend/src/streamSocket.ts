// One request, then a stream of JSON frames that ends in a terminal frame: the shape of both of
// this rail's sockets (/ws/rag and /ws/bench). Kept free of React and of every import, so plain
// node can load it (frontend/tests/lab-helpers.test.js) and the two demos cannot drift apart.
//
// THE RULE THIS EXISTS FOR: a stream that ends without its terminal frame is an error, said
// once, never a silence. The gateway relays a rail socket and closes the browser's side with
// 1000 when the upstream fails, so "closed cleanly" does not mean "finished". The Embedding Lab
// had no onclose at all and stayed "Running..." for ever; the RAG demo showed a cut-off answer
// as if it were complete.
//
// And the owner can stop it: close() is what an unmount calls. The Lab used to keep its socket
// in a local const, so leaving the tab mid-run left the benchmark running for nobody;
// the backend's cancel path can only fire once this side actually closes.

/** The part of a browser WebSocket this needs. A fake in the test implements exactly this.
 *  The handler parameters are `any` so a real WebSocket, whose handlers take an Event, fits. */
export interface SocketLike {
  onopen: ((ev: any) => any) | null
  onmessage: ((ev: any) => any) | null
  onerror: ((ev: any) => any) | null
  onclose: ((ev: any) => any) | null
  send(data: string): void
  close(): void
}

export interface StreamHandlers {
  /** Handle one parsed frame. Return true when it was the stream's terminal frame. */
  onFrame(frame: any): boolean
  /** The stream ended with no terminal frame and nobody asked it to: say so. Called once. */
  onLost(message: string): void
}

export interface StreamHandle {
  /** Stop listening and close the socket. Nothing is reported after this, including the close
   *  event it causes: an unmount is not an error. */
  close(): void
}

export const LOST_CLOSED = 'the connection closed before the server said it had finished'
export const LOST_FAILED = 'the connection failed before the server said it had finished'

export function runStream(ws: SocketLike, request: unknown, h: StreamHandlers): StreamHandle {
  let finished = false
  let failed = false
  let stopped = false
  ws.onopen = () => { if (!stopped) ws.send(JSON.stringify(request)) }
  ws.onmessage = (ev) => {
    if (stopped || finished) return
    let frame: any
    try { frame = JSON.parse(String(ev.data)) } catch { return }
    if (h.onFrame(frame)) {
      finished = true
      ws.close()
    }
  }
  // A failed socket fires error and THEN close, so the error only colours the one report the
  // close makes; reporting here as well would say it twice.
  ws.onerror = () => { failed = true }
  ws.onclose = () => {
    if (stopped || finished) return
    finished = true
    h.onLost(failed ? LOST_FAILED : LOST_CLOSED)
  }
  return {
    close() {
      if (stopped) return
      stopped = true
      ws.close()
    },
  }
}
