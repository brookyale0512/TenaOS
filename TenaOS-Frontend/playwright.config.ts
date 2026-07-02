import { defineConfig } from "@playwright/test";

/**
 * Playwright config for TenaOS end-to-end tests. Runs against a real
 * OpenMRS via the dev server (`npm run dev`) by default; CI can target a
 * deployed environment by setting BASE_URL.
 *
 * Usage:
 *   npm run test:e2e         # uses webServer (vite dev) and the local OpenMRS
 *   BASE_URL=https://staging.tenaos.example npm run test:e2e
 */
const baseURL = process.env.BASE_URL ?? "http://127.0.0.1:5173";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "on-first-retry",
    video: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: "npm run dev -- --host 127.0.0.1",
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
