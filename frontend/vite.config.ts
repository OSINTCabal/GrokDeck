import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev serves the SPA on 5175 and proxies the sidecar API so the browser build
// and the Electron build hit the same origins.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    strictPort: true,
    proxy: {
      "/api": {
        target: process.env.GROKDECK_URL || "http://127.0.0.1:9488",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});