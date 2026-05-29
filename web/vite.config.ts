import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// During `npm run dev` we serve JSON files from the analyzer's output_dir as
// Vite's `publicDir`, so a plain `fetch("./fabric_mapping.json")` works against
// the same origin in both dev and prod. Override the source location by
// setting SMA_DATA_DIR in the env or .env.local before running `vite dev`.
// Default: ../output relative to this file.
//
// In a production build (`npm run build`) the SPA expects to be served from
// the same origin as the JSON outputs, e.g. by copying the contents of
// web/dist/ next to dedicated_pools.json / fabric_mapping.json / etc., or by
// running `python -m http.server` in the analyzer's output_dir after copying.
export default defineConfig(({ mode, command }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const dataDir =
    env.SMA_DATA_DIR ||
    path.resolve(__dirname, "..", "output");

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    // For `vite dev`, treat the analyzer's output_dir as the public asset root
    // so JSON files appear at /<name>.json on the dev server. For `vite build`
    // we revert to the default (no publicDir) so the production bundle does
    // NOT bake any analyzer outputs into web/dist/.
    publicDir: command === "serve" ? dataDir : false,
    server: {
      port: 5173,
      strictPort: false,
      fs: {
        // Allow serving files from the analyzer output_dir during dev,
        // and from docs/user-guide/ (one level above the web/ folder)
        // for the in-app help system that imports markdown via ?raw.
        allow: [
          path.resolve(__dirname, ".."),
          path.resolve(__dirname, "..", "docs"),
          dataDir,
        ],
      },
    },
    build: {
      outDir: "dist",
      sourcemap: true,
      // Single-page assets are small; keep chunking friendly to file:// users.
      rollupOptions: {
        output: {
          manualChunks: {
            react: ["react", "react-dom", "react-router-dom"],
            table: ["@tanstack/react-table"],
            markdown: ["react-markdown", "remark-gfm", "rehype-slug"],
          },
        },
      },
    },
  };
});
