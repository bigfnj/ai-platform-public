// OpenMAIC — federated React module for the platform shell. Exposes ./module.
//
// This module is deliberately thin, and the thinness is the design decision worth defending.
// OpenMAIC itself is an upstream Next.js app (THU-MAIC/OpenMAIC) that owns its own router,
// middleware and server rendering, so it cannot be a federated remote the way every other rail
// here is — there is nothing to expose. It runs as a second container and the rail backend
// reverse-proxies it under /openmaic/api/app/, which keeps it behind the gateway's
// X-Platform-User gate and, just as importantly, same-origin with the shell. So what this file
// renders is the platform's half of the contract: the shared header, live model chips, and one
// full-height frame. terminal-fun's web toys are the precedent for the iframe.
//
// No own top bar or theme — the shell provides those; this renders inside an `.openmaic`
// wrapper and adopts the shell's palette via shared tokens (see web/THEMING.md).
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { RailHeader, ModelChips } from '@web-core'
import { APP_PATH, fetchCapabilities, type Capabilities } from './api'
import { frameView } from './frameView'
import {
  findAppTheme, holdFrameClass, resolvedMode, shellMode, syncFrameTheme, watchFrameDocuments,
  type HoldEnv, type ShellMode,
} from './frameTheme'
import { startVisiblePoll } from './visiblePoll'
import './theme.css'

// Matches the cadence every other rail polls its status route at. The chips describe LIVE
// residency, so they have to be re-asked rather than fetched once (RAIL_CONTRACT.md RC015).
const POLL_MS = 6000

const DARK_QUERY = '(prefers-color-scheme: dark)'
const readShellMode = (): ShellMode => shellMode(document.documentElement.getAttribute('data-theme'))
const readPrefersDark = (): boolean => window.matchMedia?.(DARK_QUERY).matches ?? false

function frameStorage(): Storage | null {
  try {
    return window.localStorage
  } catch {
    return null // blocked site data: reading the property itself throws
  }
}

function frameRoot(frame: HTMLIFrameElement | null): HTMLElement | null {
  try {
    return frame?.contentDocument?.documentElement ?? null
  } catch {
    return null
  }
}

function frameBody(frame: HTMLIFrameElement | null): HTMLElement | null {
  try {
    return frame?.contentDocument?.body ?? null
  } catch {
    return null
  }
}

const FRAME_WATCH_ENV = {
  raf: (fn: () => void) => window.requestAnimationFrame(fn),
  caf: (id: number) => window.cancelAnimationFrame(id),
  now: () => performance.now(),
}

const HOLD_ENV: HoldEnv = {
  observe: (root, onRecords) => {
    const mo = new MutationObserver((records) => onRecords(records.length))
    mo.observe(root as unknown as Node, { attributes: true, attributeFilter: ['class'] })
    return mo
  },
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (id) => window.clearTimeout(id as number),
}

