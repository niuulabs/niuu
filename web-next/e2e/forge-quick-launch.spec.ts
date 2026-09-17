import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
const baseConfig = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://forge-launch-fixture.invalid';
async function fixture(page: Page, failLaunch = false) {
  const mutations: Array<{ path: string; body: Record<string, unknown> }> = [];
  const hosts = [
    {
      id: 'thor',
      slug: 'local',
      name: 'Thor',
      baseUrl: 'http://100.66.123.128:8080',
      enabled: true,
      isDefault: true,
      config: { transport: 'embedded', defaultFolder: '/home/thor/repos/niuu' },
      tags: [],
    },
    {
      id: 'spark',
      slug: 'spark',
      name: 'Spark',
      baseUrl: 'http://100.127.141.74:8080',
      enabled: true,
      isDefault: false,
      config: { defaultFolder: '/home/xteo/repos' },
      tags: [],
    },
    {
      id: 'build',
      slug: 'build',
      name: 'Build',
      baseUrl: 'http://100.81.183.4:8080',
      enabled: true,
      isDefault: false,
      config: { defaultFolder: '/home/horde' },
      tags: [],
    },
    {
      id: 'build-bro',
      slug: 'build-bro',
      name: 'Build Bro',
      baseUrl: 'http://100.115.8.110:8080',
      enabled: true,
      isDefault: false,
      config: { defaultFolder: '/home/horde' },
      tags: [],
    },
  ];
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...baseConfig,
        theme: 'xteo',
        services: {
          ...baseConfig.services,
          forge: { mode: 'http', baseUrl: `${origin}/api/v1/forge` },
          volundr: { mode: 'http', baseUrl: `${origin}/api/v1/volundr` },
          niuu: { mode: 'http', baseUrl: `${origin}/api/v1/niuu` },
          bifrost: { mode: 'http', baseUrl: `${origin}/api/v1/bifrost` },
        },
      },
    }),
  );
  await page.routeWebSocket('ws://forge-launch-fixture.invalid/**', () => {});
  await page.route(`${origin}/**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== 'GET') {
      const body = route.request().postDataJSON() ?? {};
      mutations.push({ path, body });
      if (path.endsWith('/test'))
        return route.fulfill({ json: { ok: true, message: 'Forge is reachable' } });
      if (path.includes('/instances/')) return route.fulfill({ json: { ...hosts[0], ...body } });
      if (path.endsWith('/sessions'))
        return route.fulfill(
          failLaunch
            ? { status: 503, json: { detail: 'Forge is offline' } }
            : {
                json: {
                  ...body,
                  id: 'created-review',
                  status: 'starting',
                  created_at: '2026-09-17T12:00:00Z',
                },
              },
        );
      return route.fulfill({ status: 400, json: { detail: 'Unexpected fixture mutation' } });
    }
    if (path.endsWith('/instances')) return route.fulfill({ json: hosts });
    if (path.endsWith('/models'))
      return route.fulfill({
        json: ['claude-fable-5-1', 'gpt-6-astra', 'gpt-5.6-sol'].map((id) => ({
          id,
          name: id,
          enabled: true,
          provider: 'cloud',
          tier: 'frontier',
          effort_levels: ['low', 'medium', 'high', 'xhigh', 'max', 'ultra'],
          default_effort: 'xhigh',
        })),
      });
    if (path.endsWith('/session-definitions'))
      return route.fulfill({
        json: ['skuldClaudeInteractive', 'skuldCodex'].map((key) => ({
          key,
          displayName: key,
          defaultModel: '',
          labels: [],
          description: '',
          compatibleProviders: [],
        })),
      });
    if (path.endsWith('/repos'))
      return route.fulfill({
        json: [
          {
            id: 'niuu',
            name: 'niuu',
            org: 'niuu',
            url: 'https://example.test/niuu.git',
            cloneUrl: 'https://example.test/niuu.git',
            defaultBranch: 'main',
            branches: ['main', 'review'],
          },
        ],
      });
    if (path.endsWith('/branches')) return route.fulfill({ json: ['main', 'review'] });
    if (path.endsWith('/cluster/resources'))
      return route.fulfill({ json: { resourceTypes: [], nodes: [] } });
    if (path.endsWith('/stats')) return route.fulfill({ json: {} });
    if (path.endsWith('/stream'))
      return route.fulfill({ contentType: 'text/event-stream', body: '' });
    if (path.endsWith('/created-review'))
      return route.fulfill({
        json: {
          id: 'created-review',
          name: 'Review',
          model: 'gpt-6-astra',
          status: 'starting',
          source: { type: 'local_mount', local_path: '/home/thor/repos/niuu' },
        },
      });
    return route.fulfill({ json: [] });
  });
  return mutations;
}

test('Claude and Codex quick launch submits the native contract with optional resources omitted', async ({
  page,
}) => {
  const calls = await fixture(page);
  await page.goto('/volundr/catalog');
  await expect(page.getByRole('button', { name: 'Launch Claude', exact: true })).toBeEnabled();
  await expect(page.getByLabel('Workspace source')).toHaveValue('local_mount');
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('claude-fable-5-1');
  await expect(page.getByLabel('Effort', { exact: true })).toHaveValue('xhigh');
  await expect(
    page.getByLabel('Model', { exact: true }).locator('option[value="claude-opus-5"]'),
  ).toHaveAttribute('disabled', '');
  await page.screenshot({ path: '/tmp/forge-quick-launch-desktop.png', fullPage: true });
  await page.getByRole('button', { name: /^Codex/ }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue('gpt-6-astra');
  await page.getByLabel('Forge', { exact: true }).selectOption('spark');
  await expect(page.getByLabel('Working folder')).toHaveValue('/home/xteo/repos');
  await page.getByLabel('Effort', { exact: true }).selectOption('ultra');
  await page.getByRole('button', { name: 'Launch Codex', exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0]!.body).toMatchObject({
    definition: 'skuldCodex',
    model: 'gpt-6-astra',
    instance_id: 'spark',
    workload_config: { reasoningEffort: 'ultra' },
    source: { type: 'local_mount', local_path: '/home/xteo/repos' },
  });
  expect(calls[0]!.body).not.toHaveProperty('resource_config');
  expect(calls[0]!.body).not.toHaveProperty('launch_spec');
  await expect(page).toHaveURL(/\/volundr\/sessions\/created-review/);
});

test('Git selection, errors, and the legacy runtime remain explicit choices', async ({ page }) => {
  const calls = await fixture(page, true);
  await page.goto('/volundr/catalog');
  await expect(page.getByRole('button', { name: 'Launch Claude', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Launch Claude', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Forge is offline');
  await expect(page.getByLabel('Working folder')).toHaveValue('/home/thor/repos/niuu');
  expect(calls).toHaveLength(1);
  await page.getByLabel('Workspace source').selectOption('git');
  await page
    .getByRole('combobox', { name: 'Repository', exact: true })
    .selectOption('https://example.test/niuu.git');
  await expect(page.getByLabel('Branch', { exact: true })).toHaveValue('main');
  await page.getByRole('button', { name: 'Advanced launch', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Launch pod' })).toBeVisible();
});

test('host names and IPs are visible and edits use the registry while preserving routing', async ({
  page,
}) => {
  const calls = await fixture(page);
  await page.goto('/volundr/hosts');
  await expect(page.getByText('http://100.115.8.110:8080')).toBeVisible();
  await page.getByRole('button', { name: 'Test Spark', exact: true }).click();
  await expect(page.getByText('Forge is reachable')).toBeVisible();
  await page.getByRole('button', { name: 'Edit Thor', exact: true }).click();
  await page.getByLabel('Default folder').fill('/home/thor/review');
  await page.getByRole('button', { name: 'Save Forge', exact: true }).click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toMatchObject({
    path: '/api/v1/niuu/instances/thor',
    body: {
      baseUrl: 'http://100.66.123.128:8080',
      config: { transport: 'embedded', defaultFolder: '/home/thor/review' },
    },
  });
});

test('quick launch stays readable and inside an iPhone viewport', async ({ page }) => {
  await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/volundr/catalog');
  await expect(page.getByRole('button', { name: 'Launch Claude', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  await page.screenshot({ path: '/tmp/forge-quick-launch-phone.png', fullPage: true });
  await page.getByRole('button', { name: 'Launch Claude', exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole('button', { name: 'Launch Claude', exact: true })).toBeInViewport();
});
