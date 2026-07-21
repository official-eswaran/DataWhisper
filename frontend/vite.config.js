import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "build", // keep CRA's output dir so Docker/nginx are unchanged
    sourcemap: false,
  },
  server: {
    port: 3000,
    proxy: {
      // Dev-time proxy so the SPA and API share an origin, matching prod nginx.
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/health": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: "./src/setupTests.js",
    css: false,
    // Playwright owns e2e/; Vitest must not try to collect those specs (they
    // import @playwright/test and expect a browser, not jsdom).
    exclude: ["node_modules", "dist", "build", "e2e/**"],
  },
});
