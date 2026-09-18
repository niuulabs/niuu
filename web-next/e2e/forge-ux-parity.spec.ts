import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const baseConfig = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://forge-ux-fixture.invalid';
const parts = [
  {
    type: 'text',
    id: 'before',
    text: 'Review the implementation before continuing.',
    complete: true,
  },
  { type: 'tool_use', id: 'command', name: 'Bash', input: { command: 'git status --short' } },
  { type: 'tool_use', id: 'read', name: 'Read', input: { file_path: '/workspace/README.md' } },
  { type: 'tool_result', tool_use_id: 'command', content: 'Working tree clean' },
  { type: 'tool_result', tool_use_id: 'read', content: '# Readme' },
  {
    type: 'text',
    id: 'after',
    text: 'See the [website](https://preview.example.test/site) and [remote guide](https://preview.example.test/docs/guide.md). Open the [image reference](https://images.example.test/reference.png?revision=2) and the [local image](./diagram.svg). Open the [review notes](/home/thor/review/docs/review.md) or the [missing file](./missing.md).\n\n![Diagram](./diagram.svg)',
    complete: true,
  },
  {
    type: 'tool_use',
    id: 'file',
    name: 'present_file',
    input: {
      file_id: 'report-id',
      name: 'report.txt',
      mime: 'text/plain',
      caption: 'Review export',
    },
  },
];

async function fixture(page: Page, rich = false) {
  const mutations: string[] = [];
  const sessions = [
    { id: 'review', name: 'Forge UX review', status: 'running', activity_state: 'active' },
    { id: 'idle', name: 'API parity audit', status: 'running', activity_state: 'idle' },
    {
      id: 'attention',
      name: 'Waiting for review',
      status: 'running',
      activity_state: 'awaiting_input',
    },
    {
      id: 'stopped',
      name: 'Completed investigation',
      status: 'stopped',
      activity_state: 'stopped',
    },
    { id: 'archived', name: 'Earlier iteration', status: 'archived', activity_state: 'stopped' },
  ].map((session) => ({
    ...session,
    model: session.id === 'idle' ? 'claude-fable-5-1' : 'gpt-6-astra',
    coordination: ['review', 'idle'].includes(session.id)
      ? {
          project_id: 'lexi',
          role: session.id === 'review' ? 'coordinator' : 'worker',
          parent: session.id === 'idle' ? { instance_id: 'thor', session_id: 'review' } : null,
        }
      : undefined,
    source: { type: 'local_mount', local_path: '/home/thor/review' },
    created_at: '2026-09-16T00:00:00Z',
    last_active: '2026-09-16T10:00:00Z',
    chat_endpoint: `${origin.replace('http:', 'ws:')}/s/${session.id}/session`,
  }));
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...baseConfig,
        theme: 'xteo',
        services: {
          ...baseConfig.services,
          forge: { mode: 'http', baseUrl: `${origin}/api/v1/forge` },
          volundr: { mode: 'http', baseUrl: `${origin}/api/v1/volundr` },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://forge-ux-fixture.invalid/**', () => {});
  await page.route(`${origin}/**`, async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (route.request().method() !== 'GET') {
      mutations.push(`${route.request().method()} ${path}`);
      const session = sessions.find((item) => path.endsWith(`/sessions/${item.id}`));
      if (route.request().method() === 'PUT' && session) {
        const { name } = route.request().postDataJSON();
        if (!/^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/.test(name) || name.length > 63) {
          return route.fulfill({ status: 422, json: { detail: 'Invalid session name' } });
        }
        session.name = name;
        return route.fulfill({ json: session });
      }
      if (path.endsWith('/stop'))
        return route.fulfill({
          status: 503,
          json: { detail: 'Stop unavailable for this test session' },
        });
      return route.fulfill({ json: {} });
    }
    if (path.endsWith('/projects'))
      return route.fulfill({
        json: [{ id: 'lexi', name: 'Lexi', slug: 'lexi', status: 'active' }],
      });
    if (path.endsWith('/external-sessions'))
      return route.fulfill({
        json: [
          {
            provider: 'claude-code',
            harness: 'claude',
            external_id: 'cli-claude',
            title: 'Review notes',
            workspace_path: '/home/thor/review',
            workspace_exists: true,
            workspace_allowed: true,
          },
          {
            provider: 'codex',
            harness: 'codex',
            external_id: 'cli-codex',
            title: 'iOS improvements',
            workspace_path: '/home/thor/lexi',
            workspace_exists: true,
            workspace_allowed: true,
          },
        ],
      });
    if (path.endsWith('/files/download')) {
      if (url.searchParams.get('path') === 'missing.md')
        return route.fulfill({ status: 404, body: 'File not found' });
      if (url.searchParams.get('path') === 'diagram.svg')
        return route.fulfill({
          contentType: 'image/svg+xml',
          body: '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="100"><rect width="400" height="100" rx="12" fill="#182e42"/><text x="30" y="60" fill="white" font-size="20">Session → API → File preview</text></svg>',
        });
      await new Promise((resolve) => setTimeout(resolve, 300));
      return route.fulfill({
        contentType: 'text/markdown',
        body: '# Review notes\n\nLoaded from this session’s workspace.',
      });
    }
    if (path.includes('/files/presented/'))
      return route.fulfill({ contentType: 'text/plain', body: 'Delivered review export' });
    const session = sessions.find((item) => path.endsWith(`/sessions/${item.id}`));
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: [
          {
            id: 'fixture',
            name: 'Fixture',
            kind: 'volundr',
            enabled: true,
            baseUrl: 'http://fixture.test',
          },
        ],
      });
    const json = path.includes('/features/modules')
      ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
      : path.endsWith('/api/conversation/history') || path.endsWith('/conversation')
        ? {
            turns: [
              {
                id: 'turn',
                metadata: { usage: { astra: { inputTokens: 150344, outputTokens: 234 } } },
                role: 'assistant',
                content: '',
                parts: rich
                  ? [
                      {
                        type: 'text',
                        id: 'rich-review',
                        complete: true,
                        text: '# Forge presentation review\n\nClean **Markdown** from Claude Code and Codex, with *emphasis*, nested lists, and links to `README.md`.\n\n> [!NOTE]\n> Documents and tool output share the same viewer.\n\n1. Review the implementation\n   - [x] Session filters\n   - [ ] Next iteration\n\n| Surface | Status |\n| :--- | ---: |\n| iOS | Reference |\n| Forge | In review |\n\n```typescript\nconst theme = "xteo";\nconsole.log(theme);\n```\n\n$$\nE = mc^2\n$$\n\n```mermaid\ngraph LR; A[Claude Code] --> C[Forge]; B[Codex] --> C; C --> D[File preview];\n```',
                      },
                      ...parts,
                    ]
                  : parts,
                created_at: '2026-09-16T10:00:00Z',
              },
            ],
          }
        : session
          ? session
          : path.endsWith('/sessions')
            ? sessions.filter((item) =>
                url.searchParams.get('status') === 'archived'
                  ? item.status === 'archived'
                  : item.status !== 'archived',
              )
            : path.includes('/workflow/gates')
              ? { gates: [] }
              : path.includes('/diff')
                ? { files: [] }
                : path.includes('/stats')
                  ? {}
                  : [];
    return route.fulfill({ json });
  });
  return mutations;
}

