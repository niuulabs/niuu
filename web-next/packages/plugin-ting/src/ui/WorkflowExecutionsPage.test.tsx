import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { WorkflowExecutionsPage } from './WorkflowExecutionsPage';
import type {
  WorkflowExecution,
  WorkflowChildExecution,
  DeliveryExecution,
  DeliveryChildExecution,
} from '../domain/workflowExecution';
import type { Workflow } from '../domain/workflow';

// A workflow requires the code-delivery pack when a `wait` node's conditions
// include a `forge.*` condition type (see `workflowRequiresDeliveryPack`).
const deliveryWorkflow: Workflow = {
  id: 'workflow',
  name: 'Delivery',
  schemaVersion: 2,
  nodes: [
    {
      id: 'expand',
      kind: 'subworkflow',
      label: 'Execute workstreams',
      position: { x: 0, y: 0 },
      workflowDependency: 'workstream',
      allowedCoordinator: 'coordinator',
      inputSchema: {},
      resultSchema: {},
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
    },
    {
      id: 'forge-wait',
      kind: 'wait',
      label: 'Await authoritative delivery observation',
      conditions: ['forge.checks', 'forge.merge'],
      position: { x: 100, y: 0 },
    },
  ],
  edges: [],
};

// A workflow with a single expansion point and no delivery signal at all.
const genericWorkflow: Workflow = {
  id: 'generic-workflow',
  name: 'Generic',
  schemaVersion: 2,
  nodes: [
    {
      id: 'expand',
      kind: 'subworkflow',
      label: 'Execute children',
      position: { x: 0, y: 0 },
      workflowDependency: 'child',
      allowedCoordinator: 'coordinator',
      inputSchema: {},
      resultSchema: {},
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
    },
  ],
  edges: [],
};

// A workflow with two expansion points, neither delivery-flavored.
const multiExpansionWorkflow: Workflow = {
  id: 'multi-workflow',
  name: 'Multi expansion',
  schemaVersion: 2,
  nodes: [
    {
      id: 'expand-a',
      kind: 'subworkflow',
      label: 'Expansion A',
      position: { x: 0, y: 0 },
      workflowDependency: 'child-a',
      allowedCoordinator: 'coordinator',
      inputSchema: {},
      resultSchema: {},
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
    },
    {
      id: 'expand-b',
      kind: 'subworkflow',
      label: 'Expansion B',
      position: { x: 200, y: 0 },
      workflowDependency: 'child-b',
      allowedCoordinator: 'coordinator',
      inputSchema: {},
      resultSchema: {},
      maxChildren: 10,
      maxAttempts: 3,
      joinMode: 'all',
    },
  ],
  edges: [],
};

// A schema v2 workflow with no subworkflow node — it cannot be launched here.
const noExpansionWorkflow: Workflow = {
  id: 'no-expansion-workflow',
  name: 'No expansion',
  schemaVersion: 2,
  nodes: [
    {
      id: 'stage',
      kind: 'stage',
      label: 'Stage',
      runId: null,
      position: { x: 0, y: 0 },
    },
  ],
  edges: [],
};

