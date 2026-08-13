import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// co-worker as a module-federation REMOTE. The platform shell (host) loads
// `co_worker/module` at runtime. base='/co-worker/' so the remote's chunks
// resolve under the path the gateway serves this bundle from.
export default defineConfig({
  base: '/co-worker/',
  plugins: [
    react(),
    federation({
      name: 'co_worker',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5260,
    proxy: {
      '/co-worker/api': {
        target: 'http://127.0.0.1:8860',
        rewrite: (p) => p.replace(/^\/co-worker/, ''),
      },
    },
  },
})
