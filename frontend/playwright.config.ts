import { defineConfig, devices } from "@playwright/test";

// Runs against the real stack (`make e2e`): Keycloak, Postgres, Redis, API, worker, web, with
// LLM_PROVIDER=extractive so the whole path (real login, retrieval, reranking, grounding
// validation, citations, page rendering) is real except for the language model.
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    { name: "chromium", dependencies: ["setup"], testIgnore: /auth\.setup\.ts/ },
  ],
});
