import { test, expect, type Page } from '@playwright/test';
import { CANVAS } from '../packages/plugin-observatory/src/ui/TopologyCanvas/config';

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

test('zoom clamps at the configured floor', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomOut = page.getByRole('button', { name: /zoom out/i });
  for (let i = 0; i < 30; i++) await zoomOut.click();

  // Asserted against the config rather than a literal, so the floor can be
  // tuned for a larger topology without silently breaking this expectation.
  const pct = parseInt((await page.getByTestId('zoom-display').textContent()) ?? '0', 10);
  expect(pct).toBeGreaterThanOrEqual(Math.round(CANVAS.ZOOM_MIN * 100));
  expect(pct).toBeLessThan(Math.round(CANVAS.ZOOM_MAX * 100));
});

test('camera reset restores default zoom', async ({ page }) => {
  await page.goto('/observatory');
  await page.waitForSelector('[data-testid="zoom-display"]');

  const zoomDisplay = page.getByTestId('zoom-display');
  const initialPct = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  // Zoom in a few times
  const zoomIn = page.getByRole('button', { name: /zoom in/i });
  await zoomIn.click();
  await zoomIn.click();
  await zoomIn.click();

  // Reset
  await page.getByTestId('camera-reset').click();
  const pct = parseInt((await zoomDisplay.textContent()) ?? '0', 10);

  expect(pct).toBe(initialPct);
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

test('minimap overlay is visible with topology content', async ({ page }) => {
  await page.goto('/observatory');
  const minimapPanel = page.getByTestId('minimap-panel');
  await minimapPanel.waitFor();

  // The minimap is a canvas the render loop paints, not an SVG document.
  await expect(minimapPanel).toBeVisible();
  await expect(minimapPanel.locator('canvas')).toBeVisible();
});

// ── Registry page ─────────────────────────────────────────────────────────────
test('registry page renders entity type list', async ({ page }) => {
  await page.goto('/observatory/registry');
  await expect(page.getByText('Registry').first()).toBeVisible();
  await expect(page.getByText('Realm').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Cluster').first()).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('Run').first()).toBeVisible({ timeout: 5000 });
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

// ── Inspector ─────────────────────────────────────────────────────────────────

/**
 * Select a node the way the keyboard and screen-reader path does.
 *
 * The canvas is a single element with no DOM inside it, so the hidden node
 * list is the only way to reach a specific entity without hit-testing pixels —
 * and it is a real path an operator uses, not a test-only hook.
 */
async function selectNode(page: Page, testId: string) {
  const nodeButton = page.getByTestId(testId);
  await expect(nodeButton).toBeAttached();
  await nodeButton.evaluate((element) => {
    (element as HTMLButtonElement).click();
  });
}

test('the inspector opens empty and says so', async ({ page }) => {
  await page.goto('/observatory');
  // An empty panel with no explanation reads as a panel that failed to load.
  await expect(page.getByTestId('inspector-empty')).toBeVisible({ timeout: 5000 });
});

test('selecting a node fills the inspector with it', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('topology-node-list')).toBeAttached({ timeout: 5000 });

  await selectNode(page, 'node-btn-realm-asgard');

  const inspector = page.getByTestId('inspector');
  await expect(inspector).toBeVisible({ timeout: 3000 });
  await expect(inspector.getByRole('heading', { name: /asgard/i })).toBeVisible();
  await expect(page.getByTestId('inspector-kind')).toContainText(/realm/i);
});

test('selecting the same node again puts it down', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('topology-node-list')).toBeAttached({ timeout: 5000 });

  await selectNode(page, 'node-btn-realm-asgard');
  await expect(page.getByTestId('inspector')).toBeVisible({ timeout: 3000 });

  // The rail and the hidden list report `aria-pressed`, so pressing again has
  // to release — otherwise there is no way to stop looking at something.
  await selectNode(page, 'node-btn-realm-asgard');
  await expect(page.getByTestId('inspector-empty')).toBeVisible();
});

test('the inspector docks beside the stage rather than covering it', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-cluster-valaskjalf');

  await expect(page.getByTestId('inspector')).toBeVisible({ timeout: 3000 });
  // A floating drawer covered the canvas it was describing; this one is a
  // column of the page grid, so both are readable at once.
  await expect(page.getByTestId('topology-canvas')).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Inspector' })).toBeVisible();
});

