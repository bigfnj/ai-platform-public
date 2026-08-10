import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

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
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5240,
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
