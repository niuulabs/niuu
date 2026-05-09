import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// /volundr/templates — Templates page
// ---------------------------------------------------------------------------

test('/volundr/templates renders the templates page', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByRole('heading', { name: /templates/i })).toBeVisible();
});

test('/volundr/templates shows template cards after load', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByTestId('template-card').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates shows the default template', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByText('niuu-platform')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates shows the showcase rail count', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByRole('list', { name: 'Pod templates' })).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('template-card')).toHaveCount(6);
});

test('/volundr/templates shows the selected template detail workspace', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByText(/workspace \+ runtime bundles/i)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('detail-card')).toHaveCount(4);
});

test('/volundr/templates can switch the selected template from the rail', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByTestId('template-card').first()).toBeVisible({ timeout: 5_000 });
  await page.getByText('volundr-web', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'volundr-web' })).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates exposes template detail tabs', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.getByRole('tab', { name: 'runtime' }).click();
  await expect(page.getByTestId('tab-runtime')).toBeVisible({ timeout: 5_000 });
  await page.getByRole('tab', { name: 'workspace' }).click();
  await expect(page.getByTestId('tab-workspace')).toBeVisible({ timeout: 5_000 });
  await page.getByRole('tab', { name: 'rules' }).click();
  await expect(page.getByTestId('tab-rules')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates — bifrost-gateway surfaces its MCP server detail', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.getByRole('button', { name: 'bifrost-gateway', exact: true }).click();
  await page.getByRole('tab', { name: 'mcp' }).click();
  await expect(page.getByTestId('tab-mcp')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('mcp-server-card')).toHaveCount(2);
  await expect(page.getByText('filesystem', { exact: true })).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('uvx mcp-filesystem')).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// /volundr/clusters — Clusters page
// ---------------------------------------------------------------------------

test('/volundr/clusters renders the clusters page', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByRole('heading', { name: /clusters/i })).toBeVisible();
});

test('/volundr/clusters shows cluster cards', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByTestId('clusters-sidebar')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByRole('button', { name: 'Eitri' })).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows cluster names', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByRole('heading', { name: 'Valaskjálf' })).toBeVisible({
    timeout: 5_000,
  });
  await expect(page.getByRole('button', { name: /Eitri/i })).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows capacity bars', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByText('CPU').first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('MEMORY').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows node list', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByRole('heading', { name: 'Nodes' })).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/valaskjalf-/i).first()).toBeVisible({ timeout: 5_000 });
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
