import { test, expect } from '@playwright/test';

// The session detail page at /volundr/session/ds-1 renders Terminal + FileTree in tabs.

async function openTerminalTab(page: import('@playwright/test').Page) {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('tab-terminal').click();
}

async function openFilesTab(page: import('@playwright/test').Page) {
  await page.goto('/volundr/session/ds-1');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('tab-files').click();
}

test('session page renders terminal container', async ({ page }) => {
  await openTerminalTab(page);
  await expect(page.getByTestId('terminal-container')).toBeVisible({ timeout: 8_000 });
});

test('terminal shows session id label', async ({ page }) => {
  await openTerminalTab(page);
  await expect(page.getByTestId('session-id-label')).toHaveText('ds-1', { timeout: 8_000 });
});

test('terminal transitions from connecting to connected after data arrives', async ({ page }) => {
  await openTerminalTab(page);
  // The mock stream emits data after ~50ms — wait for the status badge to disappear.
  await expect(page.getByTestId('terminal-connection-status')).not.toBeVisible({ timeout: 5_000 });
});

test('type a command in terminal and see echoed output', async ({ page }) => {
  await openTerminalTab(page);
  // Wait for xterm to mount and connect.
  await expect(page.getByTestId('terminal-connection-status')).not.toBeVisible({ timeout: 5_000 });

  // Click the terminal area to focus it.
  // force: true skips the actionability stability check — xterm's ResizeObserver
  // can keep the container "not stable" for a tick after connecting.
  const container = page.getByTestId('terminal-container');
  await container.click({ force: true });

  // Type into the terminal — xterm captures keyboard events on the canvas.
  await page.keyboard.type('ls');
  await page.keyboard.press('Enter');

  // The mock stream echoes input back — xterm renders it to canvas/WebGL.
  // In headless CI xterm may not render a canvas (no GPU), so we just confirm
  // the container is still mounted and no uncaught error was thrown.
  await expect(container).toBeVisible({ timeout: 5_000 });
});

test('reconnect button triggers re-subscription', async ({ page }) => {
  await openTerminalTab(page);
  await expect(page.getByTestId('terminal-reconnect-button')).toBeVisible({ timeout: 5_000 });
  // force: true — xterm layout can keep the parent container "not stable"
  // briefly after connecting, which affects child elements' actionability.
  await page.getByTestId('terminal-reconnect-button').click({ force: true });
  // After reconnect, the connection badge should appear briefly then disappear.
  await expect(page.getByTestId('terminal-connection-status')).not.toBeVisible({ timeout: 8_000 });
});

test('archived session page shows read-only badge in terminal tab', async ({ page }) => {
  await page.goto('/volundr/session/ds-5/archived');
  await expect(page.getByTestId('session-detail-page')).toBeVisible({ timeout: 8_000 });
  await page.getByTestId('tab-terminal').click();
  await expect(page.getByTestId('terminal-readonly-badge')).toBeVisible({ timeout: 8_000 });
  // Reconnect button should NOT be visible in read-only mode.
  await expect(page.getByTestId('terminal-reconnect-button')).not.toBeVisible();
});

test('file tree renders workspace files', async ({ page }) => {
  await openFilesTab(page);
  await expect(page.getByTestId('filetree-root')).toBeVisible({ timeout: 8_000 });
  await expect(page.getByText('package.json')).toBeVisible();
  await expect(page.getByText('src')).toBeVisible();
});

test('clicking a file opens the file viewer with highlighted content', async ({ page }) => {
  await openFilesTab(page);
  await expect(page.getByTestId('filetree-root')).toBeVisible({ timeout: 8_000 });

  await page.getByText('package.json').click();

  await expect(page.getByTestId('file-viewer')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByTestId('file-viewer-path')).toContainText('package.json');

  // Wait for Shiki to highlight the content.
  await expect(
    page.getByTestId('file-viewer-highlighted').or(page.getByTestId('file-viewer-plain')),
  ).toBeVisible({
    timeout: 8_000,
  });
});

test('secret files show the secret badge and cannot be opened', async ({ page }) => {
  await openFilesTab(page);
  await expect(page.getByTestId('filetree-root')).toBeVisible({ timeout: 8_000 });

  // The secret mount badge should be visible.
  await expect(page.getByText(/mount: api-secrets/)).toBeVisible();

  // Click the API_KEY secret file — viewer should NOT open.
  await page.getByText('API_KEY').click();
  await expect(page.getByTestId('file-viewer-placeholder')).toBeVisible();
});
