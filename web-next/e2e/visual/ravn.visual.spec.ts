/**
 * Visual regression specs for the Ravn plugin: the ravens workbench and the
 * persona library. The fleet is answered here so the snapshot is stable.
 */

import { test, expect } from '@playwright/test';

const ravens = [
  {
    id: '11111111-1111-4111-8111-111111111111',
    persona_name: 'reviewer',
    resident_name: 'Muninn',
    kind: 'resident',
    status: 'failed',
    model: 'claude-sonnet-4-6',
    created_at: '2026-09-01T10:00:00Z',
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

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('**/api/v1/ravn/ravens', (route) => route.fulfill({ json: ravens }));
  await page.route('**/api/v1/ravn/sessions', (route) => route.fulfill({ json: [] }));
});

test('ravn workbench', async ({ page }) => {
  await page.goto('/ravn');
  await page.waitForSelector('[data-testid="ravn-health-banner"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ravn-workbench.png');
});

test('ravn persona library', async ({ page }) => {
  await page.goto('/ravn/personas?persona=reviewer');
  await page.waitForSelector('[data-testid="persona-sheet"]', { timeout: 10_000 });
  await page.waitForLoadState('networkidle');
  await expect(page).toHaveScreenshot('ravn-persona-library.png');
});
