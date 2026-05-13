import { expect, test } from '@playwright/test';

test('/mimir/ravns create form shows real checkboxes, persona select, and no legacy auth/profile fields', async ({
  page,
}) => {
  await page.goto('/mimir/ravns');
  await page.getByRole('button', { name: /create warden/i }).click();
  await page.getByLabel(/dream cycle cron/i).waitFor({ timeout: 5000 });

  const personaOptions = await page
    .locator('select[aria-label="Persona"] option')
    .evaluateAll((options) =>
      options.map((option) => ({
        value: option.getAttribute('value') ?? '',
        text: option.textContent ?? '',
      })),
    );

  const snapshot = await page.evaluate(() => {
    const targets = ['Read mount local', 'Write mount local', 'Enable console', 'Autostart'];

    return targets.map((target) => {
      const input = document.querySelector<HTMLInputElement>(
        `input[type="checkbox"][aria-label="${target}"]`,
      );
      if (!input) return { target, found: false };

      const style = window.getComputedStyle(input);
      const rect = input.getBoundingClientRect();
      return {
        target,
        found: true,
        width: rect.width,
        height: rect.height,
        opacity: style.opacity,
        display: style.display,
      };
    });
  });

  await expect(page.getByText('Auth note', { exact: false })).toHaveCount(0);
  await expect(page.getByLabel('Profile')).toHaveCount(0);
  await expect(page.locator('select[aria-label="Persona"]')).toHaveValue('mimir-warden');
  await expect(page.locator('select[aria-label="Model"] option')).not.toHaveCount(0);
  await expect(page.locator('select[aria-label="Model"]')).toContainText('Claude Sonnet 4.6');
  await expect(page.locator('select[aria-label="Model"]')).toContainText('GPT-5.5');
  await expect(page.getByText(/long-lived mimir warden/i)).toBeVisible();
  expect(personaOptions.some((option) => option.value === 'mimir-warden')).toBeTruthy();
  expect(personaOptions.length).toBeGreaterThan(0);

  for (const item of snapshot) {
    expect(item.found).toBeTruthy();
    if (item.found) {
      expect(item.width).toBeGreaterThan(8);
      expect(item.height).toBeGreaterThan(8);
      expect(item.display).not.toBe('none');
      expect(item.opacity).not.toBe('0');
    }
  }
});
