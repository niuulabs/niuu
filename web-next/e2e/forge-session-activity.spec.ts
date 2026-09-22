import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://forge-activity.invalid';

test('four-host activity uses live state clocks, accepts remote SSE and ignores stale polling', async ({
  page,
}, info) => {
  const now = Date.now();
  const iso = (seconds: number) => new Date(now + seconds * 1000).toISOString();
  const names = ['Thor', 'Build', 'BuildBro', 'Build-Kit'];
  const sessions = names.map((name, i) => ({
    id: name,
    name: `${name} review`,
    instance_id: name,
    instance_name: name,
    status: 'running',
    activity_state: ['active', 'idle', 'active', undefined][i],
    activity_state_since: iso(-60),
    turn_started_at: i === 0 || i === 2 ? iso(-120) : null,
    model: i % 2 ? 'claude-opus-5' : 'gpt-6-astra',
    source: { type: 'local_mount', local_path: '/home/thor/repos/niuu' },
    last_active: iso(0),
  }));
  let sendIdle!: () => void;
  const idleAllowed = new Promise<void>((resolve) => {
    sendIdle = resolve;
  });
  const reads: string[] = [];
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
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
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/instances'))
      return route.fulfill({
        json: names.map((name) => ({
          id: name,
          name,
          enabled: true,
          kind: 'volundr',
          baseUrl: origin,
        })),
      });
    if (url.pathname.endsWith('/sessions/stream')) {
      expect(url.searchParams.get('all_instances')).toBe('true');
      await idleAllowed;
      return route.fulfill({
        contentType: 'text/event-stream',
        body:
          'event: session_activity\ndata: ' +
          JSON.stringify({
            session_id: 'BuildBro',
            instance_id: 'BuildBro',
            state: 'idle',
            activity_state_since: iso(0),
            turn_started_at: null,
          }) +
          '\n\n',
      });
    }
    if (url.pathname.endsWith('/sessions')) {
      const host = url.searchParams.get('instance_id');
      reads.push(host ?? 'aggregate');
      return route.fulfill({
        json:
          url.searchParams.get('status') === 'archived'
            ? []
            : sessions.filter((session) => session.instance_id === host),
      });
    }
    const detail = sessions.find((session) => url.pathname.endsWith('/sessions/' + session.id));
    if (detail) return route.fulfill({ json: detail });
    return route.fulfill({ json: url.pathname.endsWith('/stats') ? {} : [] });
  });
  await page.goto('/volundr/sessions');
  const bro = page.getByTestId('pod-entry-BuildBro');
  await expect(bro).toContainText('Working 2m');
  await expect(page.getByTestId('pod-entry-Build')).toContainText('Idle 1m');
  await expect(page.getByTestId('pod-entry-Build-Kit')).toContainText('Connected');
  const before = reads.length;
  sendIdle();
  await expect(bro).toContainText('Idle', { timeout: 1500 });
  expect(reads.length).toBe(before); // Changed by SSE before the next 5-second poll.
  await expect.poll(() => reads.length, { timeout: 8000 }).toBeGreaterThan(before);
  await expect(bro).toContainText('Idle'); // The old active REST row cannot resurrect a completed turn.
  expect(reads).not.toContain('aggregate');
  await page.screenshot({ path: info.outputPath('fleet-activity.png'), animations: 'disabled' });
});