test('renames from the title and sidebar, persisting through a reload', async ({
  page,
}, testInfo) => {
  const mutations = await fixture(page);
  await page.goto('/volundr/sessions/review');
  const title = page.locator('.niuu-live-session__identity');
  await title.getByRole('button', { name: 'Rename Forge UX review' }).click();
  await page.getByRole('textbox', { name: 'Session name' }).fill('title-renamed');
  await page.screenshot({
    path: testInfo.outputPath('rename-session.png'),
    animations: 'disabled',
  });
  await page.getByRole('textbox', { name: 'Session name' }).press('Enter');
  await expect(title).toContainText('title-renamed');
  await expect(page.getByRole('textbox', { name: 'Session name' })).toHaveCount(0);
  const row = page.getByTestId('pod-entry-review').locator('..');
  await expect(row).toContainText('title-renamed');
  await row.hover();
  await row.getByRole('button', { name: 'Rename title-renamed' }).click();
  await page.getByRole('textbox', { name: 'Session name' }).fill('sidebar-renamed');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(title).toContainText('sidebar-renamed');
  await expect(row).toContainText('sidebar-renamed');
  await page.reload();
  await expect(title).toContainText('sidebar-renamed');
  await page.setViewportSize({ width: 390, height: 844 });
  await title.getByRole('button', { name: 'Rename sidebar-renamed' }).click();
  await expect(page.getByRole('textbox', { name: 'Session name' })).toHaveValue('sidebar-renamed');
  await page.screenshot({
    path: testInfo.outputPath('phone-title-rename.png'),
    animations: 'disabled',
  });
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  expect(mutations).toEqual([
    'PUT /api/v1/forge/sessions/review',
    'PUT /api/v1/forge/sessions/review',
  ]);
});

