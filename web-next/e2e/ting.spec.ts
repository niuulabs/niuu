import { test, expect, type Page } from '@playwright/test';

async function mockPlanWizardApi(page: Page) {
  await page.route('**/api/v1/ting/sagas/plan', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ json: [] });
      return;
    }
    await route.fulfill({
      json: {
        session_id: 'e2e-plan-session',
        chat_endpoint: null,
        questions: [{ id: 'scope', question: 'What should the plan cover?' }],
      },
    });
  });
  await page.route('**/api/v1/ting/sagas/decompose', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ting/sagas/extract-structure', (route) =>
    route.fulfill({
      json: {
        found: true,
        structure: {
          name: 'Authentication plan',
          phases: [
            {
              name: 'Build',
              runs: [
                {
                  name: 'Implement authentication',
                  description: 'Build the authentication module.',
                  acceptance_criteria: ['Authentication succeeds'],
                  declared_files: ['src/auth.ts'],
                  estimate_hours: 4,
                  confidence: 85,
                },
              ],
            },
          ],
          risks: [],
        },
      },
    }),
  );
  await page.route('**/api/v1/ting/sagas/commit', (route) =>
    route.fulfill({
      json: {
        id: 'e2e-auth-saga',
        tracker_id: 'E2E-1',
        tracker_type: 'linear',
        slug: 'authentication-plan',
        name: 'Authentication plan',
        repos: [],
        feature_branch: 'feat/authentication-plan',
        base_branch: 'main',
        status: 'active',
        confidence: 85,
        created_at: '2026-09-20T00:00:00Z',
        phase_summary: { total: 1, completed: 0 },
      },
    }),
  );
}

test('ting lands on Work with the primary navigation only', async ({ page }) => {
  await page.goto('/ting');

  await expect(page).toHaveURL(/\/ting\/work$/);
  await expect(page.getByTestId('ting-tab-work')).toBeVisible();
  await expect(page.getByTestId('ting-tab-workflows')).toBeVisible();
  await expect(page.getByTestId('ting-tab-sagas')).toHaveCount(0);
  await expect(page.getByTestId('ting-tab-dispatch')).toHaveCount(0);
});

test('sagas route renders search, filters, and actions', async ({ page }) => {
  await page.goto('/ting/sagas');

  await expect(page.getByRole('searchbox', { name: 'Search sagas' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('searchbox', { name: 'Filter sagas' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Import saga from tracker' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Export sagas as JSON' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create new saga' })).toBeVisible();
  const selectedSaga = page.getByRole('complementary').locator('button[aria-pressed="true"]');
  await expect(selectedSaga).toBeVisible();
  await expect(selectedSaga).toContainText(/\d+\/\d+ runs/i);
});

test('selecting a saga opens its detail route', async ({ page }) => {
  await page.goto('/ting/sagas');

  const selectedSaga = page.getByRole('complementary').locator('button[aria-pressed="true"]');
  await expect(selectedSaga).toBeVisible({ timeout: 5000 });
  await selectedSaga.click();

  await expect(page).toHaveURL(/\/ting\/sagas\/[^/]+$/);
  await expect(selectedSaga).toHaveAttribute('aria-pressed', 'true');
});

test('dispatch route renders queue and rules surfaces', async ({ page }) => {
  await page.goto('/ting/dispatch');

  await expect(page.getByRole('list', { name: 'Dispatch queue' })).toBeVisible({ timeout: 8000 });
  await expect(page.getByRole('group', { name: 'Filter runs by status' })).toBeVisible();
  await expect(page.getByRole('searchbox', { name: 'Search runs' })).toBeVisible();
  await expect(page.getByLabel('Dispatch rules panel')).toBeVisible();
});

test('dispatch route search accepts input', async ({ page }) => {
  await page.goto('/ting/dispatch');

  const search = page.getByRole('searchbox', { name: 'Search runs' });
  await search.fill('auth');
  await expect(search).toHaveValue('auth');
});

test('plan wizard renders the prompt step', async ({ page }) => {
  await page.goto('/ting/plan');

  await expect(page.getByRole('form', { name: 'Plan prompt form' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Describe your goal' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Plan wizard steps' })).toBeVisible();
});

test('plan wizard advances to clarifying questions', async ({ page }) => {
  await mockPlanWizardApi(page);
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();

  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });
  await expect(page.getByRole('heading', { name: 'Clarify your plan' })).toBeVisible();
});

test('plan wizard can decompose into running and review states', async ({ page }) => {
  await mockPlanWizardApi(page);
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();
  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });

  await page.getByRole('button', { name: 'Decompose ->' }).click();
  await expect(page.getByLabel('Decomposing plan')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Review your plan' })).toBeVisible({
    timeout: 10000,
  });
});

test('plan wizard can approve into the launched state', async ({ page }) => {
  await mockPlanWizardApi(page);
  await page.goto('/ting/plan');

  await page.getByRole('textbox', { name: 'Goal description' }).fill('Build auth module');
  await page.getByRole('button', { name: 'Next →' }).click();
  await expect(page.getByRole('form', { name: 'Clarifying questions form' })).toBeVisible({
    timeout: 5000,
  });

  await page.getByRole('button', { name: 'Decompose ->' }).click();
  await expect(page.getByRole('heading', { name: 'Review your plan' })).toBeVisible({
    timeout: 10000,
  });

  await page.getByRole('button', { name: 'Approve & Launch' }).click();
  await expect(page.getByTestId('plan-approved')).toBeVisible({ timeout: 5000 });
});

test('workflow catalog opens the existing builder', async ({ page }) => {
  await page.goto('/ting/workflows');

  await expect(page.getByTestId('simple-workflows-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('ting-tab-builder')).toHaveText(/Builder/);
  await expect(page.getByTestId('simple-workflow-edit')).toHaveText('Open in builder');
  await page.getByTestId('simple-workflow-edit').click();
  await expect(page).toHaveURL(/\/ting\/workflows\/build\?id=/);
  await expect(page.getByTestId('workflow-builder-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('workflow-builder')).toBeVisible();
  await expect(page.getByTestId('graph-view')).toBeVisible();
  await expect(page.getByTestId('ting-tab-builder')).toHaveClass(/--active/);
  await expect(page.getByTestId('ting-tab-workflows')).not.toHaveClass(/--active/);
  await page.getByTestId('ting-tab-work').click();
  await page.getByTestId('ting-tab-builder').click();
  await expect(page).toHaveURL(/\/ting\/workflows\/build/);
  await expect(page.getByTestId('workflow-builder-page')).toBeVisible();
});
