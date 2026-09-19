import { test, expect, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';

const config = JSON.parse(
  readFileSync(new URL('../apps/niuu/public/config.json', import.meta.url), 'utf8'),
);
const origin = 'http://project-assignment.invalid';

async function fixture(page: Page, initiallyAssigned = false) {
  const controls = { failSave: false, failMembership: false, delayProjects: false };
  const mutations: { path: string; body: Record<string, unknown> }[] = [];
  let revision = 0;
  let coordination: { project_id: string; role: string } | null = initiallyAssigned
    ? { project_id: 'lexi', role: 'worker' }
    : null;
  const session = () => ({
    id: 'worker',
    name: 'cross-host-worker',
    model: 'gpt-6-astra',
    status: 'running',
    activity_state: 'active',
    instance_id: 'thor',
    instance_name: 'Thor',
    coordination,
    source: { type: 'local_mount', local_path: '/home/thor/code' },
    created_at: '2026-09-19T10:00:00Z',
    last_active: '2026-09-19T10:00:00Z',
  });
  await page.route(/\/config(?:\.live)?\.json$/, (route) =>
    route.fulfill({
      json: {
        ...config,
        theme: 'xteo',
        services: {
          ...config.services,
          niuu: { mode: 'http', baseUrl: origin + '/api/v1/niuu' },
          forge: { mode: 'http', baseUrl: origin + '/api/v1/forge', sessionListTimeoutMs: 2000 },
          volundr: { mode: 'http', baseUrl: origin + '/api/v1/volundr' },
        },
      },
    }),
  );
  await page.route(origin + '/**', async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname;
    if (request.method() === 'PUT') {
      const body = request.postDataJSON();
      mutations.push({ path: path + url.search, body });
      if (controls.failSave)
        return route.fulfill({
          status: 409,
          json: { detail: 'Session project changed; reload before assigning' },
        });
      expect(body).toEqual({
        project_id: 'kit',
        project_instance_id: 'build',
        expected_revision: revision,
      });
      expect(url.searchParams.get('instance_id')).toBe('thor');
      coordination = { project_id: 'kit', role: 'worker' };
      revision++;
      return route.fulfill({ json: { session_id: 'worker', revision, coordination } });
    }
    if (path.endsWith('/instances'))
      return route.fulfill({
        json: ['thor', 'build', 'offline'].map((id) => ({
          id,
          slug: id,
          name: id === 'thor' ? 'Thor' : id === 'build' ? 'Build' : 'Offline',
          kind: 'volundr',
          enabled: true,
          baseUrl: origin,
        })),
      });
    if (path.endsWith('/sessions/worker/project'))
      return route.fulfill(
        controls.failMembership
          ? { status: 501, json: { detail: 'Update this Forge host to enable project assignment' } }
          : { json: { session_id: 'worker', revision, coordination } },
      );
    if (path.endsWith('/projects')) {
      const host = url.searchParams.get('instance_id');
      if (host === 'offline') return route.fulfill({ status: 503, json: { detail: 'Offline' } });
      if (controls.delayProjects) await new Promise((resolve) => setTimeout(resolve, 700));
      const projects = [
        {
          id: 'kit',
          slug: 'kit',
          name: 'Kit',
          status: 'active',
          instance_id: 'build',
          instance_name: 'Build',
        },
        {
          id: 'lexi',
          slug: 'lexi',
          name: 'Lexi',
          status: 'active',
          instance_id: 'thor',
          instance_name: 'Thor',
        },
      ];
      return route.fulfill({
        json: host ? projects.filter((project) => project.instance_id === host) : projects,
      });
    }
    if (path.endsWith('/sessions'))
      return route.fulfill({
        json:
          url.searchParams.get('status') === 'archived' ||
          url.searchParams.get('instance_id') !== 'thor'
            ? []
            : [session()],
      });
    if (path.endsWith('/sessions/worker')) return route.fulfill({ json: session() });
    if (path.endsWith('/conversation')) return route.fulfill({ json: { turns: [] } });
    return route.fulfill({ json: [] });
  });
  return { controls, mutations };
}

for (const phone of [false, true]) {
  test.describe(phone ? 'touch' : 'mouse', () => {
    test.use({
      viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 1000 },
      isMobile: phone,
      hasTouch: phone,
    });
    test(`assigns a running Thor session to Build's Kit project (${phone ? 'phone' : 'desktop'})`, async ({
      page,
    }, info) => {
      const { controls, mutations } = await fixture(page);
      controls.delayProjects = true;
      await page.goto('/volundr/sessions');
      const row = page.getByTestId('pod-entry-worker').locator('..');
      if (phone) await row.getByRole('button', { name: 'Actions for cross-host-worker' }).tap();
      else await row.hover();
      await row.getByRole('button', { name: 'Assign project for cross-host-worker' }).click();
      await expect(page.getByText('Loading projects and session…')).toBeVisible();
      const select = page.getByRole('combobox', { name: 'Project', exact: true });
      await expect(select.getByRole('option', { name: 'Kit · Build' })).toBeAttached();
      await select.selectOption('build:kit');
      await expect(page.getByText(/Could not load projects from Offline/)).toBeVisible();
      await page.screenshot({ path: info.outputPath('assign-project.png') });
      await page.getByRole('button', { name: 'Assign', exact: true }).click();
      await expect(select).toHaveCount(0);
      await page.getByTestId('pod-group-mode-project').click();
      await expect(page.getByTestId('pod-group-kit').getByTestId('pod-entry-worker')).toBeVisible();
      await expect(
        page.getByTestId('pod-entry-worker').locator('.forge-session-row__host'),
      ).toHaveText('Thor');
      await page.reload();
      await expect(page.getByTestId('pod-group-kit').getByTestId('pod-entry-worker')).toBeVisible();
      expect(mutations).toHaveLength(1);
    });
  });
}

test('moves from the title, retains a conflict for reload, and cancels by keyboard', async ({
  page,
}) => {
  const { controls, mutations } = await fixture(page, true);
  await page.goto('/volundr/sessions/worker');
  const title = page.locator('.niuu-live-session__identity');
  await title.getByRole('button', { name: 'Assign project for cross-host-worker' }).click();
  const select = page.getByRole('combobox', { name: 'Project', exact: true });
  await expect(select).toBeFocused();
  await expect(select.getByRole('option', { name: 'Kit · Build' })).toBeAttached();
  await select.selectOption('build:kit');
  controls.failSave = true;
  await page.getByRole('button', { name: 'Move', exact: true }).click();
  await expect(page.getByText('Session project changed; reload before assigning')).toBeVisible();
  await expect(select).toHaveValue('build:kit');
  controls.failSave = false;
  await page.getByRole('button', { name: 'Reload', exact: true }).click();
  await page.getByRole('button', { name: 'Move', exact: true }).click();
  await expect(select).toHaveCount(0);
  await title.getByRole('button', { name: 'Assign project for cross-host-worker' }).click();
  await page.keyboard.press('Escape');
  await expect(select).toHaveCount(0);
  expect(mutations).toHaveLength(2);
});

test('an old session host reports the required upgrade and cannot submit', async ({ page }) => {
  const { controls, mutations } = await fixture(page);
  controls.failMembership = true;
  await page.goto('/volundr/sessions/worker');
  await page
    .locator('.niuu-live-session__identity')
    .getByRole('button', { name: 'Assign project for cross-host-worker' })
    .click();
  await expect(page.getByText('Update this Forge host to enable project assignment')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Assign', exact: true })).toBeDisabled();
  expect(mutations).toHaveLength(0);
});
