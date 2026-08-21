import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// meeting-atlas as a module-federation REMOTE. The platform shell (host) loads
// `meeting_atlas/module` at runtime. base='/meeting-atlas/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin).
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
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5290,
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
