import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://tool-images.invalid';
const landscape = readFileSync(new URL('./fixtures/tool-images/landscape.png', import.meta.url));
const portrait = readFileSync(new URL('./fixtures/tool-images/portrait.png', import.meta.url));
const hint = (id: string, width = 200, height = 400) => ({
  type: 'tool_result',
  tool_use_id: id,
  is_image: true,
  mime_type: 'image/png',
  img_w: width,
  img_h: height,
  truncated: true,
  preview: 'Image result',
});
const call = (id: string) => ({
  type: 'tool_use',
  id,
  name: 'Read',
  input: { file_path: `/workspace/${id}.png` },
});
async function fixture(page: Page) {
  const requests: URL[] = [];
  const visibility: boolean[] = [];
  const sockets: WebSocketRoute[] = [];
  const controls = { failThumbnail: false, delay: 300 };
  const session = {
    id: 'review',
    name: 'Image review',
    status: 'running',
    activity_state: 'idle',
    model: 'claude-opus-4-6',
    source: { type: 'local_mount', local_path: '/workspace' },
    chat_endpoint: `${origin.replace('http:', 'ws:')}/forge-host/build/s/review/session`,
    created_at: '2026-09-18T00:00:00Z',
  };
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
        services: {
          ...config.services,
          forge: { mode: 'http', baseUrl: `${origin}/api/v1/forge` },
          volundr: { mode: 'http', baseUrl: `${origin}/api/v1/volundr` },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://tool-images.invalid/**', (socket) => {
    sockets.push(socket);
    socket.onMessage((raw) => {
      const event = JSON.parse(String(raw));
      if (event.type === 'set_internal_visibility') visibility.push(event.visible);
    });
  });
  await page.route(`${origin}/**`, async (route) => {
    const u = new URL(route.request().url()),
      path = u.pathname;
    if (route.request().method() !== 'GET') return route.abort();
    if (path.includes('/tool-result/')) {
      requests.push(u);
      await new Promise((resolve) => setTimeout(resolve, controls.delay));
      const id = path.split('/tool-result/')[1]!.split('/')[0]!;
      const bytes = id === 'portrait' || id === 'live' ? portrait : landscape;
      if (path.endsWith('/preview'))
        return controls.failThumbnail
          ? route.fulfill({ status: 404, json: { detail: 'Preview unavailable' } })
          : route.fulfill({ contentType: 'image/png', body: bytes });
      return route.fulfill({
        json: {
          tool_use_id: id,
          content: JSON.stringify({
            type: 'image',
            file: { base64: bytes.toString('base64'), type: 'image/png' },
          }),
        },
      });
    }
    if (path.endsWith('/conversation'))
      return route.fulfill({
        json: {
          turns: [
            {
              id: 'images',
              role: 'assistant',
              content: 'I inspected both generated layouts.',
              created_at: session.created_at,
              parts: [
                { type: 'text', text: 'I inspected both generated layouts.' },
                {
                  type: 'tool_use',
                  id: 'terminal',
                  name: 'Bash',
                  input: { command: 'ls /workspace' },
                },
                {
                  type: 'tool_result',
                  tool_use_id: 'terminal',
                  content: 'portrait.png landscape.png',
                },
                call('portrait'),
                hint('portrait'),
                { type: 'text', text: 'The portrait keeps its original proportions.' },
                call('landscape'),
                hint('landscape', 640, 400),
                {
                  type: 'text',
                  text: 'The wider chart is easy to inspect without leaving the conversation.',
                },
              ],
            },
          ],
          total_turns: 1,
          window_offset: 0,
          window_end: 1,
          history_protocol: 2,
          older_cursor: null,
          projection_revision: 'images',
        },
      });
    return route.fulfill({
      json: path.endsWith('/sessions/review')
        ? session
        : path.endsWith('/sessions')
          ? [session]
          : path.includes('/workflow/gates')
            ? { gates: [] }
            : path.includes('/features/modules')
              ? [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }]
              : path.includes('/stats')
                ? {}
                : [],
    });
  });
  return { requests, visibility, sockets, controls };
}