test('pins from the title and sidebar above every grouping and state, remembering collapse and pins', async ({
  page,
}, testInfo) => {
  const mutations = await fixture(page);
  await page.goto('/volundr/sessions/review');
  const title = page.locator('.niuu-live-session__identity');
  await title.getByRole('button', { name: 'Pin Forge UX review', exact: true }).click();
  const pinned = page.locator('.forge-session-pinned-group');
  await expect(pinned.getByTestId('pod-entry-review')).toBeVisible();
  await expect(title.getByRole('button', { name: 'Unpin Forge UX review' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  for (const mode of ['state', 'repo', 'forge', 'project']) {
    await page.getByTestId(`pod-group-mode-${mode}`).click();
    await expect(pinned).toBeVisible();
    expect(
      await page.locator('.forge-session-scroll > div').first().getAttribute('class'),
    ).toContain('forge-session-pinned-group');
    await expect(page.getByTestId('pod-entry-review')).toHaveCount(1);
  }
  // A pinned project parent must not hide its unpinned child.
  await expect(page.getByTestId('pod-group-lexi').getByTestId('pod-entry-idle')).toBeVisible();
  for (const state of ['active', 'idle', 'attention', 'stopped', 'failed', 'all', 'archived']) {
    await page.getByTestId(`session-filter-${state}`).click();
    await expect(pinned.getByTestId('pod-entry-review')).toBeVisible();
  }
  const archived = page.getByTestId('pod-entry-archived').locator('..');
  await archived.hover();
  await archived.getByRole('button', { name: 'Pin Earlier iteration' }).click();
  await page.getByTestId('session-filter-live').click();
  await expect(pinned.getByTestId('pod-entry-archived')).toBeVisible();
  await expect(pinned.getByTestId('pod-entry-review')).toBeVisible();
  await expect(page.getByTestId('pod-entry-archived')).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath('pinned-sessions.png'),
    animations: 'disabled',
  });
  await pinned.getByRole('button', { name: /^Pinned/ }).click();
  await page.reload();
  await expect(pinned.getByRole('button', { name: /^Pinned/ })).toHaveAttribute(
    'aria-expanded',
    'false',
  );
  await pinned.getByRole('button', { name: /^Pinned/ }).click();
  await expect(pinned.getByTestId('pod-entry-review')).toBeVisible();
  await page.getByTestId('pod-search').fill('Earlier');
  await expect(pinned.getByTestId('pod-entry-review')).toHaveCount(0);
  await expect(pinned.getByTestId('pod-entry-archived')).toBeVisible();
  await page.getByTestId('pod-search').clear();
  await title.getByRole('button', { name: 'Unpin Forge UX review' }).click();
  await expect(page.getByTestId('pod-group-lexi').getByTestId('pod-entry-review')).toBeVisible();
  await expect(pinned.getByTestId('pod-entry-review')).toHaveCount(0);
  expect(mutations).toEqual([]);
});

test('discovers cached commands and submits the selected command through Skuld', async ({
  page,
}, testInfo) => {
  await fixture(page);
  const controls: Record<string, unknown>[] = [];
  await page.routeWebSocket('ws://forge-ux-fixture.invalid/**', (socket) => {
    socket.send(JSON.stringify({ type: 'capabilities', slash_commands: true }));
    socket.onMessage((raw) => {
      const frame = JSON.parse(String(raw));
      controls.push(frame);
      if (frame.type === 'discover_slash_commands')
        socket.send(
          JSON.stringify({
            type: 'slash_commands',
            commands: [
              {
                name: '/review',
                kind: 'command',
                description: 'Review the current changes',
                argument_hint: '[focus]',
              },
              { name: '/compact', kind: 'command', description: 'Compact the conversation' },
            ],
          }),
        );
    });
  });
  await page.goto('/volundr/sessions/review');
  await page.getByRole('button', { name: 'Slash commands', exact: true }).click();
  await expect(page.getByRole('listbox', { name: 'Slash commands' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('slash-commands.png') });
  await page.getByRole('option', { name: /Review the current changes/ }).click();
  expect(controls.some((frame) => frame.type === 'slash_command')).toBe(false);
  const input = page.getByTestId('chat-textarea');
  await expect(input).toHaveValue('/review ');
  await input.fill('/review accessibility');
  await input.press('Enter');
  await expect
    .poll(() => controls.filter((frame) => frame.type === 'slash_command'))
    .toEqual([expect.objectContaining({ command: '/review', arguments: 'accessibility' })]);
  expect(controls.filter((frame) => frame.type === 'discover_slash_commands')).toEqual([
    { type: 'discover_slash_commands', refresh: false },
  ]);
  expect(controls.some((frame) => frame.type === 'user')).toBe(false);
});

test('counted filters, persistent width and safe row actions', async ({ page }, testInfo) => {
  const mutations = await fixture(page);
  await page.goto('/volundr/sessions');
  const live = page.getByTestId('session-filter-live');
  await expect(live).toHaveText('Live3');
  await expect(page.getByTestId('pod-entry-stopped')).toHaveCount(0);
  const resize = page.getByRole('separator', { name: 'Resize session list' });
  await resize.focus();
  await page.keyboard.press('ArrowRight');
  await expect(resize).toHaveAttribute('aria-valuenow', '360');
  await page.reload();
  await expect(resize).toHaveAttribute('aria-valuenow', '360');
  await page.getByTestId('pod-entry-review').hover();
  await page.getByTestId('pod-entry-review-archive').click();
  await expect(page.getByRole('alert')).toContainText('503');
  expect(mutations.some((value) => value.endsWith('/archive'))).toBe(false);
  await page.getByTestId('pod-entry-review-delete').click();
  await expect(page.getByRole('dialog', { name: 'Delete session?' })).toBeVisible();
  expect(mutations.some((value) => value.startsWith('DELETE'))).toBe(false);
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByTestId('session-filter-archived').click();
  await expect(page.getByTestId('pod-entry-archived')).toBeVisible();
  await page.getByTestId('session-filter-live').click();
  await page.screenshot({ path: testInfo.outputPath('desktop-sessions.png') });
});

test('hierarchical tools preserve prose and file cards through visibility changes', async ({
  page,
}, testInfo) => {
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  await expect(page.getByRole('button', { name: 'Hide tool calls and results' })).toBeVisible();
  const messageActions = page
    .getByTestId('assistant-message')
    .first()
    .locator('.niuu-chat-action-bar');
  await expect(messageActions.getByRole('button')).toHaveCount(1);
  await expect(messageActions.getByRole('button')).toHaveAttribute('title', 'Copy');
  await expect(
    page.getByTitle(/^(Regenerate|Helpful|Not helpful|Bookmark|Remove bookmark)$/),
  ).toHaveCount(0);
  const group = page.getByRole('button', { name: /Expand 2 tool calls/ });
  await expect(group).toBeVisible();
  await expect(page.getByTestId('tool-block')).toHaveCount(0);
  await group.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('tool-block')).toHaveCount(2);
  await expect(page.getByText('Review the implementation before continuing.')).toBeVisible();
  await page.getByRole('button', { name: 'Hide tool calls and results' }).click();
  await expect(page.getByRole('separator', { name: 'Hidden tool calls' })).toHaveCount(1);
  await expect(page.getByTestId('tool-group-block')).toHaveCount(0);
  await expect(page.getByTestId('presented-file-card')).toBeVisible();
  await page.getByRole('button', { name: 'review notes' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Loading file' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Review notes' })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'missing file' }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('File not found');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Open file', exact: true }).click();
  await expect(page.getByText('Delivered review export')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Show tool calls and results' }).click();
  await expect(page.getByRole('img', { name: 'Diagram' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('hierarchical-transcript.png') });
});

test('phone layout uses a full-width list and detail back navigation', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('pod-list-sidebar')).toBeVisible();
  await expect(page.getByTestId('live-session-detail-page')).not.toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('phone-list.png') });
  await page.getByTestId('pod-entry-review').click();
  await expect(page.getByTestId('pod-list-sidebar')).not.toBeVisible();
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible();
  const box = await page.getByTestId('live-session-detail-page').boundingBox();
  expect(box?.width).toBeGreaterThan(280);
  await page.getByRole('button', { name: '‹ Sessions', exact: true }).click();
  await expect(page.getByTestId('pod-list-sidebar')).toBeVisible();
});

