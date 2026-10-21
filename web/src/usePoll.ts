import { useEffect, useRef } from 'react'
import { startPoll, type PollTick } from './poll'

/**
 * Poll while mounted: `tick` now and every `intervalMs` after it settles, skipped while the
 * page is hidden, ended by unmount, by a change in `deps`, or by `tick` returning 'stop'
 * (poll.ts has the rules). Pass `null` (or false) as `tick` to not poll at all.
 *
 * The latest `tick` is always the one called, so it may read current state without being
 * listed in `deps`; `deps` is what RESTARTS the poll (a new job id, a new account).
 */
export function usePoll(tick: PollTick | null | false, intervalMs: number, deps: readonly unknown[]): void {
  const latest = useRef(tick)
  latest.current = tick
  const enabled = !!tick
  useEffect(() => {
    if (!enabled) return
    return startPoll((signal) => {
      const t = latest.current
      return t ? t(signal) : 'stop'
    }, intervalMs)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, ...deps])
}
