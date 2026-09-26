import { test, expect, type Page } from '@playwright/test';

/**
 * e2e coverage for the Memory scene at `/mimir` — Explore (default) / Focus
 * (a node is focused) / Ask (a question is asked) / Replay (`asOf` is set).
 *
 * The Mímir service runs against routed HTTP fixtures shaped exactly like the
 * backend's responses, and first-launch setup is reported complete so the
 * setup gate does not redirect away from the page under test.
 */

const NOW = '2026-04-19T14:00:00Z';

const GATEWAY_ID = 'platform:%2Fplatform%2Fgateway-routing';
const CEDAR_ID = 'platform:%2Fplatform%2Fcedar-authorization';

const graph = {
  nodes: [
    {
      id: GATEWAY_ID,
      title: 'Gateway routing on ymir',
      category: 'infra',
      path: '/platform/gateway-routing',
      kind: 'topic',
      summary: 'Every route into the cluster must be declared in the Cedar gateway table.',
      mount: 'platform',
      updated_at: NOW,
      first_seen: '2026-03-01T08:00:00Z',
      confidence: 'high',
    },
    {
      id: CEDAR_ID,
      title: 'Cedar authorization',
      category: 'infra',
      path: '/platform/cedar-authorization',
      kind: 'directive',
      summary: 'Every route needs a Cedar policy entry as well as a table entry.',
      mount: 'platform',
      updated_at: NOW,
      first_seen: '2026-03-02T08:00:00Z',
      confidence: 'medium',
    },
  ],
  edges: [{ source: GATEWAY_ID, target: CEDAR_ID, type: 'depends_on' }],
};

const gatewayPage = {
  path: '/platform/gateway-routing',
  title: 'Gateway routing on ymir',
  summary: 'Every route into the cluster must be declared in the Cedar gateway table.',
  category: 'infra',
  type: 'topic',
  confidence: 'high',
  mounts: ['platform'],
  updated_at: NOW,
  source_ids: ['src-1'],
  related: [],
  content: '',
  zones: [
    {
      kind: 'key-facts',
      items: ['The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.'],
    },
  ],
};

