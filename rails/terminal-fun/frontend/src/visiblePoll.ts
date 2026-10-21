// Poll, but not for a page nobody is looking at. Kept free of React and of every import so plain
// node can test it (frontend/tests/visible-poll.test.js). The same file is in ai-playground,
// openmaic and terminal-fun: rails duplicate rather than import (web-core is not a federation
// singleton), and the three copies are pinned by the same tests.
//
// WHY. The header's 6 s capabilities poll costs three broker round trips each
// time (one of them spawning nvidia-smi on the host), and it ignored document.hidden: a tab left
// in the background asked ten times a minute, for ever. Now a hidden page's ticks are skipped,
// and the page asks once the moment it becomes visible again, so the chips are fresh on return.
//
// The CALLER owns the interval (`every`), on purpose: the module that fetches the status route
// is where the polling visibly lives, which is also what tools/rail_conformance.py RC015 reads.
// This helper only decides whether a tick, or a return to the page, asks.

/** The page-visibility surface the poll needs. A fake in the test implements exactly this. */
export interface VisibilityEnv {
  hidden(): boolean
  /** Subscribe to visibility changes, in either direction; returns the unsubscribe. */
  onVisibilityChange(fn: () => void): () => void
}

/** Start calling `tick` on the caller's interval; return the stop. */
export type Every = (tick: () => void) => () => void

export function browserVisibility(): VisibilityEnv {
  return {
    hidden: () => document.hidden,
    onVisibilityChange: (fn) => {
      document.addEventListener('visibilitychange', fn)
      return () => document.removeEventListener('visibilitychange', fn)
    },
  }
}

/** Run `load` now (if visible) and on every tick while visible, plus once on becoming visible.
 *  Returns the stop, which a React effect's cleanup calls. */
export function startVisiblePoll(load: () => void, every: Every,
  env: VisibilityEnv = browserVisibility()): () => void {
  if (!env.hidden()) load()
  const stopTimer = every(() => { if (!env.hidden()) load() })
  const stopListening = env.onVisibilityChange(() => { if (!env.hidden()) load() })
  return () => {
    stopTimer()
    stopListening()
  }
}
