import { expect, test, type Page } from '@playwright/test';

async function mockMCPSettings(page: Page) {
  page.on('pageerror', (error) => console.error(error.message));
  await page.route(
    (url) => url.pathname === '/config.live.json',
    async (route) => {
      const response = await route.fetch();
      const config = await response.json();
      config.services.setup = { mode: 'http', baseUrl: 'http://localhost:5173/api/v1/niuu/setup' };
      config.services.integrations = {
        mode: 'http',
        baseUrl: 'http://localhost:5173/api/v1/integrations',
      };
      await route.fulfill({ json: config });
    },
  );
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown;
    if (path === '/api/v1/niuu/setup') {
      body = { enabled: false, completed: true, steps: [], completedSteps: [] };
    } else if (path === '/api/v1/integrations/settings') {
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
                id: 'integration_connections',
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
              },
            ],
          },
        ],
      };
    } else if (path === '/api/v1/integrations/catalog') {
      body = [
        {
          id: 'mcp',
          slug: 'mcp',
          name: 'MCP server',
          integration_type: 'mcp',
          adapter: '',
          credential_schema: {},
          config_schema: {},
          auth_type: 'api_key',
        },
      ];
    } else if (path === '/api/v1/integrations' || path === '/api/v1/credentials/user') {
      body = [];
    } else {
      await route.continue();
      return;
    }
    await route.fulfill({ json: body });
  });
  await page.goto('/settings/integrations/connections?config=/config.live.json');
  await expect(page.getByRole('heading', { name: 'Connect an MCP server' })).toBeVisible();
}

test('MCP OAuth discovers permissions and provides browser sign-in', async ({ page }) => {
  await mockMCPSettings(page);
  let finishDiscovery: () => void = () => {};
  const discovery = new Promise<void>((resolve) => {
    finishDiscovery = resolve;
  });
  await page.route('**/api/v1/integrations/oauth/mcp/discover', async (route) => {
    await discovery;
    await route.fulfill({
      json: {
        issuer: 'https://auth.example',
        resource: 'https://tools.example/mcp',
        scope: 'read',
        registration_available: true,
      },
    });
  });
  await page.route('**/api/v1/integrations/oauth/mcp/connect', (route) =>
    route.fulfill({ json: { url: 'https://auth.example/authorize' } }),
  );
  await page.getByLabel('Server URL', { exact: true }).fill('https://tools.example/mcp');
  await page.getByLabel('Server URL', { exact: true }).press('Tab');
  await expect(page.getByLabel('Name', { exact: true })).toBeFocused();
  await page.getByRole('button', { name: 'Discover authentication' }).click();
  await expect(page.getByRole('button', { name: 'Connecting…' })).toBeDisabled();
  finishDiscovery();
  await expect(page.getByText(/Permissions: read/)).toBeVisible();
  await page.getByRole('button', { name: 'Connect MCP server' }).click();
  await expect(page.getByRole('link', { name: 'Continue to sign in' })).toHaveAttribute(
    'href',
    'https://auth.example/authorize',
  );
});

test('MCP discovery failure remains actionable', async ({ page }) => {
  await mockMCPSettings(page);
  await page.route('**/api/v1/integrations/oauth/mcp/discover', (route) =>
    route.fulfill({ status: 422, json: { detail: 'Invalid provider metadata' } }),
  );
  await page.getByLabel('Server URL', { exact: true }).fill('https://tools.example/mcp');
  await page.getByRole('button', { name: 'Discover authentication' }).click();
  await expect(page.getByRole('alert')).toContainText('Could not connect');
  await expect(page.getByRole('button', { name: 'Discover authentication' })).toBeEnabled();
});

test('MCP API tokens use the same connection flow', async ({ page }) => {
  await mockMCPSettings(page);
  await page.route('**/api/v1/integrations/oauth/mcp/connect', async (route) => {
    expect(route.request().postDataJSON().api_token).toBe('test-token');
    await route.fulfill({ json: { connection_id: 'test-connection' } });
  });
  await page.getByLabel('Server URL', { exact: true }).fill('https://tools.example/mcp');
  await page.getByRole('combobox', { name: 'Authentication', exact: true }).selectOption('token');
  await page.getByLabel('API token', { exact: true }).fill('test-token');
  await page.getByRole('button', { name: 'Connect MCP server' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'MCP server connected' })).toBeVisible();
  await expect(page.getByLabel('API token', { exact: true })).toHaveValue('');
});
