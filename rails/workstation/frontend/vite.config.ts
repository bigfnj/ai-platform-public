import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// Shared design system (@web-core: RailHeader, ModelChips, …) lives outside this folder.
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// workstation as a module-federation REMOTE. The platform shell (host) loads
// `workstation/module` at runtime. base='/workstation/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin).
export default defineConfig({
  base: '/workstation/',
  plugins: [
    react(),
    federation({
      name: 'workstation',
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
    // 5270, not 5230: finance claimed 5230 too, and it is the routinely-used dev server.
    port: 5270,
    // Vite must be allowed to read the shared @web-core source, which lives above this folder.
    fs: { allow: [webCoreRoot] },
    // Standalone dev: proxy the module's calls to the FastAPI backend. The
    // terminal is a WebSocket, so ws:true is required on the /ws route.
    proxy: {
      '/workstation/api': {
        target: 'http://127.0.0.1:8720',
        rewrite: (p) => p.replace(/^\/workstation/, ''),
      },
      '/workstation/ws': {
        target: 'ws://127.0.0.1:8720',
        ws: true,
        rewrite: (p) => p.replace(/^\/workstation/, ''),
      },
    },
  },
})
