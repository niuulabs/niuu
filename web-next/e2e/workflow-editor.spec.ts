import { expect, test, type Page } from '@playwright/test';

// Transport fixtures exercise the production app/adapter/editor. Live proof against
// the bundled workflows is captured separately against the isolated platform.
const workflowId = '362b143f-c5a4-492b-a5f5-0b15fc2c7201';
const nodes = [
  {
    id: 'request',
    kind: 'trigger',
    label: 'Review requested',
    source: 'manual',
    dispatchEvent: 'review.requested',
    position: { x: 30, y: 100 },
  },
  {
    id: 'review',
    kind: 'stage',
    label: 'Review the change',
    runId: null,
    personaIds: ['reviewer'],
    stageMembers: [
      {
        personaId: 'reviewer',
        model: 'gpt-5.5',
        budget: 24,
        consumesEventTypes: ['review.requested', 'review.changes_requested'],
        eventFilters: {},
      },
    ],
    executionMode: 'sequential',
    joinMode: 'all',
    maxConcurrent: 1,
    position: { x: 360, y: 100 },
  },
  {
    id: 'approval',
    kind: 'gate',
    label: 'Maintainer approval',
    condition: '',
    mode: 'human_approval',
    pendingBehavior: 'help_needed',
    approvalEvent: 'gate.approved',
    changesRequestedEvent: 'gate.changes_requested',
    instructions: '',
    autoForwardAfter: '',
    position: { x: 710, y: 100 },
  },
  { id: 'complete', kind: 'end', label: 'Complete', position: { x: 1020, y: 100 } },
];
const edges = [
  {
    id: 'request-review',
    source: 'request',
    target: 'review',
    label: 'review.requested -> review.requested',
  },
  {
    id: 'review-approval',
    source: 'review',
    target: 'approval',
    label: 'review.completed -> review.completed',
  },
  {
    id: 'approval-complete',
    source: 'approval',
    target: 'complete',
    label: 'gate.approved -> gate.approved',
  },
  {
    id: 'review-retry',
    source: 'review',
    target: 'review',
    label: 'review.changes_requested -> review.changes_requested',
  },
];

