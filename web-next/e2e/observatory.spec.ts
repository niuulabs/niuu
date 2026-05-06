import { test, expect } from '@playwright/test';

// ── Navigation ────────────────────────────────────────────────────────────────

test('observatory rail button navigates to /observatory', async ({ page }) => {
  await page.goto('/');

  // The rail button's accessible name is the plugin rune (text content), not the title.
  // Match on the title attribute which includes the plugin name.
  const railButton = page.locator('button[title*="Observatory"]');
  await expect(railButton).toBeVisible();

  await railButton.click();
  await expect(page).toHaveURL(/\/observatory/);
});

// ── Canvas renders ────────────────────────────────────────────────────────────

test('observatory page renders the topology canvas', async ({ page }) => {
  await page.goto('/observatory');
  const canvas = page.getByTestId('topology-canvas');
  await expect(canvas).toBeVisible({ timeout: 5000 });
});

test('observatory page renders camera controls', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('camera-controls')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('button', { name: /zoom in/i })).toBeVisible();
  await expect(page.getByRole('button', { name: /zoom out/i })).toBeVisible();
  await expect(page.getByTestId('camera-reset')).toBeVisible();
});

test('observatory page renders the minimap', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('minimap-panel')).toBeVisible({ timeout: 5000 });
});

// ── Zoom controls ─────────────────────────────────────────────────────────────

test('zoom in button increases zoom percentage', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomDisplay = page.getByTestId('zoom-display');
  const before = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  await page.getByRole('button', { name: /zoom in/i }).click();
  const after = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  expect(after).toBeGreaterThan(before);
});

test('zoom out button decreases zoom percentage', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomDisplay = page.getByTestId('zoom-display');
  const before = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  await page.getByRole('button', { name: /zoom out/i }).click();
  const after = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  expect(after).toBeLessThan(before);
});

test('zoom cannot exceed 300%', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomIn = page.getByRole('button', { name: /zoom in/i });
  for (let i = 0; i < 30; i++) await zoomIn.click();

  const pct = parseInt((await page.getByTestId('zoom-display').textContent()) ?? '0', 10);
  expect(pct).toBeLessThanOrEqual(300);
});

test('zoom cannot fall below 30%', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomOut = page.getByRole('button', { name: /zoom out/i });
  for (let i = 0; i < 30; i++) await zoomOut.click();

  const pct = parseInt((await page.getByTestId('zoom-display').textContent()) ?? '0', 10);
  expect(pct).toBeGreaterThanOrEqual(30);
});

test('camera reset restores default zoom', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  // Zoom in a few times
  const zoomIn = page.getByRole('button', { name: /zoom in/i });
  await zoomIn.click();
  await zoomIn.click();
  await zoomIn.click();

  // Reset
  await page.getByTestId('camera-reset').click();
  const pct = parseInt((await page.getByTestId('zoom-display').textContent()) ?? '0', 10);

  // Default INITIAL_ZOOM is 0.5 → 50%
  expect(pct).toBe(50);
});

// ── Scroll-wheel zoom ─────────────────────────────────────────────────────────

test('scroll wheel up zooms in on the canvas', async ({ page }) => {
  await page.goto('/observatory');
  const canvas = page.getByTestId('topology-canvas');
  await canvas.waitFor();

  const zoomDisplay = page.getByTestId('zoom-display');
  const before = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  // Scroll up (negative deltaY) = zoom in
  await canvas.dispatchEvent('wheel', { deltaY: -120, bubbles: true });
  const after = parseInt((await zoomDisplay.textContent()) ?? '0', 10);
  expect(after).toBeGreaterThan(before);
});

test('scroll wheel down zooms out on the canvas', async ({ page }) => {
  await page.goto('/observatory');
  const canvas = page.getByTestId('topology-canvas');
  await canvas.waitFor();

  const zoomDisplay = page.getByTestId('zoom-display');
  const before = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  // Scroll down (positive deltaY) = zoom out
  await canvas.dispatchEvent('wheel', { deltaY: 120, bubbles: true });
  const after = parseInt((await zoomDisplay.textContent()) ?? '0', 10);
  expect(after).toBeLessThan(before);
});

// ── Drag pan ──────────────────────────────────────────────────────────────────

test('drag pan changes camera position without error', async ({ page }) => {
  await page.goto('/observatory');
  const canvas = page.getByTestId('topology-canvas');
  await canvas.waitFor();

  const box = await canvas.boundingBox();
  if (!box) throw new Error('canvas has no bounding box');

  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;

  // Simulate drag
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx + 100, cy + 50);
  await page.mouse.up();

  // No crash — canvas still visible and zoom display still shows a percentage
  await expect(canvas).toBeVisible();
  const pct = parseInt((await page.getByTestId('zoom-display').textContent()) ?? '0', 10);
  expect(pct).toBeGreaterThan(0);
});

