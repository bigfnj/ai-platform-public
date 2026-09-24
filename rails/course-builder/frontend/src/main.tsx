// Standalone dev entry — not loaded by the platform shell.
// Run: vite (port 5351) with the backend on 8901.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import CourseBuilderModule from './module'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CourseBuilderModule />
  </StrictMode>,
)
