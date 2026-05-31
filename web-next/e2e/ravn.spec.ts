import { test, expect } from '@playwright/test';

test('ravn overview renders at /ravn', async ({ page }) => {
  await page.goto('/ravn');
  await expect(page.getByTestId('ravn-page')).toBeVisible();
  await expect(page.getByTestId('overview-page')).toBeVisible({ timeout: 5000 });
});

test('ravn overview shows current fleet telemetry', async ({ page }) => {
  await page.goto('/ravn');

  await expect(page.getByTestId('kpi-ravens')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('kpi-sessions')).toBeVisible();
  await expect(page.getByTestId('kpi-spend')).toBeVisible();
  await expect(page.getByTestId('kpi-triggers')).toBeVisible();
  await expect(page.getByTestId('active-ravens-list')).toBeVisible();
  await expect(page.getByTestId('fleet-sparkline')).toBeVisible();
  await expect(page.getByTestId('activity-log')).toBeVisible();
});

test('ravens directory renders with a selected detail pane by default', async ({ page }) => {
  await page.goto('/ravn/ravens');

  await expect(page.getByTestId('ravens-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('ravens-sidebar')).toBeVisible();
  await expect(page.getByTestId('ravn-detail')).toBeVisible();
  await expect(page.getByTestId('detail-empty')).toHaveCount(0);
});

test('ravens grouping controls switch modes', async ({ page }) => {
  await page.goto('/ravn/ravens');

  const groupByState = page.getByTestId('group-btn-state');
  const groupByPersona = page.getByTestId('group-btn-persona');
  const groupByFlat = page.getByTestId('group-btn-none');

  await groupByState.click();
  await expect(groupByState).toHaveAttribute('aria-pressed', 'true');

  await groupByPersona.click();
  await expect(groupByPersona).toHaveAttribute('aria-pressed', 'true');

  await groupByFlat.click();
  await expect(groupByFlat).toHaveAttribute('aria-pressed', 'true');
});

test('ravens search filters the directory and can recover', async ({ page }) => {
  await page.goto('/ravn/ravens');

  const search = page.getByTestId('ravens-search');
  await expect(page.getByTestId('ravn-list-row').first()).toBeVisible({ timeout: 5000 });

  await search.fill('definitely-not-a-ravn');
  await expect(page.getByText(/no ravens match/i)).toBeVisible();

  await search.fill('');
  await expect(page.getByTestId('ravn-list-row').first()).toBeVisible({ timeout: 5000 });
});

test('ravn detail tabs switch between current sections', async ({ page }) => {
  await page.goto('/ravn/ravens');

  await expect(page.getByTestId('ravn-detail')).toBeVisible({ timeout: 5000 });

  await page.getByTestId('sectab-activity').click();
  await expect(page.getByTestId('activity-section-body')).toBeVisible();

  await page.getByTestId('sectab-sessions').click();
  await expect(page.getByTestId('sessions-section-body')).toBeVisible();

  await page.getByTestId('sectab-connectivity').click();
  await expect(page.getByTestId('connectivity-section-body')).toBeVisible();

  await page.getByTestId('sectab-overview').click();
  await expect(page.getByTestId('section-body-overview')).toBeVisible();
});

test('personas route renders the directory and detail workspace', async ({ page }) => {
  await page.goto('/ravn/personas');

  await expect(page.getByTestId('personas-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('personas-directory')).toBeVisible();
  await expect(page.getByTestId('personas-detail-pane')).toBeVisible();
});

test('personas route can switch to the YAML pane for a selected persona', async ({ page }) => {
  await page.goto('/ravn/personas');

  await expect(page.getByTestId('personas-directory')).toBeVisible({ timeout: 5000 });
  await page.getByTestId('personas-directory').getByRole('button').first().click();
  await page.getByRole('tab', { name: 'YAML' }).click();

  await expect(
    page.getByTestId('persona-yaml').or(page.getByTestId('persona-yaml-loading')),
  ).toBeVisible({ timeout: 5000 });
});

test('sessions route renders the transcript and context panes', async ({ page }) => {
  await page.goto('/ravn/sessions');

  await expect(page.getByTestId('sessions-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('sessions-header')).toBeVisible();
  await expect(page.getByRole('log', { name: 'Session transcript' })).toBeVisible();
  await expect(page.getByTestId('sessions-context')).toBeVisible();
});

test('sessions route surfaces active and closed session counts', async ({ page }) => {
  await page.goto('/ravn/sessions');
  await expect(page.getByText(/\d+ active · \d+ closed/)).toBeVisible({ timeout: 5000 });
});

test('sessions route can switch transcript filters', async ({ page }) => {
  await page.goto('/ravn/sessions');

  const toolsFilter = page.getByRole('button', { name: '+ tools' });
  await toolsFilter.click();
  await expect(toolsFilter).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('log', { name: 'Session transcript' })).toBeVisible();
});

test('budget route renders runway and recommendation surfaces', async ({ page }) => {
  await page.goto('/ravn/budget');

  await expect(page.getByTestId('runway-bar')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('fleet-sparkline')).toBeVisible();
  await expect(page.getByTestId('top-drivers')).toBeVisible();
  await expect(page.getByTestId('recommended-changes')).toBeVisible();
});
