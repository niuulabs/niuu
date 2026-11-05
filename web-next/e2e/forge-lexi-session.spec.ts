import { readFileSync } from 'node:fs';
import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';

// The Lexi-style Forge session UI, end to end against intercepted Forge and broker traffic:
// the session list (rows, unread, unread stream) and a live conversation (tool burst row and
// its calls sheet, plan bar, question panel, a message queued behind a running turn).
// Nothing here reaches a real host.
const origin = 'http://forge-lexi.invalid';
const now = Date.now();
const iso = (seconds: number) => new Date(now + seconds * 1000).toISOString();

const unreadSession = {
  id: 'gates',
  name: 'plan-review-gates',
  status: 'running',
  activity_state: 'active',
  activity_state_since: iso(-120),
  turn_started_at: iso(-120),
  model: 'claude-fable-5-1',
  instance_id: 'valhalla',
  instance_name: 'Valhalla',
  source: { type: 'git', repo: 'https://github.com/niuulabs/niuu.git', branch: 'dev' },
  chat_endpoint: 'ws://forge-lexi.invalid/s/gates/session',
  created_at: iso(-600),
  last_active: iso(-5),
  short_description: 'Running the workflow gate tests',
  read_state: {
    latest_output_seq: 14,
    latest_output_turn_id: 'reply-1',
    latest_output_at: iso(-180),
    read_through_seq: 9,
    manually_unread: false,
    revision: 3,
    is_unread: true,
  },
};
const readSession = {
  ...unreadSession,
  id: 'catalog',
  name: 'bifrost-catalog',
  activity_state: 'idle',
  activity_state_since: iso(-3600),
  turn_started_at: null,
  model: 'gpt-6-sol',
  chat_endpoint: 'ws://forge-lexi.invalid/s/catalog/session',
  short_description: 'Removing retired models from the catalog',
  read_state: { ...unreadSession.read_state, read_through_seq: 14, is_unread: false },
};
const sessions = [unreadSession, readSession];

const history = [
  {
    id: 'u1',
    role: 'user',
    content: 'Fix the human gate and run the tests.',
    created_at: iso(-130),
  },
  {
    id: 'a1',
    role: 'assistant',
    content: 'Checking the gate resolver.',
    created_at: iso(-125),
    metadata: { usage: { 'claude-fable-5-1': { inputTokens: 10, outputTokens: 20 } } },
    parts: [
      { type: 'text', id: 't1', text: 'Checking the gate resolver.', complete: true },
      { type: 'tool_use', id: 'c1', name: 'Bash', input: { command: 'git status --short' } },
      { type: 'tool_result', tool_use_id: 'c1', content: ' M src/ting/gates.py' },
      {
        type: 'tool_use',
        id: 'r1',
        name: 'Read',
        input: { file_path: 'src/ting/domain/services/gates.py' },
      },
      { type: 'tool_result', tool_use_id: 'r1', content: '412 lines' },
      { type: 'text', id: 't2', text: 'The resolver skips the human check.', complete: true },
    ],
  },
];

async function intercept(page: Page, onReadState: (body: unknown) => void = () => {}) {
  await page.route(/\/config(?:\.live)?\.json$/, async (route) => {
    const config = JSON.parse(
      readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
    );
    config.services.forge = { mode: 'http', baseUrl: `${origin}/api/v1/forge` };
    config.services.volundr = { mode: 'http', baseUrl: `${origin}/api/v1/volundr` };
    config.services.niuu = { mode: 'http', baseUrl: `${origin}/api/v1/niuu` };
    config.services.setup = { mode: 'http', baseUrl: `${origin}/api/v1/setup` };
    await route.fulfill({ json: config });
  });
  await page.route(`${origin}/**`, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/api/v1/setup')
      return route.fulfill({ json: { enabled: false, completed: true } });
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: [
          { id: 'valhalla', name: 'Valhalla', kind: 'volundr', enabled: true, baseUrl: origin },
        ],
      });
    if (path.endsWith('/read-state') && request.method() === 'PATCH') {
      onReadState(request.postDataJSON());
      return route.fulfill({
        json: { ...unreadSession.read_state, read_through_seq: 14, revision: 4, is_unread: false },
      });
    }
    if (path.endsWith('/conversation/turns/reply-1'))
      return route.fulfill({
        json: {
          turn: {
            id: 'reply-1',
            role: 'assistant',
            content: 'Fixed. Agents can no longer approve a human gate.',
            created_at: iso(-180),
          },
          index: 3,
          total_turns: 4,
        },
      });
    if (path.endsWith('/conversation') || path.endsWith('/api/conversation/history'))
      return route.fulfill({ json: { turns: history, projection_revision: 'r1' } });
    if (path.includes('/features/modules'))
      return route.fulfill({
        json: [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }],
      });
    const detail = sessions.find((session) => path.endsWith(`/sessions/${session.id}`));
    if (detail) return route.fulfill({ json: detail });
    if (path.endsWith('/sessions'))
      return route.fulfill({
        json: new URL(request.url()).searchParams.get('status') === 'archived' ? [] : sessions,
      });
    if (path.includes('/workflow/gates')) return route.fulfill({ json: { gates: [] } });
    if (path.includes('/stats')) return route.fulfill({ json: {} });
    return route.fulfill({ json: [] });
  });
}

