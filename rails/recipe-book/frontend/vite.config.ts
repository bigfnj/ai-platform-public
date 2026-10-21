import { createRequire } from 'node:module'
import path from 'node:path'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import federation from "@originjs/vite-plugin-federation";

// Shared design system (@web-core: RailHeader, ModelChips, …) lives outside this folder.
const require = createRequire(import.meta.url);
const webCoreRoot = path.dirname(require.resolve('@platform/web-core/package.json'));
const webCore = path.join(webCoreRoot, 'src');

// recipe-book as a module-federation REMOTE. The platform shell (host) loads
// `recipe_book/module` at runtime. base='/recipe-book/' so the remote's chunks
// resolve under the path the gateway serves this bundle from (same origin).
export default defineConfig({
  base: "/recipe-book/",
  plugins: [
    react(),
    federation({
      name: "recipe_book",
      filename: "remoteEntry.js",
      exposes: { "./module": "./src/module.tsx" },
      shared: ["react", "react-dom"],
    }),
  ],
  resolve: {
    alias: [{ find: "@web-core", replacement: webCore }],
  },
  build: { target: "esnext", cssCodeSplit: false },
  server: {
    // 5280, not 5240: bouquet and terminal-fun both claimed 5240 too. The second dev server
    // to start loses the port, and its /api proxy silently belongs to the other rail.
    port: 5280,
    fs: { allow: [webCoreRoot] },
    // standalone dev: proxy the module's /recipe-book/api/* calls to the FastAPI backend.
    proxy: {
      "/recipe-book/api": {
        target: "http://127.0.0.1:8830",
        rewrite: (p) => p.replace(/^\/recipe-book/, ""),
      },
    },
  },
});