const deliveryChild: DeliveryChildExecution = {
  childId: 'child',
  childKey: 'parser',
  attempt: 1,
  state: 'failed',
  dependencies: [],
  taskHandle: { agentId: 'agent', taskId: 'task' },
  workspace: null,
  evidenceValidation: { accepted: true },
  candidate: {
    attemptId: 'child',
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
  error: { kind: 'remote_failed', detail: 'Test failed' },
};

const deliveryRun: DeliveryExecution = {
  executionId: 'execution',
  name: 'Fix parser',
  prompt: 'Fix it',
  repo: 'repo',
  baseBranch: 'main',
  baseSha: 'base-sha',
  workflowId: 'workflow',
  input: {},
  state: 'blocked',
  suspensionReason: 'Review requested changes',
  currentGeneration: 1,
  planRevision: 'plan-1',
  budget: { totalUnits: 100, reservedUnits: 10, spentUnits: 20, availableUnits: 70 },
  join: {},
  createdAt: '',
  updatedAt: '',
  children: [deliveryChild],
};

/** The generic projection the same execution id returns from `/workflow-executions`. */
function genericProjectionOf(execution: DeliveryExecution): WorkflowExecution {
  return {
    executionId: execution.executionId,
    name: execution.name,
    prompt: execution.prompt,
    workflowId: execution.workflowId,
    input: execution.input,
    state: execution.state,
    suspensionReason: execution.suspensionReason,
    currentGeneration: execution.currentGeneration,
    planRevision: execution.planRevision,
    budget: execution.budget,
    join: execution.join,
    createdAt: execution.createdAt,
    updatedAt: execution.updatedAt,
    children: execution.children.map((child): WorkflowChildExecution => ({
      childId: child.childId,
      childKey: child.childKey,
      attempt: child.attempt,
      state: child.state,
      dependencies: child.dependencies,
      input: {},
      taskHandle: child.taskHandle,
      result: null,
      artifacts: [],
      resultValidation: child.evidenceValidation,
      error: child.error,
      pendingQuestions: child.pendingQuestions,
      pendingGates: child.pendingGates,
    })),
  };
}

const genericRun: WorkflowExecution = {
  executionId: 'generic-execution',
  name: 'Rotate secrets',
  prompt: 'Rotate the shared secret',
  workflowId: 'generic-workflow',
  input: {},
  state: 'running',
  suspensionReason: '',
  currentGeneration: 1,
  budget: { totalUnits: 10, reservedUnits: 0, spentUnits: 1, availableUnits: 9 },
  join: {},
  createdAt: '',
  updatedAt: '',
  children: [],
};

function setup(
  overrides: {
    workflowExecutions?: Record<string, unknown>;
    deliveryExecutions?: Record<string, unknown>;
  } = {},
  catalog: Workflow[] = [deliveryWorkflow],
) {
  const workflowExecutionsService = {
    list: vi
      .fn()
      .mockResolvedValue({ executions: [genericProjectionOf(deliveryRun)], nextCursor: null }),
    get: vi.fn().mockResolvedValue(genericProjectionOf(deliveryRun)),
    launch: vi.fn().mockResolvedValue(genericProjectionOf(deliveryRun)),
    cancel: vi.fn().mockResolvedValue({ ...genericProjectionOf(deliveryRun), state: 'canceled' }),
    reconcile: vi.fn().mockResolvedValue(genericProjectionOf(deliveryRun)),
    retry: vi.fn().mockResolvedValue(genericProjectionOf(deliveryRun)),
    waits: vi.fn().mockResolvedValue([]),
    trace: vi.fn(),
    ...overrides.workflowExecutions,
  };
  const deliveryExecutionsService = {
    get: vi.fn().mockResolvedValue(deliveryRun),
    launch: vi.fn().mockResolvedValue(deliveryRun),
    evidence: vi.fn().mockResolvedValue({
      workflowDigest: 'sha256:workflow',
      verification: { status: 'accepted', blockingReasons: [] },
    }),
    ...overrides.deliveryExecutions,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          'ting.workflowExecutions': workflowExecutionsService,
          'ting.deliveryExecutions': deliveryExecutionsService,
          'ting.workflows': { listWorkflows: async () => catalog },
        }}
      >
        <WorkflowExecutionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return { workflowExecutionsService, deliveryExecutionsService };
}

