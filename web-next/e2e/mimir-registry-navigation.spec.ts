import { expect, test } from '@playwright/test';
test('registry keeps maintenance inside instance analytics', async ({ page }) => {
  await page.goto('/mimir/registry');
  const management = page.getByRole('navigation', { name: 'Knowledge instance management' });
  await expect(management.getByRole('button', { name: 'Wardens', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Create warden/i })).toHaveCount(0);
  await management.getByRole('button', { name: 'Analytics', exact: true }).click();
  await expect(page).toHaveURL(/\/mimir\/registry\/analytics$/);
  await page.goto('/mimir/ravns');
  await expect(management.getByRole('button', { name: 'Analytics', exact: true })).toHaveAttribute(
    'aria-current',
    'page',
  );
});
