import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))
const webCore = path.join(webCoreRoot, 'src')

export default defineConfig({
  base: '/course-builder/',
  plugins: [
    react(),
    federation({
      name: 'courseBuilder',
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
    port: 5351,
    fs: { allow: [webCoreRoot] },
    proxy: {
      '/course-builder/api': {
        target: 'http://127.0.0.1:8901',
        rewrite: (p) => p.replace(/^\/course-builder/, ''),
      },
    },
  },
})
