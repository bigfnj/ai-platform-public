import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// edu-suite dashboard as a module-federation REMOTE. The shell (host) loads
// `edu_suite/module` at runtime. base='/edu-suite/' so the remote's chunks resolve
// under the path the gateway serves this bundle from (same origin as the shell).
export default defineConfig({
  base: '/edu-suite/',
  plugins: [
    react(),
    federation({
      name: 'edu_suite',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5210,
    // standalone dev: proxy the module's /edu-suite/api/* calls to the dashboard.
    proxy: {
      '/edu-suite/api': {
        target: 'http://127.0.0.1:8800',
        rewrite: (p) => p.replace(/^\/edu-suite/, ''),
      },
    },
  },
})
