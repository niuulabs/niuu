import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// /volundr/catalog — Launch catalog
// ---------------------------------------------------------------------------

test('/volundr/catalog renders the launch catalog page', async ({ page }) => {
  await page.goto('/volundr/catalog');
  await expect(page.getByRole('heading', { name: /launch catalog/i })).toBeVisible();
});

test('/volundr/catalog shows preloaded system launch specs', async ({ page }) => {
  await page.goto('/volundr/catalog');
  await expect(page.getByText('standard-claude')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('standard-codex')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/catalog identifies system-scope specs', async ({ page }) => {
  await page.goto('/volundr/catalog');
  await expect(page.getByText('system').first()).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// /volundr/clusters — Legacy redirect to Guild
// ---------------------------------------------------------------------------

test('/volundr/clusters redirects to guild', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await page.waitForURL('**/guild');
  await expect(page.getByRole('heading', { name: 'Guild' })).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// /volundr/history — History page
// ---------------------------------------------------------------------------

test('/volundr/history renders the history page', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByRole('heading', { name: /session history/i })).toBeVisible();
});

test('/volundr/history shows terminated session rows', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByTestId('history-row').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/history shows outcome chips', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByText('terminated').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/history shows filter controls', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByLabel(/raven id/i)).toBeVisible();
  await expect(page.getByLabel(/persona/i)).toBeVisible();
  await expect(page.getByLabel(/saga/i)).toBeVisible();
});

test('/volundr/history — filter by outcome (failed)', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByTestId('history-row').first()).toBeVisible({ timeout: 5_000 });
  const initialRows = await page.getByTestId('history-row').count();
  await expect(initialRows).toBeGreaterThan(1);

  await page.getByRole('button', { name: 'failed' }).click();
  // New ds-4 (active/failed) + historical ds-3 (failed) = 2
  await expect(page.getByTestId('history-row')).toHaveCount(2, { timeout: 5_000 });
});

test('/volundr/history — clicking All restores all rows', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByTestId('history-row').first()).toBeVisible({ timeout: 5_000 });
  const total = await page.getByTestId('history-row').count();

  await page.getByRole('button', { name: 'failed' }).click();
  // New ds-4 (active/failed) + historical ds-3 (failed) = 2
  await expect(page.getByTestId('history-row')).toHaveCount(2, { timeout: 5_000 });

  await page.getByRole('button', { name: 'All' }).click();
  await expect(page.getByTestId('history-row')).toHaveCount(total, { timeout: 5_000 });
});

test('/volundr/history — each row has a Details link', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByRole('link', { name: /details/i }).first()).toBeVisible({
    timeout: 5_000,
  });
});

test('/volundr/history — shows clear filters button after filter applied', async ({ page }) => {
  await page.goto('/volundr/history');
  await expect(page.getByRole('button', { name: /clear filters/i })).not.toBeVisible();

  await page.getByLabel(/raven id/i).fill('r1');
  await expect(page.getByRole('button', { name: /clear filters/i })).toBeVisible({
    timeout: 3_000,
  });
});
