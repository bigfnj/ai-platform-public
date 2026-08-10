import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// bouquet as a module-federation REMOTE. The platform shell (host) loads
// `bouquet/module` at runtime. base='/bouquet/' so the remote's chunks resolve under
// the path the gateway serves this bundle from (same origin as the shell).
export default defineConfig({
  base: '/bouquet/',
  plugins: [
    react(),
    federation({
      name: 'bouquet',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5240,
    // standalone dev: proxy the module's /bouquet/api/* calls to the FastAPI backend.
    proxy: {
      '/bouquet/api': {
        target: 'http://127.0.0.1:8840',
        rewrite: (p) => p.replace(/^\/bouquet/, ''),
      },
    },
  },
})
