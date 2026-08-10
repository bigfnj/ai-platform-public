import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

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
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5230,
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
