import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { describe, expect, it, vi } from 'vitest';
import { DeveloperExecutionsPage } from './DeveloperExecutionsPage';
import type { DeveloperExecution } from '../domain/developerExecution';

const run: DeveloperExecution = {
  executionId: 'execution',
  name: 'Fix parser',
  prompt: 'Fix it',
  repo: 'repo',
  baseBranch: 'main',
  baseSha: 'base-sha',
  workflowId: 'workflow',
  state: 'blocked',
  suspensionReason: 'Review requested changes',
  currentGeneration: 1,
  planRevision: 'plan-1',
  budget: { totalUnits: 100, reservedUnits: 10, spentUnits: 20, availableUnits: 70 },
  join: {},
  createdAt: '',
  updatedAt: '',
  children: [
    {
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
    },
  ],
};
function setup(
  overrides: Record<string, unknown> = {},
  catalog = [
    { id: 'workflow', name: 'Delivery', graph: { executionContract: 'developer-delivery/v1' } },
  ],
) {
  const service = {
    list: vi.fn().mockResolvedValue({ executions: [run], nextCursor: null }),
    get: vi.fn().mockResolvedValue(run),
    launch: vi.fn().mockResolvedValue(run),
    cancel: vi.fn().mockResolvedValue({ ...run, state: 'canceled' }),
    reconcile: vi.fn().mockResolvedValue(run),
    retry: vi.fn().mockResolvedValue(run),
    evidence: vi.fn().mockResolvedValue({
      workflowDigest: 'sha256:workflow',
      verification: { status: 'accepted', blockingReasons: [] },
    }),
    deliveryWaits: vi.fn().mockResolvedValue([]),
    ...overrides,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          'ting.developerExecutions': service,
          'ting.workflows': { listWorkflows: async () => catalog },
        }}
      >
        <DeveloperExecutionsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
  return service;
}

describe('DeveloperExecutionsPage', () => {
  it('loads older runs with the server cursor', async () => {
    const list = vi
      .fn()
      .mockResolvedValueOnce({ executions: [run], nextCursor: 'older' })
      .mockResolvedValueOnce({
        executions: [{ ...run, executionId: 'older', name: 'Older run' }],
        nextCursor: null,
      });
    setup({ list });
    fireEvent.click(await screen.findByRole('button', { name: 'Load more runs' }));
    expect(await screen.findByRole('button', { name: 'Older run · blocked' })).toBeVisible();
    expect(list).toHaveBeenLastCalledWith({ cursor: 'older' });
  });
  it('shows every outstanding child question and gate and clears resolved input', async () => {
    const waiting = {
      ...run,
      children: [
        {
          ...run.children[0]!,
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
      get: async () => waiting,
      reconcile: async () => ({
        ...waiting,
        children: [
          { ...waiting.children[0]!, state: 'running', pendingQuestions: [], pendingGates: [] },
        ],
      }),
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

  it('launches a ticket and exposes attempt state, evidence, retries, refresh and cancellation', async () => {
    const service = setup();
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
      expect(service.launch).toHaveBeenCalledWith(
        {
          workflowId: 'workflow',
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
    await waitFor(() => expect(service.retry).toHaveBeenCalledWith('execution', 'parser', 'child'));
    fireEvent.click(screen.getByRole('button', { name: 'Refresh task status' }));
    await waitFor(() => expect(service.reconcile).toHaveBeenCalledWith('execution'));
    fireEvent.click(screen.getByRole('button', { name: 'View evidence' }));
    expect(await screen.findByLabelText('Execution evidence')).toHaveTextContent('verified-sha');
    expect(screen.getByRole('heading', { name: 'Workflow results' })).toBeInTheDocument();
    expect(screen.getByText(/does not independently verify its cryptography/i)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Raw evidence JSON'));
    expect(screen.getByLabelText('Raw execution evidence')).toHaveTextContent('sha256:workflow');
    fireEvent.click(screen.getByRole('button', { name: 'Hide evidence' }));
    expect(screen.queryByLabelText('Execution evidence')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(service.cancel).toHaveBeenCalledWith('execution'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel run' })).toBeDisabled());
  });

  it('reuses a launch key only while retrying the same failed submission', async () => {
    const launch = vi
      .fn()
      .mockRejectedValueOnce(new Error('Temporary launch failure'))
      .mockRejectedValueOnce(new Error('Temporary launch failure'))
      .mockResolvedValue(run);
    const service = setup({ launch });
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
    const firstKey = service.launch.mock.calls[0]![1] as string;

    fireEvent.submit(form);
    await waitFor(() => expect(service.launch).toHaveBeenCalledTimes(2));
    expect(service.launch.mock.calls[1]![1]).toBe(firstKey);

    fireEvent.change(screen.getByLabelText('Ticket or objective'), {
      target: { value: 'Fix parser safely' },
    });
    fireEvent.submit(form);
    await waitFor(() => expect(service.launch).toHaveBeenCalledTimes(3));
    const changedFormKey = service.launch.mock.calls[2]![1] as string;
    expect(changedFormKey).not.toBe(firstKey);

    fireEvent.submit(form);
    await waitFor(() => expect(service.launch).toHaveBeenCalledTimes(4));
    expect(service.launch.mock.calls[3]![1]).not.toBe(changedFormKey);
  });

  it('shows loading and errors without claiming there are no runs', async () => {
    let reject!: (error: Error) => void;
    setup({
      list: () =>
        new Promise((_, fail) => {
          reject = fail;
        }),
    });
    expect(screen.getByText('Loading runs…')).toBeInTheDocument();
    reject(new Error('Execution service unavailable'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Execution service unavailable');
    expect(screen.queryByText('No developer workflow runs yet.')).not.toBeInTheDocument();
  });

  it('shows empty catalogs and run lists', async () => {
    setup({ list: async () => ({ executions: [], nextCursor: null }) }, []);
    expect(await screen.findByText('No developer workflows are installed.')).toBeInTheDocument();
    expect(await screen.findByText('No developer workflow runs yet.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start workflow' })).toBeDisabled();
  });

  it('opens an existing terminal run without enabling mutations', async () => {
    setup({ get: async () => ({ ...run, state: 'completed' }) });
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
      ...run,
      state: 'completed',
      updatedAt: 'terminal',
    });
    setup({
      evidence,
      get: async () => ({ ...run, state: 'running', updatedAt: 'active' }),
      reconcile,
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

  it('loads durable delivery waits into rendered and raw results', async () => {
    const deliveryWaits = vi.fn().mockResolvedValue([
      {
        waitId: 'wait-1',
        executionId: 'execution',
        mode: 'checks',
        state: 'pending',
        requestDigest: 'request-digest',
        generation: 1,
        executionRevision: 4,
        candidateDigest: 'candidate-digest',
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
      deliveryWaits,
      get: async () => ({
        ...run,
        integrationCandidate: {
          repository: 'repo',
          base_sha: 'base-sha',
          candidate_sha: 'a'.repeat(40),
        },
      }),
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Fix parser · blocked' }));
    fireEvent.click(await screen.findByRole('button', { name: 'View evidence' }));

    expect(await screen.findByText('Remote verification · review #17')).toBeInTheDocument();
    expect(screen.getByText('Recorded attempts')).toBeInTheDocument();
    expect(deliveryWaits).toHaveBeenCalledWith('execution');
    fireEvent.click(screen.getByText('Raw evidence JSON'));
    expect(screen.getByLabelText('Raw execution evidence')).toHaveTextContent('wait-1');
  });
});