async function configure(
  page: Page,
  options: {
    fail?: boolean;
    loading?: Promise<void>;
    readOnly?: boolean;
    canEdit?: boolean;
    origin?: 'bundled' | 'authored';
    versionLoading?: Promise<void>;
    childCatalog?: boolean;
    name?: string;
  } = {},
) {
  await page.route('**/config*.json', async (route) => {
    const config = await (await route.fetch()).json();
    config.demoMode = true;
    config.services['ting.workflows'] = { mode: 'http', baseUrl: '/api/v1/ting' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/ting/workflows', async (route) => {
    if (options.loading) await options.loading;
    if (options.fail) {
      await route.fulfill({ status: 500, json: { detail: 'Workflow catalog unavailable' } });
      return;
    }
    await route.fulfill({
      json: [
        {
          id: workflowId,
          name: options.name ?? 'Editor regression workflow',
          description: 'Socket geometry and editing',
          version: '1.0.0',
          scope: 'user',
          owner_id: 'test-owner',
          read_only: options.readOnly ?? false,
          can_edit: options.canEdit ?? true,
          origin: options.origin ?? 'authored',
          nodes,
          edges,
          graph: { nodes, edges },
          tags: [],
          persona_dependencies: {},
          workflow_dependencies: {},
          requirements: [],
          revision: 'test-revision',
          document_revision: 'sha256:workflow-100',
          is_head: true,
        },
        ...(options.childCatalog
          ? [
              {
                id: '362b143f-c5a4-492b-a5f5-0b15fc2c7202',
                name: 'Reusable review',
                description: 'An existing child workflow',
                version: '2.1.0',
                scope: 'system',
                read_only: true,
                nodes,
                edges,
                graph: { nodes, edges },
                revision: 'aggregate-head-token',
                document_revision: 'sha256:child-210',
              },
            ]
          : []),
      ],
    });
  });
  await page.route(`**/api/v1/ting/workflows/${workflowId}/versions`, (route) =>
    route.fulfill({
      json: [
        {
          version: '1.0.0',
          document_revision: 'sha256:workflow-100',
          created_at: '2026-09-20T12:00:00Z',
          is_head: true,
          based_on_revision: 'sha256:workflow-090',
          origin: options.origin ?? 'authored',
        },
        {
          version: '0.9.0',
          document_revision: 'sha256:workflow-090',
          created_at: '2026-09-19T12:00:00Z',
          is_head: false,
          based_on_revision: null,
          origin: options.origin ?? 'authored',
        },
      ],
    }),
  );
  await page.route(`**/api/v1/ting/workflows/${workflowId}/versions/0.9.0`, async (route) => {
    if (options.versionLoading) await options.versionLoading;
    await route.fulfill({
      json: {
        id: workflowId,
        name: 'Editor regression workflow',
        description: 'Historical socket geometry and editing',
        version: '0.9.0',
        scope: 'user',
        owner_id: 'test-owner',
        read_only: true,
        can_edit: options.canEdit ?? true,
        origin: options.origin ?? 'authored',
        nodes,
        edges,
        graph: { nodes, edges },
        tags: [],
        persona_dependencies: {},
        workflow_dependencies: {},
        requirements: [],
        revision: 'test-revision',
        document_revision: 'sha256:workflow-090',
        is_head: false,
      },
    });
  });
}

async function openEditor(page: Page) {
  await page.goto(`/ting/workflows?id=${workflowId}`);
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await page.getByTestId('zoom-fit').click();
}

async function editWorkflow(page: Page) {
  await page.getByTestId('edit-workflow').click();
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Editing');
}

async function endpointErrors(page: Page) {
  return page.getByTestId('graph-canvas').evaluate((canvas) => {
    const errors: { edge: string; direction: string; distance: number }[] = [];
    for (const edge of canvas.querySelectorAll<SVGGElement>('[data-source-node]')) {
      const path = edge.querySelectorAll('path')[1];
      if (!(path instanceof SVGPathElement)) throw new Error('Missing visible edge path');
      for (const direction of ['source', 'target'] as const) {
        const nodeId = edge.dataset[`${direction}Node`];
        const eventType = edge.dataset[`${direction}Event`];
        const socket = [
          ...canvas.querySelectorAll<SVGCircleElement>('[data-node-id][data-event-type]'),
        ].find(
          (candidate) =>
            candidate.dataset.nodeId === nodeId &&
            candidate.dataset.eventType === eventType &&
            candidate.dataset.direction === (direction === 'source' ? 'output' : 'input'),
        );
        if (!socket) throw new Error(`Missing ${direction} socket for ${edge.dataset.testid}`);
        const point = path.getPointAtLength(direction === 'source' ? 0 : path.getTotalLength());
        const screen = new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!);
        const bounds = socket.getBoundingClientRect();
        errors.push({
          edge: edge.dataset.testid!,
          direction,
          distance: Math.hypot(
            screen.x - bounds.x - bounds.width / 2,
            screen.y - bounds.y - bounds.height / 2,
          ),
        });
      }
    }
    return errors;
  });
}

test('editor connects every wire to its socket before and after zoom', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await expect(page.getByTestId('workflow-editor-header')).toHaveCount(1);
  await expect(page.getByTestId('library-panel')).toHaveCount(0);
  await expect(page.getByTestId('workflow-picker')).toHaveCount(0);
  await expect.poll(async () => (await endpointErrors(page)).length).toBe(edges.length * 2);
  await expect
    .poll(async () => Math.max(...(await endpointErrors(page)).map((item) => item.distance)))
    .toBeLessThan(1);
  const before = await page.getByTestId('zoom-level').textContent();
  await page.getByTestId('zoom-in').click();
  await expect(page.getByTestId('zoom-level')).not.toHaveText(before!);
  await expect
    .poll(async () => Math.max(...(await endpointErrors(page)).map((item) => item.distance)))
    .toBeLessThan(1);
});

