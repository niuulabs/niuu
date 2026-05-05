import { test, expect } from '@playwright/test';

/**
 * Verifies that a request from the Hello plugin round-trips through the
 * HTTP client with a Bearer token injected via the dev token-provider hook.
 *
 * Setup:
 *  - page.addInitScript sets window.__niuuPreAuth before the app boots
 *  - config.json is mocked to enable HTTP mode for the Hello service
 *  - /api/niuu/hello/greetings is intercepted to capture headers + return data
 */
test('hello plugin round-trips through HTTP client with token provider', async ({ page }) => {
  let capturedAuth: string | null = null;

  // Inject the pre-auth hook BEFORE any app scripts run
  await page.addInitScript(() => {
    (window as Window & { __niuuPreAuth?: { getToken: () => string } }).__niuuPreAuth = {
      getToken: () => 'test-token-e2e',
    };
  });

  // Return HTTP-mode config so the app uses buildHelloHttpAdapter
  await page.route('/config.json', (route) =>
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        theme: 'ice',
        plugins: { hello: { enabled: true, order: 1 } },
        services: { hello: { baseUrl: 'http://localhost:5173/api/niuu/hello', mode: 'http' } },
      }),
    }),
  );

  // Intercept the greetings fetch, capture the Authorization header, return mock data
  await page.route('/api/niuu/hello/greetings', (route) => {
    capturedAuth = route.request().headers()['authorization'] ?? null;
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        { id: '1', text: 'hello via HTTP client', mood: 'warm' },
        { id: '2', text: 'token provider wired', mood: 'curious' },
      ]),
    });
  });

  await page.goto('/');

  // Shell and plugin title should render
  await expect(page.getByText('hello · smoke test')).toBeVisible();

  // Data from the mocked HTTP endpoint should appear
  await expect(page.getByText('hello via HTTP client')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('token provider wired')).toBeVisible();

  // The HTTP client should have sent the Bearer token
  expect(capturedAuth).toBe('Bearer test-token-e2e');
});
