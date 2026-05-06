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
  await expect(page.getByText('default')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates shows the gpu-workload template', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByText('gpu-workload')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates shows version badges', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByText('v1')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates — Clone button creates a cloned template', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByTestId('template-card').first()).toBeVisible({ timeout: 5_000 });

  const initialCount = await page.getByTestId('template-card').count();

  // Click the first Clone button
  await page
    .getByRole('button', { name: /clone template/i })
    .first()
    .click();

  // Wait for the new cloned card to appear
  await expect(page.getByTestId('template-card')).toHaveCount(initialCount + 1, {
    timeout: 5_000,
  });
  await expect(page.getByText(/clone of/i)).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates — Edit button opens the editor drawer', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByTestId('template-card').first()).toBeVisible({ timeout: 5_000 });

  await page
    .getByRole('button', { name: /edit template/i })
    .first()
    .click();

  await expect(page.getByRole('dialog', { name: /edit template/i })).toBeVisible({
    timeout: 5_000,
  });
});

test('/volundr/templates — New Template button opens editor', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.getByRole('button', { name: /new template/i }).click();
  await expect(page.getByRole('dialog', { name: /new template/i })).toBeVisible({
    timeout: 5_000,
  });
});

test('/volundr/templates — editor shows form fields', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.getByRole('button', { name: /new template/i }).click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByPlaceholder('e.g. default')).toBeVisible();
  await expect(page.getByPlaceholder('ghcr.io/niuulabs/skuld')).toBeVisible();
});

test('/volundr/templates — saving with blank name shows validation error', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.getByRole('button', { name: /new template/i }).click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });

  // Clear name field
  const nameInput = page.getByPlaceholder('e.g. default');
  await nameInput.clear();

  await page.getByRole('button', { name: /save template/i }).click();
  await expect(page.getByText(/name is required/i).first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates — edit, change image, save updates the template', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByTestId('template-card').first()).toBeVisible({ timeout: 5_000 });

  await page
    .getByRole('button', { name: /edit template/i })
    .first()
    .click();
  await expect(page.getByRole('dialog', { name: /edit template/i })).toBeVisible({
    timeout: 5_000,
  });

  const imageInput = page.getByPlaceholder('ghcr.io/niuulabs/skuld');
  await imageInput.clear();
  await imageInput.fill('ghcr.io/niuulabs/skuld-new');

  await page.getByRole('button', { name: /save template/i }).click();

  // Drawer should close on success
  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5_000 });
});

test('/volundr/templates — masks secret-ref env values in card display', async ({ page }) => {
  await page.goto('/volundr/templates');
  await expect(page.getByText('gpu-workload')).toBeVisible({ timeout: 5_000 });
  // HF_TOKEN is a secret ref — its value should be masked
  await expect(page.getByText('HF_TOKEN')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('***')).toBeVisible({ timeout: 5_000 });
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
  await expect(page.getByTestId('cluster-card').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows cluster names', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByText('Eitri')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('Brokkr')).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows capacity bars', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByTestId('cap-cpu').first()).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('cap-memory').first()).toBeVisible({ timeout: 5_000 });
});

test('/volundr/clusters shows node list', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await expect(page.getByTestId('cluster-node').first()).toBeVisible({ timeout: 5_000 });
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
