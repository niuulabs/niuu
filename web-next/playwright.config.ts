import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // Generate missing snapshots on first run; compare on subsequent runs.
  // Developers run `--update-snapshots` locally to refresh committed baselines.
  updateSnapshots: 'missing',
  reporter: [['html', { open: 'on-failure' }]],
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    // The suite describes the Advanced UI; Simple mode (the default for a fresh
    // browser) has its own spec that opts in per test.
    storageState: './e2e/advanced-mode.storage.json',
  },
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.05,
      animations: 'disabled',
      caret: 'hide',
      // Hide React Query devtools button — it appears in web-next dev mode
      // but not in the committed visual baselines, causing pixel diffs.
      stylePath: './e2e/visual-test-overrides.css',
    },
  },
  projects: [
    {
      name: 'webkit-workflow-editor',
      testMatch: ['workflow-editor.spec.ts', 'ting-work.spec.ts'],
      use: {
        ...devices['Desktop Safari'],
        viewport: { width: 1440, height: 900 },
        colorScheme: 'dark',
      },
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Standardise viewport for deterministic visual comparisons.
        viewport: { width: 1440, height: 900 },
        colorScheme: 'dark',
      },
    },
  ],
  webServer: {
    // The suite runs against the production bundle: build every package (the app
    // consumes their dist CSS), build the app, then serve it with vite preview.
    command: 'pnpm preview:playwright',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
});
