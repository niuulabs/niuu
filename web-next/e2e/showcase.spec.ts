import { test, expect } from '@playwright/test';

test.describe('Showcase page — data surfaces', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/showcase');
    // Wait for shell to boot
    await expect(page.getByText('NIU-658 · Data surfaces showcase')).toBeVisible({ timeout: 8000 });
  });

  // ── KpiStrip ─────────────────────────────────────────

  test('KpiStrip renders KPI cards', async ({ page }) => {
    await expect(page.getByText('Total Dispatches')).toBeVisible();
    await expect(page.getByText('1,204')).toBeVisible();
    await expect(page.getByText('Running', { exact: true })).toBeVisible();
    await expect(page.getByText('Error Rate')).toBeVisible();
  });

  // ── FilterBar ─────────────────────────────────────────

  test('FilterBar search input is present', async ({ page }) => {
    await expect(page.getByRole('searchbox', { name: 'Search' })).toBeVisible();
  });

  test('FilterBar filters rows by search query', async ({ page }) => {
    await page.getByRole('searchbox').fill('prod-001');
    await expect(page.getByText('dispatch-prod-001')).toBeVisible();
    await expect(page.getByText('dispatch-prod-002')).not.toBeVisible();
  });

  test('FilterToggle filters to running-only rows', async ({ page }) => {
    await page.getByRole('switch', { name: 'Running only' }).click();
    await expect(page.getByText('dispatch-prod-001')).toBeVisible();
    // idle row should be hidden
    await expect(page.getByText('dispatch-prod-002')).not.toBeVisible();
  });

  // ── Table ─────────────────────────────────────────────

  test('Table shows dispatch rows', async ({ page }) => {
    await expect(page.getByRole('table', { name: 'Dispatch table' })).toBeVisible();
    await expect(page.getByText('dispatch-prod-001')).toBeVisible();
  });

  test('Table sorting: clicking Name header sorts ascending then descending', async ({ page }) => {
    const nameHeader = page.getByRole('columnheader', { name: /Name/ });

    // Table starts sorted by Name ascending; first click toggles to descending
    await nameHeader.click();
    await expect(nameHeader).toHaveAttribute('aria-sort', 'descending');

    // Click again → back to ascending
    await nameHeader.click();
    await expect(nameHeader).toHaveAttribute('aria-sort', 'ascending');
  });

  test('Table row selection: select-all checkbox selects all rows', async ({ page }) => {
    const selectAll = page.getByRole('checkbox', { name: 'select all' });
    await selectAll.click();
    const rowCheckboxes = page.getByRole('checkbox', { name: /select row/ });
    const count = await rowCheckboxes.count();
    for (let i = 0; i < count; i++) {
      await expect(rowCheckboxes.nth(i)).toBeChecked();
    }
  });

  test('Table row expand: clicking expand shows detail row', async ({ page }) => {
    const expandBtns = page.getByRole('button', { name: 'expand row' });
    await expandBtns.first().click();
    // Expanded content shows an ID field for the first (alphabetically sorted) row
    await expect(page.getByText(/ID:\s*d\d/)).toBeVisible();
  });

  test('Table row expand: clicking again collapses', async ({ page }) => {
    await page.getByRole('button', { name: 'expand row' }).first().click();
    await page.getByRole('button', { name: 'collapse row' }).click();
    await expect(page.getByText(/ID:\s*d\d/)).not.toBeVisible();
  });

  // ── LoadingState ──────────────────────────────────────

  test('LoadingState renders when mode is loading', async ({ page }) => {
    await page.getByTestId('mode-loading').click();
    await expect(page.getByText('Loading dispatches…')).toBeVisible();
  });

  // ── EmptyState ────────────────────────────────────────

  test('EmptyState renders when mode is empty', async ({ page }) => {
    await page.getByTestId('mode-empty').click();
    await expect(page.getByText('No dispatches found')).toBeVisible();
    await expect(page.getByRole('button', { name: 'New Dispatch' })).toBeVisible();
  });

  // ── ErrorState ────────────────────────────────────────

  test('ErrorState renders when mode is error', async ({ page }) => {
    await page.getByTestId('mode-error').click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByText('Could not load dispatches')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible();
  });

  test('switching back to data mode shows table again', async ({ page }) => {
    await page.getByTestId('mode-error').click();
    await page.getByTestId('mode-data').click();
    await expect(page.getByRole('table', { name: 'Dispatch table' })).toBeVisible();
  });
});
