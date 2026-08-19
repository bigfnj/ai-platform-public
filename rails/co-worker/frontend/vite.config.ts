import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// The shared design system + platform voice live outside this folder. Vite needs the alias to
// BUNDLE it, and tsconfig needs its own `paths` entry for `tsc` to RESOLVE it — neither implies
// the other, and missing the tsconfig half fails the build with TS2307 while vite is happy.
//
// fs.allow is required for `npm run dev`: vite refuses to serve files outside the project root
// unless told to, so without it the dev server 403s on every web-core import while the
// production build works fine.
const webCore = fileURLToPath(new URL('../../../web/src', import.meta.url))
const repoRoot = fileURLToPath(new URL('../../../', import.meta.url))

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
  resolve: {
    alias: [{ find: '@web-core', replacement: webCore }],
  },
  build: { target: 'esnext', cssCodeSplit: false },
  server: {
    fs: { allow: [repoRoot] },
    port: 5260,
    proxy: {
      '/co-worker/api': {
        target: 'http://127.0.0.1:8860',
        rewrite: (p) => p.replace(/^\/co-worker/, ''),
      },
    },
  },
})
