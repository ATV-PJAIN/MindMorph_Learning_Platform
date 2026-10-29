import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_PORT ?? 3102);
const baseURL = `http://127.0.0.1:${port}`;

// E2E runs against the dev server with the API fully mocked (page.route), so the suite is fast and
// deterministic and needs neither the FastAPI service nor an LLM key.
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `node node_modules/next/dist/bin/next dev -p ${port}`,
    url: baseURL,
    reuseExistingServer: false,
    env: { NEXT_DIST_DIR: ".next-e2e" },
    timeout: 120_000,
  },
});
