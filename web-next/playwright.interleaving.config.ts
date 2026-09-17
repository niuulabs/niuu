import { defineConfig } from '@playwright/test';
import base from './playwright.config';

// Never reuse an unrelated checkout's development server for the chronology acceptance gate.
export default defineConfig({
  ...base,
  testMatch: /forge-interleaving\.spec\.ts/,
  use: { ...base.use, baseURL: 'http://127.0.0.1:5199' },
  webServer: {
    command:
      "pnpm -r --filter='./packages/*' build && pnpm --filter @niuulabs/niuu dev --host 127.0.0.1 --port 5199",
    url: 'http://127.0.0.1:5199',
    reuseExistingServer: false,
    timeout: 300_000,
  },
});
