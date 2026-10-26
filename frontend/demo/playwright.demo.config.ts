import { defineConfig, devices } from "@playwright/test";

// Records the README demo (not a test suite; never run in CI). See `make demo-gif`.
// Runs against the real stack with a real LLM key, so the answer shown is a live model answer.
export default defineConfig({
  testDir: ".",
  testMatch: /record-demo\.spec\.ts/,
  timeout: 180_000,
  expect: { timeout: 60_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "./.recording",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1280, height: 800 },
    video: { mode: "on", size: { width: 1280, height: 800 } },
    launchOptions: { slowMo: 60 },
  },
});
