import { expect, test, type Page } from '@playwright/test';

const projectId = '04ca94f8-8bf9-4e01-986b-938fa17c8701';
const campaignId = '04ca94f8-8bf9-4e01-986b-938fa17c8702';
const project = {
  id: `project:${projectId}`,
  kind: 'project',
  title: 'Platform reliability',
  condition: 'active',
  rawState: { source: 'saga', status: 'active' },
  currentStep: null,
  attention: null,
  createdAt: null,
  updatedAt: null,
  source: {
    connectionId: 'tracker-team',
    provider: 'external',
    projectId: 'OPS',
    url: 'https://tracker.example/projects/OPS',
    status: 'In progress',
  },
  workflow: null,
  session: null,
  links: { self: `/ting/work/project:${projectId}`, specialist: `/ting/sagas/${projectId}` },
  actions: [],
};
const campaign = {
  ...project,
  id: `campaign:${campaignId}`,
  kind: 'specification',
  title: 'Review deployment recovery specification',
  condition: 'needsAttention',
  rawState: { source: 'campaign', status: 'awaiting_review' },
  attention: { kind: 'decision', message: 'Review the specification before implementation.' },
  source: null,
  links: { self: `/ting/work/campaign:${campaignId}`, specialist: `/ting/specs/${campaignId}` },
  actions: ['review'],
};
const task = {
  source: {
    ...project.source,
    issueId: 'issue-1',
    identifier: 'OPS-42',
    url: 'https://tracker.example/browse/OPS-42',
    status: 'In progress',
    statusType: 'started',
  },
  title: 'Retain the original tracker status when a ticket has not been dispatched',
  operationalRun: null,
  dispatch: null,
  actions: [],
};

const trackerColumn = {
  kind: 'tracker',
  review: null,
  approveColumnId: null,
  changesColumnId: null,
};
const planning = { workflowId: 'workflow-0', workflowName: 'Ticket Planning Flow' };
const delivery = { workflowId: 'workflow-1', workflowName: 'Reviewed Delivery Flow' };
const deliveryColumns = [
  { ...trackerColumn, ...delivery, id: 'building', title: 'Building', kind: 'work' },
  {
    ...trackerColumn,
    ...delivery,
    id: 'code-review',
    title: 'Code review',
    kind: 'gate',
    review: 'code',
    approveColumnId: 'merging',
    changesColumnId: 'building',
  },
  { ...trackerColumn, ...delivery, id: 'merging', title: 'Merging', kind: 'work' },
];
const board = {
  columns: [
    { ...trackerColumn, id: 'backlog', title: 'Backlog' },
    ...deliveryColumns,
    { ...trackerColumn, id: 'done', title: 'Done' },
  ],
};
const plannedBoard = {
  columns: [
    { ...trackerColumn, id: 'backlog', title: 'Backlog' },
    { ...trackerColumn, ...planning, id: 'planning', title: 'Planning', kind: 'work' },
    {
      ...trackerColumn,
      ...planning,
      id: 'plan-review',
      title: 'Plan review',
      kind: 'gate',
      review: 'plan',
      approveColumnId: 'building',
      changesColumnId: 'planning',
    },
    ...deliveryColumns,
    { ...trackerColumn, id: 'done', title: 'Done' },
  ],
};
const readyTask = {
  ...task,
  dispatch: {
    sagaId: projectId,
    issueId: 'issue-1',
    repo: 'niuulabs/niuu',
    connectionId: null,
    workflowId: null,
  },
  board: { columnId: 'backlog', state: 'notStarted', gate: null },
};
const gatedTask = {
  ...task,
  board: {
    columnId: 'code-review',
    state: 'needsDecision',
    gate: {
      id: 'gate-1',
      activationId: 'activation-1',
      nodeId: 'delivery-code-gate',
      label: 'Code review',
      condition: 'Merging lands the change on the target branch.',
      instructions: 'Read the pull request.',
      summary: '',
      requestedAt: '2026-10-30T12:00:00Z',
      attempt: 1,
    },
  },
};