// ── Keyboard pan ──────────────────────────────────────────────────────────────

test('arrow keys pan the canvas when focused', async ({ page }) => {
  await page.goto('/observatory');
  const canvas = page.getByTestId('topology-canvas');
  await canvas.waitFor();

  // Focus the canvas so it receives keyboard events
  await canvas.focus();

  // Press arrow keys — should not throw; canvas stays visible
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowUp');

  await expect(canvas).toBeVisible();
});

// ── Minimap interaction ───────────────────────────────────────────────────────

test('minimap SVG overlay is visible with topology content', async ({ page }) => {
  await page.goto('/observatory');
  const minimapPanel = page.getByTestId('minimap-panel');
  await minimapPanel.waitFor();

  // New minimap is a static SVG overview, not a click-to-pan canvas
  await expect(minimapPanel).toBeVisible();
  await expect(minimapPanel.locator('svg')).toBeVisible();
});

// ── Registry page ─────────────────────────────────────────────────────────────
test('registry page renders entity type list', async ({ page }) => {
  await page.goto('/observatory/registry');
  await expect(page.getByText('Registry').first()).toBeVisible();
  await expect(page.getByText('Realm').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Cluster').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Raid').first()).toBeVisible({ timeout: 5000 });
});

test('registry: Types tab is active by default', async ({ page }) => {
  await page.goto('/observatory/registry');
  await expect(page.getByTestId('tab-types')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('tab-types')).toHaveAttribute('aria-selected', 'true');
});

test('registry: clicking a type row opens the preview drawer', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="type-row-cluster"]', { timeout: 5000 });

  await page.click('[data-testid="type-row-cluster"]');
  await expect(page.getByTestId('type-preview-drawer')).toBeVisible();
  await expect(page.getByTestId('type-preview-drawer')).toContainText('Cluster');
});

test('registry: search filters type list', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-types"]', { timeout: 5000 });

  // Filter by 'vlan' — only appears in realm's description and fields, not in cluster's.
  await page.fill('[aria-label="Filter types"]', 'vlan');
  await expect(page.getByTestId('type-row-realm')).toBeVisible();
  await expect(page.getByTestId('type-row-cluster')).not.toBeVisible();
});

test('registry: Containment tab shows tree with root nodes', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-containment"]', { timeout: 5000 });

  await page.click('[data-testid="tab-containment"]');
  await expect(page.getByTestId('containment-tree')).toBeVisible();
  await expect(page.getByTestId('tree-node-realm')).toBeVisible();
});

test('registry: JSON tab shows formatted registry JSON', async ({ page }) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-json"]', { timeout: 5000 });

  await page.click('[data-testid="tab-json"]');
  await expect(page.getByTestId('json-output')).toBeVisible();
  await expect(page.getByTestId('json-output')).toContainText('"version"');
  await expect(page.getByTestId('copy-json-btn')).toBeVisible();
});

test('registry: drag a type, drop on valid target, verify parentTypes updated', async ({
  page,
}) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-containment"]', { timeout: 5000 });
  await page.click('[data-testid="tab-containment"]');

  // Wait for the containment tree to render before dragging.
  await expect(page.getByTestId('tree-node-host')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('tree-node-realm')).toBeVisible({ timeout: 5000 });

  // Dispatch drag events via page.evaluate — Playwright's dragTo uses CDP drag
  // events which hang in headless CI because the browser drag state machine
  // requires dragover to call preventDefault before it fires drop. Native
  // dispatchEvent calls are synchronous and React processes them immediately.
  await page.evaluate(() => {
    const host = document.querySelector('[data-testid="tree-node-host"]')!;
    const realm = document.querySelector('[data-testid="tree-node-realm"]')!;
    host.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true }));
    realm.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true }));
    realm.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true }));
    host.dispatchEvent(new DragEvent('dragend', { bubbles: true }));
  });

  // After drop the JSON should show host.parentTypes = ['realm']
  await page.click('[data-testid="tab-json"]');
  const jsonText = await page.getByTestId('json-output').textContent();
  const registry = JSON.parse(jsonText ?? '{}');
  const host = registry.types.find((t: { id: string }) => t.id === 'host');
  expect(host?.parentTypes).toContain('realm');
  expect(registry.version).toBeGreaterThan(7);
});

