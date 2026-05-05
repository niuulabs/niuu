import { test, expect } from '@playwright/test';

test('navigate to /mimir renders the page header', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByText('Mímir').first()).toBeVisible();
  await expect(page.getByText('the well of knowledge').first()).toBeVisible();
});

test('/mimir renders tab navigation', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Pages' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Sources' })).toBeVisible();
});

test('Overview tab shows KPI strip', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByText('pages').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('sources').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('lint issues').first()).toBeVisible({ timeout: 5000 });
});

test('Overview tab shows mount cards', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByRole('article', { name: /mount local/ })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('article', { name: /mount shared/ })).toBeVisible({
    timeout: 5000,
  });
});

test('Overview tab shows recent-writes feed', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByRole('log', { name: /recent writes/ })).toBeVisible({ timeout: 5000 });
});

test('switching to Pages tab shows the file tree', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Pages' }).click();
  await expect(page.getByRole('complementary', { name: /page tree/ })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByText('arch/').first()).toBeVisible({ timeout: 5000 });
});

test('can open a page and see its title', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Pages' }).click();
  // Click on the arch/ dir then overview leaf
  const archDir = page.getByText('arch/').first();
  await expect(archDir).toBeVisible({ timeout: 5000 });
  // Leaf node for overview
  await page
    .getByRole('button', { name: /overview/ })
    .first()
    .click();
  await expect(page.getByText('Architecture Overview')).toBeVisible({ timeout: 5000 });
});

test('edit a zone and cancel restores read mode', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Pages' }).click();
  // Open architecture overview
  await page
    .getByRole('button', { name: /overview/ })
    .first()
    .click();
  await expect(page.getByText('Architecture Overview')).toBeVisible({ timeout: 5000 });
  // Click edit on first zone
  const editBtn = page.getByRole('button', { name: /edit key-facts zone/ });
  await expect(editBtn).toBeVisible({ timeout: 5000 });
  await editBtn.click();
  // Zone edit area is visible
  await expect(page.getByRole('textbox', { name: /zone edit area/ })).toBeVisible();
  // Cancel returns to read mode
  await page.getByRole('button', { name: /cancel edit/ }).click();
  await expect(page.getByRole('textbox', { name: /zone edit area/ })).not.toBeVisible();
});

test('save a zone shows destination mount in success banner', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Pages' }).click();
  await page
    .getByRole('button', { name: /overview/ })
    .first()
    .click();
  await expect(page.getByText('Architecture Overview')).toBeVisible({ timeout: 5000 });
  const editBtn = page.getByRole('button', { name: /edit key-facts zone/ });
  await expect(editBtn).toBeVisible({ timeout: 5000 });
  await editBtn.click();
  await page.getByRole('button', { name: /save key-facts zone/ }).click();
  // After save, a success banner with destination mount(s) should appear
  await expect(page.getByText(/saved →/).first()).toBeVisible({ timeout: 5000 });
});

test('switching to Sources tab shows origin filter tabs', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Sources' }).click();
  await expect(page.getByRole('tab', { name: 'all' })).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('tab', { name: 'web' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'file' })).toBeVisible();
});

test('Sources tab shows source count', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Sources' }).click();
  await expect(page.getByText(/sources/)).toBeVisible({ timeout: 5000 });
});

test('filtering sources by origin updates the count', async ({ page }) => {
  await page.goto('/mimir');
  await page.getByRole('tab', { name: 'Sources' }).click();
  await expect(page.getByText('7 sources')).toBeVisible({ timeout: 5000 });
  await page.getByRole('tab', { name: 'web' }).click();
  await expect(page.getByText(/1 source/)).toBeVisible({ timeout: 3000 });
});

