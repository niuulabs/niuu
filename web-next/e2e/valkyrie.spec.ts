import { expect, test } from '@playwright/test';

test('valkyrie console renders environment and flock operations', async ({ page }) => {
  await page.goto('/valkyries');

  await expect(page.getByTestId('valkyrie-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Valhalla k8s' })).toBeVisible();
  await expect(page.getByTestId('resident-panel')).toBeVisible();
  await expect(page.getByTestId('signal-panel')).toBeVisible();
  await expect(page.getByTestId('environment-state-panel')).toBeVisible();
  await expect(page.getByTestId('decisions-panel')).toBeVisible();
  await expect(page.getByTestId('huddle-panel')).toBeVisible();
  await expect(page.getByTestId('learning-panel')).toBeVisible();
});

test('valkyrie console switches to flock learning without leaving the NATS flock model', async ({
  page,
}) => {
  await page.goto('/valkyries');

  await page.getByTestId('flock-flock-k8s').click();

  await expect(page.getByRole('heading', { name: 'Kubernetes Valkyries' })).toBeVisible();
  await expect(page.getByText('kubernetes · odin.flock.k8s.>')).toBeVisible();
  await expect(page.getByText('OOMKilled with rising queue depth')).toBeVisible();
  await expect(page.getByRole('button', { name: /adopt oomkilled/i })).toBeVisible();
});

test('valkyrie console supports operator autonomy and huddle actions', async ({ page }) => {
  await page.goto('/valkyries');

  const sigrunAutonomy = page.getByLabel('Autonomy for Sigrun');
  await expect(sigrunAutonomy).toHaveValue('delegated');
  await sigrunAutonomy.selectOption('yolo');
  await expect(sigrunAutonomy).toHaveValue('yolo');

  await page.getByRole('button', { name: 'Join' }).click();
  await page.getByLabel(/message valhalla memory/i).fill('Check the pull secret rollout.');
  await page.getByRole('button', { name: 'Send' }).click();

  await expect(page.getByText('Check the pull secret rollout.')).toBeVisible();
});
