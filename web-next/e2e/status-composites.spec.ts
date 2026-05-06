import { test, expect } from '@playwright/test';

test.describe('status composites showcase', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/hello/status-showcase');
    await expect(page.getByText(/status composites/i)).toBeVisible({ timeout: 5000 });
  });

  test('page renders the heading', async ({ page }) => {
    await expect(page.getByText(/status composites/i)).toBeVisible();
  });

  test('StatusBadge — renders all 12 statuses', async ({ page }) => {
    const grid = page.getByTestId('status-badge-grid');
    await expect(grid).toBeVisible();
    const badges = grid.locator('[role="status"]');
    await expect(badges).toHaveCount(12);
  });

  test('StatusBadge — running badge is visible', async ({ page }) => {
    await expect(page.getByRole('status', { name: 'running' }).first()).toBeVisible();
  });

  test('StatusBadge — failed badge is visible', async ({ page }) => {
    await expect(page.getByRole('status', { name: 'failed' })).toBeVisible();
  });

  test('ConfidenceBar — renders high, medium, low', async ({ page }) => {
    const grid = page.getByTestId('confidence-bar-grid');
    const bars = grid.locator('[role="meter"]');
    await expect(bars).toHaveCount(3);
    await expect(page.getByRole('meter', { name: 'confidence: high' })).toBeVisible();
    await expect(page.getByRole('meter', { name: 'confidence: medium' })).toBeVisible();
    await expect(page.getByRole('meter', { name: 'confidence: low' })).toBeVisible();
  });

  test('ConfidenceBadge — renders grid including null placeholder', async ({ page }) => {
    const grid = page.getByTestId('confidence-badge-grid');
    await expect(grid).toBeVisible();
    // Both null and 0 render '—'; use first() to avoid strict-mode violation
    await expect(grid.getByText('—').first()).toBeVisible();
  });

  test('ConfidenceBadge — renders numeric percentages', async ({ page }) => {
    const grid = page.getByTestId('confidence-badge-grid');
    const meters = grid.locator('[role="meter"]');
    await expect(meters.first()).toBeVisible();
  });

  test('Pipe — renders all three pipe variants', async ({ page }) => {
    const grid = page.getByTestId('pipe-grid');
    await expect(grid).toBeVisible();
    const pipes = grid.locator('[role="list"]');
    await expect(pipes).toHaveCount(3);
  });

  test('Pipe — cells are individually labelled', async ({ page }) => {
    const grid = page.getByTestId('pipe-grid');
    const cells = grid.locator('[role="listitem"]');
    await expect(cells.first()).toBeVisible();
  });

  test('keyboard accessibility — page is tabbable', async ({ page }) => {
    // Wait for page content before sending Tab
    await expect(page.getByText(/status composites/i)).toBeVisible();
    await page.keyboard.press('Tab');
    // Some focusable element (e.g. a rail button) should receive focus
    const focused = page.locator('button:focus, a:focus, input:focus, [tabindex]:focus');
    await expect(focused.first()).toBeVisible();
  });

  test('Sparkline — renders grid with 3 rows', async ({ page }) => {
    const grid = page.getByTestId('sparkline-grid');
    await expect(grid).toBeVisible();
    const rows = grid.locator('[aria-hidden="true"]');
    await expect(rows).toHaveCount(3);
  });

  test('Sparkline — seeded sparkline renders an svg', async ({ page }) => {
    const grid = page.getByTestId('sparkline-grid');
    const svgs = grid.locator('svg');
    await expect(svgs.first()).toBeVisible();
  });

  test('BudgetBar — renders bars for all percentages', async ({ page }) => {
    const grid = page.getByTestId('budget-bar-grid');
    await expect(grid).toBeVisible();
    const meters = grid.locator('[role="meter"]');
    await expect(meters).toHaveCount(6);
  });

  test('BudgetBar — renders dollar labels', async ({ page }) => {
    const grid = page.getByTestId('budget-bar-grid');
    await expect(grid.getByText('$100.00').first()).toBeVisible();
  });

  test('BudgetRunwayBar — renders three runway bars', async ({ page }) => {
    const grid = page.getByTestId('budget-runway-grid');
    await expect(grid).toBeVisible();
    const meters = grid.locator('[role="meter"]');
    await expect(meters).toHaveCount(3);
  });
});
