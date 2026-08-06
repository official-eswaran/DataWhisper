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

    // Issue #27. Until this existed `npm test` ran with no threshold at all, so
    // frontend coverage could regress to nothing without CI noticing — unlike
    // the backend, which has had a gate since #28.
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      // Measure the app, not the build output. Without an explicit include the
      // v8 provider also reports build/assets/*.js and playwright.config.js,
      // which drags the number to meaninglessness (47% vs the real figure).
      include: ["src/**/*.{js,jsx}"],
      exclude: [
        "src/**/*.test.{js,jsx}",
        "src/setupTests.js",
        // Composition root: imports the app and calls createRoot. Nothing to
        // assert that the render tests don't already cover.
        "src/index.jsx",
      ],
      // A floor, not a target. Measured on 2026-08-05: 63.72 statements /
      // 90.11 branches / 53.4 functions. Each threshold sits a few points under
      // so ordinary churn doesn't trip it, but deleting a suite does — which is
      // the regression #27 is actually about.
      //
      // Statements and functions are low because seven components are still
      // untested (AdminConsole, AuditLogs, Dashboard, Sidebar, AccountSettings,
      // Login, ErrorBoundary). Raise these as those land; the point today is
      // that the number can no longer fall silently.
      thresholds: {
        statements: 60,
        branches: 85,
        functions: 50,
        lines: 60,
      },
    },
  },
});
