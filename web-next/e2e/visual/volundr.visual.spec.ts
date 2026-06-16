/**
 * Visual regression specs for the Volundr plugin.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Forge overview ────────────────────────────────────────────────────────────

test('volundr forge overview matches web2', async ({ page }) => {
  await page.goto('/volundr/forge');
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('forge-page')).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveScreenshot('volundr-forge-overview.png');
});

// ── Launch catalog ────────────────────────────────────────────────────────────

test('volundr launch catalog matches web2', async ({ page }) => {
  await page.goto('/volundr/catalog');
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading', { name: /launch catalog/i })).toBeVisible({
    timeout: 10_000,
  });
  await expect(page).toHaveScreenshot('volundr-launch-catalog.png');
});

// ── Guild overview (legacy /volundr/clusters replacement) ───────────────────

test('guild overview matches web2', async ({ page }) => {
  await page.goto('/guild');
  await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading', { name: 'Guild' })).toBeVisible({ timeout: 10_000 });
  await expect(page).toHaveScreenshot('guild-overview.png');
});

// ── Sessions list ─────────────────────────────────────────────────────────────

test('volundr sessions matches web2', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await page.waitForTimeout(500);
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-sessions.png', {
    maxDiffPixelRatio: 0.03,
  });
});

// ── Session chat ──────────────────────────────────────────────────────────────

test('volundr session chat matches web2', async ({ page }) => {
  // ds-1 is the first running session in seed data
  await page.goto('/volundr/session/ds-1');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('volundr-session-chat.png');
});
