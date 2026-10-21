// Standalone dev entry point. In the platform shell the module is loaded as a federation
// remote via src/module.tsx — this file is only used for `vite dev` and for the standalone
// bundle the gateway serves at /gemini-cx/.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import GeminiCxModule from './module'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <GeminiCxModule />
  </StrictMode>,
)
