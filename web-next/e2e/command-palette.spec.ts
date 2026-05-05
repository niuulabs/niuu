import { test, expect } from '@playwright/test';

// The command palette is globally accessible from any page via ⌘K / Ctrl+K.
// In CI (Linux/chromium) we use Control+k; both shortcuts are wired in the provider.

test.describe('CommandPalette', () => {
  test.beforeEach(async ({ page }) => {
    // Start at /hello — the first non-system plugin
    await page.goto('/hello');
    await expect(page.getByText('hello from the mock adapter')).toBeVisible({ timeout: 5000 });
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
    await page.getByPlaceholder('Search commands…').fill('showcase');
    await expect(page.getByRole('option', { name: /showcase/i }).first()).toBeVisible();
    // Items that don't match should be hidden
    await expect(page.getByRole('option', { name: /^hello$/i })).toBeHidden();
  });

  test('shows empty state when nothing matches', async ({ page }) => {
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Search commands…').fill('xyzzy-no-match-ever');
    await expect(page.getByText('No commands found')).toBeVisible();
  });

  test('ArrowDown + Enter navigates to another plugin', async ({ page }) => {
    // Start on /hello, navigate to /showcase via command palette
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder('Search commands…').fill('Showcase');
    // First matching result should be the Showcase plugin command
    await expect(page.getByRole('option').first()).toBeVisible();
    // Press Enter to execute
    await page.keyboard.press('Enter');
    // Palette closes and we navigate to /showcase
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(page).toHaveURL(/\/showcase/);
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