test('xTeo and Native dark switch across the shell and document previews and survive reload', async ({
  page,
}, testInfo) => {
  await fixture(page, true);
  await page.goto('/volundr/sessions/review');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'xteo');
  await expect(page.getByRole('heading', { name: 'Forge presentation review' })).toBeVisible();
  await expect(page.locator('.niuu-chat-md-callout')).toHaveAttribute('data-callout', 'NOTE');
  await expect(page.locator('.niuu-chat-md-table')).toBeVisible();
  await expect(page.locator('pre code span[style]').first()).toBeAttached();
  await expect(page.locator('math')).toBeAttached();
  await page.getByRole('img', { name: 'Mermaid diagram', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('img', { name: 'Mermaid diagram', exact: true })).toBeVisible();
  await page.getByRole('heading', { name: 'Forge presentation review' }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('xteo-rich-markdown.png') });
  await page.getByRole('combobox', { name: 'Color theme' }).selectOption('ice');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'ice');
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Color theme' })).toHaveValue('ice');
  await page.getByRole('combobox', { name: 'Color theme' }).selectOption('xteo');
  await page.getByRole('button', { name: 'review notes', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Review notes', exact: true })).toBeVisible();
  const width = (await dialog.boundingBox())!.width;
  expect(width).toBeGreaterThan(900);
  await dialog.getByRole('button', { name: 'Source', exact: true }).click();
  await expect(dialog.locator('pre code')).toContainText('# Review notes');
  await dialog.getByRole('button', { name: 'Preview', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('xteo-file-preview.png') });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Open image Diagram', exact: true }).click();
  await expect(dialog.getByRole('img', { name: 'diagram.svg' })).toBeVisible();
  await dialog.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Fit image' })).toContainText('125%');
  await dialog.getByRole('button', { name: 'Fit image' }).click();
  await expect(dialog.getByRole('button', { name: 'Fit image' })).toContainText('100%');
});

test('phone review keeps theme switch, Chat tab, toolbar and full file preview usable', async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  const theme = page.getByRole('combobox', { name: 'Color theme' });
  await expect(theme).toBeVisible();
  const themeBox = (await theme.boundingBox())!;
  expect(themeBox.x + themeBox.width).toBeLessThanOrEqual(390);
  await theme.selectOption('ice');
  await theme.selectOption('xteo');
  const disconnect = page
    .locator('.niuu-shell__topbar')
    .getByRole('button', { name: 'Disconnect' });
  await expect(disconnect).toBeVisible();
  const disconnectBox = (await disconnect.boundingBox())!;
  expect(disconnectBox.x + disconnectBox.width).toBeLessThanOrEqual(390);
  const tab = page.locator('.niuu-live-session__tab').filter({ hasText: 'Chat' });
  await expect(tab).toBeVisible();
  expect((await tab.boundingBox())!.width).toBeGreaterThan(45);
  const back = (await page.getByRole('button', { name: '‹ Sessions', exact: true }).boundingBox())!;
  const topbar = (await page.locator('.niuu-shell__topbar').boundingBox())!;
  expect(back.y).toBeGreaterThanOrEqual(topbar.y + topbar.height);
  expect(back.height).toBeGreaterThanOrEqual(44);
  const toolbar = await page.locator('.niuu-live-session__toolbar').boundingBox();
  const tabs = await page.locator('.niuu-live-session__tabs').boundingBox();
  expect(toolbar!.y).toBeGreaterThanOrEqual(tabs!.y + tabs!.height - 1);
  await page.screenshot({ path: testInfo.outputPath('xteo-phone-detail.png') });
  await page.getByRole('button', { name: 'review notes', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('heading', { name: 'Review notes', exact: true })).toBeVisible();
  const box = (await dialog.boundingBox())!;
  expect(box.width).toBeGreaterThan(360);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath('xteo-phone-preview.png') });
});

