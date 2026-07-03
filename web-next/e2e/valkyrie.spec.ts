import { expect, test } from '@playwright/test';

test('odin review inbox lists pending decisions with full lineage', async ({ page }) => {
  await page.goto('/valkyrie/inbox');

  await expect(page.getByTestId('inbox-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('review-card')).toHaveCount(3);
  await expect(page.getByTestId('inbox-pending-count')).toHaveText('3 pending');
  await expect(page.getByTestId('review-detail')).toBeVisible();
  await expect(page.getByTestId('review-lineage')).toBeVisible();
});

test('operator can inspect the artifact and approve a held build', async ({ page }) => {
  await page.goto('/valkyrie/inbox');
  await expect(page.getByTestId('review-card')).toHaveCount(3);

  await page
    .getByTestId('review-card')
    .filter({ hasText: 'valkyrie-inspect-kubernetes-pod-oomkilled' })
    .click();
  await page.getByTestId('review-artifact').getByRole('button', { name: 'Tool' }).click();
  await expect(page.getByTestId('review-artifact')).toContainText('def run(signal: dict)');

  await page.getByLabel('Decision reason').fill('canary verified, safe');
  await page.getByRole('button', { name: /approve/i }).click();

  await expect(page.getByTestId('inbox-pending-count')).toHaveText('2 pending');
});

test('rejecting without a reason is refused', async ({ page }) => {
  await page.goto('/valkyrie/inbox');
  await expect(page.getByTestId('review-card')).toHaveCount(3);

  await page.getByRole('button', { name: /reject/i }).click();

  await expect(page.getByRole('alert')).toContainText('A reason is required');
  await expect(page.getByTestId('inbox-pending-count')).toHaveText('3 pending');
});

test('fleet view exposes autonomy control per resident', async ({ page }) => {
  await page.goto('/valkyrie/fleet');

  await expect(page.getByTestId('fleet-page')).toBeVisible({ timeout: 5000 });
  const cards = page.getByTestId('fleet-card');
  await expect(cards.first()).toBeVisible();
  await expect(cards.first().getByRole('combobox')).toBeVisible();
});

test('console is the default valkyrie view', async ({ page }) => {
  await page.goto('/valkyrie');

  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('valkyrie-roster')).toBeVisible();
  await expect(page.getByTestId('valkyrie-signal-timeline')).toBeVisible();
});

test('console explains the charter, decisions, and pending reviews', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });

  await expect(page.getByTestId('valkyrie-charter')).toContainText('Keep the Valhalla cluster');
  await expect(page.getByTestId('valkyrie-decisions')).toContainText(
    'Handled with a learned skill',
  );
  await expect(page.getByTestId('valkyrie-pending-reviews')).toBeVisible();
  await expect(page.getByTestId('valkyrie-learning')).toContainText('k8s_memory_pressure_probe');
});

test('console decision expands to show rationale and lineage', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-decisions')).toContainText(
    'Handled with a learned skill',
  );

  await page
    .getByTestId('valkyrie-decisions')
    .getByRole('button', { name: /handled with a learned skill/i })
    .click();

  const detail = page.getByTestId('decision-detail-decision-oom-1');
  await expect(detail).toContainText('Installed learning skill k8s_memory_pressure_probe');
  await expect(detail).toContainText('triggered by');
});

test('console signal browser drills into observed signals', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });

  await page.getByRole('button', { name: /browse observed signals/i }).click();
  await expect(page.getByTestId('valkyrie-signal-browser')).toContainText('pod/ravn-worker-77');
});

test('activity shows fleet telemetry', async ({ page }) => {
  await page.goto('/valkyrie/activity');

  await expect(page.getByTestId('activity-page')).toBeVisible({ timeout: 5000 });
  await expect(page.getByTestId('activity-row').first()).toBeVisible();
});

test('legacy valkyrie routes redirect to console', async ({ page }) => {
  await page.goto('/valkyries/learning');

  await expect(page).toHaveURL(/\/valkyrie$/);
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });
});

test('realms view exposes the tool-builder workflow picker', async ({ page }) => {
  await page.goto('/valkyrie/realms');

  await expect(page.getByTestId('realms-page')).toBeVisible({ timeout: 5000 });
  const cards = page.getByTestId('realm-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.first().getByTestId('tool-builder-autonomy')).toHaveText(
    'autonomous · level 2',
  );
  await expect(cards.first().getByLabel(/Tool-builder workflow/)).toBeVisible();
});

test('operator can pin a builder workflow and raise the trust level', async ({ page }) => {
  await page.goto('/valkyrie/realms');

  const midgard = page.getByTestId('realm-card').filter({ hasText: 'Midgard' });
  await expect(midgard.getByTestId('tool-builder-ungranted')).toBeVisible();

  await midgard.getByLabel('Tool-builder workflow for Midgard').selectOption('valkyrie-tool-forge');
  await midgard.getByLabel('Build trust level for Midgard').selectOption('4');
  await expect(midgard.getByTestId('tool-builder-level-hint')).toHaveText('yolo');
  await midgard.getByTestId('tool-builder-save').click();

  await expect(midgard.getByTestId('tool-builder-autonomy')).toHaveText('yolo · level 4');
});