test('mimir rune is visible in the rail', async ({ page }) => {
  await page.goto('/mimir');
  await expect(page.getByText('ᛗ').first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// /mimir/search — Search view
// ---------------------------------------------------------------------------

test('/mimir/search renders the search page', async ({ page }) => {
  await page.goto('/mimir/search');
  await expect(page.getByRole('heading', { name: /search/i })).toBeVisible();
});

test('/mimir/search shows search input', async ({ page }) => {
  await page.goto('/mimir/search');
  await expect(page.getByRole('searchbox')).toBeVisible();
});

test('/mimir/search shows mode toggle buttons', async ({ page }) => {
  await page.goto('/mimir/search');
  await expect(page.getByRole('button', { name: /full-text/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /semantic/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /hybrid/i })).toBeVisible();
});

test('/mimir/search — typing a query returns results', async ({ page }) => {
  await page.goto('/mimir/search');
  await page.getByRole('searchbox').fill('architecture');
  await expect(page.getByTestId('search-result').first()).toBeVisible({ timeout: 5000 });
});

test('/mimir/search — toggling mode changes active button', async ({ page }) => {
  await page.goto('/mimir/search');
  const ftsBtn = page.getByRole('button', { name: /full-text/i });
  await ftsBtn.click();
  await expect(ftsBtn).toHaveAttribute('aria-pressed', 'true');
});

test('/mimir/search — search result shows title and path', async ({ page }) => {
  await page.goto('/mimir/search');
  await page.getByRole('searchbox').fill('architecture');
  const firstResult = page.getByTestId('search-result').first();
  await expect(firstResult).toBeVisible({ timeout: 5000 });
  await expect(firstResult.locator('.search-page__result-title')).toBeVisible();
  await expect(firstResult.locator('.search-page__result-path')).toBeVisible();
});

test('/mimir/search — searching across mounts (hybrid mode)', async ({ page }) => {
  await page.goto('/mimir/search');
  // Hybrid is default; switch to fts and back to hybrid to verify toggle works
  await page.getByRole('button', { name: /full-text/i }).click();
  await page.getByRole('button', { name: /hybrid/i }).click();
  await expect(page.getByRole('button', { name: /hybrid/i })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.getByRole('searchbox').fill('api');
  await expect(page.getByTestId('search-result').first()).toBeVisible({ timeout: 5000 });
});

// ---------------------------------------------------------------------------
// /mimir/graph — Graph view
// ---------------------------------------------------------------------------

test('/mimir/graph renders the graph page', async ({ page }) => {
  await page.goto('/mimir/graph');
  await expect(page.getByRole('heading', { name: /knowledge graph/i })).toBeVisible();
});

test('/mimir/graph shows the graph SVG after load', async ({ page }) => {
  await page.goto('/mimir/graph');
  await expect(page.getByRole('img', { name: /knowledge graph/i })).toBeVisible({
    timeout: 5000,
  });
});

test('/mimir/graph shows node and edge counts', async ({ page }) => {
  await page.goto('/mimir/graph');
  await expect(page.getByText(/nodes/)).toBeVisible({ timeout: 5000 });
  await expect(page.getByText(/edges/)).toBeVisible({ timeout: 5000 });
});

test('/mimir/graph has hop selector', async ({ page }) => {
  await page.goto('/mimir/graph');
  await expect(page.getByRole('group', { name: /hop count/i })).toBeVisible();
});

// ---------------------------------------------------------------------------
// /mimir/entities — Entities view
// ---------------------------------------------------------------------------

test('/mimir/entities renders the entities page', async ({ page }) => {
  await page.goto('/mimir/entities');
  await expect(page.getByRole('heading', { name: /entities/i })).toBeVisible();
});

test('/mimir/entities shows entity items after load', async ({ page }) => {
  await page.goto('/mimir/entities');
  await expect(page.getByTestId('entity-item').first()).toBeVisible({ timeout: 5000 });
});

test('/mimir/entities shows entity type filter buttons', async ({ page }) => {
  await page.goto('/mimir/entities');
  await expect(page.getByRole('button', { name: /all/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /org/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /concept/i })).toBeVisible();
});

test('/mimir/entities — clicking a kind filter updates active state', async ({ page }) => {
  await page.goto('/mimir/entities');
  const orgBtn = page.getByRole('button', { name: /org/i });
  await orgBtn.click();
  await expect(orgBtn).toHaveAttribute('aria-pressed', 'true');
});
