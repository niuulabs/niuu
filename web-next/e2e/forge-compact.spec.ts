import { readFileSync } from 'node:fs';
import { test, expect, type Page } from '@playwright/test';

async function configure(page: Page, mini: boolean, fail = false, holdFlags?: Promise<void>) {
  const config = JSON.parse(
    readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
  );
  for (const id of ['ting', 'ravn', 'mimir', 'observatory', 'valkyrie', 'bifrost', 'guild'])
    config.plugins[id] = { enabled: false };
  config.services.forge = { mode: 'http', baseUrl: '/api/v1/forge' };
  config.services.volundr = { mode: 'http', baseUrl: '/api/v1/volundr' };
  await page.route('**/config.json', (route) => route.fulfill({ json: config }));
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/cluster/resources'))
      return route.fulfill({ json: { resourceTypes: [], nodes: [] } });
    if (path.endsWith('/features/modules'))
      return route.fulfill({
        json: [{ key: 'chat', enabled: true, scope: 'session', order: 0, label: 'Chat' }],
      });
    if (path.endsWith('/feature-flags')) {
      await holdFlags;
      return route.fulfill({
        status: fail ? 503 : 200,
        json: fail
          ? { detail: 'Forge unavailable' }
          : { mini_mode: mini, local_mounts_enabled: mini, file_manager_enabled: true },
      });
    }
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
  await page.goto('/volundr/sessions');
  await page.getByTestId('pod-launch-button').click();
}

for (const mini of [true, false]) {
  test(`compact launch creates a ${mini ? 'local folder' : 'Git repository'} session`, async ({
    page,
  }) => {
    await configure(page, mini);
    await expect(
      page.getByRole('status').filter({ hasText: 'Loading launch options' }),
    ).toHaveCount(0);
    await page
      .getByTestId('quick-launch-folder')
      .fill(mini ? '/tmp/project' : 'https://github.com/acme/project.git');
    if (!mini) await page.getByRole('textbox', { name: 'Branch' }).fill('dev');
    await page.screenshot({ path: test.info().outputPath('quick-launch.png') });
    const request = page.waitForRequest(
      (request) =>
        request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/sessions'),
    );
    await page.getByTestId('quick-launch-go').click();
    const body = (await request).postDataJSON();
    expect(body.source.type).toBe(mini ? 'local_mount' : 'git');
    await expect(page).toHaveURL(/sessions\/created-session$/);
  });
}

test('compact launch shows loading, then an unavailable host error', async ({ page }) => {
  let release!: () => void;
  const flags = new Promise<void>((resolve) => {
    release = resolve;
  });
  await configure(page, false, true, flags);
  await expect(page.getByText('Loading launch options…')).toBeVisible();
  await expect(page.getByTestId('quick-launch-go')).toBeDisabled();
  release();
  await expect(page.getByRole('alert')).toBeVisible({ timeout: 15000 });
  await expect(page.getByTestId('quick-launch-go')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('quick-launch')).toHaveCount(0);
});

test('session list supports keyboard resizing and operator metadata preferences', async ({
  page,
}) => {
  await configure(page, false);
  await page.keyboard.press('Escape');
  const resize = page.getByRole('separator', { name: 'Resize session list' });
  await resize.focus();
  await page.keyboard.press('End');
  await expect(resize).toHaveAttribute('aria-valuenow', '560');
  await page.getByRole('button', { name: 'Details', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Details', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

for (const room of [false, true]) {
  test(`shared compact chat folds ${room ? 'room' : 'session'} history and exposes display controls in Forge`, async ({
    page,
  }) => {
    await configure(page, false);
    await page.keyboard.press('Escape');
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

for (const mini of [true, false]) {
  test(`advanced launch preserves ${mini ? 'local' : 'Git'} input`, async ({ page }) => {
    await configure(page, mini);
    await expect(page.getByText('Loading launch options…')).toHaveCount(0);
    const source = mini ? '/tmp/project' : 'https://github.com/acme/project.git';
    await page.getByTestId('quick-launch-folder').fill(source);
    await page.getByTestId('quick-launch-name').fill('handoff-check');
    await page.getByTestId('quick-launch-prompt').fill('Validate the handoff');
    if (!mini) await page.getByLabel('Branch').fill('dev');
    await page.getByRole('button', { name: 'Advanced launch' }).click();
    await expect(
      page.getByRole('dialog', { name: 'Launch pod' }).locator('input').first(),
    ).toHaveValue(source);
    await page.getByTestId('wizard-next').click();
    await page.getByTestId('wizard-next').click();
    await expect(page.getByTestId('step-confirm-content')).toContainText(source);
    await expect(page.getByTestId('step-confirm-content')).toContainText('handoff-check');
    await expect(page.getByTestId('step-confirm-content')).toContainText('Validate the handoff');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Launch pod' })).toHaveCount(0);
  });
}
