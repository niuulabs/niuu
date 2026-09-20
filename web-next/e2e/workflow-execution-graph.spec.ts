import { expect, test, type Page } from '@playwright/test';
import {
  traceFixture,
  traceEvent,
} from '../packages/plugin-ting/src/application/workflowExecutionTrace.fixture';

const run = {
  executionId: 'run',
  name: 'Graph proof',
  prompt: 'Repair parser',
  repo: 'repo',
  workflowId: 'workflow',
  state: 'completed',
  suspensionReason: '',
  currentGeneration: 2,
  children: [],
  join: null,
  budget: { totalUnits: 10, spentUnits: 1, reservedUnits: 0, availableUnits: 9 },
  createdAt: '',
  updatedAt: '2026-09-19T12:00:00Z',
};
async function configure(page: Page) {
  await page.route('**/config*.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.ting = { mode: 'http', baseUrl: '/api/v1/ting' };
    config.services['ting.workflows'] = { mode: 'http', baseUrl: '/api/v1/ting' };
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
  await page.route('**/api/v1/ting/workflows', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/v1/ting/workflow-executions**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/evidence')) return route.fulfill({ json: {} });
    if (path.endsWith('/delivery-waits')) return route.fulfill({ json: [] });
    return route.fulfill({
      json: path.endsWith('/workflow-executions') ? { executions: [run], nextCursor: null } : run,
    });
  });
}
async function openGraph(page: Page) {
  await page.goto('/ting/workflows/runs');
  await page.getByRole('button', { name: 'Graph proof · completed' }).click();
  await page.getByRole('button', { name: 'View execution graph' }).focus();
  await page.keyboard.press('Enter');
}

test('inspects recorded stage outcomes, expands an exact child, and opens Markdown evidence', async ({
  page,
}) => {
  await configure(page);
  let childRequested = false;
  await page.route('**/workflow-executions/run/trace**', async (route) => {
    const childId = new URL(route.request().url()).searchParams.get('childId');
    childRequested ||= childId === 'c1';
    return route.fulfill({
      json: childId
        ? {
            ...traceFixture,
            selectedChildId: 'c1',
            selectedSessionId: 'child',
            workflow: {
              ...traceFixture.workflow,
              name: 'Parser child',
              graph: { nodes: [{ id: 'coder', label: 'Coder', kind: 'stage' }], edges: [] },
            },
            events: [
              {
                ...traceEvent,
                sessionId: 'child',
                nodeIds: ['coder'],
                summary: 'Implemented parser repair',
              },
            ],
          }
        : traceFixture,
    });
  });
  await openGraph(page);
  const review = page.getByRole('button', { name: 'Review, Output recorded', exact: true });
  await review.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Request changes', { exact: true })).toBeVisible();
  await expect(page.getByText('Reject invalid input', { exact: false }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Workers, Unobserved', exact: true }).click();
  await page.getByRole('button', { name: /parser · generation 1 · attempt 1/ }).click();
  await expect.poll(() => childRequested).toBe(true);
  await expect(page.getByLabel('Workflow execution')).toBeFocused();
  await page.getByRole('button', { name: 'Coder, Output recorded', exact: true }).click();
  await expect(page.getByText('Implemented parser repair', { exact: true })).toBeVisible();
  const zoom = page.getByRole('button', { name: 'Zoom in', exact: true });
  await zoom.focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', { name: 'Fit', exact: true }).click();
  await page.getByRole('button', { name: 'Open run evidence', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Execution evidence', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download Markdown' })).toBeVisible();
});

test('shows pending history and a recoverable load error', async ({ page }) => {
  await configure(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let failed = true;
  await page.route('**/workflow-executions/run/trace**', async (route) => {
    await gate;
    if (failed) return route.fulfill({ status: 503, json: { detail: 'History unavailable' } });
    return route.fulfill({ json: traceFixture });
  });
  await openGraph(page);
  await expect(page.getByText('Loading execution graph…')).toBeVisible();
  release();
  await expect(page.getByRole('alert')).toContainText('Execution graph could not be refreshed', {
    timeout: 15000,
  });
  failed = false;
  await page.getByRole('button', { name: 'Retry graph' }).click();
  await expect(
    page.getByRole('button', { name: 'Review, Output recorded', exact: true }),
  ).toBeVisible();
});