test('the inspector lists what a node is connected to, and can follow a link', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-ravn-huginn');

  const peers = page.getByTestId('inspector-connected');
  await expect(peers).toBeVisible({ timeout: 5000 });

  const first = peers.locator('[data-testid^="insp-peer-"]').first();
  const name = (await first.innerText()).split('\n')[0]!.trim();
  await first.click();

  // Following a link moves the inspector to that entity. The name is the h2;
  // the blocks below it carry h3s of their own.
  await expect(page.getByTestId('inspector').locator('h2')).toContainText(name, { timeout: 3000 });
});

test('a resident publishes an A2A card, on its own tab', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-ravn-huginn');

  // The card is a second reading of the same entity, so it lives behind a tab
  // rather than below the fold of the first one.
  await page.getByTestId('insp-tab-card').click();
  const card = page.getByTestId('agent-card');
  await expect(card).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('agent-card-url')).toContainText('agent-card.json');
  await expect(page.getByTestId('agent-card-skills')).toBeVisible();
});

test('the JSON tab shows the card exactly as it was served', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-ravn-huginn');

  await page.getByTestId('insp-tab-json').click();
  const json = page.getByTestId('agent-card-json');
  await expect(json).toBeVisible({ timeout: 5000 });
  expect(JSON.parse((await json.textContent()) ?? '{}')).toHaveProperty('cardUrl');
});

test('a workflow session publishes a card of its own kind', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-run-research');

  await page.getByTestId('insp-tab-card').click();
  await expect(page.getByTestId('agent-card')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('agent-card-session')).toBeVisible();
});

test('a node that publishes no card shows no A2A panel', async ({ page }) => {
  await page.goto('/observatory');
  await selectNode(page, 'node-btn-host-saehrimnir');

  await expect(page.getByTestId('inspector')).toBeVisible({ timeout: 5000 });
  // A host simply has no card. The panel says nothing rather than showing an
  // empty one, which would read as a card that failed to load.
  await expect(page.getByTestId('agent-card')).toHaveCount(0);
});

// ── Signal feed ───────────────────────────────────────────────────────────────

test('the signal feed is docked under the stage and carries events', async ({ page }) => {
  await page.goto('/observatory');
  const ticker = page.getByTestId('signal-ticker');
  await expect(ticker).toBeVisible({ timeout: 3000 });
  await expect(ticker.locator('[data-testid^="signal-"]').first()).toBeVisible({ timeout: 5000 });
});

test('Minimap overlay is visible on observatory page', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByRole('img', { name: /topology minimap/i })).toBeVisible({ timeout: 3000 });
});

// ── Connection layers ─────────────────────────────────────────────────────────

test('layer filters toggle and can be restored', async ({ page }) => {
  await page.goto('/observatory');

  const memory = page.getByTestId('layer-toggle-memory');
  await expect(memory).toBeVisible({ timeout: 5000 });
  await expect(memory).toHaveAttribute('aria-pressed', 'true');

  await memory.click();
  await expect(memory).toHaveAttribute('aria-pressed', 'false');

  // Other layers are unaffected by hiding one.
  await expect(page.getByTestId('layer-toggle-mesh')).toHaveAttribute('aria-pressed', 'true');

  const showAll = page.getByTestId('filter-all');
  await expect(showAll).toBeEnabled();
  await showAll.click();
  await expect(memory).toHaveAttribute('aria-pressed', 'true');
  await expect(showAll).toBeDisabled();
});

