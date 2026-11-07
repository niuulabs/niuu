import { test, expect } from '@playwright/test';

/** The Ravn plugin: the persona library at /ravn/personas, which /ravn lands on. */

test('the base route lands on the persona library', async ({ page }) => {
  await page.goto('/ravn');
  await expect(page).toHaveURL(/\/ravn\/personas/);
  await expect(page.getByTestId('persona-library')).toBeVisible({ timeout: 8_000 });
});

test('the persona library groups by family and reads as a sheet', async ({ page }) => {
  await page.goto('/ravn/personas');
  await expect(page.getByTestId('persona-library')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('persona-family-general')).toBeVisible();
  await expect(page.getByTestId('persona-sheet')).toBeVisible();
  await expect(page.getByTestId('persona-flow')).toBeVisible();

  await page.getByTestId('persona-view-yaml').click();
  await expect(page.getByTestId('persona-body-yaml')).toBeVisible();
  await page.getByTestId('persona-edit').click();
  await expect(page.getByTestId('persona-form')).toBeVisible();
});
