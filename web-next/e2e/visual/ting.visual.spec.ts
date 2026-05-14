/**
 * Visual regression specs for the Ting plugin.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Dashboard ─────────────────────────────────────────────────────────────────

test('ting dashboard matches web2', async ({ page }) => {
  await page.goto('/ting');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Active sagas', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('ting-dashboard.png');
});

// ── Sagas ─────────────────────────────────────────────────────────────────────

test('ting sagas list matches web2', async ({ page }) => {
  await page.goto('/ting/sagas');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Sagas', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('ting-sagas-list.png');
});

test('ting saga detail matches web2', async ({ page }) => {
  // Navigate to first saga detail (Auth Rewrite from seed data)
  await page.goto('/ting/sagas/00000000-0000-0000-0000-000000000001');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Auth Rewrite', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('ting-saga-detail.png');
});

// ── Dispatch ──────────────────────────────────────────────────────────────────

test('ting dispatch matches web2', async ({ page }) => {
  await page.goto('/ting/dispatch');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ting-dispatch.png');
});

// ── Workflows ─────────────────────────────────────────────────────────────────

test('ting workflows matches web2', async ({ page }) => {
  await page.goto('/ting/workflows');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ting-workflows.png');
});

// ── Plan ──────────────────────────────────────────────────────────────────────

test('ting plan matches web2', async ({ page }) => {
  await page.goto('/ting/plan');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Describe your goal', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('ting-plan.png');
});

// ── Settings ──────────────────────────────────────────────────────────────────

test('ting settings matches web2', async ({ page }) => {
  await page.goto('/ting/settings');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ting-settings.png');
});
