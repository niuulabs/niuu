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
  await expect(page.getByTestId('persona-editor')).toBeVisible();
  await expect(page.getByTestId('persona-step-body-name')).toBeVisible();
});

test('a new persona is built step by step beside its YAML, then saved', async ({ page }) => {
  await page.goto('/ravn/personas');
  await expect(page.getByTestId('persona-library')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('persona-new').click();
  await expect(page.getByTestId('persona-step-body-start')).toBeVisible();
  await page.getByTestId('persona-wizard-next').click();

  await page.getByTestId('persona-editor-name').fill('night-watch');
  await page.getByTestId('persona-editor-description').fill('Watches the nightly build.');
  await expect(page.getByTestId('persona-editor-yaml-preview')).toContainText('name: night-watch');
  await page.getByTestId('persona-step-finishes').click();
  await page.getByTestId('persona-outcome-when-done').fill('The nightly build is green.');
  await page.getByTestId('persona-step-instructions').click();
  await page.getByTestId('persona-editor-instructions').fill('Read the nightly build log.');

  await page.getByTestId('persona-editor-yaml').click();
  await expect(page.getByTestId('persona-editor-source')).toHaveValue(/night_watch\.done/);
  await expect(page.getByTestId('persona-editor-status')).toHaveText('Valid persona');
  await page.getByTestId('persona-editor-save').click();

  await expect(page.getByRole('heading', { level: 1, name: 'night-watch' })).toBeVisible();
  await expect(page.getByTestId('persona-row-night-watch')).toBeVisible();
});

test('the wizard keys through its steps and Cancel leaves it', async ({ page }) => {
  await page.goto('/ravn/personas');
  await expect(page.getByTestId('persona-library')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('persona-new').click();
  await page.getByTestId('persona-wizard-next').focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('persona-step-body-name')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(page.getByTestId('persona-sheet')).toBeVisible();
});