test('sidebar prioritizes live filters, settings, project trees and searchable imports', async ({
  page,
}, testInfo) => {
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  const sidebar = page.getByRole('navigation', { name: 'Session list' });
  await expect(sidebar.locator('[data-filter]')).toHaveText([
    'Live3',
    'Active1',
    'Idle1',
    'Needs you1',
    'Stopped1',
    'Errors0',
    'All5',
    'Archived1',
  ]);
  const launch = page.getByRole('button', { name: 'Launch a new session' });
  await launch.hover();
  await expect(page.getByRole('tooltip', { name: 'Launch a new session' })).toBeVisible();
  const headingBox = (await sidebar.getByRole('heading', { name: 'Sessions' }).boundingBox())!;
  expect(Math.abs((await launch.boundingBox())!.y - headingBox.y)).toBeLessThan(15);
  const footer = sidebar.getByRole('group', { name: 'Session list settings' });
  await expect(footer.getByRole('button', { name: 'Archive all stopped' })).toBeVisible();
  await expect(footer.getByRole('checkbox', { name: 'Show token usage' })).not.toBeChecked();
  await expect(page.getByText('150k → 234 tokens')).toHaveCount(0);
  await footer.getByRole('checkbox', { name: 'Show token usage' }).check();
  await expect(page.getByText('150k → 234 tokens')).toBeVisible();
  await page.reload();
  await expect(footer.getByRole('checkbox', { name: 'Show token usage' })).toBeChecked();
  await page.getByTestId('pod-group-mode-project').click();
  await expect(page.getByTestId('pod-group-lexi')).toBeVisible();
  await page.getByRole('button', { name: 'Collapse child sessions of Forge UX review' }).click();
  await expect(page.getByTestId('pod-entry-idle')).toHaveCount(0);
  await page.getByRole('button', { name: 'Expand child sessions of Forge UX review' }).click();
  await expect(page.getByTestId('pod-entry-idle')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('sidebar-projects.png') });
  await page.getByRole('button', { name: 'Import external CLI sessions' }).click();
  const search = page.getByRole('searchbox', { name: 'Search CLI sessions' });
  await search.fill('lexi');
  await expect(page.getByTestId('external-session-row-cli-codex')).toBeVisible();
  await expect(page.getByTestId('external-session-row-cli-claude')).toHaveCount(0);
  await search.fill('not-present');
  await expect(page.getByRole('status').filter({ hasText: 'No CLI sessions match' })).toBeVisible();
  await page.keyboard.press('Escape');
});

test('document previews grow with the window, and image hyperlinks preview, pan, copy and download in place', async ({
  page,
  context,
}, testInfo) => {
  await fixture(page);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  // A real decodable PNG, served by a separate origin with CORS for transfer controls.
  const png = Buffer.from(
    await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.width = 300;
      canvas.height = 200;
      const context = canvas.getContext('2d')!;
      context.fillStyle = '#2563eb';
      context.fillRect(0, 0, 300, 200);
      return canvas.toDataURL('image/png').split(',')[1]!;
    }),
    'base64',
  );
  await page.route('https://images.example.test/**', (route) =>
    route.fulfill({
      body: png,
      contentType: 'image/png',
      headers: { 'access-control-allow-origin': '*' },
    }),
  );
  await page.goto('/volundr/sessions/review');
  await page.getByRole('button', { name: 'review notes', exact: true }).click();
  const dialog = page.getByRole('dialog');
  const before = (await dialog.boundingBox())!;
  expect(before.width).toBeGreaterThan(1440 * 0.85);
  expect(before.height).toBeGreaterThan(900 * 0.85);
  await page.setViewportSize({ width: 1800, height: 1200 });
  await expect
    .poll(async () => (await dialog.boundingBox())!.height)
    .toBeGreaterThan(before.height + 200);
  await expect
    .poll(async () => (await dialog.boundingBox())!.width)
    .toBeGreaterThan(before.width + 200);
  await page.keyboard.press('Escape');
  const link = page.getByRole('button', { name: 'image reference', exact: true });
  await link.hover();
  await expect(
    page.locator(
      '.niuu-image-link-tooltip:not([data-state="closed"]) .niuu-image-link-thumbnail img',
    ),
  ).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('image-hover.png') });
  await link.click();
  await expect(dialog).toHaveAccessibleName('reference.png');
  await dialog.getByRole('button', { name: 'Zoom in' }).click();
  await expect(dialog.getByRole('button', { name: 'Fit image' })).toContainText('125%');
  const image = dialog.getByRole('img', { name: 'reference.png' });
  const view = await image.getAttribute('viewBox');
  const canvas = dialog.getByRole('region');
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 90, box.y + box.height / 2 + 40, { steps: 4 });
  await page.mouse.up();
  expect(await image.getAttribute('viewBox')).not.toBe(view);
  await canvas.focus();
  await page.keyboard.press('0');
  await expect(dialog.getByRole('button', { name: 'Fit image' })).toContainText('100%');
  await dialog.getByRole('button', { name: 'Copy image' }).click();
  await expect(dialog.getByText('Image copied')).toBeVisible();
  const download = page.waitForEvent('download');
  await dialog.getByRole('button', { name: 'Download', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('reference.png');
  await expect(dialog.getByRole('link', { name: 'Open original image' })).toHaveAttribute(
    'target',
    '_blank',
  );
  await page.screenshot({ path: testInfo.outputPath('image-viewer.png') });
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'local image', exact: true }).focus();
  await expect(
    page.locator(
      '.niuu-image-link-tooltip:not([data-state="closed"]) .niuu-image-link-thumbnail img',
    ),
  ).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(dialog).toHaveAccessibleName('diagram.svg');
});

