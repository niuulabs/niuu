import { test, expect } from '@playwright/test';

test('navigate to /volundr shows the session forge page', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByTestId('forge-page')).toBeVisible();
});

test('volundr overview shows KPI strip after data loads', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByTestId('forge-page')).toBeVisible();
  // Wait for KPI cards to appear — scope to the KPI region to avoid matching
  // other elements that contain these words (e.g. "Active sessions" heading,
  // LifecycleBadge "idle" pill in the sessions table).
  const kpiSection = page.getByRole('region', { name: 'Session KPIs' });
  await expect(kpiSection.getByText('active', { exact: true })).toBeVisible({ timeout: 5_000 });
  await expect(kpiSection.getByText('idle', { exact: true })).toBeVisible();
  await expect(kpiSection.getByText('total CPU', { exact: true })).toBeVisible();
});

test('volundr overview shows cluster health section', async ({ page }) => {
  await page.goto('/volundr');
  await expect(page.getByText('Cluster health')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('cluster-card').first()).toBeVisible();
});

test('volundr rail icon is visible and links to the page', async ({ page }) => {
  await page.goto('/');
  // The rail should show the ᚲ rune for Völundr.
  await expect(page.getByText('ᚲ')).toBeVisible();
});

test('navigating back from /volundr preserves the shell', async ({ page }) => {
  await page.goto('/hello');
  await expect(page.getByText('hello · smoke test')).toBeVisible();

  await page.goto('/volundr');
  await expect(page.getByText('Völundr · session forge')).toBeVisible();

  await page.goBack();
  await expect(page.getByText('hello · smoke test')).toBeVisible();
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
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 5_000 });
});

test('clicking a session in sidebar shows its detail inline', async ({ page }) => {
  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 8_000 });

  // Wait for pod entries to load and click one.
  await expect(page.getByTestId('pod-entry-ds-1')).toBeVisible({ timeout: 5_000 });
  await page.getByTestId('pod-entry-ds-1').click();

  // Detail page should render inline (no navigation).
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 5_000 });
});

// ---------------------------------------------------------------------------
// Session detail — six tabs
// ---------------------------------------------------------------------------

test('session detail page renders all six tabs', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('tab-overview')).toBeVisible();
  await expect(page.getByTestId('tab-terminal')).toBeVisible();
  await expect(page.getByTestId('tab-files')).toBeVisible();
  await expect(page.getByTestId('tab-exec')).toBeVisible();
  await expect(page.getByTestId('tab-events')).toBeVisible();
  await expect(page.getByTestId('tab-metrics')).toBeVisible();
});

test('session id is shown in the header', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-id-label')).toHaveText('ds-1', { timeout: 8_000 });
});

test('switching through all six tabs works', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });

  // Overview (default)
  await expect(page.getByTestId('tab-overview')).toHaveAttribute('aria-selected', 'true');

  // Terminal tab
  await page.getByTestId('tab-terminal').click();
  await expect(page.getByTestId('tab-terminal')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('terminal-container')).toBeVisible({ timeout: 5_000 });

  // Files tab
  await page.getByTestId('tab-files').click();
  await expect(page.getByTestId('tab-files')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('filetree-root')).toBeVisible({ timeout: 5_000 });

  // Exec tab
  await page.getByTestId('tab-exec').click();
  await expect(page.getByTestId('tab-exec')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('exec-tab')).toBeVisible();

  // Events tab
  await page.getByTestId('tab-events').click();
  await expect(page.getByTestId('tab-events')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('events-tab')).toBeVisible({ timeout: 3_000 });

  // Metrics tab
  await page.getByTestId('tab-metrics').click();
  await expect(page.getByTestId('tab-metrics')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('metrics-tab')).toBeVisible();
});

test('exec tab — run a command and see it in history', async ({ page }) => {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });

  await page.getByTestId('tab-exec').click();
  await expect(page.getByTestId('exec-input')).toBeVisible();

  // Type a command and run it.
  await page.getByTestId('exec-input').fill('echo hello');
  await page.getByTestId('exec-run-btn').click();

  // An exec entry should appear in the history.
  await expect(page.getByTestId('exec-entry').first()).toBeVisible({ timeout: 5_000 });
  // Scope to the entry to avoid strict-mode violations — the mock echoes
  // input back into the output <pre> as well as the command header span.
  await expect(page.getByTestId('exec-entry').first()).toContainText('echo hello');
});

test('archived session shows archived badge', async ({ page }) => {
  await page.goto('/volundr/session/ds-5/archived');
  await expect(page.getByTestId('session-archived-badge')).toBeVisible({ timeout: 8_000 });
});
