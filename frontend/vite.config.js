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
      // A floor, not a target. Measured on 2026-08-13 with `Sidebar`
      // covered: 91.43 statements / 94.24 branches / 70.29 functions /
      // 91.43 lines.
      //
      // **Set just under measured, and verify the gate bites (#70).** These sit
      // ~0.4 under rather than the "few points" the original #27 gate used,
      // because that headroom turned out to be wider than a whole component is
      // worth: one component moves statements by ~0.6, so 60/85/50/60 still
      // passed with Login's 13 tests deleted. The gate was decoration for
      // exactly the coverage it had just gained.
      //
      // The check when raising these: delete the suite this PR added and
      // confirm `npm test` exits non-zero. Statements and functions are what
      // bite (63.72 and 53.4 without Login); branches barely moves per
      // component and is carried by the others.
      //
      // v8 coverage is deterministic, so tight thresholds do not flake — they
      // fail only when coverage genuinely drops, which is the intent. New
      // uncovered code must come with tests or move the gate deliberately.
      //
      // One component remains untested: ErrorBoundary, which already sits at
      // 94.44% incidentally. `branches` stays at 94 because Sidebar moved it by
      // 0.17 — every branch it has was already being taken through Dashboard.
      // Statements and functions are what bite.
      thresholds: {
        statements: 91,
        branches: 94,
        functions: 70,
        lines: 91,
      },
    },
  },
});
