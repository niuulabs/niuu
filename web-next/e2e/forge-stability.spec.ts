import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);

// Use the checked-in mock services so the functional gate needs neither a live
// platform nor provider credentials. Transport/replay wires have separate tests.
test.beforeEach(async ({ page }) => {
  await page.route('**/config.json', (route) => route.fulfill({ json: config }));
});

test('Forge entry survives a hard reload', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByTestId('forge-page')).toBeVisible();
  await expect(page).toHaveURL(/\/volundr\/forge$/);
  await page.reload();
  await expect(page.getByTestId('forge-page')).toBeVisible();
});

test('a session deep link survives reload with the same session selected', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible();
  await expect(page.getByTestId('session-id-label')).toHaveAttribute('title', /^ds-1 /);
  await page.reload();
  await expect(page.getByTestId('session-id-label')).toHaveAttribute('title', /^ds-1 /);
  await expect(page.locator('#tab-diffs')).toBeVisible();
});

test('switching sessions preserves the sidebar and renders the selected session', async ({
  page,
}) => {
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('pod-list-sidebar')).toBeVisible();
  await page.getByTestId('pod-entry-ds-1').click();
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible();
  await expect(page.getByTestId('session-id-label')).toHaveAttribute('title', /^ds-1 /);
  await expect(page.getByTestId('pod-list-sidebar')).toBeVisible();
});

test('history filters survive repeated changes without losing rows', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByTestId('history-row').first()).toBeVisible();
  const total = await page.getByTestId('history-row').count();
  await page.getByRole('button', { name: 'failed', exact: true }).click();
  await expect(page.getByTestId('history-row')).toHaveCount(2);
  await page.getByRole('button', { name: 'All', exact: true }).click();
  await expect(page.getByTestId('history-row')).toHaveCount(total);
});

test('launch catalog exposes the standard Claude and Codex definitions', async ({ page }) => {
  await page.goto('/volundr/catalog');
  await expect(page.getByRole('button', { name: /standard-claude/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /standard-codex/ })).toBeVisible();
});
