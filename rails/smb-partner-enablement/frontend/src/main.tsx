import React from 'react'
import { createRoot } from 'react-dom/client'
import SmbPartnerModule from './module'

// Standalone dev entry only — inside the platform the shell mounts ./module as a federated
// remote and this file is never loaded.
createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <SmbPartnerModule />
  </React.StrictMode>,
)
