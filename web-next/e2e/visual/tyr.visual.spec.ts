/**
 * Visual regression specs for the Tyr plugin.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Dashboard ─────────────────────────────────────────────────────────────────

test('tyr dashboard matches web2', async ({ page }) => {
  await page.goto('/tyr');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Active sagas', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('tyr-dashboard.png');
});

// ── Sagas ─────────────────────────────────────────────────────────────────────

test('tyr sagas list matches web2', async ({ page }) => {
  await page.goto('/tyr/sagas');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Sagas', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('tyr-sagas-list.png');
});

test('tyr saga detail matches web2', async ({ page }) => {
  // Navigate to first saga detail (Auth Rewrite from seed data)
  await page.goto('/tyr/sagas/00000000-0000-0000-0000-000000000001');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Auth Rewrite', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('tyr-saga-detail.png');
});

// ── Dispatch ──────────────────────────────────────────────────────────────────

test('tyr dispatch matches web2', async ({ page }) => {
  await page.goto('/tyr/dispatch');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('tyr-dispatch.png');
});

// ── Workflows ─────────────────────────────────────────────────────────────────

test('tyr workflows matches web2', async ({ page }) => {
  await page.goto('/tyr/workflows');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('tyr-workflows.png');
});

// ── Plan ──────────────────────────────────────────────────────────────────────

test('tyr plan matches web2', async ({ page }) => {
  await page.goto('/tyr/plan');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('text=Describe your goal', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('tyr-plan.png');
});

// ── Settings ──────────────────────────────────────────────────────────────────

test('tyr settings matches web2', async ({ page }) => {
  await page.goto('/tyr/settings');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('tyr-settings.png');
});
