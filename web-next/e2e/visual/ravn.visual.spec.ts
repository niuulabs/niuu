/**
 * Visual regression spec for the Ravn plugin's persona library.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

test('ravn persona library', async ({ page }) => {
  await page.goto('/ravn/personas?persona=reviewer');
  await page.waitForSelector('[data-testid="persona-sheet"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ravn-persona-library.png');
});
