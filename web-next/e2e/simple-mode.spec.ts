import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
config.services.setup = { mode: 'http', baseUrl: '/api/v1/niuu/setup' };
config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
config.services.features = { mode: 'http', baseUrl: '/api/v1' };

// An already configured installation: the first-run wizard stays out of the way.
test.beforeEach(async ({ page }) => {
  let preferences: Array<{ feature_key: string; visible: boolean; sort_order: number }> = [];
  await page.route('**/api/v1/niuu/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true, steps: [], completedSteps: [] } }),
  );
  await page.route('**/api/v1/integrations{,/**}', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/features/preferences', async (route) => {
    if (route.request().method() === 'PUT') {
      preferences = route.request().postDataJSON() as typeof preferences;
    }
    await route.fulfill({ json: preferences });
  });
  await page.route(/\/config(?:\.live)?\.json$/, (route) => route.fulfill({ json: config }));
});

/**
 * Simple mode: the home page and the simplified faces of sessions, workflows
 * and residents. The rest of the suite runs in Advanced mode (see
 * playwright.config.ts), so each test here opts into Simple mode first.
 */
async function useSimpleMode(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem('niuu.compactUx.mode', 'simple');
  });
}

test('the index route lands on the home page with three choices', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/');
  await expect(page).toHaveURL(/\/home$/);
  await expect(page.getByTestId('home-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('home-choice-session')).toBeVisible();
  await expect(page.getByTestId('home-choice-workflow')).toBeVisible();
  await expect(page.getByTestId('home-choice-realm')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Home' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sessions' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Residents' })).toBeVisible();
});

test('the home page hands off to the session launch page', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/home');
  await page.getByTestId('home-choice-session').getByRole('link').click();
  await expect(page).toHaveURL(/\/volundr\/sessions\/new$/);
  await expect(page.getByTestId('simple-launch-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole('heading', { name: 'What should it work on?' })).toBeVisible();
  await expect(page.getByRole('radiogroup', { name: 'Who works on it' })).toBeVisible();
});

test('the sessions list groups by state and opens a session', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/volundr');
  await expect(page).toHaveURL(/\/volundr\/sessions/);
  await expect(page.getByTestId('simple-sessions-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('simple-session-new')).toBeVisible();
  await expect(page.getByTestId('simple-session-search')).toBeVisible();
  await expect(page.getByTestId('simple-session-list')).toBeVisible();
});

test('the workflow specialist remains reachable from its direct route', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/ting/workflows');
  await expect(page).toHaveURL(/\/ting\/workflows/);
  await expect(page.getByTestId('simple-workflows-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('simple-workflow-launch')).toBeVisible();
  await expect(page.getByTestId('simple-workflow-edit')).toBeVisible();
});

test('the residents page shows its empty state and lists personas', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/ravn');
  await expect(page).toHaveURL(/\/ravn\/residents$/);
  await expect(page.getByTestId('residents-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByText('No residents yet', { exact: true })).toBeVisible();
  await expect(page.getByTestId('personas-section')).toBeVisible();
  await expect(page.getByTestId('resident-deploy-open')).toBeVisible();
});

test('Settings changes the interface mode and preserves it across reload', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/home');
  await expect(page.getByTestId('home-page')).toBeVisible({ timeout: 8_000 });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Display mode', exact: true }).click();
  await expect(page).toHaveURL(/\/settings\/interface\/mode$/);
  const modes = page.getByRole('group', { name: 'Interface mode' });
  await expect(modes.getByRole('button', { name: 'Simple', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  const preferenceSave = page.waitForRequest(
    (request) =>
      request.method() === 'PUT' &&
      new URL(request.url()).pathname === '/api/v1/features/preferences',
  );
  await modes.getByRole('button', { name: 'Advanced', exact: true }).click();
  expect((await preferenceSave).postDataJSON()).toEqual(
    expect.arrayContaining([expect.objectContaining({ feature_key: 'ui.mode', visible: true })]),
  );
  await expect(modes.getByRole('button', { name: 'Advanced', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await page.reload();
  await expect(page).toHaveURL(/\/settings\/interface\/mode$/);
  await expect(
    page
      .getByRole('group', { name: 'Interface mode' })
      .getByRole('button', { name: 'Advanced', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
});

test('escape closes the command palette on the home page', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/home');
  await expect(page.getByTestId('home-page')).toBeVisible({ timeout: 8_000 });
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toBeHidden();
});

test('memory lands on the ask box, the realms and the feed', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/mimir');
  await expect(page.getByTestId('memory-home')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('memory-ask-box')).toBeVisible();
  await expect(page.getByTestId('memory-feed')).toBeVisible();
  await expect(page.getByTestId('memory-mount-local')).toBeVisible();
});

test('asking memory lists pages and the facts behind the answer', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/mimir/ask?q=architecture');
  await expect(page.getByTestId('memory-ask-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('memory-results')).toBeVisible();
  await expect(page.getByTestId('memory-facts')).toBeVisible();
});

test('reading a page shows beliefs, relationships and evidence', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/mimir/read?path=%2Farch%2Foverview&mount=local');
  await expect(page.getByTestId('memory-read-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('memory-facts-zone')).toBeVisible();
  await expect(page.getByTestId('memory-evidence')).toBeVisible();
});
