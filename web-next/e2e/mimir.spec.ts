import { test, expect, type Page } from '@playwright/test';

/**
 * Analytics keeps the service-wide reports (eval, query traffic)
 * behind the "Service diagnostics" disclosure.
 */
async function openServiceDiagnostics(page: Page) {
  const diagnostics = page.getByRole('group').filter({ hasText: 'Service diagnostics' });
  await diagnostics.getByText('Service diagnostics', { exact: true }).click();
  await expect(diagnostics).toHaveAttribute('open', '');
}

// First-launch setup is reported complete so the setup gate does not redirect
// every Mímir route to the wizard (the mock setup service starts unfinished).
test.beforeEach(async ({ page }) => {
  await page.route('**/config*.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
});

test('mimir rune is visible in the rail', async ({ page }) => {
  await page.goto('/mimir');
  const railButton = page.getByTestId('rail-item-mimir');
  await expect(railButton).toBeVisible();
  await expect(railButton).toHaveText('M');
});

// ---------------------------------------------------------------------------
// Legacy Mímir routes now redirect straight to the Memory scene — see
// mimir-memory.spec.ts for the scene's own coverage (Explore/Focus/Ask/Replay).
// ---------------------------------------------------------------------------

test('/mimir/pages, /mimir/sources, /mimir/search, /mimir/graph and /mimir/ingest redirect to /mimir', async ({
  page,
}) => {
  for (const legacy of ['pages', 'sources', 'search', 'graph', 'ingest']) {
    await page.goto(`/mimir/${legacy}`);
    await expect(page).toHaveURL(/\/mimir$/);
  }
});

// ---------------------------------------------------------------------------
// /mimir/analytics — Analytics view
// ---------------------------------------------------------------------------

test('/mimir/analytics renders metric tiles and the category table', async ({ page }) => {
  await page.goto('/mimir/analytics');
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  await openServiceDiagnostics(page);
  await expect(page.getByTestId('eval-tiles')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('precision @5')).toBeVisible();
  await expect(page.getByTestId('category-table')).toBeVisible();
});

test('/mimir/analytics shows the query traffic log', async ({ page }) => {
  await page.goto('/mimir/analytics');
  await openServiceDiagnostics(page);
  await expect(page.getByTestId('query-log')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('zero-result-query').first()).toBeVisible();
});
