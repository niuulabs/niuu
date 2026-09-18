import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://history-fixture.invalid';
async function fixture(page: Page, version = 2) {
  const rows = Array.from({ length: 125 }, (_, n) => ({
    id: `message-${n}`,
    role: 'assistant',
    content: `Message ${n}: ${'This is a paragraph in a long conversation. '.repeat(7)}`,
    created_at: new Date(1700000000000 + n * 1000).toISOString(),
  }));
  const requests: URL[] = [];
  const writes: string[] = [];
  const sockets: WebSocketRoute[] = [];
  const controls = { failOlder: false };
  const session = {
    id: 'review',
    name: 'Paged review',
    status: 'running',
    activity_state: 'idle',
    model: 'gpt-6-astra',
    source: { type: 'local_mount', local_path: '/workspace' },
    chat_endpoint: `${origin.replace('http:', 'ws:')}/forge-host/build/s/review/session`,
    created_at: '2026-09-18T00:00:00Z',
  };
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        services: {
          ...config.services,
          forge: { mode: 'http', baseUrl: `${origin}/api/v1/forge` },
          volundr: { mode: 'http', baseUrl: `${origin}/api/v1/volundr` },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://history-fixture.invalid/**', (socket) => {
    sockets.push(socket);
    socket.onMessage((raw) => {
      const event = JSON.parse(String(raw));
      if (event.type !== 'set_internal_visibility') writes.push(event.type);
    });
  });
  await page.route(`${origin}/**`, async (route) => {
    const u = new URL(route.request().url());
    const path = u.pathname;
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: [{ id: 'fixture', name: 'Fixture', kind: 'volundr', enabled: true, baseUrl: origin }],
      });
    if (route.request().method() !== 'GET') {
      writes.push(route.request().method());
      return route.abort();
    }
    if (path.endsWith('/conversation')) {
      requests.push(u);
      const older = u.searchParams.has('cursor') || u.searchParams.has('before');
      await new Promise((resolve) => setTimeout(resolve, older ? 180 : 60));
      if (older && controls.failOlder)
        return route.fulfill({ status: 503, json: { detail: 'Fixture unavailable' } });
      const end =
        version === 2
          ? Number(u.searchParams.get('cursor') ?? rows.length)
          : rows.length - Number(u.searchParams.get('before') ?? 0);
      const start = Math.max(0, end - Math.min(15, Number(u.searchParams.get('limit'))));
      return route.fulfill({
        json: {
          turns: rows.slice(start, end),
          total_turns: rows.length,
          window_offset: start,
          projection_revision: 'same',
          ...(version === 2
            ? { history_protocol: 2, window_end: end, older_cursor: start ? String(start) : null }
            : {}),
        },
      });
    }
    const json = path.endsWith('/sessions/review')
      ? session
      : path.endsWith('/sessions')
        ? [session]
        : path.includes('/workflow/gates')
          ? { gates: [] }
          : path.includes('/features/modules')
            ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
            : path.includes('/stats')
              ? {}
              : [];
    return route.fulfill({ json });
  });
  return { requests, writes, sockets, controls, rows };
}
for (const version of [1, 2]) {
  test(`protocol ${version}: 50 recent messages, upward paging, anchored viewport, final page`, async ({
    page,
  }, info) => {
    const { requests, writes } = await fixture(page, version);
    await page.goto('/volundr/sessions/review');
    const scroll = page.locator('.niuu-chat-messages-container');
    const messages = page.locator('[data-history-id]');
    await expect(messages).toHaveCount(50);
    await expect(messages.last()).toContainText('Message 124:');
    await expect
      .poll(() => scroll.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight))
      .toBeLessThan(2);
    await scroll.evaluate((el) => {
      el.scrollTop = 100;
      el.dispatchEvent(new Event('scroll'));
    });
    const anchor = await scroll.evaluate((el) => {
      const top = el.getBoundingClientRect().top;
      const row = Array.from(el.querySelectorAll<HTMLElement>('[data-history-id]')).find(
        (item) => item.getBoundingClientRect().bottom > top,
      )!;
      return { id: row.dataset.historyId, y: row.getBoundingClientRect().top };
    });
    await expect(messages).toHaveCount(100);
    const held = page.locator(`[data-history-id="${anchor.id}"]`);
    await expect
      .poll(async () => Math.abs((await held.boundingBox())!.y - anchor.y))
      .toBeLessThan(3);
    await page.screenshot({ path: info.outputPath(`paging-v${version}.png`) });
    await scroll.evaluate((el) => {
      el.scrollTop = 0;
      el.dispatchEvent(new Event('scroll'));
    });
    await expect(messages).toHaveCount(125);
    await expect(page.getByRole('button', { name: 'Load earlier messages' })).toHaveCount(0);
    expect(requests.every((u) => Number(u.searchParams.get('max_bytes')) === 262144)).toBe(true);
    expect(requests.every((u) => u.pathname.startsWith('/forge-host/build/'))).toBe(true);
    expect(writes).toEqual([]);
  });
}
test('failed older page stays retryable without replacing the transcript', async ({ page }) => {
  const { controls } = await fixture(page);
  await page.goto('/volundr/sessions/review');
  await expect(page.locator('[data-history-id]')).toHaveCount(50);
  controls.failOlder = true;
  await page.locator('.niuu-chat-messages-container').evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event('scroll'));
  });
  await expect(page.getByRole('button', { name: 'Retry earlier messages' })).toBeVisible();
  await expect(page.locator('[data-history-id]')).toHaveCount(50);
  controls.failOlder = false;
  await page.getByRole('button', { name: 'Retry earlier messages' }).click();
  await expect(page.locator('[data-history-id]')).toHaveCount(100);
});
test('replay recovery stays out of messages and refreshes history without sending input', async ({
  page,
}) => {
  const { sockets, rows, writes } = await fixture(page);
  await page.goto('/volundr/sessions/review');
  await expect(page.locator('[data-history-id]')).toHaveCount(50);
  rows.push({ ...rows[0]!, id: 'after-reconnect', content: 'Latest message after recovery' });
  sockets.at(-1)!.send(
    JSON.stringify({
      type: 'error',
      code: 'conversation_history_too_large',
      content:
        'Conversation history is too large for WebSocket replay. Reload history through REST.',
    }),
  );
  await expect(page.getByText('Latest message after recovery')).toBeVisible();
  await expect(
    page.getByText(
      'Conversation history is too large for WebSocket replay. Reload history through REST.',
      { exact: true },
    ),
  ).toHaveCount(0);
  expect(writes).toEqual([]);
});
test('phone supports keyboard/manual paging', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  await expect(page.locator('[data-history-id]')).toHaveCount(50);
  const older = page.getByRole('button', { name: 'Load earlier messages' });
  await older.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[data-history-id]')).toHaveCount(100);
});
