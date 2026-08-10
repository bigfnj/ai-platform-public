import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import federation from "@originjs/vite-plugin-federation";

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
  build: { target: "esnext", cssCodeSplit: false },
  server: {
    port: 5240,
    // standalone dev: proxy the module's /recipe-book/api/* calls to the FastAPI backend.
    proxy: {
      "/recipe-book/api": {
        target: "http://127.0.0.1:8830",
        rewrite: (p) => p.replace(/^\/recipe-book/, ""),
      },
    },
  },
});
