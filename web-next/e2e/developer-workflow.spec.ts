import { expect, test, type Page } from '@playwright/test';

const execution = {
  executionId: 'delivery-1',
  name: 'Parser ticket',
  prompt: 'Fix parser',
  repo: 'https://git.example/repo',
  baseBranch: 'proof-target',
  baseSha: 'base-sha',
  workflowId: '00000000-0000-4000-8000-000000000001',
  state: 'waiting',
  suspensionReason: 'Waiting for child review',
  currentGeneration: 1,
  planRevision: 'plan-1',
  budget: { totalUnits: 100, reservedUnits: 10, spentUnits: 20, availableUnits: 70 },
  join: {},
  integrationCandidate: {
    repository: 'https://git.example/repo',
    base_sha: 'base-sha',
    candidate_sha: 'a'.repeat(40),
  },
  children: [
    {
      childId: 'child-1',
      childKey: 'parser',
      attempt: 1,
      state: 'blocked',
      dependencies: [],
      taskHandle: { agentId: 'worker', taskId: 'task-1' },
      workspace: null,
      evidenceValidation: { accepted: true, blocking_reasons: [] },
      candidate: {
        attemptId: 'child-1',
        candidateSha: 'verified-sha',
        candidateTree: 'verified-tree',
        verificationReceipts: [
          {
            receipt_id: 'check-1',
            contract_id: 'parser-tests',
            exit_code: 0,
            provenance: { signature: 'present' },
          },
        ],
        reviewReceipts: [],
      },
      error: null,
      pendingQuestions: [
        {
          requestId: 'question-1',
          persona: 'coder',
          question: 'Should empty input be rejected?',
          reason: 'The ticket does not specify this case.',
          recommendation: 'Reject empty input.',
          attempted: [],
        },
      ],
    },
  ],
  createdAt: '',
  updatedAt: '',
};

async function configure(page: Page) {
  await page.route('**/config*.json', async (route) => {
    const response = await route.fetch();
    const config = await response.json();
    config.services.ting = { mode: 'http', baseUrl: '/api/v1/ting' };
    config.services['ting.workflows'] = { mode: 'http', baseUrl: '/api/v1/ting' };
    config.services.setup = { mode: 'http', baseUrl: '/api/v1/setup' };
    config.services.integrations = { mode: 'http', baseUrl: '/api/v1/integrations' };
    await route.fulfill({ json: config });
  });
  await page.route('**/api/v1/setup', (route) =>
    route.fulfill({ json: { enabled: false, completed: true } }),
  );
  await page.route('**/api/v1/ting/workflows', (route) =>
    route.fulfill({
      json: [
        {
          id: execution.workflowId,
          name: 'Developer delivery',
          schema_version: 2,
          scope: 'system',
          owner_id: null,
          graph: { executionContract: 'developer-delivery/v1' },
          nodes: [],
          edges: [],
          persona_dependencies: {},
          workflow_dependencies: {},
        },
      ],
    }),
  );
}

