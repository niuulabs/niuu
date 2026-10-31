import { expect, test, type Page } from '@playwright/test';

async function selectTheme(page: Page, theme: string) {
  await page.getByRole('button', { name: 'Color theme', exact: true }).click();
  await page
    .getByRole('button', {
      name: (
        {
          xteo: 'blue',
          ice: 'Native dark',
          amber: 'Amber',
          spring: 'Spring',
          light: 'Light',
        } as Record<string, string>
      )[theme],
      exact: true,
    })
    .click();
}

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 503, body: 'Unavailable' }));
  await page.route('**/config*.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.setup = { mode: 'http', baseUrl: '/theme-test-setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    await route.fulfill({ json: config });
  });
  await page.route('**/theme-test-setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
  await page.goto('/ting/research?config=default');
});

test('offers Light in the palette picker and preserves navigation and layout', async ({ page }) => {
  await selectTheme(page, 'xteo');
  const navigation = page.locator('.niuu-shell__rail');
  const labels = await navigation.innerText();
  const bounds = await navigation.boundingBox();
  for (const theme of ['ice', 'amber', 'spring', 'xteo', 'light']) {
    await selectTheme(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.niuu-shell')).toHaveAttribute('data-theme', theme);
    await expect(navigation).toHaveText(labels, { useInnerText: true });
    if (theme === 'xteo' || theme === 'light')
      expect(await navigation.boundingBox()).toEqual(bounds);
  }
  await expect(page.locator('.niuu-shell')).toHaveCSS('background-color', 'rgb(247, 247, 247)');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('the icon button retains the top-right reveal', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await selectTheme(page, 'light');
  await expect
    .poll(() =>
      page.evaluate(() => {
        const animation = document
          .getAnimations()
          .find((a) => a instanceof CSSAnimation && a.animationName === 'niuu-theme-reveal');
        if (!animation) return null;
        animation.pause();
        return animation.effect?.getTiming().duration;
      }),
    )
    .toBe(700);
  await page.evaluate(() => document.getAnimations().forEach((a) => a.finish()));
  await expect(page.locator('html')).not.toHaveClass(/theme-transitioning/);
});

test('borderless icon button opens all palettes and supports keyboard selection', async ({
  page,
}) => {
  await selectTheme(page, 'spring');
  const picker = page.getByRole('button', { name: 'Color theme', exact: true });
  await expect(picker).toHaveCSS('border-width', '0px');
  await expect(picker.locator('circle')).toHaveCount(0);
  await picker.focus();
  await page.keyboard.press('Enter');
  const options = page.locator('.niuu-shell__theme-option');
  await expect(options).toHaveText(['blue', 'Native dark', 'Amber', 'Spring', 'Light']);
  await page.getByRole('button', { name: 'Light', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(picker.locator('circle')).toHaveCount(1);
  await expect(picker).toBeFocused();
  await picker.click();
  await page.keyboard.press('Escape');
  await expect(options).toHaveCount(0);
  await expect(picker).toBeFocused();
});

test('closing the theme picker by clicking a field preserves that field focus', async ({
  page,
}) => {
  const search = page.getByPlaceholder('search by title, slug, question');
  await page.getByRole('button', { name: 'Color theme', exact: true }).click();
  await search.click();
  await expect(page.locator('.niuu-shell__theme-option')).toHaveCount(0);
  // Wait beyond the old deferred focus callback before checking outside focus.
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await expect(search).toBeFocused();
  await page.keyboard.type('theme');
  await expect(search).toHaveValue('theme');
});

test('Forge badges have readable Light colors and retain their dark palette', async ({ page }) => {
  await page.goto('/volundr?config=default');
  await selectTheme(page, 'light');
  const primary = page.locator('.vol-forge__kind--primary').first();
  await expect(primary).toBeVisible();
  await expect(primary).toHaveCSS('color', 'rgb(3, 105, 161)');
  await expect(page.locator('.vol-forge__kind--gpu').first()).toHaveCSS(
    'color',
    'rgb(109, 40, 217)',
  );
  await selectTheme(page, 'ice');
  await expect(primary).toHaveCSS('color', 'rgb(188, 236, 255)');
});

test('Settings labels and fields use the Light palette', async ({ page }) => {
  await selectTheme(page, 'light');
  await page.goto('/settings?config=default');
  await expect(page.locator('.settings-shell__provider-title').first()).toHaveCSS(
    'color',
    'rgb(23, 23, 23)',
  );
  await expect(page.locator('.settings-shell__sidebar-eyebrow')).toHaveCSS(
    'color',
    'rgb(82, 82, 82)',
  );
});

test('Guild scope and health labels are transparent in Light mode', async ({ page }) => {
  await page.route('**/config*.json', async (route) => {
    const config = await (await route.fetch()).json();
    config.services.setup = { mode: 'http', baseUrl: '/theme-test-setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    config.services.niuu = { mode: 'http', baseUrl: '/guild-theme-fixture' };
    await route.fulfill({ json: config });
  });
  await page.route('**/guild-theme-fixture/**', (route) =>
    route.fulfill({
      json: [
        {
          id: 'light-node',
          kind: 'volundr',
          name: 'Light node',
          slug: 'light-node',
          baseUrl: 'https://node.test',
          visibility: 'system',
          ownerId: null,
          tenantId: 'default',
          enabled: true,
          isDefault: true,
          tags: [],
          config: {},
          health: 'ok',
          createdAt: '2026-09-18T10:00:00Z',
          updatedAt: '2026-09-18T10:00:00Z',
          lastCheckedAt: '2026-09-18T10:00:00Z',
          lastSeenAt: '2026-09-18T10:00:00Z',
          lastError: null,
        },
      ],
    }),
  );
  await page.route('**/guild-theme-fixture/**/test', (route) =>
    route.fulfill({ json: { ok: true, message: 'Reachable' } }),
  );
  await page.goto('/guild?config=default');
  await selectTheme(page, 'light');
  for (const name of ['system', 'health check ok']) {
    const label = page
      .locator('.guild-label')
      .filter({ hasText: new RegExp(`^${name}$`) })
      .first();
    await expect(label).toBeVisible();
    await expect(label).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
    await expect(label).toHaveCSS('color', 'rgb(4, 120, 87)');
  }
  await selectTheme(page, 'ice');
  await expect(
    page
      .locator('.guild-label')
      .filter({ hasText: /^system$/ })
      .first(),
  ).not.toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
});
