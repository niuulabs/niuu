import { test, expect } from '@playwright/test';

test('niuu boots into the volundr front door', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();
  await expect(page).toHaveURL('http://localhost:5173/volundr');
});

test('deep-link /volundr renders the forge page directly', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();
});

test('navigating away from /volundr and back preserves the shell', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();

  await page.goto('/ting');
  await expect(page.getByRole('heading', { name: 'Ting' })).toBeVisible({ timeout: 5000 });

  await page.goBack();
  await expect(page).toHaveURL(/\/volundr$/);
  await expect(page.getByRole('heading', { name: 'Völundr' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('navigation', { name: 'Session list' })).toBeVisible();
});

test('unknown route shows not-found page', async ({ page }) => {
  await page.goto('/this-route-does-not-exist');
  await expect(page.getByText('404')).toBeVisible();
  await expect(page.getByText('Page not found.')).toBeVisible();
});
