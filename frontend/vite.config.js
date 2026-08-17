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
      // A floor, not a target. Measured on 2026-08-17 with `services/api.js`
      // covered: 97.45 statements / 95.81 branches / 95.65 functions /
      // 97.45 lines.
      //
      // **Read this before trusting `branches` as a ratchet.** v8 reports
      // branch data only for functions it actually entered, so branches inside
      // never-run code are not in the denominator at all. On 2026-08-16 this
      // metric *fell* (94.36 → 93.65) while nothing lost cover: `api.js` had 6
      // counted branches (5 covered = 83.33%), and adding one exported function
      // that tests do execute made v8 report the file's other blocks too — 17
      // counted, 9 covered. Eleven uncovered branches appeared that had been
      // there all along. Covering the file took it to 100% and the global figure
      // to 95.81, above where it started.
      //
      // The generalisable part: **this metric's denominator moves when
      // execution reaches new code**, so a branch percentage is only comparable
      // between runs over identical code. A fall is worth investigating before
      // it is worth reverting.
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
      // **All thirteen components are covered as of 2026-08-16** — the twelve
      // from #70 plus `CaptchaWidget` (#21) — and `services/api.js` since
      // 2026-08-17, each at 100% on all four metrics.
      //
      // `api.js` was written off as "thin wrappers exercised through the
      // components that call them", which was true of about half of it. The
      // other half is the session machinery: the token store, the single-flight
      // refresh, the retry-once 401 interceptor and the SSE parser. Nothing had
      // asserted any of it, and two of those are untestable from a component —
      // that concurrent 401s share one refresh call, and that an SSE event split
      // across two network chunks survives the boundary.
      //
      // **What is left is `App.jsx` at 87.3%** (the boot/refresh path), the last
      // file below 100% that is neither a component nor a service, and the
      // honest next target for anyone wanting the number higher.
      thresholds: {
        statements: 97,
        branches: 95.4,
        functions: 95.2,
        lines: 97,
      },
    },
  },
});