test('Backspace edits a name without deleting its node', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  await page.getByTestId('workflow-node-review').click();
  const panel = page.getByTestId('workflow-detail-panel');
  await expect(panel).toBeVisible();
  const name = panel.locator('input').first();
  await name.focus();
  await name.press('End');
  await name.press('Backspace');
  await expect(page.getByTestId('workflow-node-review')).toHaveCount(1);
  await expect(name).toHaveValue('Review the chang');
});

test('zoomed HTML cards stay painted and clickable at their SVG positions', async ({ page }) => {
  await configure(page, { childCatalog: true });
  await openEditor(page);
  await editWorkflow(page);
  await page.getByTestId('open-add-step').click();
  await page.getByTestId('library-add-subworkflow').click();
  await page.getByTestId('zoom-fit').click();
  await page.getByTestId('zoom-out').click();
  await page.getByTestId('zoom-out').click();
  for (const kind of ['trigger', 'stage', 'subworkflow']) {
    const node = page.locator(`[data-kind="${kind}"]`).first();
    await expect
      .poll(() =>
        node.evaluate((element) => {
          const card = element.querySelector('foreignObject')!;
          const bounds = card.getBoundingClientRect();
          const hit = document.elementFromPoint(
            bounds.x + bounds.width / 2,
            bounds.y + bounds.height / 2,
          );
          return element.contains(hit);
        }),
      )
      .toBe(true);
  }
  await page.locator('[data-kind="subworkflow"] foreignObject').click();
  await expect(page.getByTestId('child-workflow-select')).toBeVisible();
});

test('right-click searches all node types and inserts at the clicked canvas point', async ({
  page,
}) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  await page.getByTestId('zoom-in').click();
  const canvas = page.getByTestId('graph-canvas');
  const bounds = (await canvas.boundingBox())!;
  const point = { x: bounds.x + bounds.width - 30, y: bounds.y + bounds.height - 90 };
  const position = await canvas.evaluate((element, point) => {
    const layer = element.querySelector(':scope > g') as SVGGElement;
    const world = new DOMPoint(point.x, point.y).matrixTransform(layer.getScreenCTM()!.inverse());
    return { x: world.x, y: world.y };
  }, point);
  await page.mouse.click(point.x, point.y, { button: 'right' });
  const picker = page.getByRole('dialog', { name: 'Add workflow node' });
  await expect(picker).toBeVisible();
  await expect(picker.locator('[data-testid^="library-add-"]')).toHaveCount(8);
  const popup = (await picker.boundingBox())!;
  expect(popup.x + popup.width).toBeLessThanOrEqual(bounds.x + bounds.width);
  expect(popup.y + popup.height).toBeLessThanOrEqual(bounds.y + bounds.height);
  await picker.getByRole('textbox').fill('child');
  await picker.getByTestId('library-add-subworkflow').click();
  await expect(picker).toHaveCount(0);
  const added = page.locator('[data-testid^="workflow-node-node-"] foreignObject').first();
  expect(Number(await added.getAttribute('x'))).toBeCloseTo(position.x, 3);
  expect(Number(await added.getAttribute('y'))).toBeCloseTo(position.y, 3);
  await page.getByTestId('btn-undo').click();
  await expect(page.locator('[data-testid^="workflow-node-"]')).toHaveCount(nodes.length);
});

test('Mac Control-click opens the picker without panning the graph', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  const canvas = page.getByTestId('graph-canvas');
  const bounds = (await canvas.boundingBox())!;
  const before = await canvas.locator(':scope > g').getAttribute('transform');
  await canvas.click({ modifiers: ['Control'], position: { x: 120, y: bounds.height - 100 } });
  await expect(page.getByTestId('canvas-node-picker')).toBeVisible();
  await expect(page.getByTestId('library-search')).toBeFocused();
  await expect(canvas.locator(':scope > g')).toHaveAttribute('transform', before!);
});

