import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@web-core/styles.css'
import App from './App'
import { canonicalRedirect } from './canonical'

// Secure-origin backstop: an insecure visit goes to the deployment's HTTPS URL, when the build
// names one (canonical.ts says why there is no default). Before React renders, so the user never
// lands on a half-working page; localhost is itself a secure context, so it does nothing there.
const target = canonicalRedirect(import.meta.env.VITE_PLATFORM_CANONICAL_URL, location, window.isSecureContext)
if (target) location.replace(target)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
