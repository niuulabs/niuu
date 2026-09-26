import { test, expect, type Page } from '@playwright/test';

/**
 * The Health surface is per-instance: Doctor and Lint only run once an
 * instance is picked from the "Instance" combobox.
 */
async function selectInstance(page: Page, name: string) {
  const combobox = page.getByRole('combobox', { name: 'Instance' });
  await expect(combobox).toHaveText('Select an instance');
  await combobox.click();
  await page.getByRole('option', { name }).click();
  await expect(combobox).toHaveText(name);
}

/**
 * Analytics keeps the service-wide reports (eval, query traffic, dreams)
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
  const railButton = page.getByRole('button', { name: 'Mímir', exact: true });
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
// /mimir/health — consolidated Doctor + Lint surface (legacy /lint deep link)
// ---------------------------------------------------------------------------

test('/mimir/health stacks the doctor checklist above the lint detail', async ({ page }) => {
  await page.goto('/mimir/health');
  await expect(page.getByTestId('health-page')).toBeVisible();
  await selectInstance(page, 'local');
  await expect(page.getByRole('heading', { name: /doctor/i })).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[aria-label="Lint checks"]')).toBeVisible();
});

test('/mimir/lint deep link lands on the Health page', async ({ page }) => {
  await page.goto('/mimir/lint');
  await expect(page.getByTestId('health-page')).toBeVisible();
});

test('/mimir/dreams deep link lands on Analytics with the dream section', async ({ page }) => {
  await page.goto('/mimir/dreams');
  await expect(page.getByRole('heading', { name: /analytics/i })).toBeVisible({ timeout: 5000 });
  await openServiceDiagnostics(page);
  await expect(page.getByRole('heading', { name: /dreams/i })).toBeVisible();
  await expect(page.getByTestId('dream-cycle').first()).toBeVisible();
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

// ---------------------------------------------------------------------------
// /mimir/doctor — Doctor view
// ---------------------------------------------------------------------------

test('/mimir/doctor renders the scored checklist', async ({ page }) => {
  await page.goto('/mimir/doctor');
  await selectInstance(page, 'local');
  await expect(page.getByRole('heading', { name: 'Doctor' })).toBeVisible();
  await expect(page.getByTestId('doctor-score')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('doctor-check').first()).toBeVisible();
});

test('/mimir/doctor — run fixes flows through the confirm dialog', async ({ page }) => {
  await page.goto('/mimir/doctor');
  await selectInstance(page, 'local');
  await expect(page.getByTestId('doctor-score')).toHaveText('3/6', { timeout: 5000 });

  await page.getByTestId('run-fixes-btn').click();
  await expect(page.getByText('Run automatic fixes?')).toBeVisible();
  await page.getByTestId('confirm-fix').click();

  await expect(page.getByTestId('doctor-score')).toHaveText('5/6', { timeout: 5000 });
  await expect(page.getByTestId('run-fixes-btn')).toHaveCount(0);
});