test('session list shows Lexi rows, the unread filter and the unread stream', async ({
  page,
}, info) => {
  const readStateChanges: unknown[] = [];
  await intercept(page, (body) => readStateChanges.push(body));
  await page.goto('/volundr/sessions');

  const row = page.getByTestId('pod-entry-gates');
  await expect(row).toContainText('plan-review-gates');
  await expect(row).toContainText('Running the workflow gate tests');
  await expect(row).toContainText('Claude Fable 5.1');
  await expect(row).toContainText('running 2m');
  await expect(page.getByTestId('pod-entry-catalog')).toContainText('GPT-6 Sol');

  const unread = page.getByTestId('session-filter-unread');
  await expect(unread).toContainText('1');
  await unread.click();
  await expect(page.getByTestId('unread-stream-summary')).toContainText(
    '1 session has new replies',
  );
  await expect(page.getByTestId('unread-reply-gates')).toContainText(
    'Agents can no longer approve a human gate',
  );
  await page.screenshot({ path: info.outputPath('unread-stream.png'), animations: 'disabled' });

  await page.getByRole('button', { name: 'Mark plan-review-gates read' }).click();
  await expect
    .poll(() => readStateChanges)
    .toEqual([{ state: 'read', expected_revision: 3, through_seq: 14 }]);
});

test('a live conversation shows tool runs, the plan, a question and a queued message', async ({
  page,
}, info) => {
  await intercept(page);
  let socket: WebSocketRoute | undefined;
  const fromBrowser: Array<Record<string, unknown>> = [];
  await page.routeWebSocket('ws://forge-lexi.invalid/**', (ws) => {
    if (!new URL(ws.url()).pathname.endsWith('/session')) return;
    socket = ws;
    ws.onMessage((raw) => fromBrowser.push(JSON.parse(String(raw))));
  });
  await page.goto('/volundr/session/gates');
  await page.locator('#tab-chat').click();
  await expect.poll(() => Boolean(socket)).toBe(true);
  const send = (frame: object) => socket!.send(JSON.stringify(frame));
  send({ type: 'capabilities', interrupt: true, steer: true, steering_mode: 'native' });

  // One row for the burst of tool calls, with its calls in a sheet.
  const run = page.getByRole('button', { name: /^Open 2 tool calls: 1 command · 1 file read/ });
  await expect(run).toBeVisible();
  await expect(page.getByTestId('model-speaker').first()).toHaveText('Claude Fable 5.1');
  await run.click();
  const sheet = page.getByRole('dialog');
  await expect(sheet).toContainText('git status --short');
  await expect(sheet).toContainText('src/ting/domain/services/gates.py');
  await page.keyboard.press('Escape');
  await expect(sheet).toBeHidden();

  // The plan bar follows the broker's plan frame.
  send({
    type: 'plan',
    tasks: [
      { content: 'Find the bypass', status: 'completed' },
      { content: 'Run the tests', status: 'in_progress', activeForm: 'Running the tests' },
      { content: 'Open a PR', status: 'pending' },
    ],
    counts: { total: 3, completed: 1, in_progress: 1, pending: 1 },
  });
  await expect(page.locator('.niuu-plan-dock__summary')).toContainText('1/3');
  await expect(page.locator('.niuu-plan-dock__summary')).toContainText('Running the tests');

  // A message sent while the agent works stays queued until the broker says it was taken.
  const composer = page.getByRole('textbox', { name: 'Message' });
  await expect(composer).toHaveAttribute('placeholder', 'Steer this session…');
  // The key hints wait until the person types, then say Enter queues while the agent works.
  await expect(page.getByText('queue for after this step')).toBeHidden();
  await composer.fill('Also link NIU-1342 in the PR body.');
  await expect(page.getByText('queue for after this step')).toBeVisible();
  await composer.press('Enter');
  await expect.poll(() => fromBrowser.find((frame) => frame.type === 'user')).toBeTruthy();
  const sent = fromBrowser.find((frame) => frame.type === 'user')!;
  send({
    type: 'user_confirmed',
    id: 'm-queued',
    request_id: sent.request_id,
    content: 'Also link NIU-1342 in the PR body.',
    steering_state: 'pending',
    created_at: new Date().toISOString(),
  });
  const queued = page.locator('[data-testid="user-message"][data-delivery="queued"]');
  await expect(queued).toContainText('Also link NIU-1342');
  await expect(queued).toContainText('Queued');
  send({ type: 'user_active', id: 'm-queued', request_id: sent.request_id });
  await expect(page.locator('[data-testid="user-message"][data-delivery="active"]')).toContainText(
    'Also link NIU-1342',
  );

  // The agent asks; the answer goes back as ask_user_answer.
  send({
    type: 'ask_user_question',
    request_id: 'q-1',
    questions: [
      {
        question: 'Which migration set should the column go in?',
        options: [
          { label: 'Ting (Recommended)', description: 'run_steps is a Ting table.' },
          { label: 'Volundr', description: 'Next to the run page queries.' },
        ],
      },
    ],
  });
  const band = page.getByRole('region', { name: 'The agent needs your input' });
  await expect(band).toContainText('Which migration set should the column go in?');
  await expect(band).toContainText('Recommended');
  await page.screenshot({ path: info.outputPath('live-session.png'), animations: 'disabled' });
  await band.getByRole('button', { name: /^Ting/ }).click();
  await band.getByRole('button', { name: 'Submit' }).click();
  await expect
    .poll(() => fromBrowser.find((frame) => frame.type === 'ask_user_answer'))
    .toMatchObject({ request_id: 'q-1' });
});
