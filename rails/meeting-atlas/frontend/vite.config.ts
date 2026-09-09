import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// meeting-atlas as a module-federation REMOTE. The platform shell (host) loads
// `meeting_atlas/module` at runtime. base='/meeting-atlas/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin).
//
// The shared design system (@web-core: RailHeader, ...) lives outside this folder.
// Vite needs the alias to BUNDLE it and fs.allow to serve it in dev; tsconfig needs
// its own paths entry for tsc to RESOLVE it (missing that half fails with TS2307).
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')
export default defineConfig({
  base: '/meeting-atlas/',
  plugins: [
    react(),
    federation({
      name: 'meeting_atlas',
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
    fs: { allow: [webCoreRoot] },
    port: 5340,
    // Standalone dev: proxy the module's calls to the FastAPI backend, stripping the
    // /meeting-atlas prefix the gateway adds in production.
    proxy: {
      '/meeting-atlas/api': {
        target: 'http://127.0.0.1:8740',
        rewrite: (p) => p.replace(/^\/meeting-atlas/, ''),
      },
    },
  },
})
