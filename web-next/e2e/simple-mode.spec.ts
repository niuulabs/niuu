import { test, expect, type Page } from '@playwright/test';

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

test('the workflows page shows a workflow, its strip and a launch card', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/ting');
  await expect(page).toHaveURL(/\/ting\/workflows/);
  await expect(page.getByTestId('simple-workflows-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('simple-workflow-launch')).toBeVisible();
  await expect(page.getByTestId('simple-workflow-edit')).toBeVisible();
});

test('the residents page lists residents and personas', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/ravn');
  await expect(page).toHaveURL(/\/ravn\/residents$/);
  await expect(page.getByTestId('residents-page')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByTestId('resident-cards')).toBeVisible();
  await expect(page.getByTestId('personas-section')).toBeVisible();
  await expect(page.getByTestId('resident-deploy-open')).toBeVisible();
});

test('the home page can switch to the Advanced dashboard', async ({ page }) => {
  await useSimpleMode(page);
  await page.goto('/home');
  await expect(page.getByTestId('home-page')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('home-open-advanced').click();
  await expect(page).toHaveURL(/\/volundr\/forge$/);
  await expect(page.getByTestId('ui-mode-switch')).toHaveAttribute('data-mode', 'advanced');
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
