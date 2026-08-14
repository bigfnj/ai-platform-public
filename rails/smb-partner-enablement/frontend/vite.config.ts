import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// The DESKTOP surface: a module-federation REMOTE the platform shell loads at runtime.
// base='/smb-partner-enablement/' so the remote's chunks resolve under the path the gateway
// serves this bundle from (same origin as the shell).
//
// The MOBILE surface is a second, standalone build — see vite.mobile.config.ts. It emits into
// dist/m/, which the gateway's StaticFiles mount then serves at /smb-partner-enablement/m/
// with no extra routing. `npm run build` runs this config first (it empties dist) and the
// mobile config second.
export default defineConfig({
  base: '/smb-partner-enablement/',
  plugins: [
    react(),
    federation({
      name: 'smb_partner',
      filename: 'remoteEntry.js',
      exposes: { './module': './src/module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    port: 5260,
    // standalone dev: proxy HTTP + WebSocket to the FastAPI backend, stripping the
    // /smb-partner-enablement prefix the gateway would otherwise consume.
    proxy: {
      '/smb-partner-enablement/api': {
        target: 'http://127.0.0.1:8870',
        rewrite: (p) => p.replace(/^\/smb-partner-enablement/, ''),
      },
      '/smb-partner-enablement/ws': {
        target: 'ws://127.0.0.1:8870',
        ws: true,
        rewrite: (p) => p.replace(/^\/smb-partner-enablement/, ''),
      },
    },
  },
})