test('new child nodes choose a saved workflow and undo the entire assignment', async ({ page }) => {
  await configure(page, { childCatalog: true });
  await openEditor(page);
  await editWorkflow(page);
  await page.getByTestId('open-add-step').click();
  await page.getByTestId('library-add-subworkflow').click();
  await page.locator('[data-kind="subworkflow"]').click();
  const choice = page.getByTestId('child-workflow-select');
  await expect(choice).toBeVisible();
  await expect(choice.locator(`option[value^="catalog:${workflowId}:"]`)).toHaveCount(0);
  await choice.selectOption('catalog:362b143f-c5a4-492b-a5f5-0b15fc2c7202:sha256:child-210');
  await expect(page.getByTestId('child-workflow-pin')).toContainText('sha256:child-210');
  await expect(page.getByTestId('open-child-workflow')).toBeVisible();
  await page.getByTestId('btn-undo').click();
  await expect(choice).toHaveValue('');
  await page.getByTestId('btn-redo').click();
  await expect(choice).toHaveValue('catalog:362b143f-c5a4-492b-a5f5-0b15fc2c7202:sha256:child-210');
  await choice.focus();
  await choice.press('Tab');
  await expect(page.getByTestId('canvas-node-picker')).toHaveCount(0);
});

test('bundled workflow right-click explains view mode and offers same-identity editing', async ({
  page,
}) => {
  await configure(page, { readOnly: true, canEdit: true, origin: 'bundled' });
  await openEditor(page);
  const canvas = page.getByTestId('graph-canvas');
  const bounds = (await canvas.boundingBox())!;
  await canvas.click({ button: 'right', position: { x: 120, y: bounds.height - 100 } });
  const picker = page.getByTestId('canvas-node-picker');
  await expect(picker).toContainText('Viewing saved workflow');
  await picker.getByRole('button', { name: 'Edit workflow' }).click();
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Editing');
  await expect(page.getByTestId('builder-title')).toHaveText('Editor regression workflow');
  await expect(picker.locator('[data-testid^="library-add-"]')).toHaveCount(0);
});

test('right-click picker supports keyboard search, tab navigation and Escape', async ({
  page,
  browserName,
}) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  const canvas = page.getByTestId('graph-canvas');
  const bounds = (await canvas.boundingBox())!;
  await canvas.click({ button: 'right', position: { x: 120, y: bounds.height - 100 } });
  const search = page.getByRole('textbox', { name: 'Search nodes, actors, and resources' });
  await expect(search).toBeFocused();
  await search.fill('trigger');
  // Safari's default full-control navigation uses Option-Tab.
  // https://support.apple.com/guide/safari/cpsh003/mac
  await search.press(browserName === 'webkit' ? 'Alt+Tab' : 'Tab');
  await expect(page.getByTestId('library-add-trigger')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('canvas-node-picker')).toHaveCount(0);
  await expect(page.getByTestId('graph-view')).toBeFocused();
});

test('global command shortcut has one owner and library closes with Escape', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByTestId('library-panel')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await page.getByTestId('open-add-step').click();
  await expect(page.getByTestId('library-panel')).toBeVisible();
  await page.getByTestId('library-search').focus();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('library-panel')).toHaveCount(0);
});