async function configure(
  page: Page,
  options: {
    partial?: boolean;
    failDetail?: boolean;
    trackerImport?: boolean;
    results?: boolean;
    board?: boolean;
    readyTicket?: boolean;
    plannedBoard?: boolean;
  } = {},
) {
  await page.route('**/config*.json', async (route) => {
    const config = await (await route.fetch()).json();
    config.demoMode = true;
    // The demo setup service is never complete, so SetupGate would redirect mid-test.
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/niuu/setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    config.services['ting.work'] = { mode: 'http', baseUrl: '/api/v1/ting' };
    if (options.results) config.services['ting.specs'] = { mode: 'http', baseUrl: '/api/v1/ting' };
    if (options.trackerImport) {
      config.services.tracker = { mode: 'http', baseUrl: '/api/v1' };
      config.services['niuu.repos'] = { mode: 'http', baseUrl: '/api/v1/niuu' };
    }
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/ting/work?*', (route) =>
    route.fulfill({
      json: {
        projects: [project],
        campaigns: [campaign],
        executions: [],
        executionNextCursor: null,
        coverage: options.partial
          ? [
              {
                source: 'tracker',
                connectionId: 'other-team',
                status: 'unavailable',
                error: { code: 'source_unavailable', message: 'Reconnect the tracker account.' },
              },
            ]
          : [],
      },
    }),
  );
  await page.route('**/api/v1/ting/work/project/*', (route) =>
    route.fulfill(
      options.failDetail
        ? { status: 403, json: { detail: 'You do not have access to this project.' } }
        : {
            json: {
              item: project,
              tasks: [options.readyTicket ? readyTask : options.board ? gatedTask : task],
              coverage: [
                { source: 'operationalRuns', connectionId: null, status: 'complete', error: null },
              ],
              ...(options.board ? { board: options.plannedBoard ? plannedBoard : board } : {}),
            },
          },
    ),
  );
  await page.route('**/api/v1/ting/work/campaign/*', (route) =>
    route.fulfill({
      json: {
        item: { ...campaign, ...(options.results ? { campaignSlug: 'recovery' } : {}) },
        tasks: [],
        coverage: [],
      },
    }),
  );
}

test('Work is the default and provides a quiet single navigation', async ({ page }, testInfo) => {
  await configure(page);
  await page.goto('/ting');
  await expect(page).toHaveURL(/\/ting\/work$/);
  await expect(page.getByTestId('ting-tab-work')).toBeVisible();
  await expect(page.getByTestId('ting-tab-workflows')).toBeVisible();
  await expect(page.getByTestId('ting-tab-workflow-runs')).toHaveCount(0);
  await expect(page.getByTestId('work-overview')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New work' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('work-overview.png') });
  await page.getByRole('button', { name: 'New work' }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByTestId('new-work-from-tracker')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

for (const theme of ['ice', 'xteo']) {
  test(`Work follows Forge workspace typography in ${theme}`, async ({ page }, testInfo) => {
    await configure(page);
    await page.addInitScript((value) => localStorage.setItem('niuu.theme', value), theme);
    await page.goto('/volundr/sessions');
    const forgeHeading = page.getByTestId('pod-list-sidebar').getByRole('heading', {
      name: 'Sessions',
      exact: true,
    });
    await expect(forgeHeading).toBeVisible();
    const reference = await forgeHeading.evaluate((element) => {
      const style = getComputedStyle(element);
      return { fontSize: style.fontSize, fontFamily: style.fontFamily };
    });

    await page.goto('/ting/work');
    const heading = page.getByRole('heading', { name: 'Overview', exact: true });
    await expect(heading).toHaveCSS('font-size', reference.fontSize);
    await expect(heading).toHaveCSS('font-family', reference.fontFamily);
    const content = await page.locator('.ting-work-content').boundingBox();
    const title = await heading.boundingBox();
    expect(title!.x - content!.x).toBeLessThanOrEqual(24);
    expect(title!.y - content!.y).toBeLessThanOrEqual(24);
    await expect(page.getByText('Ting workspace', { exact: true })).toHaveCount(0);
    await expect(page.getByTestId('ting-tab-workflows')).toBeVisible();
    await expect(page.locator('.ting-work-sidebar a[href="/ting/workflows"]')).toHaveCount(0);
    const row = page.getByTestId(`work-row-campaign:${campaignId}`);
    await expect(row).toBeVisible();
    expect((await row.boundingBox())!.height).toBeGreaterThanOrEqual(68);
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: testInfo.outputPath(`work-workspace-${theme}.png`) });
  });
}

test('ticket detail distinguishes source status from absent execution and survives reload', async ({
  page,
}) => {
  await configure(page);
  await page.goto(`/ting/work/project:${projectId}`);
  await expect(page.getByRole('heading', { name: 'Platform reliability' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'OPS-42' })).toHaveAttribute('href', task.source.url);
  await expect(page.getByText('Not started', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Start eligible work' })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('heading', { name: task.title })).toBeVisible();
});

test('the board view survives detail, reload and back navigation', async ({ page }) => {
  await configure(page);
  await page.goto('/ting/work');
  await page.getByRole('button', { name: 'Board view' }).click();
  await expect(page).toHaveURL(/view=board/);

  const attention = page.getByTestId('board-column-attention');
  await expect(attention.getByText(campaign.title)).toBeVisible();
  await expect(attention.getByText(campaign.attention.message)).toBeVisible();

  await page.getByTestId(`work-card-project:${projectId}`).click();
  const started = page.getByTestId('ticket-board').getByTestId('board-column-started');
  await expect(started.getByRole('link', { name: 'OPS-42' })).toBeVisible();
  await expect(started.getByText('Not started', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('ticket-board')).toBeVisible();

  await page.getByRole('button', { name: /Back to work/i }).click();
  await expect(page.getByTestId('work-board')).toBeVisible();
});

test('a project board answers the gate its workflow declares', async ({ page }) => {
  await configure(page, { board: true });
  const decisions: unknown[] = [];
  await page.route('**/gate-decision', (route) => {
    decisions.push(route.request().postDataJSON());
    return route.fulfill({
      json: { status: 'resolved', sessionId: 'session-1', gateId: 'gate-1' },
    });
  });
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  const projectBoard = page.getByTestId('project-board');
  await expect(projectBoard.getByText('Reviewed Delivery Flow')).toBeVisible();
  const review = page.getByTestId('board-column-code-review');
  await review.getByRole('button', { name: gatedTask.title }).focus();
  await page.keyboard.press('Enter');

  const panel = page.getByRole('complementary', { name: 'OPS-42' });
  await expect(panel.getByText('Is this ready to merge?')).toBeVisible();
  await panel.getByRole('button', { name: 'Request changes → Building' }).click();
  await expect(panel.getByRole('alert')).toHaveText('Say what should change first.');
  expect(decisions).toEqual([]);

  await panel.getByRole('button', { name: 'Approve → Merging' }).click();
  await expect
    .poll(() => decisions)
    .toEqual([{ gateId: 'gate-1', activationId: 'activation-1', decision: 'approve', notes: '' }]);
});

test('a project board starts a ticket through its coordinator', async ({ page }) => {
  await configure(page, { board: true, readyTicket: true });
  const started: string[] = [];
  await page.route('**/tasks/*/start', (route) => {
    started.push(route.request().url());
    return route.fulfill({ json: { status: 'coordinating', sessionId: 'coordinator-1' } });
  });
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  await page.getByRole('button', { name: 'Start', exact: true }).click();

  await expect.poll(() => started.length).toBe(1);
  expect(started[0]).toContain(`/work/project/${projectId}/tasks/issue-1/start`);
  const panel = page.getByRole('complementary', { name: 'OPS-42' });
  await expect(panel.getByRole('tab', { name: 'Coordinator' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(panel.getByText('No coordinator yet')).toBeVisible();
  await panel.getByRole('tab', { name: 'Workflow' }).click();
  await expect(panel.getByText('None yet')).toBeVisible();
  await panel.getByRole('button', { name: 'Hide' }).click();
  await expect(page.getByRole('complementary', { name: 'OPS-42' })).toHaveCount(0);
});

test('a project board runs its workflows in order and asks before skipping one', async ({
  page,
}) => {
  await configure(page, { board: true, readyTicket: true, plannedBoard: true });
  const starts: unknown[] = [];
  await page.route('**/tasks/*/start', (route) => {
    starts.push(route.request().postDataJSON());
    return route.fulfill({ json: { status: 'coordinating', sessionId: 'coordinator-1' } });
  });
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  await expect(page.getByRole('button', { name: /Ticket Planning Flow/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /Reviewed Delivery Flow/ })).toBeVisible();

  await page.getByTestId('board-ticket-issue-1').dragTo(page.getByTestId('board-column-merging'));
  await expect(page.getByRole('status')).toContainText('Reviewed Delivery Flow starts at Building');

  await page.getByTestId('board-ticket-issue-1').dragTo(page.getByTestId('board-column-building'));
  await expect(page.getByRole('alert')).toContainText('This skips Ticket Planning Flow');
  await page.getByRole('button', { name: 'Skip and start' }).click();

  await expect.poll(() => starts).toEqual([{ columnId: 'building' }]);
});

test('a project board shows what its workflow defines', async ({ page }) => {
  await configure(page, { board: true });
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  await page.getByRole('button', { name: /Reviewed Delivery Flow/ }).click();

  const panel = page.getByRole('complementary', { name: 'Reviewed Delivery Flow' });
  await expect(panel.getByText('Building → Code review → Merging → Done')).toBeVisible();
  await expect(panel.getByText('A person approves')).toBeVisible();
});

test('a project board fills the page and scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 700 });
  await configure(page, { board: true });
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  await expect(page.getByTestId('project-board')).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Work workspace' })).toBeHidden();
  await page.getByRole('button', { name: gatedTask.title }).click();

  const scroller = page.getByTestId('board-scroller');
  const box = await scroller.boundingBox();
  expect(box!.y).toBeLessThan(160);
  expect(box!.y + box!.height).toBeLessThanOrEqual(700);
  await scroller.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => scroller.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0);
  await expect(page.getByTestId('board-column-done')).toBeInViewport();
});

test('a project board shows a decision the session refused', async ({ page }) => {
  await configure(page, { board: true });
  await page.route('**/gate-decision', (route) =>
    route.fulfill({
      status: 409,
      json: { detail: 'This gate changed after you opened it. Review it again.' },
    }),
  );
  await page.goto(`/ting/work/project:${projectId}?view=board`);

  await page.getByRole('button', { name: gatedTask.title }).click();
  const panel = page.getByRole('complementary', { name: 'OPS-42' });
  await panel.getByRole('button', { name: 'Approve → Merging' }).click();
  await expect(panel.getByRole('alert')).toContainText('Review it again');
});

test('the board reports a failed source and a failed project', async ({ page }) => {
  await configure(page, { partial: true, failDetail: true });
  await page.goto('/ting/work?view=board');
  await expect(page.getByText('Reconnect the tracker account.')).toBeVisible();
  await expect(page.getByTestId('work-board')).toBeVisible();

  await page.getByTestId(`work-card-project:${projectId}`).click();
  await expect(page.getByText('Work detail could not be loaded')).toBeVisible();
});

test('the layout switch works from the keyboard', async ({ page }) => {
  await configure(page);
  await page.goto('/ting/work');
  await page.getByRole('button', { name: 'Board view' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('work-board')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Board view' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});

test('the board stacks on a phone without sideways scrolling', async ({ page }) => {
  await configure(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/ting/work?view=board');
  await expect(page.getByTestId('work-board')).toBeVisible();

  const first = await page.getByTestId('board-column-attention').boundingBox();
  const second = await page.getByTestId('board-column-active').boundingBox();
  expect(second!.y).toBeGreaterThan(first!.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('filter and search survive detail and back navigation', async ({ page }) => {
  await configure(page);
  await page.goto('/ting/work?filter=attention&q=deployment');
  await page.getByTestId(`work-row-campaign:${campaignId}`).click();
  await expect(page.getByRole('heading', { name: campaign.title })).toBeVisible();
  await page.getByRole('button', { name: /Back to work/i }).click();
  await expect(page).toHaveURL(/filter=attention/);
  await expect(page).toHaveURL(/q=deployment/);
  await expect(page.getByRole('searchbox')).toHaveValue('deployment');
});

test('a disconnected source leaves available work visible', async ({ page }) => {
  await configure(page, { partial: true });
  await page.goto('/ting/work');
  await expect(page.getByText('Reconnect the tracker account.')).toBeVisible();
  await expect(page.getByTestId(`work-row-campaign:${campaignId}`)).toBeVisible();
});

test('specification evidence opens in place and submits the exact pending decision', async ({
  page,
}) => {
  await configure(page, { results: true });
  const reviews: Record<string, unknown>[] = [];
  const artifact = {
    path: '/specs/recovery.md',
    title: 'Recovery design',
    updated_at: '2026-09-20T12:00:00Z',
    publish_state: 'draft',
    source_ids: ['OPS-42'],
    summary: 'Rollback design and evidence',
  };
  const detail = {
    id: campaignId,
    slug: 'recovery',
    name: campaign.title,
    owner_id: 'test-user',
    workflow_id: projectId,
    workflow_version: '1.0.0',
    workflow_name: 'Specification Stack',
    session_id: 'spec-session',
    session_name: 'Recovery specification',
    status: 'blocked',
    created_at: '2026-09-20T12:00:00Z',
    updated_at: '2026-09-20T12:00:00Z',
    stage_state: [],
    artifacts: [artifact],
    canonical_artifacts: { sdd: artifact.path },
    metadata: {
      pending_workflow_gates: [
        {
          id: 'gate-42',
          node_id: 'spec-sdd-gate',
          summary: 'Review rollback design',
          instructions: 'Check the recovery evidence.',
        },
      ],
    },
  };
  await page.route('**/api/v1/ting/specs/campaigns/recovery', (route) =>
    route.fulfill({ json: detail }),
  );
  await page.route('**/api/v1/ting/specs/campaigns/recovery/artifact?*', (route) =>
    route.fulfill({
      json: { ...artifact, content: '# Verified rollback\n\nThe recovery procedure passed.' },
    }),
  );
  await page.route('**/api/v1/ting/specs/campaigns/recovery/review', (route) => {
    reviews.push(route.request().postDataJSON());
    return route.fulfill({ json: { ...detail, status: 'running', metadata: {} } });
  });
  await page.goto(`/ting/work/campaign:${campaignId}`);
  await page.getByRole('button', { name: /Recovery design/ }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Verified rollback' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(campaignId));
  await page.getByLabel('Review notes').fill('Rollback verified.');
  await page.getByRole('button', { name: 'Approve specification' }).click();
  await expect(page.getByText('Review submitted.')).toBeVisible();
  expect(reviews).toEqual([
    {
      decision: 'approve',
      gateId: 'gate-42',
      nodeId: 'spec-sdd-gate',
      notes: 'Rollback verified.',
    },
  ]);
});

test('tracker import preserves account identity and does not dispatch', async ({ page }) => {
  await configure(page, { trackerImport: true });
  const imports: Record<string, unknown>[] = [];
  const dispatches: string[] = [];
  const rawProject = {
    id: 'OPS',
    name: 'Platform reliability',
    description: '',
    status: 'started',
    url: 'https://tracker.example/projects/OPS',
    milestone_count: 0,
    issue_count: 1,
    tracker_type: 'external',
    tracker_name: 'Main tracker',
    tracker_connection_id: 'tracker-team',
  };
  await page.route('**/api/v1/tracker/projects', (route) =>
    route.fulfill({
      json: [
        rawProject,
        {
          ...rawProject,
          name: 'Release tracking',
          tracker_name: 'Release tracker',
          tracker_connection_id: 'release-team',
        },
      ],
    }),
  );
  await page.route('**/api/v1/niuu/repos', (route) =>
    route.fulfill({
      json: [
        {
          provider: 'github',
          org: 'niuulabs',
          name: 'volundr',
          cloneUrl: 'https://github.com/niuulabs/volundr.git',
          url: 'https://github.com/niuulabs/volundr',
          defaultBranch: 'main',
          branches: ['main'],
        },
      ],
    }),
  );
  await page.route('**/api/v1/niuu/repos/branches?*', (route) => route.fulfill({ json: ['main'] }));
  await page.route('**/api/v1/tracker/import', (route) => {
    imports.push(route.request().postDataJSON());
    return route.fulfill({
      json: {
        id: projectId,
        tracker_id: 'OPS',
        tracker_type: 'external',
        tracker_connection_id: 'release-team',
        slug: 'release-tracking',
        name: 'Release tracking',
        repos: ['niuulabs/volundr'],
        feature_branch: '',
        base_branch: 'main',
        status: 'active',
        confidence: 0,
        created_at: '2026-09-20T12:00:00Z',
        phase_summary: { total: 0, completed: 0 },
      },
    });
  });
  await page.route('**/api/v1/ting/dispatch/**', (route) => {
    if (route.request().method() === 'POST') dispatches.push(route.request().url());
    return route.fulfill({ json: [] });
  });
  await page.goto('/ting/work');
  await page.getByRole('button', { name: 'New work' }).click();
  await page.getByTestId('new-work-from-tracker').click();
  const dialog = page.getByRole('dialog', { name: 'Bring in a tracker project' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: /Release tracking/ }).click();
  await dialog.getByTestId('work-import-repository').selectOption('niuulabs/volundr');
  await expect(dialog.getByTestId('work-import-branch')).toHaveValue('main');
  await dialog.getByRole('button', { name: 'Import project', exact: true }).click();
  await expect(page.getByTestId('work-detail')).toBeVisible();
  expect(imports).toHaveLength(1);
  expect(imports[0]).toMatchObject({
    project_id: 'OPS',
    tracker_connection_id: 'release-team',
    start_immediately: false,
    repo_refs: [{ repo: 'niuulabs/volundr', branch: 'main' }],
  });
  expect(dispatches).toEqual([]);
});

for (const width of [320, 390, 1024, 1440]) {
  test(`work detail fits ${width}px and keeps the return action reachable`, async ({
    page,
  }, testInfo) => {
    await configure(page);
    await page.setViewportSize({ width, height: 900 });
    await page.goto(`/ting/work/project:${projectId}`);
    await expect(page.getByRole('heading', { name: 'Platform reliability' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Back to work/i })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`work-detail-${width}.png`) });
  });
}