test('session rows align name and state above harness, workspace and age with overlaid actions', async ({
  page,
}, testInfo) => {
  await fixture(page);
  await page.goto('/volundr/sessions');
  const row = page.getByTestId('pod-entry-review');
  await expect(row.locator('.forge-session-harness')).toHaveText('Codex');
  const claude = page.getByTestId('pod-entry-idle').locator('.forge-session-harness');
  await expect(claude).toHaveText('Claude');
  const blue = await row
    .locator('.forge-session-harness')
    .evaluate((el) => getComputedStyle(el).color);
  const orange = await claude.evaluate((el) => getComputedStyle(el).color);
  expect(blue).toBe('rgb(96, 165, 250)');
  expect(orange).toBe('rgb(251, 146, 60)');
  const name = row.locator('.forge-session-row__title');
  const state = row.locator('.forge-session-state');
  const nameBox = (await name.boundingBox())!;
  const stateBox = (await state.boundingBox())!;
  expect(Math.abs(nameBox.y - stateBox.y)).toBeLessThan(5);
  const sourceBox = (await row.locator('.forge-session-row__source').boundingBox())!;
  const ageBox = (await row.locator('.forge-session-row__age').boundingBox())!;
  expect(Math.abs(sourceBox.y - ageBox.y)).toBeLessThan(2);
  expect(sourceBox.y).toBeGreaterThan(nameBox.y + nameBox.height);
  await row.hover();
  const actions = row.locator('..').locator('.forge-session-row__actions');
  const actionsBox = (await actions.boundingBox())!;
  expect(actionsBox.y).toBeLessThan(nameBox.y + 5);
  expect(actionsBox.x).toBeLessThan(stateBox.x);
  expect(actionsBox.x + actionsBox.width).toBeGreaterThan(stateBox.x + stateBox.width);
  expect((await row.boundingBox())!.width).toBeGreaterThan(300);
  await page.screenshot({ path: testInfo.outputPath('session-row-hover.png') });
  await page.getByRole('combobox', { name: 'Color theme' }).selectOption('ice');
  await expect(row.locator('.forge-session-harness')).toHaveCSS('color', blue);
});

test('touch users can reveal row actions while the state stays visible at rest', async ({
  browser,
}, testInfo) => {
  const context = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  try {
    const mutations = await fixture(page);
    await page.goto('/volundr/sessions');
    const row = page.getByTestId('pod-entry-review').locator('..');
    const more = row.getByRole('button', { name: 'Actions for Forge UX review' });
    const actions = row.locator('.forge-session-row__actions');
    await expect(row.locator('.forge-session-state')).toBeVisible();
    await expect(actions).toHaveCSS('opacity', '0');
    await more.tap();
    await expect(actions).toHaveCSS('opacity', '1');
    await page.screenshot({ path: testInfo.outputPath('touch-row-actions.png') });
    await row.getByRole('button', { name: 'Rename Forge UX review' }).tap();
    const rename = page.getByRole('form', { name: 'Rename session' });
    await expect(rename).toBeVisible();
    const bounds = await rename.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    await page.getByRole('textbox', { name: 'Session name' }).fill('touch-review');
    await page.screenshot({
      path: testInfo.outputPath('touch-rename.png'),
      animations: 'disabled',
    });
    await page.getByRole('button', { name: 'Cancel', exact: true }).tap();
    await expect(page).toHaveURL(/\/volundr\/sessions$/);
    await expect(row).toContainText('Forge UX review');
    await row.getByRole('button', { name: 'Pin Forge UX review' }).tap();
    const pinned = page.locator('.forge-session-pinned-group');
    await expect(pinned.getByTestId('pod-entry-review')).toBeVisible();
    await expect(page).toHaveURL(/\/volundr\/sessions$/);
    await page.screenshot({
      path: testInfo.outputPath('touch-pinned-session.png'),
      animations: 'disabled',
    });
    // Moving the row remounts its touch action menu, which starts closed.
    await more.tap();
    await row.getByRole('button', { name: 'Unpin Forge UX review' }).tap();
    await expect(pinned).toHaveCount(0);
    await more.tap();
    await expect(actions).toHaveCSS('opacity', '1');
    await more.tap();
    await expect(actions).toHaveCSS('opacity', '0');
    expect(mutations).toEqual([]);
  } finally {
    await context.close();
  }
});

