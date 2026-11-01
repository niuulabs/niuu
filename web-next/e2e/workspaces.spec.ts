import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
config.services.setup = { mode: 'http', baseUrl: '/api/v1/niuu/setup' };
config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };

// An already configured installation: the first-run wizard stays out of the way.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/niuu/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true, steps: [], completedSteps: [] } }),
  );
  await page.route('**/api/v1/integrations{,/**}', (route) => route.fulfill({ json: [] }));
  await page.route(/\/config(?:\.live)?\.json$/, (route) => route.fulfill({ json: config }));
});

test('workspaces are grouped under the project they serve', async ({ page }) => {
  await page.goto('/realms');

  const project = page.getByTestId('project-group-project-yggdrasil');
  await expect(project.getByTestId('realm-card-valhalla')).toBeVisible({ timeout: 8_000 });
  await expect(project.getByTestId('realm-card-printer-forge')).toBeVisible();
  await expect(page.getByTestId('project-group-none')).toContainText('Not in a project');
});

test('a project shows its workspaces and what they share', async ({ page }) => {
  await page.goto('/realms');
  await page.getByRole('link', { name: 'Yggdrasil' }).click();

  await expect(page.getByTestId('project-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('project-workspaces')).toContainText('Valhalla');
  await expect(page.getByRole('textbox', { name: 'Project brief' })).toHaveValue(
    'The residents that keep the clusters.',
  );
});

test('a project nobody can find says so', async ({ page }) => {
  await page.goto('/realms/projects/nope');

  await expect(page.getByText('No such project')).toBeVisible({ timeout: 8_000 });
  await page.getByRole('link', { name: 'Back to workspaces' }).click();
  await expect(page.getByTestId('realms-home')).toBeVisible();
});

test('a workspace keeps its board, resident and connections in tabs', async ({ page }) => {
  await page.goto('/realms/valhalla');
  await expect(page.getByTestId('realm-project-link')).toHaveText('Yggdrasil', { timeout: 8_000 });
  const tabs = page.getByRole('group', { name: 'Workspace section' });

  await tabs.getByRole('button', { name: /^Board/ }).click();
  await expect(page.getByText('No tracker connected')).toBeVisible();

  await tabs.getByRole('button', { name: 'Resident' }).click();
  await expect(page.getByTestId('realm-settings')).toBeVisible();

  await tabs.getByRole('button', { name: 'Connections' }).click();
  await expect(page.getByTestId('connection-tracker')).toContainText('A workspace works without');
});

test('the old settings address lands on the resident tab', async ({ page }) => {
  await page.goto('/realms/valhalla/settings');

  await expect(page.getByTestId('realm-settings')).toBeVisible({ timeout: 8_000 });
  await expect(page).toHaveURL(/\/realms\/valhalla\?tab=resident/);
});

test('a running resident gets a workspace without a deployment', async ({ page }) => {
  await page.goto('/realms');
  const unclaimed = page.getByTestId('unclaimed-residents');
  await unclaimed.getByRole('button', { name: 'Create workspace' }).first().click();

  await expect(page.getByTestId('adopt-dialog')).toBeVisible();
  await page.getByRole('dialog').getByRole('button', { name: 'Create workspace' }).click();

  await expect(page.getByTestId('realm-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('realm-page')).not.toContainText('no resident');
});
