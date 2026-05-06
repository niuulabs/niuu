import { test, expect } from '@playwright/test';

test.describe('Form primitives — /hello/form-showcase', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/hello/form-showcase');
    await page.waitForSelector('h1', { state: 'visible' });
  });

  test('page renders the form showcase heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Form Showcase' })).toBeVisible();
  });

  test('form fields are present', async ({ page }) => {
    await expect(page.getByLabel(/Full name/i)).toBeVisible();
    await expect(page.getByLabel(/Email/i)).toBeVisible();
    await expect(page.getByLabel(/Bio/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /Submit/i })).toBeVisible();
  });

  test('submitting empty form shows ValidationSummary with all errors', async ({ page }) => {
    await page.getByRole('button', { name: /Submit/i }).click();

    const summary = page.getByRole('alert').first();
    await expect(summary).toBeVisible();
    await expect(summary).toContainText('Please fix the following issues');
    await expect(summary).toContainText('Full name is required');
    await expect(summary).toContainText('Email is required');
    await expect(summary).toContainText('Bio is required');
  });

  test('name field has aria-invalid after failed submit', async ({ page }) => {
    await page.getByRole('button', { name: /Submit/i }).click();
    const nameInput = page.getByLabel(/Full name/i);
    await expect(nameInput).toHaveAttribute('aria-invalid', 'true');
  });

  test('email field has aria-invalid after failed submit', async ({ page }) => {
    await page.getByRole('button', { name: /Submit/i }).click();
    const emailInput = page.getByLabel(/Email/i);
    await expect(emailInput).toHaveAttribute('aria-invalid', 'true');
  });

  test('clicking Full name error in summary focuses the name field', async ({ page }) => {
    await page.getByRole('button', { name: /Submit/i }).click();
    await expect(page.getByRole('alert').first()).toBeVisible();

    const summaryButton = page.getByRole('button', { name: /Full name.*required/i });
    await summaryButton.click();

    const nameInput = page.getByLabel(/Full name/i);
    await expect(nameInput).toBeFocused();
  });

  test('clicking Email error in summary focuses the email field', async ({ page }) => {
    await page.getByRole('button', { name: /Submit/i }).click();
    await expect(page.getByRole('alert').first()).toBeVisible();

    const summaryButton = page.getByRole('button', { name: /Email.*required/i });
    await summaryButton.click();

    const emailInput = page.getByLabel(/Email/i);
    await expect(emailInput).toBeFocused();
  });

  test('invalid email shows specific error message', async ({ page }) => {
    await page.getByLabel(/Full name/i).fill('Jane Doe');
    await page.getByLabel(/Email/i).fill('not-valid-email');
    await page.getByRole('button', { name: /Submit/i }).click();

    await expect(page.getByRole('alert').first()).toContainText('valid email');
  });

  test('filling all required fields and submitting shows success', async ({ page }) => {
    await page.getByLabel(/Full name/i).fill('Jane Doe');
    await page.getByLabel(/Email/i).fill('jane@example.com');
    await page.getByLabel(/Bio/i).fill('Software engineer');

    // Select role via Radix Select
    await page.getByRole('combobox').first().click();
    await page.getByRole('option', { name: 'Administrator' }).click();

    // Select team via Combobox
    const combobox = page.getByRole('combobox').last();
    await combobox.fill('Platform');
    await page.getByRole('option', { name: 'Platform' }).click();

    await page.getByRole('button', { name: /Submit/i }).click();

    await expect(page.getByText('Form submitted successfully')).toBeVisible();
  });

  test('keyboard: can tab through all form fields', async ({ page }) => {
    await page.getByLabel(/Full name/i).focus();

    const nameInput = page.getByLabel(/Full name/i);
    await expect(nameInput).toBeFocused();

    await page.keyboard.press('Tab');
    const emailInput = page.getByLabel(/Email/i);
    await expect(emailInput).toBeFocused();

    await page.keyboard.press('Tab');
    const bioInput = page.getByLabel(/Bio/i);
    await expect(bioInput).toBeFocused();
  });
});
