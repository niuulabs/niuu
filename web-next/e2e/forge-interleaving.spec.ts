import { test, expect, type WebSocketRoute } from '@playwright/test';

// Every network operation is intercepted: this exercises the actual app, HTTP adapter, socket
// hook and transcript components without opening or changing any provider/user session.
test('native text/tool anchors survive live completion, snapshot repair and legacy fallback', async ({
  page,
}, testInfo) => {
  const origin = 'http://forge-interleaving.invalid';
  const session = {
    id: 'interleaving-browser-canary',
    name: 'Interleaving browser canary',
    status: 'running',
    model: 'gpt-6-astra',
    source: { type: 'git', repo: 'example.invalid/canary', branch: 'test' },
    chat_endpoint: 'ws://forge-interleaving.invalid/s/canary/session',
    created_at: '2026-09-08T00:00:00Z',
    last_active: '2026-09-08T21:00:00Z',
    activity_state: 'active',
  };
  await page.route('**/config.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.forge = { mode: 'http', baseUrl: `${origin}/api/v1/forge` };
    config.services.volundr = { mode: 'http', baseUrl: `${origin}/api/v1/volundr` };
    await route.fulfill({ json: config });
  });
  await page.route(`${origin}/**`, (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = path.includes('/features/modules')
      ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
      : path.endsWith('/api/conversation/history')
        ? { turns: [] }
        : path.endsWith(`/sessions/${session.id}`)
          ? session
          : path.endsWith('/sessions')
            ? [session]
            : path.includes('/workflow/gates')
              ? { gates: [] }
              : path.includes('/diff')
                ? { files: [] }
                : path.includes('/stats')
                  ? {}
                  : [];
    return route.fulfill({ json });
  });
  let socket: WebSocketRoute | undefined;
  await page.routeWebSocket('ws://forge-interleaving.invalid/**', (ws) => {
    if (ws.url().endsWith('/session')) socket = ws;
  });
  await page.goto(`/volundr/session/${session.id}`);
  await page.locator('#tab-chat').click();
  await page.getByRole('button', { name: 'Show tool calls and results' }).click();
  await expect.poll(() => Boolean(socket)).toBe(true);
  const send = (frame: object) => socket!.send(JSON.stringify(frame));
  const text = (id: string, value: string, phase: string) => ({
    type: 'text',
    id,
    text: value,
    phase,
    turn_id: 'native-turn',
    thread_id: 'native-thread',
    complete: true,
  });
  const a = text('a', 'Before tools café 東京', 'commentary');
  const b = text(
    'b',
    '**Final answer**\n\n| Check | Result |\n| --- | --- |\n| Replay | Preserved |',
    'final_answer',
  );
  const tool = {
    type: 'tool_use',
    id: 'command',
    name: 'Bash',
    input: { command: 'printf captured' },
  };
  send({ type: 'assistant', turn_id: 'native-turn', message: { content: [a] } });
  send({ type: 'content_block_start', turn_id: 'native-turn', content_block: tool });
  send({ type: 'assistant', turn_id: 'native-turn', message: { content: [b] } });
  const before = page.locator('[data-text-id="a"]');
  const final = page.locator('[data-text-id="b"]');
  await expect(before).toBeVisible();
  await expect(final.locator('table')).toBeVisible();
  await before.evaluate((node) => node.setAttribute('data-observed-anchor', 'retained'));
  send({
    type: 'content_block_start',
    turn_id: 'native-turn',
    content_block: { type: 'tool_result', tool_use_id: 'command', content: 'captured' },
  });
  send({ type: 'result', turn_id: 'native-turn' });
  await expect(before).toHaveAttribute('data-observed-anchor', 'retained');
  await expect(page.getByText('Before tools café 東京', { exact: true })).toHaveCount(1);
  await expect(page.getByText('Final answer', { exact: true })).toHaveCount(1);
  const order = await page
    .locator('.niuu-chat-assistant-content')
    .last()
    .evaluate((node) =>
      [...node.querySelectorAll('[data-text-id], [data-testid="tool-block"]')].map(
        (item) => item.getAttribute('data-text-id') ?? 'tool',
      ),
    );
  expect(order).toEqual(['a', 'tool', 'b']);
  send({
    type: 'conversation_history',
    projection_revision: 'repaired-2',
    turns: [
      {
        id: 'canonical',
        role: 'assistant',
        content: `${a.text}\n\n${b.text}`,
        parts: [a, tool, b, { type: 'tool_result', tool_use_id: 'command', content: 'captured' }],
        created_at: '2026-09-08T00:00:00Z',
      },
    ],
  });
  await expect(before).toHaveCount(1);
  await expect(before).toHaveAttribute('data-observed-anchor', 'retained');
  await expect(final).toHaveAttribute('data-text-phase', 'final_answer');
  await page.screenshot({ path: testInfo.outputPath('structured-replay.png'), fullPage: true });
  send({
    type: 'conversation_history',
    projection_revision: 'legacy',
    turns: [
      {
        id: 'canonical',
        role: 'assistant',
        content: 'Legacy prose remains readable once.',
        parts: [tool],
        created_at: '2026-09-08T00:00:00Z',
      },
    ],
  });
  await expect(page.getByText('Legacy prose remains readable once.', { exact: true })).toHaveCount(
    1,
  );
  await expect(page.getByTestId('tool-block')).toHaveCount(1);
});
