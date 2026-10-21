import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// Shared design system (@web-core: RailHeader, ModelChips, …) lives outside this folder.
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// src/standalone.css styles the standalone dev PAGE (html, body, #root), so it must never
// enter the build. With cssCodeSplit:false the federation plugin has remoteEntry.js inject
// every stylesheet the build emits into the shell's document, and nothing removes it: when
// main.tsx imported this file, its html/body/#root rules inset the whole platform by 18px
// and pinned the page background. So no module imports it; the dev server alone links it
// into index.html (apply: 'serve'), and `vite build` never sees it.
const standaloneDevCss = {
  name: 'ai-playground-standalone-dev-css',
  apply: 'serve' as const,
  transformIndexHtml: {
    order: 'pre' as const,
    handler: () => [{ tag: 'link', attrs: { rel: 'stylesheet', href: '/src/standalone.css' },
                      injectTo: 'head' as const }],
  },
}

// ai-playground as a module-federation REMOTE. The platform shell (host) loads
// `ai_playground/module` at runtime. base='/ai-playground/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin as the shell).
export default defineConfig({
  base: '/ai-playground/',
  plugins: [
    standaloneDevCss,
    react(),
    federation({
      name: 'ai_playground',
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
    port: 5250,
    // '.' (this frontend) as well as web-core: a fs.allow list REPLACES Vite's default
    // root, and with web-core alone the dev server refused its own index.html with a 403.
    fs: { allow: ['.', webCoreRoot] },
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
