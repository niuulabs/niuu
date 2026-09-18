import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://session-loading.invalid';
async function fixture(page: Page) {
  const requests: URL[] = [];
  const control = { recovered: false };
  const session = (id: string, instance: string) => ({
    id,
    name: `${instance} session`,
    instance_id: instance,
    instance_name: instance,
    status: 'running',
    activity_state: 'idle',
    model: 'gpt-6-astra',
    source: { type: 'local_mount', local_path: '/workspace' },
    chat_endpoint: `ws://session-loading.invalid/s/${id}/session`,
    created_at: '2026-09-18T00:00:00Z',
  });
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
        services: {
          ...config.services,
          niuu: { mode: 'http', baseUrl: origin + '/api/v1/niuu' },
          forge: { mode: 'http', baseUrl: origin + '/api/v1/forge', sessionListTimeoutMs: 1000 },
          volundr: { mode: 'http', baseUrl: origin + '/api/v1/volundr' },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://session-loading.invalid/**', (socket) =>
    socket.onMessage(() => {}),
  );
  await page.route(origin + '/**', async (route) => {
    const url = new URL(route.request().url()),
      path = url.pathname;
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: ['thor', 'offline'].map((id) => ({
          id,
          name: id,
          kind: 'volundr',
          enabled: true,
          baseUrl: origin,
        })),
      });
    if (path.endsWith('/sessions')) {
      requests.push(url);
      const id = url.searchParams.get('instance_id');
      if (!id)
        return route.fulfill({
          status: 500,
          json: { detail: 'Aggregate listing must not block this view' },
        });
      if (id === 'offline' && !control.recovered) return; // Fixture keeps the read pending until the client deadline.
      if (url.searchParams.get('status') === 'archived') return route.fulfill({ json: [] });
      return route.fulfill({ json: [session(id, id)] });
    }
    if (path.endsWith('/sessions/thor')) return route.fulfill({ json: session('thor', 'thor') });
    if (path.endsWith('/sessions/offline'))
      return route.fulfill({ json: session('offline', 'offline') });
    if (path.endsWith('/conversation'))
      return route.fulfill({
        json: {
          turns: [
            {
              id: 'one',
              role: 'assistant',
              content: 'The healthy session is ready.',
              created_at: '2026-09-18T00:00:00Z',
            },
          ],
          total_turns: 1,
          window_offset: 0,
        },
      });
    return route.fulfill({
      json: path.includes('/workflow/gates')
        ? { gates: [] }
        : path.includes('/features/modules')
          ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
          : path.includes('/stats')
            ? {}
            : [],
    });
  });
  return { requests, control };
}
for (const phone of [false, true])
  test(`healthy sessions appear before a stuck host; timeout and retry stay visible (${phone ? 'phone' : 'desktop'})`, async ({
    page,
  }, info) => {
    if (phone) await page.setViewportSize({ width: 390, height: 844 });
    const { requests, control } = await fixture(page);
    await page.goto('/volundr/sessions');
    const row = page.getByTestId('pod-entry-thor');
    await expect(row).toBeVisible();
    await expect(page.getByLabel('Forge connections')).toContainText('offline: loading');
    await expect(page.getByLabel('Forge connections')).toContainText('offline: unavailable');
    await expect(row).toBeVisible();
    await expect(page.getByText('Failed to load sessions', { exact: true })).toHaveCount(0);
    control.recovered = true;
    const retry = page.getByRole('button', { name: 'Retry Forge connections' });
    await retry.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('pod-entry-offline')).toBeVisible();
    await expect(page.getByLabel('Forge connections')).toHaveCount(0);
    expect(requests.every((url) => url.searchParams.has('instance_id'))).toBe(true);
    await page.screenshot({
      path: info.outputPath('sessions-recovered.png'),
      animations: 'disabled',
    });
  });