describe('WorkflowExecutionsPage', () => {
  it('loads older runs with the server cursor', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce({
        executions: [genericProjectionOf(deliveryRun)],
        nextCursor: 'older',
      })
      .mockResolvedValueOnce({
        executions: [
          { ...genericProjectionOf(deliveryRun), executionId: 'older', name: 'Older run' },
        ],
        nextCursor: null,
      });
    setup({ workflowExecutions: { list } });
    fireEvent.click(await screen.findByRole('button', { name: 'Load more runs' }));
    expect(await screen.findByRole('button', { name: 'Older run · blocked' })).toBeVisible();
    expect(list).toHaveBeenLastCalledWith({ cursor: 'older' });
  });

  it('shows every outstanding child question and gate and clears resolved input', async () => {
    const waiting = {
      ...genericProjectionOf(deliveryRun),
      children: [
        {
          ...genericProjectionOf(deliveryRun).children[0]!,
          state: 'blocked',
          error: null,
          pendingQuestions: [
            {
              requestId: 'q1',
              persona: 'coder',
              question: 'Which input format?',
              reason: 'Two formats are documented.',
              recommendation: 'Use JSON.',
              attempted: [],
            },
            {
              requestId: 'q2',
              persona: 'reviewer',
              question: 'Keep compatibility?',
              reason: '',
              recommendation: '',
              attempted: [],
            },
          ],
          pendingGates: [
            {
              gateId: 'g1',
              nodeId: 'review',
              label: 'Review decision',
              condition: '',
              instructions: 'Confirm the selected format.',
              summary: 'Implementation is waiting.',
            },
          ],
        },
      ],
    };
    setup({
      workflowExecutions: {
        get: async () => waiting,
        reconcile: async () => ({
          ...waiting,
          children: [
            { ...waiting.children[0]!, state: 'running', pendingQuestions: [], pendingGates: [] },
          ],
        }),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    expect(await screen.findByText('Which input format?')).toBeInTheDocument();
    expect(screen.getByText('Keep compatibility?')).toBeInTheDocument();
    expect(screen.getByText('Recommendation: Use JSON.')).toBeInTheDocument();
    expect(screen.getByText('Confirm the selected format.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh task status' }));
    await waitFor(() => expect(screen.queryByText('Which input format?')).not.toBeInTheDocument());
    expect(screen.queryByText('Review decision')).not.toBeInTheDocument();
  });

  it('preselects the single expansion point of a delivery workflow and launches through the delivery route', async () => {
    const { workflowExecutionsService, deliveryExecutionsService } = setup();
    await screen.findByRole('option', { name: 'Delivery' });
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'workflow' } });
    expect(await screen.findByText('Expansion point: Execute workstreams')).toBeInTheDocument();
    expect(screen.queryByLabelText('Expansion point')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Fix parser' },
    });
    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'https://git.example/repo' },
    });
    fireEvent.change(screen.getByLabelText('Target branch'), { target: { value: 'target' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Launch developer workflow' }));
    await waitFor(() =>
      expect(deliveryExecutionsService.launch).toHaveBeenCalledWith(
        {
          workflowId: 'workflow',
          parentNodeId: 'expand',
          prompt: 'Fix parser',
          repo: 'https://git.example/repo',
          baseBranch: 'target',
        },
        expect.any(String),
      ),
    );
    expect(workflowExecutionsService.launch).not.toHaveBeenCalled();
    await screen.findByText('Review requested changes');
    expect(screen.getByText('Test failed')).toBeInTheDocument();
  });

  it('launches a generic (non-delivery) workflow through the generic route with no git fields', async () => {
    const { workflowExecutionsService, deliveryExecutionsService } = setup({}, [genericWorkflow]);
    await screen.findByRole('option', { name: 'Generic' });
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'generic-workflow' } });
    expect(await screen.findByText('Expansion point: Execute children')).toBeInTheDocument();
    expect(screen.queryByLabelText('Repository')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Target branch')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Rotate the shared secret' },
    });
    fireEvent.submit(screen.getByRole('form', { name: 'Launch developer workflow' }));
    await waitFor(() =>
      expect(workflowExecutionsService.launch).toHaveBeenCalledWith(
        {
          workflowId: 'generic-workflow',
          parentNodeId: 'expand',
          prompt: 'Rotate the shared secret',
        },
        expect.any(String),
      ),
    );
    expect(deliveryExecutionsService.launch).not.toHaveBeenCalled();
  });

  it('lists every expansion point when a workflow declares more than one', async () => {
    setup({}, [multiExpansionWorkflow]);
    await screen.findByRole('option', { name: 'Multi expansion' });
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'multi-workflow' } });
    const picker = await screen.findByLabelText('Expansion point');
    expect(screen.getByRole('option', { name: 'Expansion A' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Expansion B' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start workflow' })).toBeDisabled();
    fireEvent.change(picker, { target: { value: 'expand-b' } });
    expect(screen.getByRole('button', { name: 'Start workflow' })).toBeEnabled();
  });

  it('blocks launch with an inline message when the workflow has no expansion point', async () => {
    setup({}, [noExpansionWorkflow]);
    await screen.findByRole('option', { name: 'No expansion' });
    fireEvent.change(screen.getByLabelText('Workflow'), {
      target: { value: 'no-expansion-workflow' },
    });
    expect(
      await screen.findByText(
        'This workflow has no subworkflow expansion point; it cannot be launched from here.',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start workflow' })).toBeDisabled();
  });

  it('renders a non-delivery execution without any delivery chrome', async () => {
    setup(
      {
        workflowExecutions: {
          list: vi.fn().mockResolvedValue({ executions: [genericRun], nextCursor: null }),
          get: vi.fn().mockResolvedValue(genericRun),
        },
      },
      [genericWorkflow],
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Rotate secrets · running' }));
    await screen.findByText('running · Generation 1');
    fireEvent.click(screen.getByRole('button', { name: 'View evidence' }));
    expect(await screen.findByRole('region', { name: 'Execution waits' })).toBeInTheDocument();
    expect(
      await screen.findByText('No waits have been recorded for this execution.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Execution evidence' })).not.toBeInTheDocument();
    expect(screen.queryByText('Raw evidence JSON')).not.toBeInTheDocument();
  });

  it('fetches and renders delivery data for a delivery execution', async () => {
    setup();
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    fireEvent.click(await screen.findByRole('button', { name: 'View evidence' }));
    expect(await screen.findByLabelText('Execution evidence')).toHaveTextContent('verified-sha');
    expect(screen.getByRole('heading', { name: 'Workflow results' })).toBeInTheDocument();
  });

  it('launches a ticket and exposes attempt state, evidence, retries, refresh and cancellation', async () => {
    const { workflowExecutionsService, deliveryExecutionsService } = setup();
    await screen.findByRole('option', { name: 'Delivery' });
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'workflow' } });
    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Fix parser' },
    });
    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'https://git.example/repo' },
    });
    fireEvent.change(screen.getByLabelText('Target branch'), { target: { value: 'target' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'model' } });
    fireEvent.change(screen.getByLabelText('Connection'), { target: { value: 'connection' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Launch developer workflow' }));
    await waitFor(() =>
      expect(deliveryExecutionsService.launch).toHaveBeenCalledWith(
        {
          workflowId: 'workflow',
          parentNodeId: 'expand',
          prompt: 'Fix parser',
          repo: 'https://git.example/repo',
          baseBranch: 'target',
          model: 'model',
          connectionId: 'connection',
        },
        expect.any(String),
      ),
    );
    await screen.findByText('Review requested changes');
    expect(screen.getByText('Test failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry parser' }));
    await waitFor(() =>
      expect(workflowExecutionsService.retry).toHaveBeenCalledWith('execution', 'parser', 'child'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Refresh task status' }));
    await waitFor(() =>
      expect(workflowExecutionsService.reconcile).toHaveBeenCalledWith('execution'),
    );
    fireEvent.click(screen.getByRole('button', { name: 'View evidence' }));
    expect(await screen.findByLabelText('Execution evidence')).toHaveTextContent('verified-sha');
    expect(screen.getByRole('heading', { name: 'Workflow results' })).toBeInTheDocument();
    expect(screen.getByText(/does not independently verify its cryptography/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Raw evidence JSON'));
    expect(screen.getByLabelText('Raw execution evidence')).toHaveTextContent('sha256:workflow');
    fireEvent.click(screen.getByRole('button', { name: 'Hide evidence' }));
    expect(screen.queryByLabelText('Execution evidence')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(workflowExecutionsService.cancel).toHaveBeenCalledWith('execution'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel run' })).toBeDisabled());
  });

  it('reuses a launch key only while retrying the same failed submission', async () => {
    const launch = vi
      .fn()
      .mockRejectedValueOnce(new Error('Temporary launch failure'))
      .mockRejectedValueOnce(new Error('Temporary launch failure'))
      .mockResolvedValue(deliveryRun);
    const { deliveryExecutionsService } = setup({ deliveryExecutions: { launch } });
    await screen.findByRole('option', { name: 'Delivery' });
    fireEvent.change(screen.getByLabelText('Workflow'), { target: { value: 'workflow' } });
    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Fix parser' },
    });
    fireEvent.change(screen.getByLabelText('Repository'), {
      target: { value: 'https://git.example/repo' },
    });
    fireEvent.change(screen.getByLabelText('Target branch'), { target: { value: 'target' } });
    const form = screen.getByRole('form', { name: 'Launch developer workflow' });

    fireEvent.submit(form);
    expect(await screen.findByRole('alert')).toHaveTextContent('Temporary launch failure');
    const firstKey = deliveryExecutionsService.launch.mock.calls[0]![1] as string;

    fireEvent.submit(form);
    await waitFor(() => expect(deliveryExecutionsService.launch).toHaveBeenCalledTimes(2));
    expect(deliveryExecutionsService.launch.mock.calls[1]![1]).toBe(firstKey);

    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Fix parser safely' },
    });
    fireEvent.submit(form);
    await waitFor(() => expect(deliveryExecutionsService.launch).toHaveBeenCalledTimes(3));
    const changedFormKey = deliveryExecutionsService.launch.mock.calls[2]![1] as string;
    expect(changedFormKey).not.toBe(firstKey);

    fireEvent.submit(form);
    await waitFor(() => expect(deliveryExecutionsService.launch).toHaveBeenCalledTimes(4));
    expect(deliveryExecutionsService.launch.mock.calls[3]![1]).not.toBe(changedFormKey);
  });

  it('shows loading and errors without claiming there are no runs', async () => {
    let reject!: (error: Error) => void;
    setup({
      workflowExecutions: {
        list: () =>
          new Promise((_, fail) => {
            reject = fail;
          }),
      },
    });
    expect(screen.getByText('Loading runs…')).toBeInTheDocument();
    reject(new Error('Execution service unavailable'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Execution service unavailable');
    expect(screen.queryByText('No developer workflow runs yet.')).not.toBeInTheDocument();
  });

  it('shows empty catalogs and run lists', async () => {
    setup({ workflowExecutions: { list: async () => ({ executions: [], nextCursor: null }) } }, []);
    expect(await screen.findByText('No developer workflows are installed.')).toBeInTheDocument();
    expect(await screen.findByText('No developer workflow runs yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start workflow' })).toBeDisabled();
  });

  it('opens an existing terminal run without enabling mutations', async () => {
    setup({
      workflowExecutions: {
        get: async () => ({ ...genericProjectionOf(deliveryRun), state: 'completed' }),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    await screen.findByText('completed · Generation 1');
    expect(screen.getByRole('button', { name: 'Refresh task status' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel run' })).toBeDisabled();
  });

  it('refreshes visible evidence when an active execution becomes terminal', async () => {
    const evidence = vi
      .fn()
      .mockResolvedValueOnce({ verification: { status: 'pending' } })
      .mockResolvedValueOnce({ verification: { status: 'accepted' } });
    const reconcile = vi.fn().mockResolvedValue({
      ...genericProjectionOf(deliveryRun),
      state: 'completed',
      updatedAt: 'terminal',
    });
    setup({
      workflowExecutions: {
        get: async () => ({
          ...genericProjectionOf(deliveryRun),
          state: 'running',
          updatedAt: 'active',
        }),
        reconcile,
      },
      deliveryExecutions: {
        evidence,
        get: async () => ({ ...deliveryRun, state: 'running', updatedAt: 'active' }),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    await screen.findByText('running · Generation 1');
    fireEvent.click(screen.getByRole('button', { name: 'View evidence' }));
    await waitFor(() => expect(evidence).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('button', { name: 'Refresh task status' }));

    await waitFor(() => expect(reconcile).toHaveBeenCalledWith('execution'));
    await waitFor(() => expect(evidence).toHaveBeenCalledTimes(2));
    expect(screen.getByLabelText('Execution evidence')).toHaveTextContent(
      'Current child evidence status reported by the server: accepted',
    );
  });

  it('loads durable waits into rendered and raw results', async () => {
    const waits = vi.fn().mockResolvedValue([
      {
        waitId: 'wait-1',
        executionId: 'execution',
        nodeId: 'delivery-publication-wait',
        conditionType: 'forge.checks',
        state: 'pending',
        requestDigest: 'request-digest',
        generation: 1,
        executionRevision: 4,
        request: {
          repository: 'repo',
          reviewNumber: 17,
          expectedHeadSha: 'a'.repeat(40),
          expectedBaseSha: 'base-sha',
          expectedTargetBranch: 'main',
          policyId: 'policy',
        },
        nextPollAt: '2026-01-01T00:00:00Z',
        attemptCount: 1,
        lastError: '',
        observation: null,
      },
    ]);
    setup({
      workflowExecutions: { waits },
      deliveryExecutions: {
        get: async () => ({
          ...deliveryRun,
          integrationCandidate: {
            repository: 'repo',
            base_sha: 'base-sha',
            candidate_sha: 'a'.repeat(40),
          },
        }),
      },
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    fireEvent.click(await screen.findByRole('button', { name: 'View evidence' }));

    expect(await screen.findByText('Remote verification · review #17')).toBeInTheDocument();
    expect(screen.getByText('Recorded attempts')).toBeInTheDocument();
    expect(waits).toHaveBeenCalledWith('execution');
    fireEvent.click(screen.getByText('Raw evidence JSON'));
    expect(screen.getByLabelText('Raw execution evidence')).toHaveTextContent('wait-1');
  });
});
