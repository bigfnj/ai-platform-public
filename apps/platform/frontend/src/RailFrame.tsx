// One wrapper around every federated rail: the Suspense fallback it always had, plus the error
// boundary it never did.
//
// WHY THIS EXISTS. The shell had no boundary anywhere — zero hits for componentDidCatch or
// getDerivedStateFromError across the shell and all 14 rail frontends — so React 18 unmounted the
// whole tree on any throw inside a rail module, or on a rejected federated import(). The user got
// a blank white page: no app rail, no top bar, no Log Out. And because `activeEntry` falls back to
// railApps[0], clearing the hash landed on the same broken rail again, so there was no route back
// in from the UI at all; only hand-editing the URL to #/admin recovered it.
//
// It also made a promise the admin console already displays come true. The Rail Manager's "no
// bundle" badge tells an admin a rail with no built frontend "would render blank rather than show
// an error" — which was accurate, and is the failure this component converts into a card.
//
// Deliberately ONE component wrapping all 15 rails rather than a boundary per branch: a boundary
// that has to be remembered for each new rail is a boundary that will be missed, and the missing
// one is discovered by a user seeing a white page.
import { Component, Suspense } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

function Frame({ children }: { children: ReactNode }) {
  // The shell's own wrapper for any full-panel message, matching ComingSoon in App.tsx and the
  // Suspense fallbacks this replaces, so a failure is laid out like every other panel state.
  return (
    <div className="module">
      <div className="card">{children}</div>
    </div>
  )
}

class RailBoundary extends Component<
  { label: string; children: ReactNode },
  { err: Error | null }
> {
  state: { err: Error | null } = { err: null }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  componentDidCatch(err: Error, info: ErrorInfo) {
    // Kept: the console is the only place the stack survives, and a federated import failure
    // reads as a bare "Failed to fetch dynamically imported module" without the rail's name.
    console.error(`[shell] rail "${this.props.label}" failed`, err, info.componentStack)
  }

  render() {
    const { err } = this.state
    if (!err) return this.props.children
    // No retry button, and the reason is mechanical rather than a judgement call. React's `lazy`
    // latches its rejection: the payload's status is set to Rejected once and nothing resets it,
    // so a retry rethrows the cached error without refetching the bundle. A button that cannot
    // work is worse than no button.
    //
    // That same latch is why RELOAD is named here. The most reachable failure is not a missing
    // bundle — every bundle exists on this deployment — it is a gateway restart or a network blip
    // during the first click on a rail. That rejection is then permanent for the life of the page
    // even after the gateway is healthy again, and switching away and back rethrows it, so
    // reloading is the actual cure and the card has to say so.
    return (
      <Frame>
        <p className="error-line" style={{ margin: '0 0 8px' }}>
          {this.props.label} failed to load.
        </p>
        <div className="empty" style={{ padding: '4px 0 0', textAlign: 'left' }}>
          The rest of the platform is unaffected — pick another app from the rail on the left.
          If this rail worked earlier, reload the page: a connection blip while it was loading
          is remembered until you do. If it keeps failing, its frontend bundle may not be built,
          and an admin can check Admin → Rails for a “no bundle” badge on it.
        </div>
      </Frame>
    )
  }
}

export default function RailFrame({
  label,
  children,
}: {
  /** The rail's display name, used for BOTH the loading line and the failure card.
   *
   * One prop rather than two. The 15 Suspense fallbacks this replaces each carried their own
   * wording ("Loading IEP Present Levels…"), and that wording is exactly what a failure should
   * name too, so splitting it would only create a way for the two to disagree.
   *
   * Callers must also pass a `key` of the rail id. Every branch renders a RailFrame at the same
   * position in the tree, so without one React reconciles them as the same instance and a failed
   * rail's error state would survive switching to a healthy rail.
   */
  label: string
  children: ReactNode
}) {
  return (
    <RailBoundary label={label}>
      <Suspense fallback={<Frame><div className="empty">Loading {label}…</div></Frame>}>
        {children}
      </Suspense>
    </RailBoundary>
  )
}
