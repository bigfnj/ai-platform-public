// Standalone dev entry point. In the platform shell the module is loaded as a federation
// remote via src/module.tsx — this file is only used for `vite dev` and for the standalone
// bundle the gateway serves at /openmaic/.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import OpenMaicModule from './module'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <OpenMaicModule />
  </StrictMode>,
)
