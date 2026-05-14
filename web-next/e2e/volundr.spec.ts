import { test, expect } from '@playwright/test';

test('navigate to /volundr shows the sessions page', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });
});

test('volundr overview shows KPI strip after data loads', async ({ page }) => {
  await page.goto('/volundr/overview');
  await expect(page.getByTestId('volundr-overview')).toBeVisible({ timeout: 8_000 });
  // Wait for KPI cards to appear — scope to the KPI region to avoid matching
  // other elements that contain these words (e.g. "Active sessions" heading,
  // LifecycleBadge "idle" pill in the sessions table).
  const kpiSection = page.getByRole('region', { name: 'Session KPIs' });
  await expect(kpiSection.getByText('active', { exact: true })).toBeVisible({ timeout: 5_000 });
  await expect(kpiSection.getByText('idle', { exact: true })).toBeVisible();
  await expect(kpiSection.getByText('total CPU', { exact: true })).toBeVisible();
});

test('volundr overview shows cluster health section', async ({ page }) => {
  await page.goto('/volundr/overview');
  await expect(page.getByTestId('volundr-overview')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByText('Cluster health')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('cluster-card').first()).toBeVisible();
});

test('volundr rail icon is visible and links to the page', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Völundr' })).toBeVisible();
});

test('navigating back from /volundr preserves the shell', async ({ page }) => {
  await page.goto('/ting');
  await expect(page.getByRole('heading', { name: 'Ting' })).toBeVisible({ timeout: 5_000 });

  await page.goto('/volundr');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });

  await page.goBack();
  await expect(page).toHaveURL(/\/ting$/);
  await expect(page.getByRole('heading', { name: 'Ting' })).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// Sessions page
// ---------------------------------------------------------------------------

test('sessions page shows pod list sidebar with state groups', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('pod-list-sidebar')).toBeVisible();
  await expect(page.getByTestId('pod-group-active')).toBeVisible();
});

test('sessions page auto-selects first running session and shows detail', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });

  // Detail page should be embedded inline for the auto-selected session.
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible({ timeout: 5_000 });
});

test('clicking a session in sidebar shows its detail inline', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });

  // Wait for pod entries to load and click one.
  await expect(page.getByTestId('pod-entry-ds-1')).toBeVisible({ timeout: 5_000 });
  await page.getByTestId('pod-entry-ds-1').click();

  // Detail page should render inline (no navigation).
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// Session detail — live capability-driven tabs
// ---------------------------------------------------------------------------

test('session detail page renders the live diff surface', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('#tab-diffs')).toBeVisible();
  await expect(page.locator('#tab-diffs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('diffs-tab')).toBeVisible();
});

test('session id is shown in the header', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-id-label')).toHaveAttribute('title', 'ds-1', {
    timeout: 8_000,
  });
});

test('session detail shows the default diff viewer state', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.locator('#tab-diffs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByText('changed files').first()).toBeVisible();
  await expect(page.getByText('Select a file to view changes')).toBeVisible();
});

test('archived session shows archived badge', async ({ page }) => {
  await page.goto('/volundr/session/ds-5/archived');
  await expect(page.getByTestId('live-session-detail-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByText('Archived')).toBeVisible();
});
