import { defineConfig, devices } from '@playwright/test';

// Functional Forge checks have their own gate; screenshot baseline work must
// never disable session navigation, reload and recovery coverage.
export default defineConfig({
  testDir: './e2e',
  testMatch: 'forge-stability.spec.ts',
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  reporter: [['list'], ['junit', { outputFile: '../.forge-results/browser.xml' }]],
  outputDir: 'test-results/forge',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:5193',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command:
      "pnpm -r --filter='./packages/*' build && pnpm --filter @niuulabs/niuu exec vite --host 127.0.0.1 --port 5193 --strictPort",
    url: 'http://127.0.0.1:5193',
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
