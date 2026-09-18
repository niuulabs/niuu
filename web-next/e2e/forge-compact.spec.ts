import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';

async function configure(page: Page) {
  // The compact, Codex-style conversation is an opt-in display preference.
  await page.addInitScript(() =>
    localStorage.setItem('niuu.compactUx.conversationView', 'compact'),
  );
  const config = JSON.parse(
    readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
  );
  for (const id of ['ting', 'ravn', 'mimir', 'observatory', 'valkyrie', 'bifrost', 'guild'])
    config.plugins[id] = { enabled: false };
  config.services.forge = { mode: 'http', baseUrl: '/api/v1/forge' };
  config.services.volundr = { mode: 'http', baseUrl: '/api/v1/volundr' };
  config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
  config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
  await page.route(/\/config(?:\.live)?\.json$/, (route) => route.fulfill({ json: config }));
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/setup')
      return route.fulfill({ json: { enabled: false, completed: true } });
    if (path.endsWith('/integrations/catalog'))
      return route.fulfill({
        json: [
          {
            slug: 'anthropic',
            name: 'Anthropic',
            integration_type: 'ai_provider',
            model_vendor: 'anthropic',
          },
        ],
      });
    if (path.endsWith('/integrations'))
      return route.fulfill({
        json: [
          {
            id: 'provider-1',
            slug: 'anthropic',
            integration_type: 'ai_provider',
            credential_name: 'anthropic-test',
            enabled: true,
            config: {},
            credential_status: 'valid',
          },
        ],
      });
    if (path.endsWith('/cluster/resources'))
      return route.fulfill({ json: { resourceTypes: [], nodes: [] } });
    if (path.endsWith('/features/modules'))
      return route.fulfill({
        json: [{ key: 'chat', enabled: true, scope: 'session', order: 0, label: 'Chat' }],
      });
    if (path.endsWith('/feature-flags'))
      return route.fulfill({
        json: { mini_mode: false, local_mounts_enabled: false, file_manager_enabled: true },
      });
    if (path.endsWith('/session-definitions'))
      return route.fulfill({
        json: [
          {
            key: 'skuldClaude',
            display_name: 'Claude Code',
            default_model: 'claude-opus-4-8',
            labels: ['session', 'claude'],
            compatible_providers: ['anthropic'],
          },
        ],
      });
    if (path.endsWith('/sessions') && route.request().method() === 'POST')
      return route.fulfill({
        status: 201,
        json: {
          id: 'created-session',
          name: 'project',
          status: 'starting',
          model: 'claude-opus-4-8',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          source: { type: 'git', repo: 'https://github.com/acme/project.git', branch: 'dev' },
        },
      });
    return route.fulfill({ json: [] });
  });
}

for (const room of [false, true]) {
  test(`shared compact chat folds ${room ? 'room' : 'session'} history and exposes display controls in Forge`, async ({
    page,
  }) => {
    await configure(page);
    const session = {
      id: 'chat-1',
      name: 'Compact chat',
      status: 'running',
      activity_state: 'idle',
      model: 'claude-opus-4-8',
      chat_endpoint: '/s/chat-1/session',
      created_at: '2026-09-12T12:00:00Z',
      updated_at: '2026-09-12T12:00:06Z',
      source: { type: 'git', repo: 'https://github.com/acme/project.git', branch: 'dev' },
    };
    const turns = [
      { id: 'u', role: 'user', content: 'Check the project', created_at: '2026-09-12T12:00:00Z' },
      {
        id: 'work',
        role: 'assistant',
        content: 'Inspecting project files',
        created_at: '2026-09-12T12:00:01Z',
      },
      {
        id: 'answer',
        role: 'assistant',
        content: 'The project checks passed.',
        created_at: '2026-09-12T12:00:06Z',
      },
    ];
    const history = room
      ? turns
          .map((turn) => ({
            ...turn,
            participant_meta:
              turn.role === 'assistant'
                ? { peer_id: 'reviewer', display_name: 'Reviewer', participant_type: 'ravn' }
                : undefined,
          }))
          .concat([
            {
              id: 'builder-work',
              role: 'assistant',
              content: 'Builder is checking',
              created_at: '2026-09-12T12:00:07Z',
              participant_meta: {
                peer_id: 'builder',
                display_name: 'Builder',
                participant_type: 'ravn',
              },
            },
            {
              id: 'builder-answer',
              role: 'assistant',
              content: 'Builder has finished',
              created_at: '2026-09-12T12:00:08Z',
              participant_meta: {
                peer_id: 'builder',
                display_name: 'Builder',
                participant_type: 'ravn',
              },
            },
          ])
      : turns;
    await page.route('**/api/v1/forge/sessions**', (route) => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill({
        json: path.endsWith('/sessions')
          ? [session]
          : path.endsWith('/chat-1')
            ? session
            : path.endsWith('/conversation')
              ? { turns: history }
              : [],
      });
    });
    await page.route('**/s/chat-1/api/conversation/history', (route) =>
      route.fulfill({ json: { turns: history } }),
    );
    await page.routeWebSocket('**/s/chat-1/session', (socket) => {
      socket.onMessage(() => {});
    });
    await page.goto('/volundr/sessions/chat-1');
    await page.locator('#tab-chat').click();
    await expect(page.getByText('The project checks passed.')).toBeVisible();
    await expect(page.getByTestId('conversation-view-toggle')).toBeVisible();
    if (room) {
      await expect(page.getByText('Builder has finished')).toBeVisible();
      await expect(page.getByTestId('worked-toggle')).toHaveCount(2);
    }
    const work = page.getByTestId('worked-toggle').first();
    await expect(work).toBeVisible();
    if ((await work.getAttribute('aria-expanded')) === 'false') await work.click();
    await expect(page.getByText('Inspecting project files')).toBeVisible();
    await page.getByText('Display', { exact: true }).click();
    await page.getByLabel('Agent avatars').check();
    await expect(page.getByLabel('Agent avatars')).toBeChecked();
    await page.getByLabel('Timestamps').selectOption('always');
    await page.screenshot({ path: test.info().outputPath('compact-chat.png') });
  });
}
