import { test, expect, type Page } from '@playwright/test';

test.describe('Ting legacy settings redirects', () => {
  const settingsConfig = '?config=/config.live.json';

  async function mockMountedSettings(page: Page) {
    const providerSchemas = new Map<string, unknown>([
      [
        '/api/v1/identity/settings',
        {
          title: 'You',
          scope: 'user',
          sections: [
            { id: 'profile', label: 'Profile', fields: [] },
            { id: 'tokens', label: 'Personal access tokens', fields: [] },
          ],
        },
      ],
      [
        '/api/v1/credentials/settings',
        {
          title: 'Credentials',
          scope: 'user',
          sections: [
            { id: 'user', label: 'User credentials', fields: [] },
            { id: 'tenant', label: 'Tenant credentials', fields: [] },
          ],
        },
      ],
      [
        '/api/v1/integrations/settings',
        {
          title: 'Integrations',
          scope: 'user',
          sections: [{ id: 'connections', label: 'Connections', fields: [] }],
        },
      ],
      [
        '/api/v1/forge/settings',
        {
          title: 'Forge',
          scope: 'service',
          sections: [{ id: 'storage', label: 'Storage', fields: [] }],
        },
      ],
      [
        '/api/v1/ting/settings',
        {
          title: 'Ting',
          scope: 'service',
          sections: [
            { id: 'general', label: 'General', fields: [] },
            { id: 'dispatch', label: 'Dispatch rules', fields: [] },
            { id: 'flock', label: 'Flock defaults', fields: [] },
            { id: 'notifications', label: 'Notifications', fields: [] },
          ],
        },
      ],
      [
        '/api/v1/mimir/settings',
        {
          title: 'Mimir',
          scope: 'service',
          sections: [{ id: 'service', label: 'Service', fields: [] }],
        },
      ],
      [
        '/api/v1/ravn/settings',
        {
          title: 'Ravn',
          scope: 'service',
          sections: [{ id: 'runtime', label: 'Runtime', fields: [] }],
        },
      ],
      [
        '/api/v1/observatory/settings',
        {
          title: 'Observatory',
          scope: 'service',
          sections: [{ id: 'streams', label: 'Streams', fields: [] }],
        },
      ],
      [
        '/api/v1/bifrost/settings',
        {
          title: 'Bifrost',
          scope: 'service',
          sections: [{ id: 'service', label: 'Service', fields: [] }],
        },
      ],
    ]);

    await page.route('**/api/v1/**', async (route) => {
      const url = new URL(route.request().url());
      const schema = providerSchemas.get(url.pathname);
      if (schema) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(schema),
        });
        return;
      }
      await route.continue();
    });
  }

  async function expectUnifiedSettingsChrome(page: Page, heading: string, panelCode: string) {
    await expect(page.locator('.settings-shell__sidebar-eyebrow')).toHaveText('Settings');
    await expect(page.locator('.settings-shell__panel-topline')).toBeVisible();
    await expect(page.locator('.settings-shell__panel-heading')).toHaveText(heading);
    await expect(page.locator('.settings-shell__panel-code')).toHaveText(panelCode);
  }

  test('redirects the legacy settings index into the unified settings shell', async ({ page }) => {
    await mockMountedSettings(page);
    await page.goto(`/ting/settings${settingsConfig}`);
    await page.waitForURL(/\/settings\/ting(?:\?.*)?$/);
    await expectUnifiedSettingsChrome(page, 'General', 'ting.general');
  });

  test('redirects a supported legacy section to its unified settings section', async ({ page }) => {
    await mockMountedSettings(page);
    await page.goto(`/ting/settings/dispatch${settingsConfig}`);
    await page.waitForURL(/\/settings\/ting\/dispatch(?:\?.*)?$/);
    await expectUnifiedSettingsChrome(page, 'Dispatch rules', 'ting.dispatch');
  });

  test('redirects the integrations legacy section into the unified integrations section', async ({
    page,
  }) => {
    await mockMountedSettings(page);
    await page.goto(`/ting/settings/integrations${settingsConfig}`);
    await page.waitForURL(/\/settings\/integrations\/connections(?:\?.*)?$/);
    await expectUnifiedSettingsChrome(page, 'Connections', 'integrations.connections');
  });

  test('redirects unsupported legacy sections to the Ting settings overview', async ({ page }) => {
    await mockMountedSettings(page);
    await page.goto(`/ting/settings/personas${settingsConfig}`);
    await page.waitForURL(/\/settings\/ting(?:\?.*)?$/);
    await expectUnifiedSettingsChrome(page, 'General', 'ting.general');
  });
});
