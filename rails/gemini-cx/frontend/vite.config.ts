import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// The shared design system (@web-core: RailHeader, ModelChips, …) lives outside this folder.
// Vite needs the alias to BUNDLE it and fs.allow to serve it in dev; tsconfig needs its own
// paths entry for tsc to RESOLVE it (missing that half fails the build with TS2307).
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// gemini-cx as a module-federation REMOTE. The platform shell (host) loads
// `gemini_cx/module` at runtime. base='/gemini-cx/' so the remote's chunks resolve under the
// path the gateway serves this bundle from (same origin as the shell, so no CORS).
export default defineConfig({
  base: '/gemini-cx/',
  plugins: [
    react(),
    federation({
      name: 'gemini_cx',
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
    port: 5330,
    fs: { allow: [webCoreRoot] },
    // standalone dev: proxy HTTP + WebSocket to the FastAPI backend, stripping the
    // /gemini-cx prefix that the gateway would otherwise consume.
    proxy: {
      '/gemini-cx/api': {
        target: 'http://127.0.0.1:8880',
        rewrite: (p) => p.replace(/^\/gemini-cx/, ''),
      },
      '/gemini-cx/ws': {
        target: 'ws://127.0.0.1:8880',
        ws: true,
        rewrite: (p) => p.replace(/^\/gemini-cx/, ''),
      },
    },
  },
})
