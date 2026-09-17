import { readFileSync } from 'node:fs';
import { test, expect } from '@playwright/test';

test('reconnect an account from a failed chat without losing the session', async ({ page }) => {
  const config = JSON.parse(
    readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
  );
  config.services.forge = { mode: 'http', baseUrl: '/api/v1/forge' };
  config.services.volundr = { mode: 'http', baseUrl: '/api/v1/volundr' };
  config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
  config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
  await page.route('**/config*.json', (route) => route.fulfill({ json: config }));
  const session = {
    id: 'reconnect',
    name: 'Reconnect check',
    status: 'failed',
    error: 'Authentication requires reconnection',
    model: 'gpt-test',
    source: { type: 'git', repo: 'https://example.test/project', branch: 'dev' },
    created_at: '2026-09-17T00:00:00Z',
    last_active: '2026-09-17T00:00:00Z',
  };
  let release: () => void = () => {};
  const hold = new Promise<void>((resolve) => {
    release = resolve;
  });
  let fail = true;
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/v1/setup')
      return route.fulfill({ json: { enabled: false, completed: true } });
    if (path.endsWith('/features/modules'))
      return route.fulfill({
        json: [{ key: 'chat', scope: 'session', enabled: true, label: 'Chat', order: 0 }],
      });
    if (path.endsWith('/sessions')) return route.fulfill({ json: [session] });
    if (path.endsWith('/sessions/reconnect')) return route.fulfill({ json: session });
    if (path.endsWith('/conversation')) return route.fulfill({ json: { turns: [] } });
    if (path.endsWith('/integrations/catalog'))
      return route.fulfill({
        json: [
          {
            slug: 'openai',
            name: 'OpenAI',
            integration_type: 'ai_provider',
            credential_schema: {
              required: ['api_key'],
              properties: { api_key: { type: 'password', label: 'API key' } },
            },
            config_schema: {},
          },
        ],
      });
    if (path === '/api/v1/integrations') {
      await hold;
      return route.fulfill({
        json: [
          {
            id: 'openai-work',
            slug: 'openai',
            credential_name: 'openai-work',
            integration_type: 'ai_provider',
            config: {},
            enabled: true,
            credential_status: 'auth_required',
          },
        ],
      });
    }
    if (path === '/api/v1/integrations/openai-work' && route.request().method() === 'PUT') {
      expect(route.request().postDataJSON().credential.api_key).toBe('test-only');
      if (fail) {
        fail = false;
        return route.fulfill({ status: 503, json: { detail: 'Storage unavailable' } });
      }
      return route.fulfill({
        json: {
          id: 'openai-work',
          slug: 'openai',
          credential_name: 'openai-work',
          config: {},
          enabled: true,
        },
      });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto('/volundr/sessions/reconnect');
  await page.getByRole('button', { name: 'Reconnect account' }).click();
  await expect(page.getByText('Loading accounts…')).toBeVisible();
  release();
  await page.getByRole('combobox', { name: 'Account', exact: true }).selectOption('openai-work');
  await page.getByLabel('API key', { exact: false }).fill('test-only');
  await page.getByLabel('API key', { exact: false }).press('Tab');
  await expect(page.getByRole('button', { name: 'Connect OpenAI' })).toBeFocused();
  await page.getByRole('button', { name: 'Connect OpenAI' }).press('Enter');
  await expect(page.getByRole('alert').filter({ hasText: 'Storage unavailable' })).toBeVisible();
  await page.getByRole('button', { name: 'Connect OpenAI' }).click();
  await expect(page.getByText('Account updated.')).toBeVisible();
  await page.getByRole('dialog').press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await expect(page).toHaveURL(/\/sessions\/reconnect$/);
});
