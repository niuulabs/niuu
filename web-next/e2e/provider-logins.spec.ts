import { expect, test, type Page } from '@playwright/test';

// Provider responses are explicitly mocked. Real CLI challenge creation is
// verified separately; these tests exercise the mounted Connections UI.
async function connections(page: Page, slug: string, failStart = false) {
  const claude = slug === 'claude-code';
  let state = 'pending';
  let polls = 0;
  const enrollment = () => ({
    id: 'test-enrollment',
    connectionId: 'test-connection',
    providerSlug: slug,
    credentialName: 'test-credential',
    state,
    inputRequired: claude,
    verificationUri: claude
      ? 'https://claude.ai/oauth/authorize'
      : 'https://auth.openai.com/codex/device',
    userCode: claude ? '' : 'TEST-CODE',
    expiresAt: '2099-01-01T00:00:00Z',
    errorCode: '',
  });
  await page.route(
    (url) => url.pathname === '/config.live.json',
    (route) =>
      route.fulfill({
        json: {
          demoMode: true,
          theme: 'ice',
          plugins: { settings: { enabled: true }, integrations: { enabled: true } },
          services: { integrations: { mode: 'http', baseUrl: '/api/v1/integrations' } },
        },
      }),
  );
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = [];
    if (path === '/api/v1/integrations/settings')
      body = {
        title: 'Integrations',
        scope: 'user',
        sections: [
          {
            id: 'connections',
            label: 'Connections',
            fields: [],
            resources: [
              {
                id: 'connections',
                type: 'integrations',
                label: 'Integration connections',
                listPath: '/api/v1/integrations',
                catalogPath: '/api/v1/integrations/catalog',
                createPath: '/api/v1/integrations',
                deletePath: '/api/v1/integrations/{id}',
                credentialListPath: '/api/v1/credentials/user',
                testPath: '/api/v1/integrations/{id}/test',
                oauthAuthorizePath: '/api/v1/integrations/oauth/{slug}/authorize',
                oauthDisconnectPath: '/api/v1/integrations/oauth/{slug}/disconnect',
                enrollmentStartPath: '/api/v1/integrations/enrollments',
                enrollmentStatusPath: '/api/v1/integrations/enrollments/{id}',
                enrollmentCancelPath: '/api/v1/integrations/enrollments/{id}',
                enrollmentCodePath: '/api/v1/integrations/enrollments/{id}/code',
              },
            ],
          },
        ],
      };
    if (path === '/api/v1/integrations/catalog')
      body = [
        {
          id: slug,
          slug,
          name: claude ? 'Claude Code (subscription)' : 'OpenAI Codex (ChatGPT)',
          auth_type: claude ? 'browser_login' : 'device_code',
          integration_type: 'ai_provider',
          credential_enrollment: {
            method: claude ? 'claude_setup' : 'codex_device',
            default_credential_name: 'test-credential',
            credential_field: claude ? 'token' : 'auth.json',
          },
        },
      ];
    if (path === '/api/v1/integrations/enrollments') {
      if (failStart) {
        await route.fulfill({ status: 503, json: { detail: 'Login service unavailable' } });
        return;
      }
      body = enrollment();
    }
    if (path === '/api/v1/integrations/enrollments/test-enrollment') {
      if (route.request().method() === 'DELETE') state = 'cancelled';
      else if (state === 'pending' && ++polls > 1) state = 'awaiting_user';
      body = enrollment();
    }
    if (path.endsWith('/test-enrollment/code')) {
      expect(route.request().postDataJSON()).toEqual({ code: 'test-browser-code' });
      state = 'complete';
      body = enrollment();
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/settings/integrations/connections?config=/config.live.json');
  return {
    complete: () => {
      state = 'complete';
    },
  };
}

test('Codex shows startup, accepts keyboard activation, and completes', async ({ page }) => {
  const provider = await connections(page, 'codex');
  const connect = page.getByRole('button', { name: 'Connect Codex' });
  await connect.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Preparing sign-in…')).toBeVisible();
  await expect(page.getByText('TEST-CODE')).toBeVisible();
  await expect(page.getByRole('link', { name: 'the provider login page' })).toHaveAttribute(
    'href',
    'https://auth.openai.com/codex/device',
  );
  provider.complete();
  await expect(page.getByText('Codex account connected.')).toBeVisible();
});

test('Claude submits the authorization code and clears it', async ({ page }) => {
  await connections(page, 'claude-code');
  await page.getByRole('button', { name: 'Connect Claude Code' }).click();
  const code = page.getByLabel('Authorization code');
  await expect(code).toHaveCSS('border-top-style', 'solid');
  await expect(code).toHaveCSS('border-top-width', '1px');
  await expect(page.getByRole('link', { name: 'the provider login page' })).toHaveCSS(
    'text-decoration-line',
    'underline',
  );
  await code.fill('test-browser-code');
  await page.getByRole('button', { name: 'Complete sign-in' }).click();
  await expect(page.getByText('Claude Code account connected.')).toBeVisible();
  await expect(page.getByLabel('Authorization code')).toHaveCount(0);
});

test('a failed start stays retryable', async ({ page }) => {
  await connections(page, 'codex', true);
  const connect = page.getByRole('button', { name: 'Connect Codex' });
  await connect.click();
  await expect(page.locator('.settings-shell__status--error')).toBeVisible();
  await expect(connect).toBeEnabled();
});

test('cancelling a pending login stops the cached challenge', async ({ page }) => {
  await connections(page, 'codex');
  await page.getByRole('button', { name: 'Connect Codex' }).click();
  await page.getByRole('button', { name: 'Cancel login' }).click();
  await expect(page.getByText('Login cancelled. Start it again to retry.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Connect Codex' })).toBeEnabled();
});