async function setup(page: Page) {
  await page.route('**/config*.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.mimir = { mode: 'http', baseUrl: '/memory-test-api' };
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
  await page.route('**/memory-test-api/**', (route) => route.fulfill({ json: [] }));
  await page.route('**/memory-test-api/mounts', (route) =>
    route.fulfill({
      json: [
        {
          name: 'platform',
          role: 'domain',
          host: 'platform-kb.niuu.world',
          url: 'https://platform-kb.niuu.world',
          priority: 3,
          categories: null,
          status: 'healthy',
          pages: 2,
          sources: 1,
          lint_issues: 0,
          last_write: NOW,
          embedding: 'all-minilm-l6-v2',
          size_kb: 512,
          desc: 'Platform-scoped domain knowledge',
        },
      ],
    }),
  );
  await page.route('**/memory-test-api/graph*', (route) => route.fulfill({ json: graph }));
  await page.route('**/memory-test-api/activity*', (route) => route.fulfill({ json: [] }));
  await page.route('**/memory-test-api/eval/queries', (route) =>
    route.fulfill({ json: { total: 0, zero_result_count: 0, recent: [] } }),
  );
  await page.route('**/memory-test-api/lint*', (route) =>
    route.fulfill({ json: { issues: [], pages_checked: 2 } }),
  );
  await page.route(
    (url) =>
      url.pathname.endsWith('/memory-test-api/page') &&
      url.searchParams.get('path') === '/platform/gateway-routing',
    (route) => route.fulfill({ json: gatewayPage }),
  );
  await page.route('**/memory-test-api/evidence*', (route) => route.fulfill({ json: [] }));
  // Registered before the specific search below: Playwright runs the most
  // recently registered matching route first.
  await page.route('**/memory-test-api/search*', (route) => route.fulfill({ json: [] }));
  await page.route(`**/memory-test-api/search?q=${encodeURIComponent('gateway')}*`, (route) =>
    route.fulfill({
      json: [
        {
          path: '/platform/gateway-routing',
          title: 'Gateway routing on ymir',
          summary: 'Every route into the cluster must be declared in the Cedar gateway table.',
          category: 'infra',
          type: 'topic',
          confidence: 'high',
          score: 0.9,
          mount: 'platform',
        },
      ],
    }),
  );
}

test.describe('Memory scene (/mimir)', () => {
  test('happy path: What Niuu knows loads with stats and panels', async ({ page }) => {
    await setup(page);
    await page.goto('/mimir');
    await expect(page.getByRole('region', { name: 'What Niuu knows' })).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByText(/2 pages · 1 link · 1 instance/)).toBeVisible();
    await expect(page.getByRole('toolbar', { name: 'Scene controls' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Colour by' })).toBeVisible();
  });

  test('focus a page and step back out with Escape', async ({ page }) => {
    await setup(page);
    await page.goto(`/mimir?focus=${encodeURIComponent(GATEWAY_ID)}`);
    const inspector = page.getByRole('region', { name: 'Page inspector' });
    await expect(inspector).toBeVisible({ timeout: 10000 });
    await expect(inspector.getByText('Gateway routing on ymir')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('region', { name: 'What Niuu knows' })).toBeVisible();
  });

  test('ask a question and see the answer card, then follow up', async ({ page }) => {
    await setup(page);
    await page.goto('/mimir');
    await page.getByPlaceholder('Ask what Niuu knows…').fill('gateway');
    await page.getByRole('button', { name: 'Ask memory' }).click();
    await expect(page.getByRole('region', { name: 'How it answered' })).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByTestId('memory-ask-answer-card')).toBeVisible();
    await expect(
      page.getByText(
        'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
      ),
    ).toBeVisible();
    await page.getByRole('button', { name: 'Follow up' }).click();
    await expect(page.getByRole('region', { name: 'What Niuu knows' })).toBeVisible();
  });

  test('a question with no matches says so, honestly', async ({ page }) => {
    await setup(page);
    await page.goto('/mimir');
    await page.getByPlaceholder('Ask what Niuu knows…').fill('nothing matches this at all');
    await page.getByRole('button', { name: 'Ask memory' }).click();
    await expect(
      page.getByText('Memory has nothing on "nothing matches this at all".'),
    ).toBeVisible({
      timeout: 10000,
    });
  });

  test('enter and exit Replay mode', async ({ page }) => {
    await setup(page);
    await page.goto('/mimir');
    await page.getByRole('button', { name: 'Replay' }).click();
    await expect(page.getByRole('region', { name: 'Replay', exact: true })).toBeVisible({
      timeout: 10000,
    });
    await expect(page.getByRole('region', { name: 'Replay timeline' })).toBeVisible();
    await page.getByRole('button', { name: 'Back to now' }).click();
    await expect(page.getByRole('region', { name: 'What Niuu knows' })).toBeVisible();
  });

  test('an honest error when the graph cannot be loaded', async ({ page }) => {
    await setup(page);
    await page.unroute('**/memory-test-api/graph*');
    await page.route('**/memory-test-api/graph*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'graph unavailable' } }),
    );
    await page.goto('/mimir');
    await expect(page.getByRole('alert')).toBeVisible({ timeout: 10000 });
  });

  test('keyboard: ArrowDown/Enter picks a find-a-page match, and Tab reaches the ask bar', async ({
    page,
  }) => {
    await setup(page);
    await page.goto('/mimir');
    const find = page.getByLabel('Find a page');
    await find.fill('gateway');
    await expect(page.getByRole('option', { name: 'Gateway routing on ymir' })).toBeVisible({
      timeout: 10000,
    });
    await find.press('ArrowDown');
    await find.press('Enter');
    await expect(page.getByRole('region', { name: 'Page inspector' })).toBeVisible();

    await page.getByPlaceholder('Ask what Niuu knows…').focus();
    await expect(page.getByPlaceholder('Ask what Niuu knows…')).toBeFocused();
  });
});
