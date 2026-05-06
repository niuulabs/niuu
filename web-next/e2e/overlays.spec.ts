import { test, expect } from '@playwright/test';

test.describe('Dialog', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/overlays');
  });

  test('opens when trigger is clicked', async ({ page }) => {
    await page.getByTestId('dialog-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByText('Confirm action')).toBeVisible();
    await expect(page.getByTestId('dialog-body')).toBeVisible();
  });

  test('closes with Escape key', async ({ page }) => {
    await page.getByTestId('dialog-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('closes with the close (✕) button', async ({ page }) => {
    await page.getByTestId('dialog-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: 'Close' }).click();
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('closes with the Cancel button inside the dialog', async ({ page }) => {
    await page.getByTestId('dialog-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByTestId('dialog-cancel').click();
    await expect(page.getByRole('dialog')).toBeHidden();
  });

  test('focus trap: Tab key stays within dialog', async ({ page }) => {
    await page.getByTestId('dialog-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();

    // Tab through all focusable elements — focus should cycle within the dialog.
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
      const focused = await page.evaluate(() => document.activeElement?.closest('[role="dialog"]'));
      expect(focused).not.toBeNull();
    }
  });

  test('focus returns to trigger after dialog closes', async ({ page }) => {
    const trigger = page.getByTestId('dialog-trigger');
    await trigger.click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
    await expect(trigger).toBeFocused();
  });
});

test.describe('Drawer', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/overlays');
  });

  test('opens when trigger is clicked', async ({ page }) => {
    await page.getByTestId('drawer-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.getByText('Side panel')).toBeVisible();
  });

  test('closes with Escape key', async ({ page }) => {
    await page.getByTestId('drawer-trigger').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toBeHidden();
  });
});

test.describe('Popover', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/overlays');
  });

  test('opens when trigger is clicked', async ({ page }) => {
    await page.getByTestId('popover-trigger').click();
    await expect(page.getByTestId('popover-body')).toBeVisible();
  });

  test('closes on Escape key', async ({ page }) => {
    await page.getByTestId('popover-trigger').click();
    await expect(page.getByTestId('popover-body')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('popover-body')).toBeHidden();
  });
});
