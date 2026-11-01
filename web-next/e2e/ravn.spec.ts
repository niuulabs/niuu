import { test, expect, type Page } from '@playwright/test';

/**
 * The Ravn plugin: the ravens workbench at /ravn and the persona library at
 * /ravn/personas. Fleet reads go over HTTP, so they are answered here.
 */

const RUNNING = '11111111-1111-4111-8111-111111111111';
const FAILED = '22222222-2222-4222-8222-222222222222';

const ravens = [
  {
    id: RUNNING,
    persona_name: 'reviewer',
    resident_name: 'Muninn',
    kind: 'resident',
    status: 'active',
    model: 'claude-sonnet-4-6',
    created_at: '2026-09-01T10:00:00Z',
    updated_at: '2026-09-24T09:00:00Z',
    backend: 'local',
    engine: 'openclaw',
    profile_id: 'nemoclaw-local',
    desired_state: 'running',
    observed_state: 'active',
    capabilities: ['chat', 'session.list', 'session.create', 'runtime.restart', 'logs'],
    conditions: [],
    managed: true,
    instance_id: 'local',
    instance_name: 'Local Forge',
    message_count: 12,
    tokens_used: 34904,
    cost: '0.07',
  },
  {
    id: FAILED,
    persona_name: 'product-steward',
    resident_name: 'Proof',
    kind: 'resident',
    status: 'failed',
    model: 'gpt-5.6-sol',
    created_at: '2026-07-12T13:12:12Z',
    updated_at: '2026-09-24T11:36:21Z',
    backend: 'local',
    engine: 'ravn',
    profile_id: 'ravn-local',
    desired_state: 'running',
    observed_state: 'failed',
    capabilities: ['chat', 'runtime.restart', 'logs'],
    conditions: [
      {
        type: 'BackendReady',
        status: 'unknown',
        reason: 'ReconcileFailed',
        message: 'connection aborted',
        lastTransitionAt: '2026-09-24T11:36:21Z',
      },
    ],
    managed: true,
    instance_id: 'local',
    instance_name: 'Local Forge',
  },
];

async function answerRavnApi(
  page: Page,
  options: { ravensStatus?: number; delayMs?: number } = {},
) {
  await page.route('**/api/v1/ravn/ravens', async (route) => {
    if (options.delayMs) await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    if (options.ravensStatus) {
      await route.fulfill({
        status: options.ravensStatus,
        json: { detail: 'Ravn fleet is unavailable' },
      });
      return;
    }
    await route.fulfill({ json: ravens });
  });
  await page.route('**/api/v1/ravn/sessions', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ravn/ravens/*/sessions**', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ravn/ravens/*/logs**', (route) =>
    route.fulfill({
      json: {
        entries: [
          {
            timestamp_ms: Date.parse('2026-09-24T09:41:02Z'),
            level: 'INFO',
            source: 'ravn',
            target: 'ravn.agent',
            message: 'turn started',
            fields: {},
          },
        ],
        buffer_total: 1,
      },
    }),
  );
  await page.route('**/api/v1/ravn/deployment-profiles', (route) =>
    route.fulfill({
      json: [
        {
          id: 'nemoclaw-local',
          displayName: 'NemoClaw (Local)',
          description: 'NVIDIA OpenClaw resident hosted by the local container engine',
          backend: 'local',
          engine: 'openclaw',
          capabilities: ['chat', 'session.list', 'logs'],
          defaultModel: 'niuu/nvidia/nemotron-3-super',
          allowedModels: ['niuu/nvidia/nemotron-3-super'],
          instance_id: 'local',
          instance_name: 'Local Forge',
        },
      ],
    }),
  );
}

test('the workbench opens on the ravn that needs attention and says why', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto('/ravn');

  await expect(page.getByTestId('ravn-list')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole('heading', { level: 1, name: /Proof/ })).toBeVisible();
  await expect(page.getByTestId('ravn-health-banner')).toContainText(
    'Not running — backend not ready · reconcile failed',
  );
  await expect(page.getByTestId('ravn-talk')).toBeDisabled();
});

test('the workbench shows a loading state before the fleet arrives', async ({ page }) => {
  await answerRavnApi(page, { delayMs: 1_500 });
  await page.goto('/ravn');
  await expect(page.getByTestId('ravn-workbench-loading')).toBeVisible();
  await expect(page.getByTestId('ravn-list')).toBeVisible({ timeout: 8_000 });
});

test('the workbench says why the fleet could not be loaded', async ({ page }) => {
  await answerRavnApi(page, { ravensStatus: 503 });
  await page.goto('/ravn');
  await expect(page.getByTestId('ravn-workbench-error')).toContainText(
    'Ravn fleet is unavailable',
    { timeout: 8_000 },
  );
});

test('state pills and search narrow the list', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto('/ravn');
  const list = page.getByTestId('ravn-list');
  await expect(list).toBeVisible({ timeout: 8_000 });

  await page.getByTestId('ravn-filter-running').click();
  await expect(list.getByText('Proof')).toHaveCount(0);
  await expect(list.getByText('Muninn')).toBeVisible();

  await page.getByTestId('ravn-filter-all').click();
  await page.getByTestId('ravn-search').fill('definitely-not-a-ravn');
  await expect(page.getByTestId('ravn-list-empty')).toBeVisible();
});

test('a ravn page moves between tabs by click and arrow key', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto(`/ravn?ravn=${RUNNING}&instance_id=local`);
  await expect(page.getByRole('heading', { level: 1, name: /Muninn/ })).toBeVisible({
    timeout: 8_000,
  });
  await expect(page.getByText('No conversations yet')).toBeVisible();

  await page.getByTestId('ravn-tab-activity').click();
  await expect(page.getByRole('log', { name: 'Runtime logs' })).toContainText('turn started');

  await page.getByTestId('ravn-tab-activity').press('ArrowRight');
  await expect(page).toHaveURL(/tab=setup/);
  await expect(page.getByTestId('ravn-setup-tab')).toBeVisible();

  await page.getByTestId('ravn-tab-setup').press('ArrowRight');
  await expect(page.getByTestId('ravn-usage-tab')).toBeVisible();
});

test('the deploy dialog lists runtimes and closes on Escape', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto('/ravn');
  await page.getByTestId('ravn-deploy-open').click();
  const dialog = page.getByRole('dialog', { name: 'Deploy a ravn' });
  await expect(dialog.getByTestId('ravn-deploy-profile-nemoclaw-local')).toBeVisible();
  await expect(dialog.getByTestId('ravn-deploy-submit')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('old addresses land on the workbench', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto(`/ravn/sessions?session=s-1&ravn_id=${RUNNING}&instance_id=local`);
  await expect(page).toHaveURL(new RegExp(`/ravn\\?.*ravn=${RUNNING}.*tab=chat`));
  await page.goto('/ravn/budget');
  await expect(page).toHaveURL(/\/ravn\?tab=usage/);
});

test('the persona library groups by family and reads as a sheet', async ({ page }) => {
  await answerRavnApi(page);
  await page.goto('/ravn/personas');
  await expect(page.getByTestId('persona-library')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('persona-family-general')).toBeVisible();
  await expect(page.getByTestId('persona-sheet')).toBeVisible();
  await expect(page.getByTestId('persona-flow')).toBeVisible();

  await page.getByTestId('persona-view-yaml').click();
  await expect(page.getByTestId('persona-body-yaml')).toBeVisible();
  await page.getByTestId('persona-edit').click();
  await expect(page.getByTestId('persona-form')).toBeVisible();
});
