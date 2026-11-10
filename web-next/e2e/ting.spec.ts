import { test, expect } from '@playwright/test';

test('ting lands on Work with the primary navigation only', async ({ page }) => {
  await page.goto('/ting');

  await expect(page).toHaveURL(/\/ting\/work$/);
  await expect(page.getByTestId('ting-tab-work')).toBeVisible();
  await expect(page.getByTestId('ting-tab-workflows')).toBeVisible();
  await expect(page.getByTestId('ting-tab-sagas')).toHaveCount(0);
  await expect(page.getByTestId('ting-tab-dispatch')).toHaveCount(0);
});

test('old project links land in Work, where projects are listed', async ({ page }) => {
  await page.goto('/ting/sagas');
  await expect(page).toHaveURL(/\/ting\/work$/);
});

test('dispatch route renders queue and rules surfaces', async ({ page }) => {
  await page.goto('/ting/dispatch');

  await expect(page.getByRole('list', { name: 'Dispatch queue' })).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole('group', { name: 'Filter runs by status' })).toBeVisible();
  await expect(page.getByRole('searchbox', { name: 'Search runs' })).toBeVisible();
  await expect(page.getByLabel('Dispatch rules panel')).toBeVisible();
});

test('dispatch route search accepts input', async ({ page }) => {
  await page.goto('/ting/dispatch');

  const search = page.getByRole('searchbox', { name: 'Search runs' });
  await search.fill('auth');
  await expect(search).toHaveValue('auth');
});

test('the retired plan wizard sends people to Projects', async ({ page }) => {
  await page.goto('/ting/plan/new');
  await expect(page).toHaveURL(/\/realms$/);
});

test('one Workflows page views, edits and redirects old builder links', async ({ page }) => {
  await page.goto('/ting/workflows');

  await expect(page.getByTestId('workflow-catalog-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('ting-tab-builder')).toHaveCount(0);
  await expect(page.getByTestId('ting-tab-workflows')).toHaveClass(/--active/);
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await expect(page.getByTestId('graph-view')).toBeVisible();
  await expect(page.getByTestId('workflow-run-panel')).toBeVisible();
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Viewing');

  await page.goto('/ting/workflows/build');
  await expect(page).toHaveURL(/\/ting\/workflows$/);
  await expect(page.getByTestId('workflow-catalog-page')).toBeVisible();
});
