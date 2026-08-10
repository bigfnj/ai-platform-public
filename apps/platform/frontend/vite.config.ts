import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

const webCore = fileURLToPath(new URL('../../../web/src', import.meta.url))
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))

// The shell is the federation HOST: it loads each app's frontend as a remote at
// runtime. Remotes are served same-origin by the gateway under /<app>/, so the
// remote URL is a plain path. web-core is aliased from outside this folder.
export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'shell',
      remotes: {
        edu_suite: '/edu-suite/assets/remoteEntry.js',
        iep_app: '/iep/assets/remoteEntry.js',
        recipe_book: '/recipe-book/assets/remoteEntry.js',
        bouquet: '/bouquet/assets/remoteEntry.js',
        workstation: '/workstation/assets/remoteEntry.js',
        terminal_fun: '/terminal-fun/assets/remoteEntry.js',
        ai_playground: '/ai-playground/assets/remoteEntry.js',
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
    fs: { allow: [repoRoot] },
    proxy: {
      '/api': 'http://127.0.0.1:8700',
      '/edu-suite': 'http://127.0.0.1:8700',
      '/iep': 'http://127.0.0.1:8700',
      '/recipe-book': 'http://127.0.0.1:8700',
      '/bouquet': 'http://127.0.0.1:8700',
      // ws:true so the terminal's /workstation/ws/* upgrade proxies through in dev.
      '/workstation': { target: 'http://127.0.0.1:8700', ws: true },
      '/terminal-fun': { target: 'http://127.0.0.1:8700', ws: true },
      // ws:true so the RAG demo's /ai-playground/ws/rag token stream proxies through in dev.
      '/ai-playground': { target: 'http://127.0.0.1:8700', ws: true },
    },
  },
})
