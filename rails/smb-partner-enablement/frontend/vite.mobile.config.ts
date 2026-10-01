import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Same @web-core alias as the desktop build so the mobile bundle resolves the shared design
// system too, and fs.allow the repo root so its dev server can serve web/src.
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

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
  resolve: {
    alias: [{ find: '@web-core', replacement: webCore }],
  },
  build: {
    target: 'esnext',
    outDir: '../dist/m',
    emptyOutDir: false,
  },
  server: {
    port: 5261,
    fs: { allow: [webCoreRoot] },
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