test('historical versions remain viewable, editable, and launch exactly', async ({ page }) => {
  await configure(page);
  let launchBody: Record<string, unknown> | null = null;
  await page.route(`**/api/v1/ting/workflows/${workflowId}/launch`, async (route) => {
    launchBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        workflow_id: workflowId,
        workflow_name: 'Editor regression workflow',
        slug: 'historical-editor-run',
        session_id: 'historical-session',
        session_name: 'Historical editor run',
        status: 'starting',
        cluster_name: 'test',
      },
    });
  });
  await openEditor(page);

  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Viewing');
  await page.getByTestId('workflow-version-picker').selectOption('0.9.0');
  await expect(page.getByTestId('workflow-version-picker')).toHaveValue('0.9.0');
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Viewing');

  await page.getByTestId('launch-workflow').click();
  await page.getByTestId('workflow-launch-prompt').fill('Run the historical workflow');
  await page.getByRole('button', { name: 'Launch', exact: true }).click();
  await expect.poll(() => launchBody?.workflow_version).toBe('0.9.0');
  await expect(page).toHaveURL(/\/volundr\/sessions\/historical-session$/);

  await page.goto(`/ting/workflows?id=${workflowId}`);
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await page.getByTestId('workflow-version-picker').selectOption('0.9.0');
  await editWorkflow(page);
  await expect(page.getByTestId('builder-title')).toHaveText('Editor regression workflow');
});

test('Edit stays blocked until a requested historical document is loaded', async ({ page }) => {
  let releaseVersion!: () => void;
  const versionLoading = new Promise<void>((resolve) => {
    releaseVersion = resolve;
  });
  await configure(page, { versionLoading });
  await openEditor(page);

  await page.getByTestId('workflow-version-picker').selectOption('0.9.0');
  await expect(page.getByTestId('workflow-versions-loading')).toBeVisible();
  await expect(page.getByTestId('edit-workflow')).toBeDisabled();
  await expect(page.getByTestId('launch-workflow')).toBeDisabled();

  releaseVersion();
  await expect(page.getByTestId('workflow-version-picker')).toHaveValue('0.9.0');
  await expect(page.getByTestId('edit-workflow')).toBeEnabled();
});

test('workflow catalog exposes a loading state before the real editor mounts', async ({ page }) => {
  let release!: () => void;
  const loading = new Promise<void>((resolve) => {
    release = resolve;
  });
  await configure(page, { loading });
  await page.goto('/ting/workflows/build');
  await expect(page.getByText(/Loading workflows/i)).toBeVisible();
  await expect(page.getByTestId('workflow-builder')).toHaveCount(0);
  release();
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
});

test('workflow catalog failure is visible and never replaced by a fake editor', async ({
  page,
}) => {
  await configure(page, { fail: true });
  await page.goto('/ting/workflows/build');
  await expect(page.getByRole('alert')).toContainText('API request failed: 500', {
    timeout: 20_000,
  });
  await expect(page.getByTestId('workflow-builder')).toHaveCount(0);
});

test('Add node, canvas Tab and right-click share one searchable picker', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await editWorkflow(page);
  await expect(page.getByRole('button', { name: 'Library', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Add node/ })).toHaveCount(1);
  // Edit places focus on the graph; no manual focus() workaround for the shortcut.
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('library-search')).toBeFocused();
  await expect(page.getByTestId('canvas-node-picker')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await page.getByTestId('open-add-step').click();
  await expect(page.getByTestId('library-search')).toBeFocused();
  await page.keyboard.press('Escape');
  const canvas = page.getByTestId('graph-canvas');
  const bounds = (await canvas.boundingBox())!;
  const point = { x: 220, y: bounds.height - 120 };
  await canvas.click({ position: point });
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('library-search')).toBeFocused();
  await page.getByTestId('library-search').press('Tab');
  await expect(page.getByTestId('canvas-node-picker')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await canvas.click({ position: point, button: 'right' });
  await expect(page.getByTestId('library-search')).toBeFocused();
  await expect(
    page.getByTestId('canvas-node-picker').locator('[data-testid^="library-add-"]'),
  ).toHaveCount(8);
  await page.keyboard.press('Escape');
  await page.getByTestId('workflow-node-review').click();
  await page.keyboard.press('Tab');
  await expect(page.getByTestId('library-search')).toBeFocused();
});

