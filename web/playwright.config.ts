import { defineConfig, devices } from "@playwright/test";

// End to end tests run the public demo as deployed (api.public: fixture PR, replayed answers,
// fake GitHub poster, private visitors, limits) on its own port and databases, plus a production
// build of the web app.
const API_PORT = 8001;
const WEB_PORT = 3100;
const API_TOKEN = "e2e-only-token-not-a-secret-0123456789";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command:
        "rm -f .reviewpilot/e2e-api.db* .reviewpilot/e2e-checkpoints.db* && " +
        `uv run uvicorn api.public:app --port ${API_PORT}`,
      cwd: "..",
      url: `http://localhost:${API_PORT}/health`,
      env: {
        REVIEWPILOT_DEMO: "1",
        REVIEWPILOT_API_TOKEN: API_TOKEN,
        // api.public refuses to start with any of these, even from your own shell.
        ANTHROPIC_API_KEY: "",
        ANTHROPIC_AUTH_TOKEN: "",
        GITHUB_TOKEN: "",
        GH_TOKEN: "",
        LANGSMITH_API_KEY: "",
        LANGCHAIN_API_KEY: "",
        DATABASE_URL: "",
        REVIEWPILOT_DEMO_DELAY_SCALE: "0.3",
        REVIEWPILOT_API_DB: ".reviewpilot/e2e-api.db",
        REVIEWPILOT_CHECKPOINT_DB: ".reviewpilot/e2e-checkpoints.db",
      },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run build && npx next start -p ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}`,
      env: { REVIEWPILOT_API_URL: `http://localhost:${API_PORT}`, REVIEWPILOT_API_TOKEN: API_TOKEN },
      reuseExistingServer: false,
      timeout: 240_000,
    },
  ],
});
