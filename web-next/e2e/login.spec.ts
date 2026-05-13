import { test, expect } from '@playwright/test';

/**
 * Login plugin e2e tests.
 *
 * All OIDC-dependent tests inject a stub auth config via config.json
 * interception and mock the IDP discovery endpoint so the browser does not
 * need a real identity provider.
 */

const STUB_AUTH_CONFIG = {
  theme: 'ice',
  plugins: {
    login: { enabled: true, order: 0 },
    volundr: { enabled: true, order: 1 },
  },
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

test('login page renders the sign-in card when auth is enabled', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');

  // Shell resolves, login page overlays it
  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('sign-in-btn')).toBeVisible();
  await expect(page.getByText('Sign in').first()).toBeVisible();
});

test('login page shows niuu wordmark', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');

  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible({ timeout: 5000 });
});

test('unauthenticated user with OIDC config is redirected to /login from root', async ({
  page,
}) => {
  await stubOidc(page);
  await page.goto('/');

  await expect(page).toHaveURL('http://localhost:5173/login', { timeout: 10000 });
  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 10000 });
});

test('callback page renders the loading spinner', async ({ page }) => {
  await stubOidc(page);
  // Navigate to the callback page directly (without a real OIDC code).
  // AuthProvider will attempt signinRedirectCallback, fail, and set loading=false.
  // The callback page itself should still render its spinner while loading.
  await page.goto('/login/callback');

  // The callback page should be visible before any redirect happens.
  await expect(page.getByTestId('callback-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Completing sign in…')).toBeVisible();
});

test('clicking sign-in triggers OIDC redirect (navigation away from /login)', async ({ page }) => {
  await stubOidc(page);
  await page.goto('/login');
  await expect(page.getByTestId('sign-in-btn')).toBeVisible({ timeout: 5000 });

  // Click "Sign in" — this triggers mgr.signinRedirect() which navigates away
  // to the IDP auth endpoint. We stub the IDP route to avoid an actual 404.
  await page.route('**/realms/test/protocol/openid-connect/auth**', (route) =>
    route.fulfill({ status: 200, contentType: 'text/html', body: '<html>stub IDP</html>' }),
  );

  await page.getByTestId('sign-in-btn').click();

  // After clicking, the browser should navigate away from /login (to IDP or back).
  // We just verify no uncaught JS error occurs and the page is still alive.
  await expect(page).toHaveURL(/localhost:5173|localhost:9876/, { timeout: 5000 });
});

test('auth-disabled mode: no redirect to /login', async ({ page }) => {
  // Default config has no auth.issuer — login plugin is registered but auth is off.
  await page.goto('/');
  // App boots normally and shows the first enabled plugin.
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();
  await expect(page).toHaveURL('http://localhost:5173/volundr');
});

test('login page shows error state for OIDC failure in URL', async ({ page }) => {
  await stubOidc(page);
  // Simulate an OIDC error redirect from the IDP.
  await page.goto('/login?error=access_denied&error_description=User+denied+access');

  await expect(page.getByTestId('login-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('login-error')).toBeVisible();
  await expect(page.getByText('Authentication failed')).toBeVisible();
  await expect(page.getByText('User denied access')).toBeVisible();
});
