/**
 * Visual regression specs for the Ravn plugin.
 *
 * All views live at /ravn with tab-based navigation. Tests click each tab to
 * reach the target view, then compare the full page snapshot.
 *
 * Shell tabs: Overview, Ravens, Personas, Sessions, Budget
 * (Triggers and Events content lives within the Overview page.)
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/ravn');
  await page.waitForSelector('[data-testid="ravn-page"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
});

// ── Overview ──────────────────────────────────────────────────────────────────

test('ravn overview matches web2', async ({ page }) => {
  // Overview is the default tab at /ravn
  await page.waitForSelector('[data-testid="overview-page"]', { timeout: 5_000 });
  await expect(page).toHaveScreenshot('ravn-overview.png');
});

// ── Ravens ─────────────────────────────────────────────────────────────────────

test('ravn ravens split view matches web2', async ({ page }) => {
  await page.getByTestId('ravn-tab-ravens').click();
  await page.waitForSelector('[data-testid="ravens-page"]', { timeout: 5_000 });
  await page.waitForSelector('[data-testid="layout-split"]', { timeout: 5_000 });
  await expect(page).toHaveScreenshot('ravn-ravens-split.png');
});

// ── Sessions ──────────────────────────────────────────────────────────────────

test('ravn sessions matches web2', async ({ page }) => {
  await page.getByTestId('ravn-tab-sessions').click();
  await page.waitForTimeout(400);
  await expect(page).toHaveScreenshot('ravn-sessions.png');
});

// ── Budget ─────────────────────────────────────────────────────────────────────

test('ravn budget matches web2', async ({ page }) => {
  await page.getByTestId('ravn-tab-budget').click();
  await page.waitForTimeout(400);
  await expect(page).toHaveScreenshot('ravn-budget.png');
});

// ── Personas ───────────────────────────────────────────────────────────────────

test('ravn personas matches web2', async ({ page }) => {
  await page.getByTestId('ravn-tab-personas').click();
  await page.waitForSelector('[data-testid="personas-page"]', { timeout: 5_000 });
  // Select "reviewer" persona to show the form (matches web2 baseline)
  await page.getByText('reviewer', { exact: true }).first().click();
  await page.waitForTimeout(300);
  await expect(page).toHaveScreenshot('ravn-personas.png');
});
