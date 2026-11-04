import { test, expect } from '@playwright/test';

test('niuu boots into the first rail plugin, home', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('home-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('rail-item-home')).toBeVisible();
  await expect(page).toHaveURL('http://localhost:5173/home');
});

test('deep-link /volundr renders the forge page directly', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByRole('heading', { name: 'Forge', exact: true, level: 1 })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByTestId('forge-page')).toBeVisible();
  await expect(page).toHaveURL('http://localhost:5173/volundr/forge');
});

test('navigating away from /volundr and back preserves the shell', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByRole('heading', { name: 'Forge', exact: true, level: 1 })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByTestId('forge-page')).toBeVisible();

  await page.goto('/ting');
  await expect(page.getByRole('heading', { name: 'Work', exact: true, level: 1 })).toBeVisible({
    timeout: 5000,
  });

  await page.goBack();
  await expect(page).toHaveURL(/\/volundr\/forge$/);
  await expect(page.getByRole('heading', { name: 'Forge', exact: true, level: 1 })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByTestId('forge-page')).toBeVisible();
});

test('unknown route shows not-found page', async ({ page }) => {
  await page.goto('/this-route-does-not-exist');
  await expect(page.getByText('404')).toBeVisible();
  await expect(page.getByText('Page not found.')).toBeVisible();
});
