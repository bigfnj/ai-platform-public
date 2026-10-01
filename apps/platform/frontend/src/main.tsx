import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@web-core/styles.css'
import App from './App'

// Secure-origin backstop. getUserMedia (mic / dictation) is gated on a secure context, which
// is keyed to the origin in the address bar — no header or app setting can grant it on a
// bare-http LAN origin, where navigator.mediaDevices is simply undefined. Caddy already
// 308s the known insecure origins to the public HTTPS front door; this catches anything that
// slips past it (a new hostname, or a direct hit on the gateway's own port with Caddy out of
// the path) before React renders, so the user never lands on a half-working page. localhost
// is itself a secure context, so this correctly does nothing there.
//
// Override the target per deployment with VITE_PLATFORM_CANONICAL_URL at build time; set it
// empty to disable the backstop (the mic panel then just explains why it is disabled).
const CANONICAL_SECURE_URL =
  import.meta.env.VITE_PLATFORM_CANONICAL_URL ?? 'https://platform.example.com'

if (CANONICAL_SECURE_URL && !window.isSecureContext) {
  try {
    const target = new URL(
      location.pathname + location.search + location.hash,
      CANONICAL_SECURE_URL,
    )
    // Only navigate if it actually moves us to a different (and secure) origin — never loop.
    if (target.origin !== location.origin && target.protocol === 'https:') {
      location.replace(target.href)
    }
  } catch { /* a malformed canonical URL must not block the app from loading */ }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
