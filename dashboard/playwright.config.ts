import { defineConfig, devices } from "@playwright/test";

// Starts the stub engine API (contracts/api.py) and the dashboard, then runs e2e/ against them.
// ENGINE_PORT picks the engine's port (default 8000, as in contracts/README.md).
const ENGINE_PORT = Number(process.env.ENGINE_PORT ?? 8000);
const DASHBOARD_PORT = Number(process.env.DASHBOARD_PORT ?? 3100);
const ENGINE_URL = `http://localhost:${ENGINE_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // the freeze test changes shared engine state
  workers: 1,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${DASHBOARD_PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `uv run uvicorn contracts.api:create_stub_app --factory --port ${ENGINE_PORT}`,
      cwd: "..",
      url: `${ENGINE_URL}/metrics`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --port ${DASHBOARD_PORT}`,
      url: `http://localhost:${DASHBOARD_PORT}`,
      env: { ENGINE_API_URL: ENGINE_URL },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
