// Standalone dev entry (npm run dev). In production the shell loads
// ./module as a federated remote instead of this file.
import React from 'react'
import { createRoot } from 'react-dom/client'
import EduSuiteModule from './module'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <EduSuiteModule />
  </React.StrictMode>,
)
