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
import { useEffect, useState } from 'react'
import { RailHeader, ModelChips } from '@web-core'
import { APP_PATH, fetchCapabilities, type Capabilities } from './api'
import './theme.css'

// Matches the cadence every other rail polls its status route at. The chips describe LIVE
// residency, so they have to be re-asked rather than fetched once (RAIL_CONTRACT.md RC015).
const POLL_MS = 6000

export default function OpenMaicModule() {
  const [caps, setCaps] = useState<Capabilities | null>(null)
  // Bumped by the Reload button. Used as the iframe's key, so React unmounts and remounts the
  // frame — a src reassignment would be a no-op when the URL has not changed, and reaching into
  // contentWindow.location.reload() is a cross-document call the sandbox need not permit.
  const [reloadKey, setReloadKey] = useState(0)

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
    load()
    const id = window.setInterval(load, POLL_MS)
    return () => { live = false; window.clearInterval(id) }
  }, [reloadKey])

  // Only a definite `false` hides the frame. Before the first poll lands caps is null, and
  // guessing "down" there would flash the error panel on every mount of a perfectly healthy rail.
  const appDown = caps?.app.reachable === false

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
