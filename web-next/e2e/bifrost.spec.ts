import { expect, test } from '@playwright/test';

test('/bifrost renders the LLM control-plane catalog', async ({ page }) => {
  await page.goto('/bifrost');

  await expect(page.getByRole('heading', { name: 'Bifröst' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByText('Enabled models')).toBeVisible();
  await expect(page.getByText('Healthy providers')).toBeVisible();

  await page.goto('/bifrost/models');
  await expect(page.getByRole('table', { name: 'Model inventory' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'Model' })).toBeVisible();
  await expect(page.getByRole('columnheader', { name: 'Runtime' })).toBeVisible();
  await expect(page.getByText('Claude Sonnet 4.6')).toBeVisible();
  await expect(page.getByText('GPT-5.5')).toBeVisible();
  await expect(page.getByText('skuldClaude')).toBeVisible();
  await expect(page.getByText('skuldCodex')).toBeVisible();
  await expect(
    page.getByRole('main').getByRole('button', { name: /overview|models|providers|usage/i }),
  ).toHaveCount(0);

  await page.goto('/bifrost/providers');
  await expect(page.getByRole('heading', { name: 'anthropic' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'openai' })).toBeVisible();
});

test('launch wizard runtime selector reads from the Bifrost model catalog', async ({ page }) => {
  await page.goto('/volundr/forge');
  await page.getByRole('button', { name: /custom launch/i }).click();
  await expect(page.getByText('Launch pod')).toBeVisible({ timeout: 5000 });

  await page.getByTestId('wizard-next').click();
  await page.getByTestId('wizard-next').click();

  const modelOptions = await page
    .locator('select[aria-label="Select model"] option')
    .evaluateAll((options) => options.map((option) => option.textContent ?? ''));

  expect(modelOptions.some((option) => option.includes('Claude Sonnet 4.6'))).toBeTruthy();
  expect(modelOptions.some((option) => option.includes('GPT-5.5'))).toBeTruthy();
});
