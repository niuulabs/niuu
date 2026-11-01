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

async function configure(
  page: Page,
  options: {
    partial?: boolean;
    failDetail?: boolean;
    trackerImport?: boolean;
    results?: boolean;
  } = {},
) {
  await page.route('**/config*.json', async (route) => {
    const config = await (await route.fetch()).json();
    config.demoMode = true;
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
              tasks: [task],
              coverage: [
                { source: 'operationalRuns', connectionId: null, status: 'complete', error: null },
              ],
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
