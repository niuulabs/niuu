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

test('activity groups events into stories and hides routine triage by default', async ({
  page,
}) => {
  await page.goto('/valkyrie/activity');

  await expect(page.getByTestId('activity-page')).toBeVisible({ timeout: 5000 });
  const investigation = page
    .getByTestId('story-investigation')
    .filter({ hasText: 'Registry token rollover broke image pulls' });
  await expect(investigation).toBeVisible();
  // The ambient idle-triage story stays hidden until the operator opts in.
  await expect(page.getByTestId('story-triage')).toHaveCount(0);

  await page.getByRole('button', { name: 'show routine' }).click();
  await expect(page.getByTestId('story-triage')).toContainText(
    'Triaged 6 routine signals — nothing needed',
  );

  // The investigation expands into its causal thread: signal → judgment →
  // court decision → action, with the action's lifecycle visible.
  await investigation.getByRole('button', { name: /registry token rollover/i }).click();
  const thread = page.getByTestId('story-thread');
  await expect(thread).toContainText('Pod ravn-worker-77 stuck in ImagePullBackOff');
  await expect(thread).toContainText('ODIN approved refresh_pull_secret_and_restart');
  await expect(thread).toContainText('Refreshed the registry pull secret');
  await expect(thread.getByTestId('story-action-status')).toHaveText('completed');
});

test('an activity judgment links to the learned skill it used', async ({ page }) => {
  await page.goto('/valkyrie/activity');
  await expect(page.getByTestId('activity-page')).toBeVisible({ timeout: 5000 });

  await page
    .getByTestId('story-investigation')
    .filter({ hasText: 'Registry token rollover broke image pulls' })
    .getByRole('button', { name: /registry token rollover/i })
    .click();
  await page.getByTestId('skill-link-registry_token_refresh_check').click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('pull-secret freshness');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

test('activity debug view keeps the raw event tail', async ({ page }) => {
  await page.goto('/valkyrie/activity');
  await expect(page.getByTestId('activity-page')).toBeVisible({ timeout: 5000 });

  await page.getByRole('button', { name: 'debug view' }).click();
  await expect(page.getByTestId('activity-debug-list')).toBeVisible();
  await expect(page.getByTestId('activity-row').first()).toBeVisible();
});

test('legacy valkyrie routes redirect to console', async ({ page }) => {
  await page.goto('/valkyries/learning');

  await expect(page).toHaveURL(/\/valkyrie$/);
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });
});

test('operator can open a learned skill from the console and read its code', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });

  const panel = page.getByTestId('valkyrie-learned-skills');
  await expect(panel).toContainText('Learned skills');
  await expect(panel).toContainText('k8s_memory_pressure_probe');
  await expect(panel).toContainText('Read-only probe that inspects');

  await panel.getByTestId('learned-skill-k8s_memory_pressure_probe').click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('k8s_memory_pressure_probe');
  await expect(dialog).toContainText('kubernetes>=29.0.0');
  await expect(dialog).toContainText('def run(signal: dict)');
  await expect(dialog).toContainText('def test_matches_oomkilled_pod');

  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});

test('a learned-skill judgment links to the skill viewer', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-learned-activity')).toBeVisible({ timeout: 5000 });

  await page
    .getByTestId('valkyrie-learned-activity')
    .getByTestId('skill-link-k8s_memory_pressure_probe')
    .click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Read-only probe that inspects');
  await expect(dialog).toContainText('def run(signal: dict)');
});

test('the retired realms tab redirects to the console', async ({ page }) => {
  await page.goto('/valkyrie/realms');

  await expect(page).toHaveURL(/\/valkyrie$/);
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });
});

test('console shows the effective build autonomy from the realm grant', async ({ page }) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });

  // Sigrun on env-k8s-valhalla → realm `valhalla` whose build grant is level 2.
  await expect(page.getByTestId('valkyrie-effective-autonomy')).toHaveText(
    'Autonomous · effective (realm grant level 2)',
  );
  await expect(page.getByTestId('valkyrie-configured-autonomy')).toHaveText(
    'configured autonomous',
  );

  // Runa is configured guarded but shares the realm: the grant wins.
  await page.getByRole('button', { name: /Runa/ }).click();
  await expect(page.getByTestId('valkyrie-effective-autonomy')).toHaveText(
    'Autonomous · effective (realm grant level 2)',
  );
  await expect(page.getByTestId('valkyrie-configured-autonomy')).toHaveText('configured guarded');
  await expect(page.getByTestId('valkyrie-authority')).toContainText(
    'realm build governance — valhalla',
  );
  await expect(page.getByTestId('tool-builder-autonomy')).toHaveText('autonomous · level 2');
});

test('operator pins a builder workflow and raises the trust level from the console', async ({
  page,
}) => {
  await page.goto('/valkyrie');
  await expect(page.getByTestId('valkyrie-console-page')).toBeVisible({ timeout: 5000 });

  // Saga lives on env-host-jozef → realm `host-jozef` with no build grant yet.
  await page.getByRole('button', { name: /Saga/ }).click();
  await expect(page.getByTestId('valkyrie-effective-autonomy')).toHaveText(
    'Guarded · configured (no realm build grant)',
  );
  const card = page.getByTestId('tool-builder-card');
  await expect(card.getByTestId('tool-builder-ungranted')).toBeVisible();

  await card.getByLabel('Tool-builder workflow for Jozef host').selectOption('valkyrie-tool-forge');
  await card.getByLabel('Build trust level for Jozef host').selectOption('4');
  await expect(card.getByTestId('tool-builder-level-hint')).toHaveText('yolo');
  await card.getByTestId('tool-builder-save').click();

  await expect(card.getByTestId('tool-builder-autonomy')).toHaveText('yolo · level 4');
  await expect(page.getByTestId('valkyrie-effective-autonomy')).toHaveText(
    'Yolo · effective (realm grant level 4)',
  );
});
