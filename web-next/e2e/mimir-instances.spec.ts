import { expect, test, type Page } from '@playwright/test';

async function setup(page: Page) {
  await page.route('**/config.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.mimir = { mode: 'http', baseUrl: '/instance-test-api' };
    await route.fulfill({ json: config });
  });
  await page.route('**/instance-test-api/**', (route) => route.fulfill({ json: [] }));
  await page.route('**/instance-test-api/deployments', (route) =>
    route.fulfill({
      json: { cluster: 'test', namespace: 'knowledge', backends: [], releases: [] },
    }),
  );
  await page.route('**/instance-test-api/graph*', (route) =>
    route.fulfill({ json: { nodes: [], edges: [] } }),
  );
}

test('inspects backend storage on the analytics page', async ({ page }) => {
  await setup(page);
  await page.route('**/instance-test-api/instances/inspect*', (route) =>
    route.fulfill({
      json: [
        {
          mount: 'research',
          backend: 'gbrain',
          metrics: { 'Storage engine': 'postgres' },
          unavailable: ['Write history'],
        },
      ],
    }),
  );
  await page.goto('/mimir/analytics');
  await expect(page.getByRole('region', { name: 'Instance inspection' })).toContainText('postgres');
});

test('uses a modal for the connection form and labels connection removal', async ({ page }) => {
  await setup(page);
  await page.route('**/instance-test-api/registry/mounts', (route) =>
    route.fulfill({
      json: [
        {
          id: 'remote',
          name: 'shared-test',
          kind: 'remote',
          role: 'shared',
          lifecycle: 'registered',
          url: 'https://mimir.test',
          enabled: true,
          default_read_priority: 10,
          health_status: 'healthy',
          health_message: '',
          desc: '',
          categories: [],
        },
      ],
    }),
  );
  await page.goto('/mimir/registry');
  await expect(page.getByRole('button', { name: '+ Deploy instance', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '+ Add connection', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Add connection', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('button', { name: 'Remove connection', exact: true }).hover();
  await expect(page.getByRole('tooltip')).toContainText('Remove connection');
});

test('Inspect preserves the analytics dashboard for an auto-mounted Helm instance', async ({
  page,
}) => {
  await setup(page);
  await page.route('**/instance-test-api/mounts', (route) =>
    route.fulfill({
      json: [
        {
          name: 'gbrain-ui',
          role: 'shared',
          access_scope: 'tenant',
          host: 'ymir',
          url: '',
          priority: 10,
          categories: [],
          status: 'healthy',
          pages: 3,
          sources: 0,
          last_write: '',
          embedding: 'fts',
          size_kb: 0,
          desc: '',
        },
      ],
    }),
  );
  await page.route('**/instance-test-api/deployments', (route) =>
    route.fulfill({
      json: {
        cluster: 'ymir',
        namespace: 'knowledge',
        backends: ['gbrain'],
        releases: [
          {
            name: 'gbrain-ui',
            backend: 'gbrain',
            target: 'cluster',
            ready: true,
            message: 'Ready',
          },
        ],
      },
    }),
  );
  await page.route('**/instance-test-api/deployments/gbrain-ui*', (route) =>
    route.fulfill({
      json: {
        name: 'gbrain-ui',
        ready: true,
        message: 'Ready',
        logs: {},
      },
    }),
  );
  await page.route('**/instance-test-api/instances/inspect*', (route) =>
    route.fulfill({
      json: [
        {
          mount: 'gbrain-ui',
          backend: 'gbrain',
          metrics: { Pages: 3, 'Storage engine': 'postgres' },
          unavailable: [],
        },
      ],
    }),
  );
  await page.goto('/mimir/registry');
  await expect(page.getByText(/gbrain · Managed/)).toBeVisible();
  await page.getByRole('button', { name: 'Inspect', exact: true }).click();
  await expect(page.locator('.mimir-instance-cards')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Knowledge composition' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Relationship coverage' })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Instance runtime' })).toBeVisible();
  await expect(page.locator('.mimir-instance-cards')).toHaveCSS('display', 'grid');
});