test('headers keep theme and logout visible with a visualization dropdown', async ({ page }) => {
  await configure(page);
  await openEditor(page);
  await expect(page.getByRole('group', { name: 'Interface mode' })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: 'Color theme' })).toBeVisible();
  await expect(page.getByRole('group', { name: 'Workflow view' })).toHaveCount(0);
  const visualization = page.getByRole('combobox', { name: 'Visualization', exact: true });
  await visualization.selectOption('pipeline');
  await expect(page.getByTestId('pipeline-view')).toBeVisible();
  await visualization.selectOption('yaml');
  await expect(page.getByTestId('yaml-view')).toBeVisible();
  await visualization.selectOption('graph');
  await expect(page.getByTestId('graph-canvas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open application menu' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Disconnect', exact: true })).toBeVisible();
  const title = await page.locator('.niuu-shell__topbar-title').boundingBox();
  const tabs = await page.locator('.niuu-shell__tabs').boundingBox();
  const controls = await page.locator('.niuu-shell__topbar-right').boundingBox();
  expect(title!.x + title!.width).toBeLessThanOrEqual(tabs!.x);
  expect(tabs!.x + tabs!.width).toBeLessThanOrEqual(controls!.x);
});

test('child exploration returns to the exact historical parent draft', async ({ page }) => {
  await configure(page, { childCatalog: true });
  await openEditor(page);
  await page.getByTestId('workflow-version-picker').selectOption('0.9.0');
  await editWorkflow(page);
  await page.getByTestId('open-add-step').click();
  await page.getByTestId('library-add-subworkflow').click();
  const childNode = page.locator('[data-kind="subworkflow"]');
  const childTestId = (await childNode.getAttribute('data-testid'))!;
  await childNode.click();
  await page
    .getByTestId('child-workflow-select')
    .selectOption('catalog:362b143f-c5a4-492b-a5f5-0b15fc2c7202:sha256:child-210');
  await expect(page.getByTestId('workflow-dirty-state')).toBeVisible();
  await page.getByTestId('open-child-workflow').click();
  await expect(page.getByTestId('builder-title')).toHaveText('Reusable review');
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Viewing');
  await page
    .getByRole('navigation', { name: 'Workflow path' })
    .getByRole('button', { name: 'Editor regression workflow', exact: true })
    .click();
  await expect(page.getByTestId('builder-title')).toHaveText('Editor regression workflow');
  await expect(page.getByTestId('workflow-version-picker')).toHaveValue('0.9.0');
  await expect(page.getByTestId('workflow-editor-mode')).toHaveText('Editing');
  await expect(page.getByTestId('workflow-dirty-state')).toBeVisible();
  await expect(page.getByTestId('save-workflow')).toBeEnabled();
  await page.getByTestId(childTestId).click();
  await expect(page.getByTestId('child-workflow-select')).toHaveValue(
    'catalog:362b143f-c5a4-492b-a5f5-0b15fc2c7202:sha256:child-210',
  );
  await page.getByTestId('btn-undo').click();
  await expect(page.getByTestId(childTestId)).toHaveCount(0);
});

test('editor gives long names room alongside the Work navigation', async ({ page }) => {
  await page.setViewportSize({ width: 2048, height: 950 });
  await configure(page, { name: 'Research Campaign copy for cross-team investigation' });
  await page.goto('/ting/workflows/build');
  const title = page.getByTestId('builder-title');
  await expect(title).toHaveText('Research Campaign copy for cross-team investigation');
  await expect(page.getByRole('navigation', { name: 'Workflow execution' })).toHaveCount(0);
  expect(await title.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  const picker = await page.getByTestId('workflow-picker-trigger').boundingBox();
  const view = await page.getByTestId('workflow-visualization').boundingBox();
  expect(view!.x - (picker!.x + picker!.width)).toBeGreaterThan(100);
  await page.getByTestId('workflow-more').locator('summary').click();
  await expect(
    page.getByTestId('workflow-more').getByText('Workflow runs', { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByTestId('ting-tab-workflow-runs')).toHaveCount(0);
  await expect(page.getByTestId('ting-tab-work')).toBeVisible();
  await expect(page.getByTestId('ting-tab-workflows')).toHaveClass(/niuu-shell__tab--active/);
});
