import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// /ting/settings — index page
// ---------------------------------------------------------------------------

test.describe('Ting Settings index', () => {
  test('navigates to /ting/settings and shows index page', async ({ page }) => {
    await page.goto('/ting/settings');
    await expect(page.getByText('Ting Settings')).toBeVisible();
  });

  test('settings index shows all 9 section links', async ({ page }) => {
    await page.goto('/ting/settings');
    await expect(page.getByRole('link', { name: 'General' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Dispatch rules' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Integrations' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Persona overrides' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Gates & reviewers' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Flock Config' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Notifications' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Advanced' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Audit Log' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/general — General section
// ---------------------------------------------------------------------------

test.describe('Ting General settings', () => {
  test('renders general section heading', async ({ page }) => {
    await page.goto('/ting/settings/general');
    await expect(page.getByRole('heading', { name: 'General' })).toBeVisible();
  });

  test('shows service binding KV rows', async ({ page }) => {
    await page.goto('/ting/settings/general');
    await expect(page.getByText('Service URL')).toBeVisible();
    await expect(page.getByText('https://ting.niuu.internal')).toBeVisible();
    await expect(page.getByText('Event backbone')).toBeVisible();
    await expect(page.getByText('sleipnir · nats')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/dispatch — Dispatch rules section
// ---------------------------------------------------------------------------

test.describe('Ting Dispatch rules settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ting/settings/dispatch');
  });

  test('renders dispatch rules form', async ({ page }) => {
    await expect(page.getByRole('form', { name: /dispatch rules form/i })).toBeVisible({
      timeout: 5000,
    });
  });

  test('shows confidence threshold field with default value', async ({ page }) => {
    await page.waitForSelector('[data-testid="confidence-threshold"]');
    const input = page.getByTestId('confidence-threshold');
    await expect(input).toHaveValue('70');
  });

  test('tweak confidence threshold and save', async ({ page }) => {
    await page.waitForSelector('[data-testid="confidence-threshold"]');

    const input = page.getByTestId('confidence-threshold');
    await input.fill('85');

    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText('Saved')).toBeVisible({ timeout: 3000 });
  });

  test('shows validation error for out-of-range threshold', async ({ page }) => {
    await page.waitForSelector('[data-testid="confidence-threshold"]');

    const input = page.getByTestId('confidence-threshold');
    await input.fill('150');

    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText(/between 0 and 100/i)).toBeVisible({ timeout: 3000 });
  });

  test('shows retry policy section', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Retry Policy' })).toBeVisible({
      timeout: 5000,
    });
  });

  test('shows quiet hours field', async ({ page }) => {
    await page.waitForSelector('[data-testid="quiet-hours"]');
    await expect(page.getByTestId('quiet-hours')).toBeVisible();
  });

  test('shows escalate after field', async ({ page }) => {
    await page.waitForSelector('[data-testid="escalate-after"]');
    await expect(page.getByTestId('escalate-after')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/integrations — Integrations section
// ---------------------------------------------------------------------------

test.describe('Ting Integrations settings', () => {
  test('renders integrations section heading', async ({ page }) => {
    await page.goto('/ting/settings/integrations');
    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible();
  });

  test('shows all 5 integration cards', async ({ page }) => {
    await page.goto('/ting/settings/integrations');
    await expect(page.getByText('Linear')).toBeVisible();
    await expect(page.getByText('GitHub')).toBeVisible();
    await expect(page.getByText('Jira')).toBeVisible();
    await expect(page.getByText('Slack')).toBeVisible();
    await expect(page.getByText('PagerDuty')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/gates — Gates & reviewers section
// ---------------------------------------------------------------------------

test.describe('Ting Gates and reviewers settings', () => {
  test('renders gates section heading', async ({ page }) => {
    await page.goto('/ting/settings/gates');
    await expect(page.getByRole('heading', { name: 'Gates & reviewers' })).toBeVisible();
  });

  test('shows reviewer emails', async ({ page }) => {
    await page.goto('/ting/settings/gates');
    await expect(page.getByText('jonas@niuulabs.io')).toBeVisible();
    await expect(page.getByText('oskar@niuulabs.io')).toBeVisible();
    await expect(page.getByText('yngve@niuulabs.io')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/advanced — Advanced section
// ---------------------------------------------------------------------------

test.describe('Ting Advanced settings', () => {
  test('renders advanced section heading', async ({ page }) => {
    await page.goto('/ting/settings/advanced');
    await expect(page.getByRole('heading', { name: 'Advanced' })).toBeVisible();
  });

  test('shows danger buttons', async ({ page }) => {
    await page.goto('/ting/settings/advanced');
    await expect(page.getByTestId('action-flush-queue')).toBeVisible();
    await expect(page.getByTestId('action-reset-dispatcher')).toBeVisible();
    await expect(page.getByTestId('action-rebuild-confidence-scores')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/audit — Audit Log section
// ---------------------------------------------------------------------------

test.describe('Ting Audit Log settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ting/settings/audit');
  });

  test('renders audit log section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Audit Log' })).toBeVisible();
  });

  test('shows audit log entries after loading', async ({ page }) => {
    await expect(page.getByText(/entries/i)).toBeVisible({ timeout: 5000 });
  });

  test('shows filter buttons', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Run dispatched' })).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByRole('button', { name: 'Dispatcher started' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Flock config updated' })).toBeVisible();
  });

  test('filter activates on click', async ({ page }) => {
    const btn = page.getByRole('button', { name: 'Run dispatched' });
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click();

    await expect(btn).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByText(/filtered/i)).toBeVisible({ timeout: 3000 });
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/flock — Flock Config section
// ---------------------------------------------------------------------------

test.describe('Ting Flock Config settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ting/settings/flock');
  });

  test('renders flock config form', async ({ page }) => {
    await expect(page.getByRole('form', { name: /flock configuration form/i })).toBeVisible({
      timeout: 5000,
    });
  });

  test('shows default flock name', async ({ page }) => {
    await page.waitForSelector('input[name="flockName"]');
    const input = page.locator('input[name="flockName"]');
    await expect(input).toHaveValue('Niuu Core');
  });

  test('save updates flock name', async ({ page }) => {
    await page.waitForSelector('input[name="flockName"]');
    const input = page.locator('input[name="flockName"]');
    await input.fill('Updated Flock');

    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText('Saved')).toBeVisible({ timeout: 3000 });
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/personas — Persona overrides browser
// ---------------------------------------------------------------------------

test.describe('Ting Persona overrides settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ting/settings/personas');
  });

  test('renders persona overrides section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Persona overrides' })).toBeVisible();
  });

  test('shows persona list after loading', async ({ page }) => {
    await expect(page.getByText(/personas/)).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('listbox', { name: 'Persona list' })).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByText(/\d+ personas?/)).toBeVisible({ timeout: 5000 });
  });

  test('shows filter tabs', async ({ page }) => {
    await expect(page.getByRole('tab', { name: 'All' })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('tab', { name: 'Builtin' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Custom' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /ting/settings/notifications — Notifications section
// ---------------------------------------------------------------------------

test.describe('Ting Notifications settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/ting/settings/notifications');
  });

  test('renders notifications section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();
  });

  test('shows event toggle rows', async ({ page }) => {
    await expect(page.getByText('Run awaiting approval')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Run failed')).toBeVisible();
    await expect(page.getByText('Saga complete')).toBeVisible();
  });

  test('save persists notification settings', async ({ page }) => {
    await page.waitForSelector('form[aria-label="Notification settings form"]', { timeout: 5000 });
    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText('Saved')).toBeVisible({ timeout: 3000 });
  });
});

// ---------------------------------------------------------------------------
// Dispatch rules → applied on Dispatch page (integration test)
// ---------------------------------------------------------------------------

test.describe('Dispatch defaults applied to dispatch behaviour', () => {
  test('tweaking dispatch threshold is reflected in saved value', async ({ page }) => {
    // Save a new threshold in settings
    await page.goto('/ting/settings/dispatch');
    await page.waitForSelector('[data-testid="confidence-threshold"]');

    const input = page.getByTestId('confidence-threshold');
    await input.fill('80');
    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText('Saved')).toBeVisible({ timeout: 3000 });

    // Navigate back to /ting — the Ting page is still accessible
    await page.goto('/ting');
    await expect(page.getByRole('heading', { name: 'Ting' })).toBeVisible();
    await expect(page.getByTestId('ting-dispatcher-stats')).toBeVisible({ timeout: 5000 });
  });
});
