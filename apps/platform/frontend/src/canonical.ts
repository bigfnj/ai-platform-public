// The secure-origin backstop's one decision, pure so node can test it
// (apps/platform/frontend/tests/shell-status.test.js); main.tsx acts on it before React renders.
//
// getUserMedia (mic / dictation) is gated on a secure context, which is keyed to the origin in
// the address bar, so on a bare-http LAN origin navigator.mediaDevices is simply undefined. When
// a deployment names its canonical HTTPS URL at build time (VITE_PLATFORM_CANONICAL_URL), an
// insecure visit is sent there instead of landing on a half-working page.
//
// NO DEFAULT. A built-in domain would send every other install's LAN visitors over http to a
// host that is not theirs. Unset or empty means no redirect, which is the right behaviour for
// any install that has not said where its HTTPS front door is. A deployment sets
// VITE_PLATFORM_CANONICAL_URL for `vite build` (an env file or the build environment).

/** Where to send this visit, or null to stay. */
export function canonicalRedirect(
  configured: string | undefined,
  here: { href: string; origin: string },
  isSecureContext: boolean,
): string | null {
  if (!configured || isSecureContext) return null
  try {
    const u = new URL(here.href)
    const target = new URL(u.pathname + u.search + u.hash, configured)
    // Only to a different, secure origin: never a loop, never a downgrade.
    if (target.origin === here.origin || target.protocol !== 'https:') return null
    return target.href
  } catch {
    return null // a malformed canonical URL must not block the app from loading
  }
}
