import { test, expect } from '@playwright/test';

test('personal storage selects clusters, shows mounting, and confirms cleanup accessibly', async ({
  page,
}) => {
  let ready = false;
  const deleted: string[] = [];
  await page.route('**/api/v1/niuu/instances?**', (route) =>
    route.fulfill({
      json: ['valhalla', 'noatun'].map((id) => ({
        id,
        slug: id,
        name: id,
        kind: 'volundr',
        base_url: `https://${id}.test`,
        enabled: true,
        is_default: false,
        tags: [],
      })),
    }),
  );
  await page.route('**/api/v1/forge/storage/home?**', (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get('instance_id') === 'noatun')
      return route.fulfill({
        status: 501,
        json: { detail: 'Home file management is not enabled on this cluster' },
      });
    if (route.request().method() === 'DELETE') {
      deleted.push(url.searchParams.get('path') ?? '');
      return route.fulfill({ json: { status: 'ready' } });
    }
    if (!ready)
      return route.fulfill({ json: { status: 'starting', detail: 'Mounting your home storage' } });
    return route.fulfill({
      json: {
        status: 'ready',
        capacity_bytes: 68719476736,
        available_bytes: 67645734912,
        entries: [{ name: 'cache', path: 'tmp/cache', kind: 'directory', size: 0 }],
      },
    });
  });
  await page.goto('/settings/storage/home?config=/config.live.json');
  await expect(page.getByText('Mounting your home storage')).toBeVisible();
  ready = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByText('63.0 GiB available of 64.0 GiB')).toBeVisible();
  await page.getByRole('button', { name: 'Temporary files', exact: true }).click();
  await expect(page.getByText('Home/tmp/sessions', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Delete cache', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Delete home files' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(deleted).toEqual([]);
  await page.getByRole('button', { name: 'Delete cache', exact: true }).click();
  await page.getByRole('button', { name: 'Delete permanently' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(deleted).toEqual(['tmp/cache']);
  await page.getByRole('combobox', { name: 'Storage cluster' }).selectOption('noatun');
  await expect(page.getByRole('alert')).toContainText('not enabled on this cluster');
});