test('the Observatory opens calm, and calm is one click away again', async ({ page }) => {
  await page.goto('/observatory');

  // Platform wiring and telemetry dominate by edge count and say the least
  // about what the estate is doing, so the first look leaves them down.
  await expect(page.getByTestId('layer-toggle-platform')).toHaveAttribute('aria-pressed', 'false', {
    timeout: 5000,
  });
  await expect(page.getByTestId('layer-toggle-observability')).toHaveAttribute(
    'aria-pressed',
    'false',
  );

  await page.getByTestId('filter-all').click();
  await expect(page.getByTestId('layer-toggle-platform')).toHaveAttribute('aria-pressed', 'true');

  await page.getByTestId('filter-calm').click();
  await expect(page.getByTestId('layer-toggle-platform')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('layer-toggle-mesh')).toHaveAttribute('aria-pressed', 'true');
});

test('compute classes can be switched off and back on', async ({ page }) => {
  await page.goto('/observatory');

  const own = page.getByTestId('compute-toggle-own');
  await expect(own).toHaveAttribute('aria-pressed', 'true', { timeout: 5000 });
  await own.click();
  await expect(own).toHaveAttribute('aria-pressed', 'false');

  await page.getByTestId('filter-none').click();
  await expect(page.getByTestId('compute-toggle-k8s')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('layer-toggle-mesh')).toHaveAttribute('aria-pressed', 'false');
});

test('every connection layer offers a toggle with a count', async ({ page }) => {
  await page.goto('/observatory');
  for (const layer of ['mesh', 'memory', 'inference', 'platform', 'observability', 'signals']) {
    await expect(page.getByTestId(`layer-toggle-${layer}`)).toBeVisible({ timeout: 5000 });
  }
});

// ── Rail sections ─────────────────────────────────────────────────────────────

test('rail sections collapse and expand independently', async ({ page }) => {
  await page.goto('/observatory');

  const residents = page.getByTestId('subnav-section-rail-residents');
  await expect(residents).toBeVisible({ timeout: 5000 });
  await expect(residents).toHaveAttribute('open', '');

  await page.getByTestId('subnav-toggle-rail-residents').click();
  await expect(residents).not.toHaveAttribute('open', '');
  await expect(page.getByTestId('subnav-section-rail-clusters')).toHaveAttribute('open', '');

  await page.getByTestId('subnav-toggle-rail-residents').click();
  await expect(residents).toHaveAttribute('open', '');
});

test('picking a resident from the rail selects it on the stage', async ({ page }) => {
  await page.goto('/observatory');

  const row = page.getByTestId('rail-row-ravn-huginn');
  await expect(row).toBeVisible({ timeout: 5000 });
  await row.click();

  await expect(row).toHaveAttribute('aria-pressed', 'true');
  await expect(
    page.getByTestId('inspector').getByRole('heading', { name: /huginn/i }),
  ).toBeVisible();
});

// ── Present mode ──────────────────────────────────────────────────────────────

test('present mode stands the rail, inspector and feed down', async ({ page }) => {
  await page.goto('/observatory');

  const stage = page.getByTestId('observatory-page');
  await expect(stage).toHaveAttribute('data-presenting', 'false', { timeout: 5000 });

  await page.getByTestId('present-toggle').click();
  await expect(stage).toHaveAttribute('data-presenting', 'true');
  // The rail is not ours to collapse: an empty subnav is what the shell closes.
  await expect(page.getByTestId('observatory-subnav')).toHaveCount(0);

  await page.getByTestId('present-toggle').click();
  await expect(stage).toHaveAttribute('data-presenting', 'false');
});

// ── Motion ────────────────────────────────────────────────────────────────────

