import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// The shared design system lives outside this folder. Vite needs the alias to BUNDLE it and
// tsconfig needs its own `paths` entry for `tsc` to RESOLVE it; neither implies the other,
// and missing the tsconfig half fails with TS2307 while vite stays happy.
//
// fs.allow is required for `npm run dev`: vite refuses to serve files outside the project
// root unless told to, so without it the dev server 403s on every web-core import while the
// production build works fine.
const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

// watch-builder as a module-federation REMOTE. The platform shell (host) loads
// `watch_builder/module` at runtime. base='/watch-builder/' so the remote's chunks resolve
// under the path the gateway serves this bundle from.
export default defineConfig({
  base: '/watch-builder/',
  plugins: [
    react(),
    federation({
      name: 'watch_builder',
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
    fs: { allow: [webCoreRoot] },
    port: 5370,
    proxy: {
      '/watch-builder/api': {
        target: 'http://127.0.0.1:8920',
        rewrite: (p) => p.replace(/^\/watch-builder/, ''),
      },
    },
  },
})