export default function OpenMaicModule() {
  const [caps, setCaps] = useState<Capabilities | null>(null)
  // Bumped by the Reload button. Used as the iframe's key, so React unmounts and remounts the
  // frame — a src reassignment would be a no-op when the URL has not changed, and reaching into
  // contentWindow.location.reload() is a cross-document call the sandbox need not permit.
  const [reloadKey, setReloadKey] = useState(0)
  const frameRef = useRef<HTMLIFrameElement>(null)

  // The shell's light/dark mode, followed live: App.tsx sets data-theme on <html> (absent for
  // System), and System follows the OS. recipe-book watches the same two signals.
  const [mode, setMode] = useState<ShellMode>(readShellMode)
  const [prefersDark, setPrefersDark] = useState<boolean>(readPrefersDark)
  useEffect(() => {
    const update = () => { setMode(readShellMode()); setPrefersDark(readPrefersDark()) }
    const mo = new MutationObserver(update)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const mq = window.matchMedia?.(DARK_QUERY)
    mq?.addEventListener?.('change', update)
    return () => { mo.disconnect(); mq?.removeEventListener?.('change', update) }
  }, [])

  // Header chips: poll capabilities so the four-state dots track residency (and the resolved
  // model tracks the admin Rails panel) without a reload — the broker evicts on a keep_alive
  // expiry and generating a course warms the model back up, neither of which this UI causes or
  // hears about. The same payload carries app.reachable, so this one poll is also how the frame
  // learns the courseware container came back.
  //
  // reloadKey is a dependency on purpose: clicking Reload tears the interval down and starts it
  // with an immediate load(), so the header and the frame refresh together instead of the chips
  // lagging up to six seconds behind the thing the user just re-fetched.
  useEffect(() => {
    let live = true
    const load = () =>
      fetchCapabilities()
        .then((c) => { if (live) setCaps(c) })
        .catch(() => { /* keep the last known state rather than blanking the header */ })
    // Skipped while the page is hidden, and run once on return (visiblePoll.ts): a background
    // tab used to cost three broker round trips plus an app probe every 6 s.
    const stop = startVisiblePoll(load, (tick) => {
      const id = window.setInterval(tick, POLL_MS)
      return () => window.clearInterval(id)
    })
    return () => { live = false; stop() }
  }, [reloadKey])

  // Only a definite `false` hides the frame, and only if the app has not answered in this
  // mount: once a class is open in the frame, one slow 5 s probe must not unmount it (see
  // frameView.ts). Reload starts the count again, since it remounts the frame anyway.
  const everReachable = useRef(false)
  useEffect(() => { everReachable.current = false }, [reloadKey])
  if (caps?.app.reachable === true) everReachable.current = true
  const view = frameView(caps?.app.reachable, everReachable.current)
  const appDown = view === 'down'

  // Hand the shell's mode to the framed app (frameTheme.ts says how and why). A LAYOUT effect,
  // so on a mount or a Reload the storage write lands in the same task that inserted the iframe,
  // before its document can even be fetched, let alone mount the theme hook that reads it. With
  // the app already up, its own setTheme is called as well, so its theme button follows too.
  // reloadKey and appDown are dependencies because each of them can put a NEW frame in place.
  useLayoutEffect(() => {
    const frame = frameRef.current
    syncFrameTheme(mode, prefersDark, frameStorage(), frameRoot(frame), findAppTheme(frameBody(frame)))
  }, [mode, prefersDark, reloadKey, appDown])

  // And to each new document the frame loads, before that document first paints, held until the
  // app applies the same mode itself: the app's server-rendered page is light until it hydrates
  // and its hook applies the stored mode, which took the first 2-3 s of every load.
  // The latest mode is read through a ref, so a shell change made while the frame is loading is
  // the one handed over.
  const latestMode = useRef({ mode, prefersDark })
  latestMode.current = { mode, prefersDark }
  useLayoutEffect(() => {
    if (appDown) return
    let release: (() => void) | null = null
    const wantDark = () => resolvedMode(latestMode.current.mode, latestMode.current.prefersDark) === 'dark'
    const stop = watchFrameDocuments(() => frameRef.current, (doc) => {
      release?.()
      if (doc.documentElement) release = holdFrameClass(doc.documentElement, wantDark, HOLD_ENV)
    }, FRAME_WATCH_ENV)
    return () => { stop(); release?.() }
  }, [reloadKey, appDown])

  return (
    <div className="openmaic">
      <RailHeader
        icon="🎓"
        title="OpenMAIC"
        subtitle="Turn a topic or a document you upload into an interactive class — generated slides, quizzes and simulations, taught by an AI teacher with narration and a live whiteboard. Runs on this platform's own models."
        chips={
          <ModelChips
            status={caps ? (caps.broker === 'ok' ? 'ok' : 'unreachable') : 'checking'}
            models={caps?.models}
          />
        }
        actions={
          <button
            className="om-reload"
            onClick={() => setReloadKey((k) => k + 1)}
            title="Reload the OpenMAIC app in the frame below and re-check its status"
          >
            Reload
          </button>
        }
      />
      {/* Its own slot, before the frame's, so showing it never moves (and so never remounts)
          the iframe: keeping the class the user is in is the point of the warning. */}
      {view === 'frame-warning' && (
        <div className="om-warn" role="status">
          The last status check could not reach the OpenMAIC app. The class below has been left
          as it is; if it has stopped responding, use Reload.
        </div>
      )}
      {appDown ? (
        <div className="om-down">
          <h2>The OpenMAIC app is not responding.</h2>
          <p>
            This rail is a wrapper: the class itself runs in a separate <code>openmaic-app</code>
            {' '}container, and the rail backend could not reach it
            {caps?.app.url ? <> at <code>{caps.app.url}</code></> : null}. The model chips above
            still describe this platform, so a broker that reads ok here tells you the GPU side is
            fine and the courseware container is the part that needs starting.
          </p>
          <p className="om-retry">
            Its status is re-checked every few seconds — the class appears here on its own once the
            container is back, or use Reload above to check immediately.
          </p>
        </div>
      ) : (
        // allow-same-origin is REQUIRED, not a relaxation that crept in: the framed app is
        // first-party content served through the gateway on this very origin, and every request
        // it makes for its own /_next/* bundles is gated on the session cookie. An opaque origin
        // withholds that cookie, so each of those subresources 401s and the frame renders blank.
        // allow-forms and allow-popups are what the app needs to work at all — it is a form-driven
        // authoring tool that opens generated material in a new tab. Top-level navigation stays
        // blocked, which is the sandbox bit that matters for a frame this rail does not control.
        <iframe
          ref={frameRef}
          className="appframe"
          src={APP_PATH}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          title="OpenMAIC"
          key={reloadKey}
        />
      )}
    </div>
  )
}
