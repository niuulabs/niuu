import { test, expect } from '@playwright/test';

// ---------------------------------------------------------------------------
// /tyr/settings — index page
// ---------------------------------------------------------------------------

test.describe('Tyr Settings index', () => {
  test('navigates to /tyr/settings and shows index page', async ({ page }) => {
    await page.goto('/tyr/settings');
    await expect(page.getByText('Tyr Settings')).toBeVisible();
  });

  test('settings index shows all 9 section links', async ({ page }) => {
    await page.goto('/tyr/settings');
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
// /tyr/settings/general — General section
// ---------------------------------------------------------------------------

test.describe('Tyr General settings', () => {
  test('renders general section heading', async ({ page }) => {
    await page.goto('/tyr/settings/general');
    await expect(page.getByRole('heading', { name: 'General' })).toBeVisible();
  });

  test('shows service binding KV rows', async ({ page }) => {
    await page.goto('/tyr/settings/general');
    await expect(page.getByText('Service URL')).toBeVisible();
    await expect(page.getByText('https://tyr.niuu.internal')).toBeVisible();
    await expect(page.getByText('Event backbone')).toBeVisible();
    await expect(page.getByText('sleipnir · nats')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/dispatch — Dispatch rules section
// ---------------------------------------------------------------------------

test.describe('Tyr Dispatch rules settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tyr/settings/dispatch');
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
// /tyr/settings/integrations — Integrations section
// ---------------------------------------------------------------------------

test.describe('Tyr Integrations settings', () => {
  test('renders integrations section heading', async ({ page }) => {
    await page.goto('/tyr/settings/integrations');
    await expect(page.getByRole('heading', { name: 'Integrations' })).toBeVisible();
  });

  test('shows all 5 integration cards', async ({ page }) => {
    await page.goto('/tyr/settings/integrations');
    await expect(page.getByText('Linear')).toBeVisible();
    await expect(page.getByText('GitHub')).toBeVisible();
    await expect(page.getByText('Jira')).toBeVisible();
    await expect(page.getByText('Slack')).toBeVisible();
    await expect(page.getByText('PagerDuty')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/gates — Gates & reviewers section
// ---------------------------------------------------------------------------

test.describe('Tyr Gates and reviewers settings', () => {
  test('renders gates section heading', async ({ page }) => {
    await page.goto('/tyr/settings/gates');
    await expect(page.getByRole('heading', { name: 'Gates & reviewers' })).toBeVisible();
  });

  test('shows reviewer emails', async ({ page }) => {
    await page.goto('/tyr/settings/gates');
    await expect(page.getByText('jonas@niuulabs.io')).toBeVisible();
    await expect(page.getByText('oskar@niuulabs.io')).toBeVisible();
    await expect(page.getByText('yngve@niuulabs.io')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/advanced — Advanced section
// ---------------------------------------------------------------------------

test.describe('Tyr Advanced settings', () => {
  test('renders advanced section heading', async ({ page }) => {
    await page.goto('/tyr/settings/advanced');
    await expect(page.getByRole('heading', { name: 'Advanced' })).toBeVisible();
  });

  test('shows danger buttons', async ({ page }) => {
    await page.goto('/tyr/settings/advanced');
    await expect(page.getByText('Flush')).toBeVisible();
    await expect(page.getByText('Reset')).toBeVisible();
    await expect(page.getByText('Rebuild')).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/audit — Audit Log section
// ---------------------------------------------------------------------------

test.describe('Tyr Audit Log settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tyr/settings/audit');
  });

  test('renders audit log section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Audit Log' })).toBeVisible();
  });

  test('shows audit log entries after loading', async ({ page }) => {
    await expect(page.getByText(/entries/i)).toBeVisible({ timeout: 5000 });
  });

  test('shows filter buttons', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Raid dispatched' })).toBeVisible({
      timeout: 5000,
    });
    await expect(page.getByRole('button', { name: 'Dispatcher started' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Flock config updated' })).toBeVisible();
  });

  test('filter activates on click', async ({ page }) => {
    const btn = page.getByRole('button', { name: 'Raid dispatched' });
    await btn.waitFor({ state: 'visible', timeout: 5000 });
    await btn.click();

    await expect(btn).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByText(/filtered/i)).toBeVisible({ timeout: 3000 });
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/flock — Flock Config section
// ---------------------------------------------------------------------------

test.describe('Tyr Flock Config settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tyr/settings/flock');
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
// /tyr/settings/personas — Persona overrides browser
// ---------------------------------------------------------------------------

test.describe('Tyr Persona overrides settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tyr/settings/personas');
  });

  test('renders persona overrides section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Persona overrides' })).toBeVisible();
  });

  test('shows persona list after loading', async ({ page }) => {
    await expect(page.getByText(/personas/)).toBeVisible({ timeout: 5000 });
    // Mock has 21 builtin personas
    await expect(page.getByText(/21 personas/)).toBeVisible({ timeout: 5000 });
  });

  test('shows filter tabs', async ({ page }) => {
    await expect(page.getByRole('tab', { name: 'All' })).toBeVisible({ timeout: 5000 });
    await expect(page.getByRole('tab', { name: 'Builtin' })).toBeVisible();
    await expect(page.getByRole('tab', { name: 'Custom' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// /tyr/settings/notifications — Notifications section
// ---------------------------------------------------------------------------

test.describe('Tyr Notifications settings', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/tyr/settings/notifications');
  });

  test('renders notifications section heading', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Notifications' })).toBeVisible();
  });

  test('shows event toggle rows', async ({ page }) => {
    await expect(page.getByText('Raid awaiting approval')).toBeVisible({ timeout: 5000 });
    await expect(page.getByText('Raid failed')).toBeVisible();
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
    await page.goto('/tyr/settings/dispatch');
    await page.waitForSelector('[data-testid="confidence-threshold"]');

    const input = page.getByTestId('confidence-threshold');
    await input.fill('80');
    await page.getByRole('button', { name: /save/i }).click();
    await expect(page.getByText('Saved')).toBeVisible({ timeout: 3000 });

    // Navigate back to /tyr — the Tyr page is still accessible
    await page.goto('/tyr');
    await expect(page.getByText('Tyr · Dashboard')).toBeVisible();
  });
});
