/**
 * Visual regression specs for the Volundr plugin.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Forge overview ────────────────────────────────────────────────────────────

test('volundr forge overview matches web2', async ({ page }) => {
  await page.goto('/volundr');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('[data-testid="forge-page"]', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('volundr-forge-overview.png');
});

// ── Templates ─────────────────────────────────────────────────────────────────

test('volundr templates matches web2', async ({ page }) => {
  await page.goto('/volundr/templates');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-templates.png');
});

// ── Clusters ──────────────────────────────────────────────────────────────────

test('volundr clusters matches web2', async ({ page }) => {
  await page.goto('/volundr/clusters');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-clusters.png');
});

// ── Sessions list ─────────────────────────────────────────────────────────────

test('volundr sessions matches web2', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await page.waitForTimeout(500);
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-sessions.png');
});

// ── Session chat ──────────────────────────────────────────────────────────────

test('volundr session chat matches web2', async ({ page }) => {
  // ds-1 is the first running session in seed data
  await page.goto('/volundr/session/ds-1');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-session-chat.png');
});