test('launches a ticket, retries the exact attempt, shows evidence and cancellation', async ({
  page,
}) => {
  await configure(page);
  let created = false;
  let canceled = false;
  let retried = false;
  await page.route('**/api/v1/ting/workflow-executions**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/evidence'))
      return route.fulfill({
        json: {
          schemaVersion: 1,
          executionId: execution.executionId,
          workflowDigest: 'sha256:workflow',
          verification: { status: 'accepted', blockingReasons: [] },
        },
      });
    if (path.endsWith('/delivery-waits'))
      return route.fulfill({
        json: [
          {
            waitId: 'wait-1',
            executionId: execution.executionId,
            mode: 'checks',
            state: 'ready',
            requestDigest: 'request-digest',
            generation: 1,
            executionRevision: 4,
            candidateDigest: 'candidate-digest',
            request: {
              repository: execution.repo,
              reviewNumber: 18,
              expectedHeadSha: 'a'.repeat(40),
              expectedBaseSha: 'base-sha',
              expectedTargetBranch: 'proof-target',
              policyId: 'policy',
            },
            nextPollAt: '2026-01-01T00:00:00Z',
            attemptCount: 1,
            lastError: '',
            observation: {
              status: 'checks_passed',
              repository: execution.repo,
              reviewNumber: 18,
              expectedHeadSha: 'a'.repeat(40),
              expectedBaseSha: 'base-sha',
              expectedTargetBranch: 'proof-target',
              observedAt: '2026-01-01T00:00:00Z',
              reason: '',
              candidate: null,
              mergeReceipt: null,
              checks: {
                receipt_id: 'remote-receipt',
                provider: 'forge-provider',
                repository: execution.repo,
                review_number: 18,
                candidate_sha: 'a'.repeat(40),
                tested_base_sha: 'base-sha',
                observed_at: '2026-01-01T00:00:00Z',
                provenance: null,
                checks: [{ name: 'remote-ci', conclusion: 'passing', details_url: null }],
              },
            },
          },
        ],
      });
    if (path.endsWith('/cancel')) canceled = true;
    if (path.endsWith('/children/parser/retry')) {
      expect(route.request().postDataJSON()).toEqual({ attempt_id: 'child-1' });
      retried = true;
    }
    if (path.endsWith('/workflow-executions') && route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      expect(route.request().headers()['idempotency-key']).toMatch(
        /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
      expect(body.prompt).toBe('Fix parser');
      expect(body.baseBranch).toBe('proof-target');
      created = true;
      return route.fulfill({ status: 201, json: execution });
    }
    const run = { ...execution, state: canceled ? 'canceled' : execution.state };
    return route.fulfill({
      json: path.endsWith('/workflow-executions')
        ? { executions: created ? [run] : [], nextCursor: null }
        : run,
    });
  });
  await page.goto('/ting/workflows/runs');
  await expect(page.getByRole('heading', { name: 'Developer workflows' })).toBeVisible();
  await page
    .getByRole('combobox', { name: 'Workflow', exact: true })
    .selectOption(execution.workflowId);
  await page.getByLabel('Ticket or objective').fill('Fix parser');
  await page.getByLabel('Repository', { exact: true }).fill('https://git.example/repo');
  await page.getByLabel('Target branch').fill('proof-target');
  await page.getByRole('button', { name: 'Start workflow' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('table')).toContainText('parser');
  await expect(page.getByRole('table')).toContainText('task-1');
  await expect(page.getByRole('table')).toContainText('Should empty input be rejected?');
  await expect(page.getByRole('table')).toContainText('Recommendation: Reject empty input.');
  await page.getByRole('button', { name: 'Retry parser' }).click();
  await expect.poll(() => retried).toBe(true);
  await page.getByRole('button', { name: 'View evidence' }).click();
  await expect(page.getByRole('region', { name: 'Execution evidence', exact: true })).toContainText(
    'verified-sha',
  );
  await expect(page.getByRole('heading', { name: 'Workflow results' })).toBeVisible();
  await expect(page.getByText(/does not independently verify its cryptography/i)).toBeVisible();
  await expect(page.getByText('Remote verification · review #18')).toBeVisible();
  await expect(page.getByRole('cell', { name: 'remote-ci' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'passing' })).toBeVisible();
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.getByRole('button', { name: 'Copy Markdown' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('[role="status"]').filter({ hasText: 'Markdown copied' })).toHaveText(
    'Markdown copied',
  );
  await page.getByText('Raw evidence JSON').click();
  await expect(page.getByLabel('Raw execution evidence')).toContainText('sha256:workflow');
  await page.getByRole('button', { name: 'Cancel run' }).click();
  await expect(page.getByRole('button', { name: 'Cancel run' })).toBeDisabled();
});

test('shows loading then an actionable service error', async ({ page }) => {
  await configure(page);
  let release!: () => void;
  const ready = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route('**/api/v1/ting/workflow-executions', async (route) => {
    await ready;
    return route.fulfill({
      status: 503,
      json: { detail: 'Developer execution is not configured' },
    });
  });
  await page.goto('/ting/workflows/runs');
  await expect(page.getByText('Loading runs…')).toBeVisible();
  release();
  await expect(page.getByRole('alert')).toContainText('Developer execution is not configured', {
    timeout: 15000,
  });
});
