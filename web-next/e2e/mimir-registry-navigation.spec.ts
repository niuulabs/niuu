import { expect, test } from '@playwright/test';
test('registry has instances, health and analytics views', async ({ page }) => {
  await page.goto('/mimir/registry');
  const management = page.getByRole('navigation', { name: 'Knowledge instance management' });
  await expect(management.getByRole('button')).toHaveText(['Instances', 'Health', 'Analytics']);
  await management.getByRole('button', { name: 'Analytics', exact: true }).click();
  await expect(page).toHaveURL(/\/mimir\/registry\/analytics$/);
});
