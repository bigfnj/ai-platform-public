import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import federation from '@originjs/vite-plugin-federation'

// Federation build of the IEP Present Levels app, deployed as a standalone platform app.
// Distinct remote name ('iep_app') so the shell can register it alongside edu_suite;
// base='/iep/' so chunks resolve under the gateway path; VITE_API_BASE baked to '/iep/api'
// so the module talks to the IEP-only backend instance (IEP_ONLY=1). It exposes the bespoke
// single-page IEP module (src/iep_module.tsx), NOT the generic edu-suite dashboard.
// Output -> dist-iep/ (served by the gateway at /iep/).
export default defineConfig({
  base: '/iep/',
  define: {
    'import.meta.env.VITE_API_BASE': JSON.stringify('/iep/api'),
  },
  plugins: [
    react(),
    federation({
      name: 'iep_app',
      filename: 'remoteEntry.js',
      // The IEP-only app uses its own bespoke single-page module (upload -> jobless parse ->
      // review -> generate -> preview/copy/download), NOT the generic edu-suite dashboard.
      exposes: { './module': './src/iep_module.tsx' },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: { target: 'esnext', cssCodeSplit: false, outDir: 'dist-iep' },
})
