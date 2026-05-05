/**
 * Visual regression specs for the Observatory plugin.
 *
 * Each test navigates to a view in web-next, waits for content to settle, and
 * calls `toHaveScreenshot()`. On first run (or `--update-snapshots`) Playwright
 * writes the baseline PNG alongside this file. CI compares against it.
 *
 * Tolerance: maxDiffPixelRatio defaults to 0.05 (5%) — set globally in
 * playwright.config.ts. Tighten per-test by passing `{ maxDiffPixelRatio: 0.02 }`
 * as parity improves.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Canvas view ────────────────────────────────────────────────────────────────

test('observatory canvas matches web2', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="topology-canvas"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('observatory-canvas.png');
});

// ── Registry ──────────────────────────────────────────────────────────────────

test('observatory registry — types tab matches web2', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-types"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('observatory-registry-types.png');
});

test('observatory registry — containment tab matches web2', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-containment"]', { timeout: 10_000 });
  await page.click('[data-testid="tab-containment"]');
  await page.waitForTimeout(300);
  await expect(page).toHaveScreenshot('observatory-registry-containment.png');
});

test('observatory registry — JSON tab matches web2', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-json"]', { timeout: 10_000 });
  await page.click('[data-testid="tab-json"]');
  await page.waitForTimeout(300);
  await expect(page).toHaveScreenshot('observatory-registry-json.png');
});
