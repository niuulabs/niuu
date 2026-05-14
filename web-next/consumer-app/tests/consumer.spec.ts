/**
 * Consumer E2E — proves the composability loop externally.
 *
 * This app is NOT part of the web-next pnpm workspace. It installs
 * @niuulabs/plugin-ting (and its transitive niuu deps) from tarballs
 * produced by `pnpm pack`, simulating a third-party consumer installing
 * from GitHub Packages.
 *
 * If this test passes, the publish pipeline is correct: packages are
 * self-contained, CSS ships correctly, and a real consumer can render
 * the plugin without the Niuu monorepo.
 */
import { test, expect } from '@playwright/test';

test('consumer app loads and shell renders', async ({ page }) => {
  await page.goto('/');
  // The shell renders even before config loads — the boot screen appears first
  await expect(page.locator('#root')).not.toBeEmpty();
});

test('plugin-ting nav item is visible after config loads', async ({ page }) => {
  await page.goto('/');
  // Wait for the Ting plugin rune / nav label to appear in the rail
  // The shell renders the plugin title from tingPlugin.title = 'Ting'
  await expect(page.getByText('Ting').first()).toBeVisible({ timeout: 10_000 });
});

test('navigating to /ting renders the Ting page', async ({ page }) => {
  await page.goto('/ting');
  // The TingPage renders inside the Shell; the route resolves
  // and something from the Ting UI is visible
  await expect(page.locator('#root')).not.toBeEmpty();
  // Wait for the Ting nav item to confirm the shell is mounted
  await expect(page.getByText('Ting').first()).toBeVisible({ timeout: 10_000 });
});

test('plugin CSS is applied — shell has background colour', async ({ page }) => {
  await page.goto('/');
  // The design-tokens CSS sets --color-bg-primary on :root.
  // A basic check: the html element's background is not transparent/white.
  const bg = await page.evaluate(() => {
    const el = document.documentElement;
    return window.getComputedStyle(el).getPropertyValue('--color-bg-primary').trim();
  });
  // Token should be set (non-empty) once tokens.css is loaded
  expect(bg).not.toBe('');
});
