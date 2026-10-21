// Config for the canvas screenshot harness. No federation plugin, relative base, its own
// outDir so it never touches the real `frontend/dist` that the gateway mounts.
import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const require = createRequire(import.meta.url)
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'))

export default defineConfig({
  root: __dirname,
  // Relative, so the build can be served from any path. It still has to be served over HTTP
  // rather than opened as a file: a `type="module"` script is CORS-blocked from a file:// URL
  // and the page renders blank with no visible error, which is a confusing half hour.
  base: './',
  plugins: [react()],
  resolve: {
    alias: [{ find: '@web-core', replacement: path.join(webCoreRoot, 'src') }],
  },
  build: {
    target: 'esnext',
    cssCodeSplit: false,
    outDir: path.join(__dirname, 'dist'),
    emptyOutDir: true,
  },
})