test('images are top-level, reserve aspect ratio, and open the existing full image viewer', async ({
  page,
}, info) => {
  const { requests } = await fixture(page);
  await page.goto('/volundr/sessions/review');
  const cards = page.getByTestId('tool-image-card');
  await expect(cards).toHaveCount(2);
  const first = cards.first().locator('svg').first();
  const before = await first.boundingBox();
  expect(before!.height).toBe(200);
  expect(before!.width).toBe(100);
  await expect(cards.first().locator('image')).toHaveAttribute('href', /^blob:/);
  expect(await first.boundingBox()).toEqual(before);
  expect(requests.every((u) => u.pathname.endsWith('/preview'))).toBe(true);
  expect(requests.every((u) => u.pathname.startsWith('/forge-host/build/'))).toBe(true);
  expect(
    await cards.first().evaluate((el) => Boolean(el.closest('.niuu-tool-block, .niuu-tool-group'))),
  ).toBe(false);
  await page.locator('.niuu-chat-messages-container').evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.screenshot({ path: info.outputPath('inline-images.png'), animations: 'disabled' });
  const open = page.getByRole('button', { name: 'Open image portrait.png', exact: true });
  await open.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole('button', { name: 'Download', exact: true })).toBeEnabled();
  expect(requests.some((u) => u.pathname.endsWith('/tool-result/portrait'))).toBe(true);
  const box = await dialog.boundingBox();
  const viewport = page.viewportSize()!;
  expect(box!.width).toBeGreaterThan(viewport.width * 0.8);
  expect(box!.height).toBeGreaterThan(viewport.height * 0.8);
  await dialog.getByRole('button', { name: 'Zoom in' }).click();
  await expect(dialog.getByText('125%')).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Open original image' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('image-viewer.png'), animations: 'disabled' });
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(open).toBeFocused();
});

test('hiding tools keeps previews and accepts new live Claude image results', async ({ page }) => {
  const { sockets, visibility } = await fixture(page);
  await page.goto('/volundr/sessions/review');
  await expect(page.getByTestId('tool-image-card')).toHaveCount(2);
  const hide = page.getByRole('button', { name: /Hide tool/ });
  await hide.click();
  await expect(page.getByRole('button', { name: 'Show tool calls and results' })).toBeVisible();
  await expect(page.getByTestId('tool-image-card')).toHaveCount(2);
  await expect.poll(() => visibility.length).toBeGreaterThan(0);
  expect(visibility.every(Boolean)).toBe(true);
  const socket = sockets.at(-1)!;
  socket.send(JSON.stringify({ type: 'content_block_start', content_block: call('live') }));
  socket.send(JSON.stringify({ type: 'user', message: { content: [hint('live')] } }));
  await expect(
    page.getByRole('button', { name: 'Open image live.png', exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId('tool-image-card')).toHaveCount(3);
});

test('unavailable thumbnails stay retryable without automatically downloading originals', async ({
  page,
}) => {
  const { requests, controls } = await fixture(page);
  controls.failThumbnail = true;
  await page.goto('/volundr/sessions/review');
  await expect(page.getByRole('button', { name: 'Retry thumbnail' })).toHaveCount(2);
  expect(requests.every((u) => u.pathname.endsWith('/preview'))).toBe(true);
  controls.failThumbnail = false;
  await page.getByRole('button', { name: 'Retry thumbnail' }).first().click();
  await expect(page.getByTestId('tool-image-card').first().locator('image')).toHaveAttribute(
    'href',
    /^blob:/,
  );
  await page.getByRole('button', { name: 'Open image landscape.png', exact: true }).click();
  await expect(
    page.getByRole('dialog').getByRole('button', { name: 'Download', exact: true }),
  ).toBeEnabled();
});

test('phone keeps cards and the image viewer within the viewport', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page);
  await page.goto('/volundr/sessions/review');
  const open = page.getByRole('button', { name: 'Open image landscape.png', exact: true });
  await expect(open).toBeVisible();
  const card = await open.boundingBox();
  expect(card!.x + card!.width).toBeLessThanOrEqual(390);
  expect(card!.height).toBeLessThanOrEqual(202);
  await open.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Download', exact: true })).toBeEnabled();
  const box = await dialog.boundingBox();
  expect(box!.width).toBeLessThanOrEqual(390);
  expect(box!.height).toBeLessThanOrEqual(844);
  await page.screenshot({
    path: info.outputPath('phone-image-viewer.png'),
    animations: 'disabled',
  });
});
