import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The MOBILE surface: a standalone SPA, deliberately NOT a federation remote.
//
// The platform shell is a fixed two-column grid with a 76px rail and assumes a desktop
// viewport, so rendering the phone experience inside it would mean fighting the shell rather
// than designing for a phone. This build emits a self-contained app into dist/m/, which the
// gateway already serves at /smb-partner-enablement/m/ via its per-rail StaticFiles mount
// (html=true). No gateway routing change is required, and the entitlement gate still applies
// because the path's first segment is the rail id.
//
// emptyOutDir is false because the federation build owns dist/ and runs first.
export default defineConfig({
  base: '/smb-partner-enablement/m/',
  root: 'mobile',
  plugins: [react()],
  build: {
    target: 'esnext',
    outDir: '../dist/m',
    emptyOutDir: false,
  },
  server: {
    port: 5261,
    proxy: {
      '/smb-partner-enablement/api': {
        target: 'http://127.0.0.1:8870',
        rewrite: (p) => p.replace(/^\/smb-partner-enablement/, ''),
      },
      '/smb-partner-enablement/ws': {
        target: 'ws://127.0.0.1:8870',
        ws: true,
        rewrite: (p) => p.replace(/^\/smb-partner-enablement/, ''),
      },
    },
  },
})