test('the stage can be held still, and the choice outlives a reload', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('topology-canvas')).toBeVisible({ timeout: 5000 });

  const hold = page.getByTestId('motion-toggle');
  await expect(hold).toHaveAttribute('aria-pressed', 'false');
  await expect(hold).toHaveAccessibleName('Hold the stage still');

  await hold.click();
  await expect(hold).toHaveAttribute('aria-pressed', 'true');
  await expect(hold).toHaveAccessibleName('Let the stage move');
  // Holding the stage stills what is drawn on it; it does not take it away.
  await expect(page.getByTestId('topology-canvas')).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('motion-toggle')).toHaveAttribute('aria-pressed', 'true', {
    timeout: 5000,
  });

  await page.getByTestId('motion-toggle').click();
  await expect(page.getByTestId('motion-toggle')).toHaveAttribute('aria-pressed', 'false');
});

// ── 3D view ───────────────────────────────────────────────────────────────────

/**
 * Put the estate on the 3D stage and wait for the model to arrive.
 *
 * The toggle lives in the shell's topbar slot rather than on the page, which is
 * exactly what these specs should be checking: the two views share the rail,
 * the inspector and the filters, and only the stage changes.
 */
async function switchTo3D(page: Page) {
  await page.getByTestId('view-toggle-3d').click();
  await expect(page.getByTestId('topology-scene3d')).toBeVisible({ timeout: 5000 });
}

test('observatory switches between the plan and the model', async ({ page }) => {
  await page.goto('/observatory');
  await expect(page.getByTestId('topology-canvas')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('view-toggle-2d')).toHaveAttribute('aria-pressed', 'true');

  await switchTo3D(page);
  await expect(page.getByTestId('topology-canvas')).toHaveCount(0);
  await expect(page.getByTestId('view-toggle-3d')).toHaveAttribute('aria-pressed', 'true');

  await page.getByTestId('view-toggle-2d').click();
  await expect(page.getByTestId('topology-canvas')).toBeVisible();
});

test('3D view draws into a WebGL canvas and offers its camera controls', async ({ page }) => {
  await page.goto('/observatory');
  await switchTo3D(page);

  await expect(page.getByTestId('topology-scene3d-host').locator('canvas')).toBeVisible();
  await expect(page.getByTestId('camera-controls-3d')).toBeVisible();
  // The gesture set differs from the plan's, and nothing else on screen says so.
  await expect(page.getByTestId('scene3d-hint')).toContainText('orbit');
  await expect(page.getByTestId('minimap-panel-3d')).toBeVisible();
});

test('3D view zooms from its camera controls', async ({ page }) => {
  await page.goto('/observatory');
  await switchTo3D(page);

  const readout = page.getByTestId('zoom-display-3d');
  await expect(readout).toBeVisible();
  const before = await readout.textContent();

  await page.getByRole('button', { name: /zoom in/i }).click();
  await expect(readout).not.toHaveText(before ?? '', { timeout: 3000 });
});

test('3D view orbits on drag without losing the stage', async ({ page }) => {
  await page.goto('/observatory');
  await switchTo3D(page);

  const stage = page.getByTestId('topology-scene3d-host');
  const box = (await stage.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 160, box.y + box.height / 2 - 40, { steps: 8 });
  await page.mouse.up();

  await expect(stage).toHaveAttribute('data-dragging', 'false');
  await expect(stage.locator('canvas')).toBeVisible();
});

test('3D view keeps the inspector answering for a selection made in the rail', async ({ page }) => {
  await page.goto('/observatory');
  await switchTo3D(page);

  // The hidden node list is the keyboard and screen-reader path into the same
  // selection the canvas offers, and it must work on either stage.
  const nodeButton = page.getByTestId('topology-node-list').getByRole('button').first();
  await expect(nodeButton).toBeAttached();
  await nodeButton.evaluate((element) => (element as HTMLButtonElement).click());

  await expect(page.getByTestId('inspector')).toBeVisible({ timeout: 3000 });
});

test('3D view survives a filter change without emptying the stage', async ({ page }) => {
  await page.goto('/observatory');
  await switchTo3D(page);

  await page.getByTestId('layer-toggle-platform').click();
  await expect(page.getByTestId('topology-scene3d-host').locator('canvas')).toBeVisible();
});
