import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://runtime-version.invalid';

test('runtime inspection shows loading, version mismatch and retry without restarting', async ({
  page,
}) => {
  const session = {
    id: 'runtime-review',
    name: 'Runtime review',
    instance_id: 'build',
    status: 'running',
    activity_state: 'idle',
    model: 'gpt-6-astra',
    source: { type: 'local_mount', local_path: '/home/horde' },
    created_at: '2026-09-22T00:00:00Z',
  };
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = false;
  const mutations: string[] = [];
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        services: {
          ...config.services,
          niuu: { mode: 'http', baseUrl: origin + '/api/v1/niuu' },
          forge: { mode: 'http', baseUrl: origin + '/api/v1/forge' },
          volundr: { mode: 'http', baseUrl: origin + '/api/v1/volundr' },
        },
      },
    }),
  );
  await page.route(origin + '/**', async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    if (route.request().method() !== 'GET') mutations.push(path);
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: [{ id: 'build', name: 'Build', kind: 'volundr', enabled: true, baseUrl: origin }],
      });
    if (path.endsWith('/sessions'))
      return route.fulfill({ json: url.searchParams.has('status') ? [] : [session] });
    if (path.endsWith('/sessions/runtime-review')) return route.fulfill({ json: session });
    if (path.endsWith('/runtime-version')) {
      expect(url.searchParams.get('instance_id')).toBe('build');
      await ready;
      return route.fulfill(
        fail
          ? { status: 503, json: { detail: 'unavailable' } }
          : {
              json: {
                state: 'different',
                current: { revision: 'old12345' },
                available: { revision: 'new12345' },
              },
            },
      );
    }
    if (path.endsWith('/conversation'))
      return route.fulfill({ json: { turns: [], total_turns: 0 } });
    return route.fulfill({
      json: path.includes('/workflow/gates') ? { gates: [] } : path.includes('/stats') ? {} : [],
    });
  });
  await page.goto('/volundr/sessions');
  await page.getByTestId('pod-entry-runtime-review').click();
  await page.getByRole('button', { name: 'Runtime version', exact: true }).click();
  await expect(page.getByText('Checking runtime version…')).toBeVisible();
  release();
  await expect(page.getByText('old12345')).toBeVisible();
  await expect(page.getByText('new12345')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByText('Skuld runtime', { exact: true })).not.toBeVisible();
  await page.getByRole('button', { name: 'Runtime update available' }).focus();
  await page.keyboard.press('Enter');
  fail = true;
  await page.getByRole('button', { name: 'Check again' }).click();
  await expect(page.getByText('This Forge could not report its runtime version.')).toBeVisible();
  expect(mutations.filter((path) => /\/(start|stop|resume)$/.test(path))).toEqual([]);
});
