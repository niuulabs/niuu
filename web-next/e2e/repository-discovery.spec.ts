import { expect, test } from '@playwright/test';

test('loads repository metadata first, then selected branches with visible progress and errors', async ({
  page,
}) => {
  page.on('pageerror', (error) => {
    throw error;
  });
  const first = 'https://git.example.com/ncp/vmaas/observability/example.git';
  const second = 'https://git.example.com/ncp/another.git';
  let releaseRepos!: () => void;
  let releaseBranches!: () => void;
  const reposReady = new Promise<void>((resolve) => {
    releaseRepos = resolve;
  });
  const branchesReady = new Promise<void>((resolve) => {
    releaseBranches = resolve;
  });
  const branchRequests: string[] = [];
  await page.route('**/config.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    for (const id of ['ting', 'ravn', 'mimir', 'observatory', 'valkyrie'])
      config.plugins[id] = { enabled: false };
    config.services['niuu.repos'] = { mode: 'http', baseUrl: '/api/v1/niuu' };
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
  await page.route('**/api/v1/niuu/repos', async (route) => {
    await reposReady;
    await route.fulfill({
      json: {
        gitlab: [
          {
            provider: 'gitlab',
            org: 'ncp/vmaas/observability',
            name: 'example',
            clone_url: first,
            url: first,
            default_branch: 'main',
          },
          {
            provider: 'gitlab',
            org: 'ncp',
            name: 'another',
            clone_url: second,
            url: second,
            default_branch: 'main',
          },
        ],
      },
    });
  });
  await page.route('**/api/v1/niuu/repos/branches?*', async (route) => {
    const url = new URL(route.request().url()).searchParams.get('repo_url')!;
    branchRequests.push(url);
    if (url === first) {
      await branchesReady;
      await route.fulfill({ json: ['main', 'dev'] });
    } else {
      await route.fulfill({ status: 502, json: { detail: 'GitLab is unavailable' } });
    }
  });

  await page.goto('/volundr/sessions');
  await expect(page.getByTestId('sessions-page')).toBeVisible();
  await page.getByRole('button', { name: 'Launch a new session' }).click();
  await expect(page.getByText('Loading repositories…')).toBeVisible();
  expect(branchRequests).toEqual([]);
  releaseRepos();
  const picker = page.getByTestId('quick-launch-repo');
  await expect(picker).toBeVisible();
  expect(branchRequests).toEqual([]);
  await picker.selectOption(first);
  await expect(page.getByText('Loading branches…')).toBeVisible();
  expect(branchRequests).toEqual([first]);
  releaseBranches();
  await expect(page.getByTestId('quick-launch-branch')).toBeVisible();
  await page.getByTestId('quick-launch-branch').selectOption('dev');
  await picker.selectOption(second);
  await expect(
    page.getByRole('alert').filter({ hasText: 'Could not load branches' }),
  ).toBeVisible();
  await picker.selectOption(first);
  await expect(page.getByTestId('quick-launch-branch')).toBeVisible();
  expect(branchRequests).toEqual([first, second]);
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('quick-launch')).toHaveCount(0);
});
