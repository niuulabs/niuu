/**
 * Visual regression specs for the Login plugin.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Login page ────────────────────────────────────────────────────────────────

test('login page matches web2', async ({ page }) => {
  await page.goto('/login');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('login-page.png');
});
