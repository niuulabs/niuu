/**
 * Visual regression specs for the Mimir plugin.
 *
 * Mimir has a main overview page (/mimir) with three tabs and dedicated
 * sub-routes for search, graph, and entity views.
 */

import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});

// ── Home — Overview tab ───────────────────────────────────────────────────────

test('mimir overview matches web2', async ({ page }) => {
  await page.goto('/mimir');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(500);
  await expect(page).toHaveScreenshot('mimir-overview.png');
});

// ── Pages tab ─────────────────────────────────────────────────────────────────

test('mimir pages — tree matches web2', async ({ page }) => {
  await page.goto('/mimir/pages');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('mimir-pages-tree.png');
});

test('mimir pages — reader matches web2', async ({ page }) => {
  await page.goto('/mimir/pages');
  await page.waitForLoadState('networkidle');
  await page
    .getByRole('button', { name: /overview/i })
    .first()
    .click();
  await page.waitForTimeout(400);
  await expect(page).toHaveScreenshot('mimir-pages-reader.png');
});

// ── Sources ───────────────────────────────────────────────────────────────────

test('mimir sources matches web2', async ({ page }) => {
  await page.goto('/mimir/sources');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('mimir-sources.png');
});

// ── Search ────────────────────────────────────────────────────────────────────

test('mimir search matches web2', async ({ page }) => {
  await page.goto('/mimir/search');
  await page.waitForLoadState('networkidle');
  await page.waitForSelector('input[type="search"]', { timeout: 10_000 });
  await expect(page).toHaveScreenshot('mimir-search.png');
});

// ── Graph ─────────────────────────────────────────────────────────────────────

test('mimir graph matches web2', async ({ page }) => {
  await page.goto('/mimir/graph');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('mimir-graph.png');
});

// ── Entities ──────────────────────────────────────────────────────────────────

test('mimir entities matches web2', async ({ page }) => {
  await page.goto('/mimir/entities');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('mimir-entities.png');
});

// ── Ingest ───────────────────────────────────────────────────────────────────

test('mimir ingest matches web2', async ({ page }) => {
  await page.goto('/mimir/ingest');
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('mimir-ingest.png');
});
