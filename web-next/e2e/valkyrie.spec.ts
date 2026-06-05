import { expect, test } from '@playwright/test';

test('valkyrie console renders environment and flock operations', async ({ page }) => {
  await page.goto('/valkyrie');

  await expect(page.getByTestId('valkyrie-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Valhalla k8s' })).toBeVisible();
  await expect(page.getByTestId('valkyrie-live-console')).toBeVisible();
  await expect(page.getByTestId('valkyrie-live-scope-rail')).toBeVisible();
  await expect(page.getByTestId('valkyrie-event-log')).toBeVisible();
  await expect(page.getByTestId('valkyrie-work-queue')).toBeVisible();
  await expect(page.getByTestId('valkyrie-llm-status')).toBeVisible();
  await expect(page.getByTestId('valkyrie-court-panel')).toBeVisible();
  await expect(page.getByTestId('valkyrie-actions-panel')).toBeVisible();
});

test('valkyrie console switches between live route views', async ({ page }) => {
  await page.goto('/valkyrie');

  await page.getByRole('button', { name: /topology/i }).click();
  await expect(page).toHaveURL(/\/valkyrie\/topology$/);
  await expect(page.getByTestId('valkyrie-topology-view')).toBeVisible();

  await page.getByRole('button', { name: /learning/i }).click();
  await expect(page).toHaveURL(/\/valkyrie\/learning$/);
  await expect(page.getByTestId('valkyrie-learning-ops')).toBeVisible();

  await page.getByRole('button', { name: /huddles/i }).click();
  await expect(page).toHaveURL(/\/valkyrie\/huddles$/);
  await expect(page.getByTestId('valkyrie-huddles-view')).toBeVisible();

  await page.getByRole('button', { name: /autonomy/i }).click();
  await expect(page).toHaveURL(/\/valkyrie\/autonomy$/);
  await expect(page.getByTestId('valkyrie-autonomy-panel')).toBeVisible();
});

test('legacy plural valkyries path redirects to the canonical console', async ({ page }) => {
  await page.goto('/valkyries');

  await expect(page).toHaveURL(/\/valkyrie$/);
  await expect(page.getByTestId('valkyrie-page')).toBeVisible({ timeout: 5000 });
});
