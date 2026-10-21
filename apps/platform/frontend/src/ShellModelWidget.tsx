import { useCallback, useRef, useState } from 'react'
import { HttpError, ModelWidget, platformApi, usePoll } from '@web-core'
import type { PlatformStatus } from '@web-core'
import { nextStatus, type StatusAnswer, type StatusState } from './shell_status'

const STATUS_MS = 5000

/**
 * The top-bar model widget together with the status poll that feeds it.
 *
 * The poll's state lives HERE, not in App. When App held it, every 5 s answer re-rendered App,
 * which rebuilt the active rail's element and reconciled the whole rail (recipe-book's ~900-card
 * grid twelve times a minute, hidden tab included). Now an answer re-renders this widget only.
 * The poll skips while the tab is hidden (usePoll), and a 401 hands control back to App to show
 * the login form instead of reading as "Broker offline" for ever.
 */
export function ShellModelWidget({ isAdmin, onSessionLost }: { isAdmin: boolean; onSessionLost: () => void }) {
  const [state, setState] = useState<StatusState>({ status: null, misses: 0 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = useRef(state)
  latest.current = state

  const refresh = useCallback(async (signal?: AbortSignal) => {
    let answer: StatusAnswer
    try {
      answer = { ok: true, status: await platformApi.status() }
    } catch (e) {
      answer = { ok: false, httpStatus: e instanceof HttpError ? e.status : undefined }
    }
    if (signal?.aborted) return 'stop' as const
    const step = nextStatus(latest.current, answer)
    if (step.sessionLost) {
      onSessionLost()
      return 'stop' as const
    }
    latest.current = { status: step.status, misses: step.misses }
    setState(latest.current)
  }, [onSessionLost])

  usePoll((signal) => refresh(signal), STATUS_MS, [refresh])

  // Unload and Cancel. Their failures used to escape as unhandled rejections (try/finally, no
  // catch), so a refused or failed click simply did nothing; the reason now shows in the popover.
  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      if (e instanceof HttpError && e.status === 401) { onSessionLost(); return }
      setError((e as Error).message || 'The request failed.')
    } finally {
      setBusy(false)
    }
    await refresh()
  }, [refresh, onSessionLost])

  return (
    <ModelWidget
      status={state.status as PlatformStatus | null}
      busy={busy}
      isAdmin={isAdmin}
      error={error}
      onUnload={(m) => void run(() => platformApi.unload(m))}
      onCancel={(seq) => void run(() => platformApi.cancel(seq))}
    />
  )
}
