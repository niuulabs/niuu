import { test, expect, type Page } from '@playwright/test';

test.describe('Ting legacy settings redirects', () => {
  async function expectUnifiedSettingsChrome(page: Page) {
    await expect(page.locator('.settings-shell__sidebar-eyebrow')).toHaveText('Settings');
    await expect(page.locator('.settings-shell__panel-topline')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Ting Settings' })).toBeVisible();
    await expect(page.getByText('ting.settings')).toBeVisible();
  }

  test('redirects the legacy settings index into the unified settings shell', async ({ page }) => {
    await page.goto('/ting/settings');
    await page.waitForURL('**/settings/ting');
    await expectUnifiedSettingsChrome(page);
  });

  test('redirects a supported legacy section to its unified settings section', async ({ page }) => {
    await page.goto('/ting/settings/dispatch');
    await page.waitForURL('**/settings/ting/dispatch');
    await expectUnifiedSettingsChrome(page);
  });

  test('redirects the integrations legacy section into the unified integrations section', async ({
    page,
  }) => {
    await page.goto('/ting/settings/integrations');
    await page.waitForURL('**/settings/ting/integrations');
    await expectUnifiedSettingsChrome(page);
  });

  test('redirects unsupported legacy sections to the Ting settings overview', async ({ page }) => {
    await page.goto('/ting/settings/personas');
    await page.waitForURL('**/settings/ting');
    await expectUnifiedSettingsChrome(page);
  });
});
