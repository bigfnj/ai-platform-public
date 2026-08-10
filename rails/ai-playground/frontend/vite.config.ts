import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// ai-playground as a module-federation REMOTE. The platform shell (host) loads
// `ai_playground/module` at runtime. base='/ai-playground/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin as the shell).
export default defineConfig({
  base: '/ai-playground/',
  plugins: [
    react(),
    federation({
      name: 'ai_playground',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5250,
    // standalone dev: proxy the module's HTTP + WebSocket calls to the FastAPI backend,
    // stripping the /ai-playground prefix the gateway would otherwise consume.
    proxy: {
      '/ai-playground/api': {
        target: 'http://127.0.0.1:8850',
        rewrite: (p) => p.replace(/^\/ai-playground/, ''),
      },
      '/ai-playground/ws': {
        target: 'ws://127.0.0.1:8850',
        ws: true,
        rewrite: (p) => p.replace(/^\/ai-playground/, ''),
      },
    },
  },
})
