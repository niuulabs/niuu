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
    text: 'Open the [review notes](/home/thor/review/docs/review.md) or the [missing file](./missing.md).\n\n![Diagram](./diagram.svg)',
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
    model: 'gpt-6-astra',
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
      if (path.endsWith('/stop'))
        return route.fulfill({
          status: 503,
          json: { detail: 'Stop unavailable for this test session' },
        });
      return route.fulfill({ json: {} });
    }
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
    const json = path.includes('/features/modules')
      ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
      : path.endsWith('/api/conversation/history')
        ? {
            turns: [
              {
                id: 'turn',
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
  const group = page.getByRole('button', { name: /Expand 2 tool calls/ });
  await expect(group).toBeVisible();
  await expect(page.getByTestId('tool-block')).toHaveCount(0);
  await group.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('tool-block')).toHaveCount(2);
  await expect(page.getByText('Review the implementation before continuing.')).toBeVisible();
  await page.getByRole('button', { name: 'Hide tool calls and results' }).click();
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
