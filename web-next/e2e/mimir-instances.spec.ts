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

test('inspects backend storage and requests a Flux deployment using target defaults', async ({
  page,
}) => {
  await setup(page);
  await page.route('**/instance-test-api/instances/inspect*', (route) =>
    route.fulfill({
      json: [
        {
          mount: 'research',
          backend: 'gbrain',
          metrics: { 'Storage engine': 'postgres' },
          unavailable: ['Dream history'],
        },
      ],
    }),
  );
  await page.goto('/mimir/analytics');
  await expect(page.getByRole('region', { name: 'Instance inspection' })).toContainText('postgres');
  let submitted: unknown;
  await page.route('**/instance-test-api/deployments', async (route) => {
    if (route.request().method() === 'POST') {
      submitted = route.request().postDataJSON();
      return route.fulfill({ status: 202, json: { ready: false } });
    }
    return route.fulfill({
      json: {
        cluster: 'test',
        namespace: 'knowledge',
        backends: ['gbrain'],
        releases: [],
        target: 'cluster',
        dream_available: true,
      },
    });
  });
  await page.goto('/mimir/registry');
  await page.getByRole('button', { name: '+ Deploy instance', exact: true }).click();
  await page
    .getByRole('region', { name: 'Instance deployments' })
    .getByLabel('Instance name', { exact: true })
    .fill('research');
  await page.getByRole('button', { name: /gbrain Connected memory/ }).click();
  await expect(page.getByLabel('Attach a warden')).toHaveCount(0);
  await page.getByRole('combobox', { name: 'Deploy to' }).click();
  await page.getByRole('option', { name: 'test', exact: true }).click();
  await page.getByLabel('Enable scheduled dream cycles').check();
  await page.getByLabel('Schedule (cron, UTC)').fill('0 3 * * *');
  await page.getByRole('button', { name: 'Deploy instance' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText(/Deployment requested/)).toBeVisible();
  expect(submitted).toEqual({
    name: 'research',
    backend: 'gbrain',
    target: 'cluster',
    warden: false,
    dream: { enabled: true, schedule: '0 3 * * *', phases: ['lint', 'backlinks', 'orphans'] },
  });
});

test('shows deployment loading and failure without claiming success', async ({ page }) => {
  await setup(page);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/instance-test-api/deployments', async (route) => {
    await pending;
    await route.fulfill({ status: 503, json: { detail: 'Flux unavailable' } });
  });
  await page.goto('/mimir/registry');
  await page.getByRole('button', { name: '+ Deploy instance', exact: true }).click();
  await expect(page.getByText('Finding deployment targets…')).toBeVisible();
  release();
  await expect(page.getByText(/Deployment targets could not be loaded/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Deploy instance' })).toHaveCount(0);
});

test('uses modals for both entry points and labels connection removal', async ({ page }) => {
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
          path: '',
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
  await page.getByRole('button', { name: '+ Deploy instance', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Deploy instance', exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
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
          lint_issues: 0,
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
        dream_results: [],
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
