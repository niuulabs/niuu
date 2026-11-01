import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://forge-overview.invalid';
async function fixture(page: Page) {
  const control = { recovered: false };
  const requests: URL[] = [];
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
        services: {
          ...config.services,
          niuu: { mode: 'http', baseUrl: origin + '/api/v1/niuu' },
          forge: { mode: 'http', baseUrl: origin + '/api/v1/forge', sessionListTimeoutMs: 2000 },
          volundr: { mode: 'http', baseUrl: origin + '/api/v1/volundr' },
        },
      },
    }),
  );
  await page.route(origin + '/**', async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: ['thor', 'offline'].map((id) => ({
          id,
          slug: id,
          name: id,
          kind: 'volundr',
          enabled: true,
          baseUrl: origin,
        })),
      });
    if (['/stats', '/cluster/resources', '/sessions'].some((suffix) => path.endsWith(suffix))) {
      requests.push(url);
      const id = url.searchParams.get('instance_id');
      if (!id)
        return route.fulfill({
          status: 500,
          json: { detail: 'Dashboard must not request aggregate inventory or metrics' },
        });
      if (id === 'offline' && !control.recovered) return;
      if (path.endsWith('/stats'))
        return route.fulfill({
          json: {
            instance_id: id,
            active_sessions: 1,
            tokens_today: 1200,
            cost_today: 1.25,
            sessions_today: 1,
            total_sessions: 2,
          },
        });
      if (path.endsWith('/cluster/resources'))
        return route.fulfill({
          json: {
            instances: [{ id, slug: id, name: id }],
            nodes: [
              {
                name: `${id}-node`,
                instance_id: id,
                allocatable: { cpu: '8', memory: '16Gi' },
                allocated: { cpu: '2', memory: '2Gi' },
              },
            ],
          },
        });
      return route.fulfill({
        json: url.searchParams.has('status')
          ? []
          : [
              {
                id,
                name: `${id} session`,
                instance_id: id,
                instance_name: id,
                status: 'running',
                model: 'gpt-6-astra',
                source: { type: 'local_mount', local_path: '/workspace' },
                created_at: '2026-09-18T00:00:00Z',
              },
            ],
      });
    }
    return route.fulfill({ json: [] });
  });
  return { requests, control };
}

for (const phone of [false, true])
  test(`Forge renders healthy hosts immediately and recovers offline metrics (${phone ? 'phone' : 'desktop'})`, async ({
    page,
  }, info) => {
    if (phone) await page.setViewportSize({ width: 390, height: 844 });
    const { requests, control } = await fixture(page);
    await page.goto('/volundr/forge');
    await expect(page.getByTestId('quick-launch-panel')).toBeVisible();
    await expect(page.getByTestId('inflight-panel')).toContainText('thor session');
    await expect(page.getByTestId('cluster-load-row')).toContainText('thor');
    await expect(page.getByLabel('Forge metrics')).toContainText('$1.25');
    await expect(page.getByLabel('Forge connections')).toContainText('offline metrics: loading');
    await expect(page.getByLabel('Forge connections')).toContainText(
      'offline resources: unavailable',
    );
    await expect(page.getByLabel('Forge connections')).toContainText('Metrics cover loaded hosts');
    await expect(page.getByTestId('cluster-load-row')).toHaveCount(1);
    control.recovered = true;
    await page.getByRole('button', { name: 'Retry Forge connections' }).click();
    await expect(page.getByTestId('cluster-load-row')).toHaveCount(2);
    await expect(page.getByLabel('Forge metrics')).toContainText('$2.50');
    await expect(page.getByLabel('Forge connections')).toHaveCount(0);
    expect(requests.every((url) => url.searchParams.has('instance_id'))).toBe(true);
    await page.screenshot({ path: info.outputPath('forge-ready.png') });
  });
