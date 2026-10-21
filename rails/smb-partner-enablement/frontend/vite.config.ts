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
  resolve: {
    alias: [{ find: '@web-core', replacement: webCore }],
  },
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    // 5320 per rails/smb-partner-enablement/rail.json. Moved out of the 5210-5300 band on
    // import: 5270 is workstation's on this platform (conformance RC002/RC009).
    port: 5320,
    fs: { allow: [webCoreRoot] },
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
