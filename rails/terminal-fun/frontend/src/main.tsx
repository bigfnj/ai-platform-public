// Standalone dev entry (npm run dev). In production the platform shell loads
// ./module as a federated remote instead of this file.
import React from 'react'
import { createRoot } from 'react-dom/client'
import TerminalFunModule from './module'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <TerminalFunModule />
  </React.StrictMode>,
)
