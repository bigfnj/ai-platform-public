import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// The shell is the federation HOST: it loads each app's frontend as a remote at
// runtime. Remotes are served same-origin by the gateway under /<app>/, so the
// remote URL is a plain path. web-core is aliased from outside this folder.
export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'shell',
      remotes: {
        recipe_book: '/recipe-book/assets/remoteEntry.js',
        workstation: '/workstation/assets/remoteEntry.js',
        terminal_fun: '/terminal-fun/assets/remoteEntry.js',
        ai_playground: '/ai-playground/assets/remoteEntry.js',
        co_worker: '/co-worker/assets/remoteEntry.js',
        smb_partner: '/smb-partner-enablement/assets/remoteEntry.js',
        gemini_cx: '/gemini-cx/assets/remoteEntry.js',
        meeting_atlas: '/meeting-atlas/assets/remoteEntry.js',
        openmaic: '/openmaic/assets/remoteEntry.js',
      },
      shared: ['react', 'react-dom'],
    }),
  ],
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: [{ find: '@web-core', replacement: webCore }],
  },
  build: { target: 'esnext' },
  server: {
    port: 5201,
    fs: { allow: [webCoreRoot] },
    proxy: {
      '/api': 'http://127.0.0.1:8700',
      '/recipe-book': 'http://127.0.0.1:8700',
      // ws:true so the terminal's /workstation/ws/* upgrade proxies through in dev.
      '/workstation': { target: 'http://127.0.0.1:8700', ws: true },
      '/terminal-fun': { target: 'http://127.0.0.1:8700', ws: true },
      // ws:true so the RAG demo's /ai-playground/ws/rag token stream proxies through in dev.
      '/ai-playground': { target: 'http://127.0.0.1:8700', ws: true },
      // The three rails imported from the public sibling never got dev-proxy entries, so
      // `npm run dev` on the shell 404'd them while the containers were fine — invisible
      // until someone actually develops against them. ws:true where the rail streams.
      '/co-worker': 'http://127.0.0.1:8700',
      '/smb-partner-enablement': { target: 'http://127.0.0.1:8700', ws: true },
      '/gemini-cx': { target: 'http://127.0.0.1:8700', ws: true },
      // No ws:true — meeting-atlas has no WebSocket surface.
      '/meeting-atlas': 'http://127.0.0.1:8700',
      // No ws:true — openmaic has no WebSocket surface.
      '/openmaic': 'http://127.0.0.1:8700',
    },
  },
})
