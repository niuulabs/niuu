import { test, expect } from '@playwright/test';

test('niuu boots and hello plugin renders', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('hello · smoke test')).toBeVisible();
  await expect(
    page.getByText('loading…').or(page.getByText('hello from the mock adapter')),
  ).toBeVisible();
  await expect(page.getByText('hello from the mock adapter')).toBeVisible({ timeout: 5000 });
});

test('deep-link /hello renders hello page directly', async ({ page }) => {
  await page.goto('/hello');
  await expect(page.getByText('hello · smoke test')).toBeVisible();
  await expect(page.getByText('hello from the mock adapter')).toBeVisible({ timeout: 5000 });
});

test('navigating away from /hello and back preserves the shell', async ({ page }) => {
  await page.goto('/hello');
  await expect(page.getByText('hello from the mock adapter')).toBeVisible({ timeout: 5000 });

  // Navigate to root (which redirects back to /hello, the only plugin)
  await page.goto('/');
  await expect(page.getByText('hello · smoke test')).toBeVisible();
  await expect(page.getByText('hello from the mock adapter')).toBeVisible({ timeout: 5000 });

  // Browser back button goes back to /hello cleanly
  await page.goBack();
  await expect(page.getByText('hello · smoke test')).toBeVisible();
});

test('unknown route shows not-found page', async ({ page }) => {
  await page.goto('/this-route-does-not-exist');
  await expect(page.getByText('404')).toBeVisible();
  await expect(page.getByText('Page not found.')).toBeVisible();
});
