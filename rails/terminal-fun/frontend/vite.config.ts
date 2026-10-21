import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// Shared design system (@web-core: RailHeader, ModelChips, …). Resolved through node from the
// DECLARED dependency rather than by counting '../' up to the repo root: the depth was the only
// thing tying this rail to its position in the monorepo, so lifting the rail out meant editing
// vite.config and tsconfig rather than changing one line in package.json.
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// terminal-fun as a module-federation REMOTE. The platform shell (host) loads
// `terminal_fun/module` at runtime. base='/terminal-fun/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin).
export default defineConfig({
  base: '/terminal-fun/',
  plugins: [
    react(),
    federation({
      name: 'terminal_fun',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  resolve: {
    alias: [{ find: '@web-core', replacement: webCore }],
  },
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    // web-core resolves outside this folder (a linked workspace package), so vite has to be
    // told it may read there — without it the dev server 403s on every @web-core import.
    fs: { allow: [webCoreRoot] },
    // 5290, not 5240: bouquet and recipe-book both claimed 5240 too. The second dev server
    // to start loses the port, and its /api proxy silently belongs to the other rail.
    port: 5290,
    // Standalone dev: proxy the module's calls to the FastAPI backend. The
    // terminal is a WebSocket, so ws:true is required on the /ws route.
    proxy: {
      '/terminal-fun/api': {
        target: 'http://127.0.0.1:8730',
        rewrite: (p) => p.replace(/^\/terminal-fun/, ''),
      },
      '/terminal-fun/ws': {
        target: 'ws://127.0.0.1:8730',
        ws: true,
        rewrite: (p) => p.replace(/^\/terminal-fun/, ''),
      },
    },
  },
})
