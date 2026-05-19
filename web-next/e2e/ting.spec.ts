import { test, expect } from '@playwright/test';

test('ting dashboard renders with dispatcher stats and KPI cards', async ({ page }) => {
  await page.goto('/ting');

  await expect(page.getByRole('heading', { name: 'Ting' })).toBeVisible();
  await expect(page.getByTestId('ting-dispatcher-stats')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('.ting-kpi__label', { hasText: 'Active sagas' }).first()).toBeVisible();
  await expect(page.locator('.ting-kpi__label', { hasText: 'Active runs' }).first()).toBeVisible();
  await expect(page.locator('.ting-kpi__label', { hasText: 'Merged · 24h' }).first()).toBeVisible();
});

test('sagas route renders search, filters, and actions', async ({ page }) => {
  await page.goto('/ting/sagas');

  await expect(page.getByRole('searchbox', { name: 'Search sagas' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('searchbox', { name: 'Filter sagas' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Import saga from tracker' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Export sagas as JSON' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create new saga' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Auth Rewrite/i })).toBeVisible();
  await expect(page.getByText('feat/auth-rewrite').first()).toBeVisible();
  await expect(page.getByText(/1\/3 runs/i).first()).toBeVisible();
});

test('saga detail route shows phase content', async ({ page }) => {
  await page.goto('/ting/sagas/00000000-0000-0000-0000-000000000001');

  await expect(page.getByText(/Phase 1/i).first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByLabel('Stage progress')).toBeVisible();
  await expect(page.getByLabel('Confidence signals')).toBeVisible();
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

test('plan wizard renders the prompt step', async ({ page }) => {
  await page.goto('/ting/plan');

  await expect(page.getByRole('form', { name: 'Plan prompt form' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Describe your goal' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Plan wizard steps' })).toBeVisible();
});

test('plan wizard advances to clarifying questions', async ({ page }) => {
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();

  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('heading', { name: 'Clarify your plan' })).toBeVisible();
});

test('plan wizard can decompose into runing and review states', async ({ page }) => {
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();
  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });

  await page.getByRole('button', { name: 'Decompose →' }).click();
  await expect(page.getByLabel('Decomposing plan')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Review your plan' })).toBeVisible({
    timeout: 10000,
  });
});

test('plan wizard can approve into the launched state', async ({ page }) => {
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();
  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });

  await page.getByRole('button', { name: 'Decompose →' }).click();
  await expect(page.getByRole('heading', { name: 'Review your plan' })).toBeVisible({
    timeout: 10000,
  });

  await page.getByRole('button', { name: 'Approve & Launch' }).click();
  await expect(page.getByTestId('plan-approved')).toBeVisible({ timeout: 5000 });
});

test('workflow builder route renders the builder shell', async ({ page }) => {
  await page.goto('/ting/workflows');

  await expect(page.getByTestId('workflow-builder-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await expect(page.getByTestId('graph-view')).toBeVisible();
});
