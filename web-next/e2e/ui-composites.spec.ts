import { test, expect } from '@playwright/test';

test.describe('UI Composites showcase', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ui-showcase');
    await expect(page.getByTestId('ui-showcase')).toBeVisible({ timeout: 5000 });
  });

  test('showcase page loads and renders all sections', async ({ page }) => {
    await expect(page.getByTestId('section-persona-avatar')).toBeVisible();
    await expect(page.getByTestId('section-ravn-avatar')).toBeVisible();
    await expect(page.getByTestId('section-mount-chip')).toBeVisible();
    await expect(page.getByTestId('section-deploy-badge')).toBeVisible();
    await expect(page.getByTestId('section-lifecycle-badge')).toBeVisible();
  });

  test('PersonaAvatar renders all 9 roles', async ({ page }) => {
    const section = page.getByTestId('section-persona-avatar');
    const roles = ['plan', 'build', 'verify', 'review', 'gate', 'audit', 'ship', 'index', 'report'];
    for (const role of roles) {
      await expect(section.locator(`[aria-label="${role} persona"]`)).toBeVisible();
    }
  });

  test('RavnAvatar renders all 9 role variants', async ({ page }) => {
    const section = page.getByTestId('section-ravn-avatar');
    // Each RavnAvatar renders an SVG shape
    const svgs = section.locator('svg');
    await expect(svgs).toHaveCount(9);
  });

  test('MountChip renders all 6 role variants', async ({ page }) => {
    const section = page.getByTestId('section-mount-chip');
    const chips = section.locator('.niuu-mount-chip');
    await expect(chips).toHaveCount(6);
  });

  test('DeployBadge renders all 5 deployment kinds', async ({ page }) => {
    const section = page.getByTestId('section-deploy-badge');
    const kinds = ['k8s', 'systemd', 'pi', 'mobile', 'ephemeral'];
    for (const kind of kinds) {
      // DeployBadge and its inner StateDot both carry aria-label; take the first (outer badge)
      await expect(section.getByLabel(kind).first()).toBeVisible();
    }
  });

  test('LifecycleBadge renders all 7 lifecycle states', async ({ page }) => {
    const section = page.getByTestId('section-lifecycle-badge');
    const states = [
      'provisioning',
      'ready',
      'running',
      'idle',
      'terminating',
      'terminated',
      'failed',
    ];
    for (const state of states) {
      // LifecycleBadge and its inner StateDot both carry aria-label; take the first (outer badge)
      await expect(section.getByLabel(state).first()).toBeVisible();
    }
  });

  test('LifecycleBadge "failed" state renders with critical styling', async ({ page }) => {
    const section = page.getByTestId('section-lifecycle-badge');
    const failedBadge = section.getByLabel('failed').first();
    await expect(failedBadge).toHaveClass(/niuu-lifecycle-badge--failed/);
  });

  test('LifecycleBadge "running" state has a pulsing dot', async ({ page }) => {
    const section = page.getByTestId('section-lifecycle-badge');
    const runningBadge = section.getByLabel('running').first();
    const pulsingDot = runningBadge.locator('.niuu-state-dot--pulse');
    await expect(pulsingDot).toBeVisible();
  });

  test('keyboard accessibility: tab reaches the showcase content', async ({ page }) => {
    await page.keyboard.press('Tab');
    // The page should be keyboard-navigable — focus moves to some interactive element
    const focused = page.locator('button:focus, a:focus, input:focus, [tabindex]:focus');
    await expect(focused.first()).toBeVisible();
  });
});
