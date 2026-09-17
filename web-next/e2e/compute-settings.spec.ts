import { test, expect, type Page } from '@playwright/test';

const policy = { profile: 'standard', max_machines: 2, warm_min: 1, paused: false };
const schema = {
  title: 'Forge',
  scope: 'admin',
  sections: [
    {
      id: 'compute',
      label: 'Compute pool',
      path: '/admin/settings/compute',
      saveLabel: 'Save pool settings',
      fields: [
        { key: 'profile', label: 'Machine profile', type: 'text', value: 'standard' },
        { key: 'max_machines', label: 'Maximum machines', type: 'number', value: 2 },
        { key: 'warm_min', label: 'Ready spare machines', type: 'number', value: 1 },
        { key: 'paused', label: 'Pause new allocations', type: 'boolean', value: false },
      ],
    },
  ],
};

async function mockSettings(page: Page) {
  await page.route('**/api/v1/forge/settings', async (route) => {
    await route.fulfill({ json: schema });
  });
}

test('admin edits a provider-neutral pool in mounted settings', async ({ page }) => {
  await mockSettings(page);
  let saved: unknown;
  await page.route('**/api/v1/forge/admin/settings/compute', async (route) => {
    saved = route.request().postDataJSON();
    await route.fulfill({ json: saved });
  });
  await page.goto('/settings/volundr/compute?config=/config.live.json');
  await expect(page.getByRole('heading', { name: 'Compute pool', exact: true })).toBeVisible();
  await page.getByRole('spinbutton', { name: 'Ready spare machines', exact: true }).fill('2');
  await page.getByRole('checkbox', { name: 'Pause new allocations', exact: true }).check();
  await page.getByRole('button', { name: 'Save pool settings', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Saved.', { exact: true })).toBeVisible();
  expect(saved).toEqual({ ...policy, warm_min: 2, paused: true });
});

test('pool save failure leaves the form editable', async ({ page }) => {
  await mockSettings(page);
  await page.route('**/api/v1/forge/admin/settings/compute', async (route) => {
    await route.fulfill({
      status: 422,
      json: { detail: 'Warm minimum cannot exceed maximum machines' },
    });
  });
  await page.goto('/settings/volundr/compute?config=/config.live.json');
  await page.getByRole('spinbutton', { name: 'Ready spare machines', exact: true }).fill('3');
  await page.getByRole('button', { name: 'Save pool settings', exact: true }).click();
  await expect(page.getByTestId('settings-save-error')).toContainText('Warm minimum cannot exceed');
  await expect(page.getByRole('button', { name: 'Save pool settings', exact: true })).toBeEnabled();
});

test('controls stay absent while loading and when access is denied', async ({ page }) => {
  let resolve!: () => void;
  const gate = new Promise<void>((done) => {
    resolve = done;
  });
  await page.route('**/api/v1/forge/settings', async (route) => {
    await gate;
    await route.fulfill({ status: 403, json: { detail: 'Admin role required' } });
  });
  await page.goto('/settings/volundr/compute?config=/config.live.json');
  await expect(page.getByRole('button', { name: 'Save pool settings', exact: true })).toHaveCount(
    0,
  );
  const denied = page.waitForResponse(
    (response) => response.url().endsWith('/api/v1/forge/settings') && response.status() === 403,
  );
  resolve();
  await denied;
  await expect(page.getByRole('button', { name: 'Save pool settings', exact: true })).toHaveCount(
    0,
  );
});
