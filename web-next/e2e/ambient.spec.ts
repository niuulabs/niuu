import { test, expect } from '@playwright/test';

/**
 * Ambient background e2e smoke tests.
 *
 * Verifies that each of the three ambient variants renders without JS errors
 * and that the login page structure is intact regardless of which ambient is active.
 */

const STUB_AUTH_CONFIG = {
  theme: 'ice',
  plugins: {
    login: { enabled: true, order: 0 },
    hello: { enabled: true, order: 1 },
  },
  services: { hello: { mode: 'mock' } },
  auth: {
    issuer: 'http://localhost:9876/realms/test',
    clientId: 'niuu-web',
  },
};

async function stubOidc(page: import('@playwright/test').Page) {
  await page.route('**/config.json', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(STUB_AUTH_CONFIG),
    }),
  );

  await page.route('**/realms/test/.well-known/openid-configuration', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        issuer: 'http://localhost:9876/realms/test',
        authorization_endpoint: 'http://localhost:9876/realms/test/protocol/openid-connect/auth',
        token_endpoint: 'http://localhost:9876/realms/test/protocol/openid-connect/token',
        end_session_endpoint: 'http://localhost:9876/realms/test/protocol/openid-connect/logout',
        jwks_uri: 'http://localhost:9876/realms/test/protocol/openid-connect/certs',
        response_types_supported: ['code'],
        subject_types_supported: ['public'],
        id_token_signing_alg_values_supported: ['RS256'],
      }),
    }),
  );
}

const AMBIENT_VARIANTS = ['topology', 'constellation', 'lattice'] as const;

for (const variant of AMBIENT_VARIANTS) {
  test(`ambient "${variant}": login page renders without canvas errors`, async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') consoleErrors.push(msg.text());
    });
    page.on('pageerror', (err) => consoleErrors.push(err.message));

    // Pre-set localStorage so the ambient hook picks up the variant
    await stubOidc(page);
    await page.goto('/login');
    await page.evaluate((v) => {
      localStorage.setItem('niuu-login-ambient', v);
    }, variant);
    await page.reload();

    await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
    await expect(page.getByTestId('sign-in-btn')).toBeVisible();

    // No JS errors from canvas or SVG rendering
    const ambientErrors = consoleErrors.filter(
      (e) => !e.includes('favicon') && !e.includes('config.json'),
    );
    expect(ambientErrors).toHaveLength(0);
  });
}

test('ambient "topology": canvas element is present in the DOM', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');
  await page.evaluate(() => localStorage.setItem('niuu-login-ambient', 'topology'));
  await page.reload();

  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-testid="ambient-topology"]')).toBeVisible();
});

test('ambient "constellation": SVG element is present in the DOM', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');
  await page.evaluate(() => localStorage.setItem('niuu-login-ambient', 'constellation'));
  await page.reload();

  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-testid="ambient-constellation"]')).toBeVisible();
});

test('ambient "lattice": SVG element is present in the DOM', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');
  await page.evaluate(() => localStorage.setItem('niuu-login-ambient', 'lattice'));
  await page.reload();

  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-testid="ambient-lattice"]')).toBeVisible();
});