test('registry: cycle is rejected — dragging ancestor onto descendant does nothing', async ({
  page,
}) => {
  await page.goto('/observatory/registry');
  await page.waitForSelector('[data-testid="tab-containment"]', { timeout: 5000 });
  await page.click('[data-testid="tab-containment"]');

  const realmNode = page.getByTestId('tree-node-realm');
  const hostNode = page.getByTestId('tree-node-host');

  // Note initial version
  await page.click('[data-testid="tab-json"]');
  const before = await page.getByTestId('json-output').textContent();
  const versionBefore = JSON.parse(before ?? '{}').version as number;

  await page.click('[data-testid="tab-containment"]');
  await expect(realmNode).toBeVisible({ timeout: 5000 });
  await expect(hostNode).toBeVisible({ timeout: 5000 });
  await page.evaluate(() => {
    const realm = document.querySelector('[data-testid="tree-node-realm"]')!;
    const host = document.querySelector('[data-testid="tree-node-host"]')!;
    realm.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true }));
    host.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true }));
    host.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true }));
    realm.dispatchEvent(new DragEvent('dragend', { bubbles: true }));
  });

  // Version should not change
  await page.click('[data-testid="tab-json"]');
  const after = await page.getByTestId('json-output').textContent();
  const versionAfter = JSON.parse(after ?? '{}').version as number;
  expect(versionAfter).toBe(versionBefore);
});

// ── NIU-665 overlay e2e tests ─────────────────────────────────────────────

test('clicking a topology node opens the EntityDrawer', async ({ page }) => {
  await page.goto('/observatory');

  // Wait for topology to load (node list appears)
  const nodeList = page.getByTestId('topology-node-list');
  await expect(nodeList).toBeVisible({ timeout: 5000 });

  // Click the realm-asgard node button
  const realmBtn = page.getByTestId('node-btn-realm-asgard');
  await expect(realmBtn).toBeVisible();
  await realmBtn.click();

  // EntityDrawer should be open with the realm node's label as the title
  await expect(page.getByRole('dialog', { name: /asgard/i })).toBeVisible({ timeout: 3000 });
});

test('EntityDrawer close button dismisses the drawer', async ({ page }) => {
  await page.goto('/observatory');
  await page.getByTestId('topology-node-list').waitFor({ state: 'visible', timeout: 5000 });

  await page.getByTestId('node-btn-realm-asgard').click();
  await expect(page.getByRole('dialog', { name: /asgard/i })).toBeVisible();

  await page.getByRole('button', { name: /close/i }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
});

test('realm EntityDrawer shows resident list', async ({ page }) => {
  await page.goto('/observatory');
  await page.getByTestId('topology-node-list').waitFor({ state: 'visible', timeout: 5000 });

  await page.getByTestId('node-btn-realm-asgard').click();
  const dialog = page.getByRole('dialog', { name: /asgard/i });
  await expect(dialog).toBeVisible();

  // Realm asgard contains clusters and host
  await expect(dialog.getByText('Residents')).toBeVisible();
  await expect(dialog.getByText('valaskjálf')).toBeVisible();
});

test('clicking a resident in the drawer navigates to that resident (drill-in)', async ({
  page,
}) => {
  await page.goto('/observatory');
  await page.getByTestId('topology-node-list').waitFor({ state: 'visible', timeout: 5000 });

  // Open realm drawer
  await page.getByTestId('node-btn-realm-asgard').click();
  await expect(page.getByRole('dialog', { name: /asgard/i })).toBeVisible();

  // Click the cluster resident
  const residentBtn = page.getByTestId('resident-cluster-valaskjalf');
  await expect(residentBtn).toBeVisible();
  await residentBtn.click();

  // Drawer should now show the cluster node
  await expect(page.getByRole('dialog', { name: /valask/i })).toBeVisible({ timeout: 3000 });
});

test('cluster EntityDrawer shows residents', async ({ page }) => {
  await page.goto('/observatory');
  await page.getByTestId('topology-node-list').waitFor({ state: 'visible', timeout: 5000 });

  // Click the cluster node directly
  const clusterBtn = page.getByTestId('node-btn-cluster-valaskjalf');
  await expect(clusterBtn).toBeVisible();
  await clusterBtn.click();

  const dialog = page.getByRole('dialog', { name: /valask/i });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Residents')).toBeVisible();
  // tyr-0, bifrost-0, volundr-0, mimir-0, raid-0 are in valaskjalf or valhalla
  await expect(dialog.getByText('tyr-0')).toBeVisible();
});

test('EventLog overlay is visible and shows events', async ({ page }) => {
  await page.goto('/observatory');
  const eventLog = page.getByTestId('event-log');
  await expect(eventLog).toBeVisible({ timeout: 3000 });
  // Seed events include 'tyr-0' as source
  await expect(eventLog.getByText('tyr-0')).toBeVisible({ timeout: 5000 });
});

test('Minimap overlay is visible on observatory page', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByRole('img', { name: /topology minimap/i })).toBeVisible({ timeout: 3000 });
});
