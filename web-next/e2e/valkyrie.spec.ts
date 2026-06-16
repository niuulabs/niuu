import { expect, test } from '@playwright/test';

test('odin review inbox lists pending decisions with full lineage', async ({ page }) => {
  await page.goto('/valkyrie');

  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('review-card')).toHaveCount(3);
  await expect(page.getByTestId('inbox-pending-count')).toHaveText('3 pending');
  await expect(page.getByTestId('review-detail')).toBeVisible();
  await expect(page.getByTestId('review-lineage')).toBeVisible();
});

test('operator can inspect the artifact and approve a held build', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('review-card')).toHaveCount(3);

  await page
    .getByTestId('review-card')
    .filter({ hasText: 'valkyrie-inspect-kubernetes-pod-oomkilled' })
    .click();
  await page.getByRole('tab', { name: 'tool' }).click();
  await expect(page.getByTestId('review-artifact')).toContainText('def run(signal: dict)');

  await page.getByLabel('Decision reason').fill('canary verified, safe');
  await page.getByRole('button', { name: /approve/i }).click();

  await expect(page.getByTestId('inbox-pending-count')).toHaveText('2 pending');
});

test('rejecting without a reason is refused', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('review-card')).toHaveCount(3);

  await page.getByRole('button', { name: /reject/i }).click();

  await expect(page.getByRole('alert')).toContainText('A reason is required');
  await expect(page.getByTestId('inbox-pending-count')).toHaveText('3 pending');
});

test('fleet view exposes autonomy control per resident', async ({ page }) => {
  await page.goto('/valkyrie/fleet');

  await expect(page.getByTestId('fleet-page')).toBeVisible({ timeout: 5000 });
  const cards = page.getByTestId('fleet-card');
  await expect(cards.first()).toBeVisible();
  await expect(cards.first().getByRole('combobox')).toBeVisible();
});

test('activity shows the decided ledger', async ({ page }) => {
  await page.goto('/valkyrie/activity');

  await expect(page.getByTestId('activity-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('activity-row').first()).toContainText('decided by');
});

test('legacy valkyrie routes redirect to the inbox', async ({ page }) => {
  await page.goto('/valkyries/learning');

  await expect(page).toHaveURL(/\/valkyrie$/);
  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 5000 });
});
