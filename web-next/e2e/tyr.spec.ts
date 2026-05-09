import { test, expect } from '@playwright/test';

test('tyr dashboard renders with dispatcher stats and KPI cards', async ({ page }) => {
  await page.goto('/tyr');

  await expect(page.getByRole('heading', { name: 'Tyr' })).toBeVisible();
  await expect(page.getByTestId('tyr-dispatcher-stats')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Active sagas')).toBeVisible();
  await expect(page.getByText('Active raids')).toBeVisible();
  await expect(page.getByText('Merged · 24h')).toBeVisible();
});

test('sagas route renders search, filters, and actions', async ({ page }) => {
  await page.goto('/tyr/sagas');

  await expect(page.getByRole('searchbox', { name: 'Search sagas' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('searchbox', { name: 'Filter sagas' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Import saga from tracker' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Export sagas as JSON' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create new saga' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Saga list' })).toBeVisible();
});

test('saga detail route shows phase content', async ({ page }) => {
  await page.goto('/tyr/sagas/00000000-0000-0000-0000-000000000001');

  await expect(page.getByText(/Phase 1/i).first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByLabel('Stage progress')).toBeVisible();
  await expect(page.getByLabel('Confidence drift')).toBeVisible();
});

test('dispatch route renders queue and rules surfaces', async ({ page }) => {
  await page.goto('/tyr/dispatch');

  await expect(page.getByRole('list', { name: 'Dispatch queue' })).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole('group', { name: 'Filter raids by status' })).toBeVisible();
  await expect(page.getByRole('searchbox', { name: 'Search raids' })).toBeVisible();
  await expect(page.getByLabel('Dispatch rules panel')).toBeVisible();
});

test('dispatch route search accepts input', async ({ page }) => {
  await page.goto('/tyr/dispatch');

  const search = page.getByRole('searchbox', { name: 'Search raids' });
  await search.fill('auth');
  await expect(search).toHaveValue('auth');
});

test('plan wizard renders the prompt step', async ({ page }) => {
  await page.goto('/tyr/plan');

  await expect(page.getByRole('form', { name: 'Plan prompt form' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Describe your goal' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Plan wizard steps' })).toBeVisible();
});

test('plan wizard advances to clarifying questions', async ({ page }) => {
  await page.goto('/tyr/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();

  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('heading', { name: 'Clarify your plan' })).toBeVisible();
});

test('plan wizard can decompose into raiding and review states', async ({ page }) => {
  await page.goto('/tyr/plan');

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
  await page.goto('/tyr/plan');

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
  await page.goto('/tyr/workflows');

  await expect(page.getByTestId('workflow-builder-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await expect(page.getByTestId('graph-view')).toBeVisible();
});
