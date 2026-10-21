// One way to poll, for every frontend.
//
// The audit counted 18 pollers and 16 of them ignored document.hidden: the shell's 5 s status
// poll alone is 12 /v1/status calls a minute per open tab, each spawning nvidia-smi and two
// Ollama reads on the broker, for a tab nobody is looking at. The rest had their own gaps: an
// interval that fires again while the last request is still out, a loop with no way to stop
// it on unmount, a job poll that keeps asking about a job that will never finish. This is the
// shared answer, so each poller states only what it fetches and when it is done:
//
//   - a tick is skipped while the page is hidden, and runs as soon as it is visible again;
//   - the next tick is scheduled when the previous one SETTLES, so slow answers never overlap;
//   - a tick returns 'stop' to end the poll (a terminal job, a 404 for a deleted one);
//   - a tick that throws does not end it (a transient failure is the poller's to judge);
//   - stop() cancels the pending timer, aborts the signal every tick was handed (pass it to
//     fetch), and an in-flight tick that settles afterwards schedules nothing.
//
// No React and no DOM import here, so node can test it with a fake clock (web/tests/
// poll.test.js); usePoll.ts is the hook over it.

export type PollVerdict = 'stop' | void
export type PollTick = (signal: AbortSignal) => PollVerdict | Promise<PollVerdict>

/** What the poller needs from the page. Injectable so a test can drive time and visibility. */
export interface PollEnv {
  setTimeout(fn: () => void, ms: number): unknown
  clearTimeout(id: unknown): void
  hidden(): boolean
  /** Subscribe to visibility changes; returns the unsubscribe. */
  onVisibility(fn: () => void): () => void
}

export function browserPollEnv(): PollEnv {
  return {
    setTimeout: (fn, ms) => window.setTimeout(fn, ms),
    clearTimeout: (id) => window.clearTimeout(id as number),
    hidden: () => typeof document !== 'undefined' && document.visibilityState === 'hidden',
    onVisibility: (fn) => {
      document.addEventListener('visibilitychange', fn)
      return () => document.removeEventListener('visibilitychange', fn)
    },
  }
}

/**
 * For a poll that waits on work another process finishes (a guardrail judge, a narration pass):
 * returns true once `limit` answers in a row have changed nothing, so the poller can stop and SAY
 * so, rather than waiting for ever on a worker a restart killed. `signature` is whatever
 * identifies the state being waited on; any change starts the count again.
 */
export function stallDetector(limit: number): (signature: string) => boolean {
  let last: string | null = null
  let same = 0
  return (signature) => {
    same = signature === last ? same + 1 : 0
    last = signature
    return same >= limit
  }
}

/** Start polling: one tick now (or as soon as the page is visible), then one every
 *  `intervalMs` after the previous tick settles. Returns stop(). */
export function startPoll(tick: PollTick, intervalMs: number, env: PollEnv = browserPollEnv()): () => void {
  const ac = new AbortController()
  let stopped = false
  let timer: unknown = null
  let inFlight = false
  // A tick came due while the page was hidden; run it the moment the page is visible.
  let owed = false

  const schedule = () => {
    if (!stopped) timer = env.setTimeout(fire, intervalMs)
  }

  const fire = async () => {
    timer = null
    if (stopped) return
    if (env.hidden()) {
      owed = true
      return
    }
    inFlight = true
    let verdict: PollVerdict
    try {
      verdict = await tick(ac.signal)
    } catch {
      verdict = undefined
    }
    inFlight = false
    if (stopped) return
    if (verdict === 'stop') {
      stop()
      return
    }
    schedule()
  }

  const unsubscribe = env.onVisibility(() => {
    if (stopped || env.hidden() || !owed || inFlight || timer !== null) return
    owed = false
    void fire()
  })

  const stop = () => {
    if (stopped) return
    stopped = true
    if (timer !== null) env.clearTimeout(timer)
    timer = null
    unsubscribe()
    ac.abort()
  }

  void fire()
  return stop
}
