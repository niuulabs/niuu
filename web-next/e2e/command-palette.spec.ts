import { test, expect } from '@playwright/test';

// The command palette is globally accessible from any page via ⌘K / Ctrl+K.
// In CI (Linux/chromium) we use Control+k; both shortcuts are wired in the provider.

test.describe('CommandPalette', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/volundr');
    await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();
  });

  test('Ctrl+K opens the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByPlaceholder('Search commands…')).toBeVisible();
  });

  test('Escape closes the palette', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('Ctrl+K toggles the palette closed', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Control+k');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('⌘K button in topbar opens the palette', async ({ page }) => {
    await page.getByRole('button', { name: 'Open command palette (⌘K)' }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
  });

  test('type to filter narrows results', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Search commands…').fill('ravn');
    await expect(page.getByRole('option', { name: /ravn/i }).first()).toBeVisible();
    // Items that don't match should be hidden
    await expect(page.getByRole('option', { name: /mimir/i })).toBeHidden();
  });

  test('shows empty state when nothing matches', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Search commands…').fill('xyzzy-no-match-ever');
    await expect(page.getByText('No commands found')).toBeVisible();
  });

  test('ArrowDown + Enter navigates to another plugin', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Search commands…').fill('Ravn');
    await expect(page.getByRole('option', { name: /ravn/i }).first()).toBeVisible();
    await page.getByRole('option', { name: /ravn/i }).first().click();
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(page).toHaveURL(/\/ravn/);
  });

  test('ArrowDown moves the active selection', async ({ page }) => {
    await page.keyboard.press('Control+k');
    // First item is selected by default
    const firstOption = page.getByRole('option').first();
    await expect(firstOption).toHaveAttribute('aria-selected', 'true');
    // Move down
    await page.keyboard.press('ArrowDown');
    await expect(firstOption).toHaveAttribute('aria-selected', 'false');
    await expect(page.getByRole('option').nth(1)).toHaveAttribute('aria-selected', 'true');
  });
});
