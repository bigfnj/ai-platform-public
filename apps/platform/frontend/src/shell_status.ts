// What one answer to the shell's status poll does to the top-bar model widget. Pure, so node
// can test it (apps/platform/frontend/tests/shell-status.test.js); ShellModelWidget.tsx is the
// component around it.
import type { PlatformStatus } from '@web-core/types'

export interface StatusState {
  status: PlatformStatus | null
  /** Consecutive polls that did not produce a reachable broker. */
  misses: number
}

export type StatusAnswer =
  | { ok: true; status: PlatformStatus | null }
  | { ok: false; httpStatus?: number }

export interface StatusStep extends StatusState {
  /** The session is gone (expired, revoked, or the account deleted): go back to Login. */
  sessionLost: boolean
}

/** Misses before the widget admits the broker is offline. One slow or failed poll (the health
 *  probe timing out while the GPU loads a model) must not flip it. */
export const OFFLINE_AFTER = 3

export function nextStatus(prev: StatusState, answer: StatusAnswer): StatusStep {
  // A 401 is not a broker outage. Every authenticated route answers it once the session cookie
  // stops being valid, and before this the widget read it as "Broker offline" and the shell kept
  // polling every 5 s forever, with no way back to the login form short of a reload.
  if (!answer.ok && answer.httpStatus === 401) {
    return { status: null, misses: 0, sessionLost: true }
  }
  if (answer.ok && answer.status && answer.status.broker_reachable) {
    return { status: answer.status, misses: 0, sessionLost: false }
  }
  const misses = prev.misses + 1
  if (misses < OFFLINE_AFTER) return { status: prev.status, misses, sessionLost: false }
  return { status: answer.ok ? answer.status ?? null : null, misses, sessionLost: false }
}
