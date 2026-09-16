import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// The shared design system (@web-core: RailHeader, ModelChips, …) lives outside this folder.
// Vite needs the alias to BUNDLE it and fs.allow to serve it in dev; tsconfig needs its own
// paths entry for tsc to RESOLVE it (missing that half fails the build with TS2307).
//
// Resolved through require.resolve rather than a hand-counted '../../../web': the depth is a
// property of where this rail happens to sit in the tree, so a counted path breaks silently the
// day the folder moves, and fails as "cannot find @web-core" rather than as "wrong depth".
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// openmaic as a module-federation REMOTE. The platform shell (host) loads `openmaic/module`
// at runtime. base='/openmaic/' so the remote's chunks resolve under the path the gateway serves
// this bundle from (same origin as the shell, so no CORS — and, for this rail in particular, the
// same origin as the iframed app, which is what keeps the gateway session cookie attached).
export default defineConfig({
  base: '/openmaic/',
  plugins: [
    react(),
    federation({
      name: 'openmaic',
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
    port: 5350,
    fs: { allow: [webCoreRoot] },
    // standalone dev: proxy to the FastAPI backend, stripping the /openmaic prefix that the
    // gateway would otherwise consume. One entry covers both surfaces this rail has —
    // /api/capabilities for the header chips, and /api/app/* for the reverse-proxied OpenMAIC
    // Next.js app in the iframe, which pulls its own /_next/* assets back through that same
    // prefix. No ws:true: this rail has no WebSocket surface, matching the shell's own dev
    // proxy entry for /openmaic.
    proxy: {
      '/openmaic/api': {
        target: 'http://127.0.0.1:8900',
        rewrite: (p) => p.replace(/^\/openmaic/, ''),
      },
    },
  },
})