test('keeps Markdown link previews in one viewport and reveals target tooltips', async ({
  page,
}, testInfo) => {
  const mutations = await fixture(page);
  await page.route('https://preview.example.test/**', (route) =>
    route.fulfill({
      contentType: route.request().url().endsWith('.md') ? 'text/markdown' : 'text/html',
      headers: { 'access-control-allow-origin': '*' },
      body: route.request().url().endsWith('.md')
        ? '# Remote guide\n\n[Next page](../next)'
        : '<h1>Embedded website</h1>',
    }),
  );
  await page.goto('/volundr/sessions/review');
  const website = page.getByRole('button', { name: 'website', exact: true });
  await website.hover();
  await expect(
    page.getByRole('tooltip', { name: 'https://preview.example.test/site', exact: true }),
  ).toBeVisible();
  await website.click();
  const panel = page.getByRole('dialog');
  await expect(panel).toHaveClass(/forge-resource-dialog/);
  await expect(
    page
      .frameLocator('iframe[title="Preview of site"]')
      .getByRole('heading', { name: 'Embedded website' }),
  ).toBeVisible();
  const box = await panel.boundingBox();
  expect(box!.width).toBeGreaterThan(1200);
  expect(box!.height).toBeGreaterThan(780);
  await expect(panel.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute(
    'href',
    'https://preview.example.test/site',
  );
  expect(page.context().pages()).toHaveLength(1);
  await page.keyboard.press('Escape');
  await expect(website).toBeFocused();
  await page.getByRole('button', { name: 'remote guide', exact: true }).click();
  await expect(panel.getByRole('heading', { name: 'Remote guide', exact: true })).toBeVisible();
  await panel.getByRole('button', { name: 'Next page' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(panel.getByRole('link', { name: 'Open in new tab' })).toHaveAttribute(
    'href',
    'https://preview.example.test/next',
  );
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: 'Session workspace details' }).click();
  await expect(
    page.getByRole('dialog').getByText('/home/thor/review', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Copy session ID' })).toBeVisible();
  await expect(page.getByTestId('session-model')).toHaveText('GPT-6 Astra');
  await expect(page.getByText('Hierarchical', { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath('workspace-popover.png') });
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Session workspace details' })).toBeFocused();
  expect(mutations).toEqual([]);
});

test('aligns sidebar controls and uses red stop and delete actions in both themes', async ({
  page,
}, testInfo) => {
  const mutations = await fixture(page);
  await page.goto('/volundr/sessions/review');
  const sidebar = page.locator('.forge-session-list');
  await expect(page.getByTestId('pod-entry-review')).toBeVisible();
  for (const theme of ['xteo', 'ice']) {
    await page.evaluate(
      (value) => document.documentElement.setAttribute('data-theme', value),
      theme,
    );
    const filters = await sidebar
      .locator('.forge-session-filter-group')
      .first()
      .locator('button')
      .evaluateAll((nodes) =>
        nodes.map((node) => ({
          x: node.getBoundingClientRect().x,
          width: node.getBoundingClientRect().width,
          y: node.getBoundingClientRect().y,
          scrollWidth: node.scrollWidth,
          clientWidth: node.clientWidth,
        })),
      );
    expect(new Set(filters.map((box) => box.y)).size).toBe(1);
    expect(
      Math.max(...filters.map((box) => box.width)) - Math.min(...filters.map((box) => box.width)),
    ).toBeLessThan(1);
    const row = page.getByTestId('pod-entry-review').locator('..');
    await row.hover();
    const stop = page.getByTestId('pod-entry-review-stop');
    const remove = page.getByTestId('pod-entry-review-delete');
    await expect(stop).toHaveCSS('color', 'rgb(252, 165, 165)');
    await expect(remove).toHaveCSS('color', 'rgb(252, 165, 165)');
    await stop.hover();
    await expect(stop).toHaveCSS('background-color', 'rgb(239, 68, 68)');
    await page.screenshot({ path: testInfo.outputPath(`sidebar-${theme}.png`) });
  }
  expect(mutations).toEqual([]);
});

test('session tab settings persist, gear is full-sized, and account disconnect lives in the header', async ({
  page,
}, testInfo) => {
  const mutations = await fixture(page);
  await page.goto('/volundr/sessions/review');
  const tabs = page.locator('.niuu-live-session__tab');
  await expect(tabs).toHaveCount(3);
  await expect(tabs).toContainText(['Chat', 'Diffs', 'Files']);
  const gear = page
    .locator('.niuu-shell__rail')
    .getByRole('button', { name: 'Settings', exact: true });
  expect((await gear.boundingBox())!.height).toBeGreaterThanOrEqual(40);
  expect((await gear.locator('svg').boundingBox())!.width).toBeGreaterThanOrEqual(24);
  await expect(
    page.locator('.niuu-shell__rail').getByRole('button', { name: /Logout|Disconnect|Sign in/i }),
  ).toHaveCount(0);
  await expect(
    page.locator('.niuu-shell__topbar').getByRole('button', { name: 'Disconnect' }),
  ).toBeVisible();
  await gear.click();
  await page.getByRole('button', { name: 'Session tabs', exact: true }).click();
  await expect(page.getByRole('checkbox', { name: /Chat/ })).toBeDisabled();
  for (const name of ['Diff', 'Files'])
    await expect(page.getByRole('checkbox', { name, exact: true })).toBeChecked();
  for (const name of ['Terminal', 'Chronicle', 'Telemetry', 'Log'])
    await expect(page.getByRole('checkbox', { name, exact: true })).not.toBeChecked();
  await page.getByRole('checkbox', { name: 'Telemetry', exact: true }).check();
  await page.screenshot({
    path: testInfo.outputPath('session-tab-settings.png'),
    animations: 'disabled',
  });
  await page.reload();
  await expect(page.getByRole('checkbox', { name: 'Telemetry', exact: true })).toBeChecked();
  await page.goto('/volundr/sessions/review');
  await expect(tabs).toHaveCount(4);
  await expect(page.getByRole('tab', { name: /Telemetry/ })).toBeVisible();
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Disconnected', exact: true })).toBeVisible();
  await expect(page.locator('.niuu-shell')).toHaveCount(0);
  await page.getByRole('button', { name: 'Reconnect', exact: true }).click();
  await expect(page.getByRole('tab', { name: /Chat/ })).toBeVisible();
  expect(mutations).toEqual([]);
});

test('delayed history has one clean loading surface and reveals a long conversation at the bottom', async ({
  page,
}, testInfo) => {
  await fixture(page);
  let release!: () => void;
  const historyReady = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/forge/sessions/review/conversation?*', async (route) => {
    await historyReady;
    await route.fulfill({
      json: {
        turns: Array.from({ length: 50 }, (_, index) => {
          const i = index + 50;
          return {
            id: `long-${i}`,
            role: 'assistant',
            content: `Message ${i + 1}: ${'A long session review paragraph with enough content for several lines. '.repeat(8)}`,
            created_at: new Date(1700000000000 + i * 1000).toISOString(),
          };
        }),
      },
    });
  });
  await page.goto('/volundr/sessions/review');
  await expect(page.getByRole('status', { name: 'Loading conversation…' })).toBeVisible();
  await expect(page.locator('.niuu-chat-messages-container')).toHaveCount(0);
  await expect(page.locator('[data-testid="session-chat"]')).toHaveCount(0);
  await page.screenshot({
    path: testInfo.outputPath('clean-session-loading.png'),
    animations: 'disabled',
  });
  // Capture the very first painted transcript position, not merely the eventual position.
  await page.evaluate(() => {
    const seen: number[] = [];
    (window as unknown as { initialScrollDistances: number[] }).initialScrollDistances = seen;
    const sample = () => {
      const el = document.querySelector('.niuu-chat-messages-container');
      if (el) seen.push(el.scrollHeight - el.scrollTop - el.clientHeight);
      if (seen.length < 8) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
  release();
  await expect(page.getByText(/^Message 100:/)).toBeInViewport();
  const scroll = page.locator('.niuu-chat-messages-container');
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { initialScrollDistances: number[] }).initialScrollDistances.length,
      ),
    )
    .toBe(8);
  expect(
    await page.evaluate(() =>
      (window as unknown as { initialScrollDistances: number[] }).initialScrollDistances.every(
        (d) => d <= 2,
      ),
    ),
  ).toBe(true);
  await expect(scroll).toHaveCSS('scrollbar-width', 'thin');
  await expect(page.locator('.forge-session-scroll')).toHaveCSS('scrollbar-width', 'thin');
  // Late content growth stays pinned, but readers who scrolled up keep their position.
  await scroll.locator('.niuu-chat-messages-inner').evaluate((el) => {
    const content = document.createElement('div');
    content.style.height = '300px';
    el.append(content);
  });
  await expect
    .poll(() => scroll.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight))
    .toBeLessThan(2);
  await scroll.evaluate((el) => {
    el.scrollTop = 300;
    el.dispatchEvent(new Event('scroll'));
  });
  await scroll.locator('.niuu-chat-messages-inner').evaluate((el) => {
    const content = document.createElement('div');
    content.style.height = '300px';
    el.append(content);
  });
  await expect.poll(() => scroll.evaluate((el) => el.scrollTop)).toBe(300);
});

test('an enabled Terminal tab explains unsupported hosts without trying to spawn a shell', async ({
  page,
}) => {
  const mutations = await fixture(page);
  await page.addInitScript(() =>
    localStorage.setItem('niuu.forge.sessionTabs', 'chat,diffs,files,terminal'),
  );
  await page.route('**/terminal/api/terminal/sessions', (route) =>
    route.fulfill({ contentType: 'text/html', body: '<html>Fallback application</html>' }),
  );
  await page.goto('/volundr/sessions/review');
  await page.getByRole('tab', { name: /Terminal/ }).click();
  await expect(
    page.getByText('This Forge host does not provide terminal access for this session.'),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByText('Terminal unavailable', { exact: true })).toBeVisible();
  expect(mutations).toEqual([]);
});

test('centers top-level navigation and keeps compact account controls clear at every width', async ({
  page,
}, testInfo) => {
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  const header = page.locator('.niuu-shell__topbar');
  const tabs = page.locator('.niuu-shell__tabs');
  const account = header.getByRole('button', { name: 'Disconnect', exact: true });
  await expect(account).toHaveText('');
  await expect(page.getByText('Private connection', { exact: true })).toHaveCount(0);
  await expect(
    header.getByRole('combobox', { name: 'Color theme' }).locator('option:checked'),
  ).toHaveText('blue');
  await account.hover();
  await expect(page.getByRole('tooltip', { name: 'Disconnect', exact: true })).toBeVisible();
  await page.mouse.move(0, 0);
  await account.focus();
  await expect(page.getByRole('tooltip', { name: 'Disconnect', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  for (const width of [1440, 1024, 820, 761, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const headerBox = (await header.boundingBox())!;
    const tabsBox = (await tabs.boundingBox())!;
    const controlsBox = (await page.locator('.niuu-shell__topbar-right').boundingBox())!;
    const titleBox = (await page.locator('.niuu-shell__topbar-title').boundingBox())!;
    expect(
      Math.abs(tabsBox.x + tabsBox.width / 2 - (headerBox.x + headerBox.width / 2)),
    ).toBeLessThan(1);
    expect(controlsBox.x + controlsBox.width).toBeLessThanOrEqual(width);
    expect(titleBox.x + titleBox.width).toBeLessThanOrEqual(controlsBox.x);
    if (width > 760) {
      expect(titleBox.x + titleBox.width).toBeLessThanOrEqual(tabsBox.x + 1);
      expect(tabsBox.x + tabsBox.width).toBeLessThanOrEqual(controlsBox.x);
    } else {
      expect(tabsBox.y).toBeGreaterThanOrEqual(controlsBox.y + controlsBox.height);
    }
    await expect(account).toBeVisible();
    if (width === 1440 || width === 390)
      await page.screenshot({
        path: testInfo.outputPath(`centered-header-${width}.png`),
        animations: 'disabled',
      });
  }
});
