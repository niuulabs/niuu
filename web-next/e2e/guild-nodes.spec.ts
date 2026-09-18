import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://guild-fixture.invalid';
async function fixture(page: Page) {
  let rows = [
    {
      id: 'build-kit',
      kind: 'volundr',
      name: 'build-kit',
      slug: 'build-kit',
      baseUrl: 'https://build-kit.test',
      visibility: 'tenant',
      ownerId: null,
      tenantId: 'default',
      enabled: true,
      isDefault: false,
      config: {
        defaultFolder: '/home/build',
        transport: 'remote',
        credentialBinding: { scope: 'tenant', name: 'forge' },
      },
      tags: [],
      createdAt: '2026-09-18T10:00:00Z',
      updatedAt: '2026-09-18T10:00:00Z',
    },
  ];
  const controls = { fail: false, delay: 0 };
  const writes: Array<{ method: string; path: string; body: Record<string, unknown> }> = [];
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
        services: { ...config.services, niuu: { mode: 'http', baseUrl: `${origin}/api/v1/niuu` } },
      },
    }),
  );
  await page.route(`${origin}/**`, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (path.endsWith('/test'))
      return route.fulfill({ json: { ok: false, message: 'Connection timed out' } });
    if (method === 'GET') {
      if (path.endsWith('/instances')) {
        if (controls.delay) await new Promise((resolve) => setTimeout(resolve, controls.delay));
        return route.fulfill({ json: rows });
      }
      return route.fulfill({ json: path.includes('/credentials/') ? { credentials: [] } : [] });
    }
    const body = request.postDataJSON() ?? {};
    writes.push({ method, path, body });
    if (controls.fail)
      return route.fulfill({ status: 403, json: { detail: 'Registry access denied' } });
    if (method === 'DELETE') {
      rows = [];
      return route.fulfill({ status: 204 });
    }
    if (method === 'PATCH') {
      rows = [{ ...rows[0]!, ...body }];
      return route.fulfill({ json: rows[0] });
    }
    return route.fulfill({ status: 400, json: { detail: 'Unexpected fixture write' } });
  });
  return { controls, writes };
}

test('Guild edits a node, preserves its configuration and deletes its registration', async ({
  page,
}) => {
  const { writes, controls } = await fixture(page);
  controls.delay = 300;
  await page.goto('/guild');
  await expect(page.getByText('Loading registry…')).toBeVisible();
  const edit = page.getByRole('button', { name: 'Edit settings', exact: true });
  await edit.click();
  await page.getByLabel('Name', { exact: true }).fill('Build Kit');
  await page.getByLabel('Server URL', { exact: false }).fill('http://100.90.20.64:8080');
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: 'Build Kit', exact: true, level: 2 }),
  ).toBeVisible();
  expect(writes[0]).toMatchObject({
    method: 'PATCH',
    path: '/api/v1/niuu/instances/build-kit',
    body: {
      baseUrl: 'http://100.90.20.64:8080',
      config: {
        transport: 'remote',
        defaultFolder: '/home/build',
        credentialBinding: { scope: 'tenant', name: 'forge' },
      },
    },
  });
  expect(writes[0]!.body).not.toHaveProperty('visibility');
  await page.getByRole('button', { name: 'Delete node', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Its server and sessions keep running');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(writes).toHaveLength(1);
  await page.getByRole('button', { name: 'Delete node', exact: true }).click();
  await dialog.getByRole('button', { name: 'Delete node', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId('guild-instance-card-build-kit')).toHaveCount(0);
  expect(writes[1]!.method).toBe('DELETE');
});

test('Guild keeps edits on failure and supports keyboard cancel and retry', async ({ page }) => {
  const { controls, writes } = await fixture(page);
  await page.goto('/guild');
  const edit = page.getByRole('button', { name: 'Edit settings', exact: true });
  await edit.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Name', { exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(writes).toHaveLength(0);
  await edit.click();
  await page.getByLabel('Name', { exact: true }).fill('Updated kit');
  controls.fail = true;
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('alert')).toHaveText('Registry access denied');
  await expect(page.getByLabel('Name', { exact: true })).toHaveValue('Updated kit');
  controls.fail = false;
  await page.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('button', { name: 'Delete node', exact: true }).click();
  controls.fail = true;
  await page.getByRole('dialog').getByRole('button', { name: 'Delete node', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText('Registry access denied');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await expect(
    page.getByRole('heading', { name: 'Updated kit', exact: true, level: 2 }),
  ).toBeVisible();
});
