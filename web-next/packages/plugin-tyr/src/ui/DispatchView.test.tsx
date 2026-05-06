import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DispatchView } from './DispatchView';
import { createMockDispatcherService } from '../adapters/mock';
import { createMockDispatchBus } from '../adapters/mock';
import { createMockWorkflowService } from '../adapters/mock';
import type {
  IDispatcherService,
  IDispatchBus,
  IWorkflowService,
  DispatchQueueItem,
} from '../ports';
import type { DispatcherState } from '../domain/dispatcher';

function makeDispatcherState(overrides: Partial<DispatcherState> = {}): DispatcherState {
  return {
    id: '00000000-0000-0000-0000-000000000999',
    running: true,
    threshold: 70,
    maxConcurrentRaids: 3,
    autoContinue: false,
    updatedAt: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function makeQueueItem(overrides: Partial<DispatchQueueItem> = {}): DispatchQueueItem {
  return {
    sagaId: '00000000-0000-0000-0000-000000000001',
    sagaName: 'Test Saga',
    sagaSlug: 'test-saga',
    repos: ['niuulabs/volundr'],
    featureBranch: 'feat/test',
    phaseName: 'Phase 1',
    issueId: 'issue-1',
    identifier: 'NIU-010',
    title: 'Test Raid',
    description: 'A test raid',
    status: 'todo',
    priority: 0,
    priorityLabel: '',
    estimate: 4,
    url: 'https://linear.app/issue/NIU-010',
    ...overrides,
  };
}

function makeServices(
  overrides: {
    dispatcher?: Partial<IDispatcherService>;
    dispatch?: Partial<IDispatchBus>;
    workflows?: Partial<IWorkflowService>;
  } = {},
) {
  const queue = [makeQueueItem()];
  const dispatcherBase = createMockDispatcherService();
  const dispatchBase = createMockDispatchBus();
  const workflowsBase = createMockWorkflowService();

  const dispatcher: IDispatcherService = {
    ...dispatcherBase,
    getState: async () => makeDispatcherState(),
    ...overrides.dispatcher,
  };

  const dispatch: IDispatchBus = {
    ...dispatchBase,
    getQueue: async () => queue,
    ...overrides.dispatch,
  };

  const workflows: IWorkflowService = {
    ...workflowsBase,
    ...overrides.workflows,
  };

  return {
    'tyr.dispatcher': dispatcher,
    'tyr.dispatch': dispatch,
    'tyr.workflows': workflows,
  };
}

function wrap(services: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('DispatchView', () => {
  it('shows loading state initially', () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    expect(screen.getByText(/loading dispatch queue/i)).toBeInTheDocument();
  });

  it('renders rule summary card after loading', async () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => expect(screen.getByText('Dispatch rules')).toBeInTheDocument());
    expect(screen.getAllByText('70%').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('off')).toBeInTheDocument();
  });

  it('renders the dispatcher queue list', async () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => expect(screen.getByText('Test Raid')).toBeInTheDocument());
    expect(screen.getByText(/Test Saga/)).toBeInTheDocument();
    expect(screen.getByText(/NIU-010/)).toBeInTheDocument();
  });

  it('shows error state when dispatcher service fails', async () => {
    const services = makeServices({
      dispatcher: {
        getState: async () => {
          throw new Error('dispatcher offline');
        },
      },
    });
    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() =>
      expect(screen.getByText(/failed to load dispatch queue/i)).toBeInTheDocument(),
    );
  });

  it('shows error state when queue loading fails', async () => {
    const services = makeServices({
      dispatch: {
        getQueue: async () => {
          throw new Error('dispatch offline');
        },
      },
    });
    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() =>
      expect(screen.getByText(/failed to load dispatch queue/i)).toBeInTheDocument(),
    );
  });

  it('filters to ready items only', async () => {
    const user = userEvent.setup();
    const services = makeServices({
      dispatch: {
        getQueue: async () => [
          makeQueueItem({ issueId: 'issue-1', title: 'Ready Raid', status: 'todo' }),
          makeQueueItem({
            issueId: 'issue-2',
            identifier: 'NIU-011',
            title: 'Queued Raid',
            status: 'queued',
          }),
        ],
      },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('Ready Raid'));

    await user.click(screen.getByRole('button', { name: /ready/i }));
    expect(screen.getByText('Ready Raid')).toBeInTheDocument();
    expect(screen.queryByText('Queued Raid')).not.toBeInTheDocument();
  });

  it('filters to queue items only', async () => {
    const user = userEvent.setup();
    const services = makeServices({
      dispatch: {
        getQueue: async () => [
          makeQueueItem({ issueId: 'issue-1', title: 'Ready Raid', status: 'todo' }),
          makeQueueItem({
            issueId: 'issue-2',
            identifier: 'NIU-011',
            title: 'Running Raid',
            status: 'running',
          }),
        ],
      },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('Ready Raid'));

    await user.click(screen.getByRole('button', { name: /queue/i }));
    expect(screen.queryByText('Ready Raid')).not.toBeInTheDocument();
    expect(screen.getByText('Running Raid')).toBeInTheDocument();
  });

  it('shows empty state when no items match the blocked filter', async () => {
    const user = userEvent.setup();
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('button', { name: /blocked/i }));
    expect(screen.getByText(/no raids match/i)).toBeInTheDocument();
  });

  it('filters by search query', async () => {
    const user = userEvent.setup();
    const services = makeServices({
      dispatch: {
        getQueue: async () => [
          makeQueueItem({ issueId: 'issue-1', title: 'OIDC integration' }),
          makeQueueItem({ issueId: 'issue-2', identifier: 'NIU-011', title: 'PAT generation' }),
        ],
      },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('OIDC integration'));

    await user.type(screen.getByRole('searchbox'), 'oidc');
    expect(screen.getByText('OIDC integration')).toBeInTheDocument();
    expect(screen.queryByText('PAT generation')).not.toBeInTheDocument();
  });

  it('batch dispatch bar appears when items are selected', async () => {
    const user = userEvent.setup();
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    expect(screen.getByText('selected')).toBeInTheDocument();
  });

  it('calls approve with saga and issue details and clears selection', async () => {
    const user = userEvent.setup();
    const approveSpy = vi.fn().mockResolvedValue([
      {
        issueId: 'issue-1',
        sessionId: 'sess-1',
        sessionName: 'NIU-010',
        status: 'spawned',
        clusterName: '',
      },
    ]);
    const services = makeServices({ dispatch: { approve: approveSpy } });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    await user.click(screen.getByRole('button', { name: /dispatch now/i }));
    await waitFor(() =>
      expect(approveSpy).toHaveBeenCalledWith([
        {
          sagaId: '00000000-0000-0000-0000-000000000001',
          issueId: 'issue-1',
          repo: 'niuulabs/volundr',
        },
      ]),
    );
    await waitFor(() => expect(screen.queryByText(/1 selected/i)).not.toBeInTheDocument());
  });

  it('shows a dispatching overlay while approval is in flight', async () => {
    const user = userEvent.setup();
    let resolveApprove:
      | ((
          value: {
            issueId: string;
            sessionId: string;
            sessionName: string;
            status: string;
            clusterName: string;
          }[],
        ) => void)
      | null = null;
    const approveSpy = vi.fn(
      () =>
        new Promise<
          {
            issueId: string;
            sessionId: string;
            sessionName: string;
            status: string;
            clusterName: string;
          }[]
        >((resolve) => {
          resolveApprove = resolve;
        }),
    );
    const services = makeServices({ dispatch: { approve: approveSpy } });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    await user.click(screen.getByRole('button', { name: /dispatch now/i }));

    await waitFor(() => expect(screen.queryByText(/1 selected/i)).not.toBeInTheDocument());
    expect(screen.getByText(/dispatching 1 raid/i)).toBeInTheDocument();
    expect(screen.getAllByText(/submitting/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Test Raid').length).toBeGreaterThan(0);

    resolveApprove?.([
      {
        issueId: 'issue-1',
        sessionId: 'sess-1',
        sessionName: 'NIU-010',
        status: 'spawned',
        clusterName: '',
      },
    ]);

    await waitFor(() => {
      expect(screen.queryByText(/dispatching 1 raid/i)).not.toBeInTheDocument();
    });
  });

  it('shows synthesized confidence for dispatcher queue items', async () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => expect(screen.getByText('100')).toBeInTheDocument());
  });

  it('shows Pause dispatcher button in header', async () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /pause dispatcher/i })).toBeInTheDocument(),
    );
  });

  it('shows Resume dispatcher button when dispatcher is stopped', async () => {
    const services = makeServices({
      dispatcher: { getState: async () => makeDispatcherState({ running: false }) },
    });
    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /resume dispatcher/i })).toBeInTheDocument(),
    );
  });

  it('calls setRunning(false) when Pause is clicked', async () => {
    const user = userEvent.setup();
    const setRunningSpy = vi.fn().mockResolvedValue(undefined);
    const getStateSpy = vi
      .fn()
      .mockResolvedValueOnce(makeDispatcherState({ running: true }))
      .mockResolvedValue(makeDispatcherState({ running: false }));
    const services = makeServices({
      dispatcher: { getState: getStateSpy, setRunning: setRunningSpy },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByRole('button', { name: /pause dispatcher/i }));

    await user.click(screen.getByRole('button', { name: /pause dispatcher/i }));
    await waitFor(() => expect(setRunningSpy).toHaveBeenCalledWith(false));
  });

  it('shows Edit button in dispatch rules panel', async () => {
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => screen.getByText('Dispatch rules'));
    expect(screen.getByRole('button', { name: /edit/i })).toBeInTheDocument();
  });

  it('opens threshold modal when Override threshold is clicked', async () => {
    const user = userEvent.setup();
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    await user.click(screen.getByRole('button', { name: /override threshold/i }));
    await waitFor(() =>
      expect(screen.getByText('Override dispatch threshold')).toBeInTheDocument(),
    );
  });

  it('opens workflow modal when Apply workflow is clicked', async () => {
    const user = userEvent.setup();
    render(<DispatchView />, { wrapper: wrap(makeServices()) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    await user.click(screen.getByRole('button', { name: /apply workflow/i }));
    await waitFor(() => expect(screen.getByText('Apply workflow override')).toBeInTheDocument());
  });

  it('shows error toast when approval fails', async () => {
    const user = userEvent.setup();
    const services = makeServices({
      dispatch: {
        approve: vi.fn().mockRejectedValue(new Error('network error')),
      },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByText('Test Raid'));

    await user.click(screen.getByRole('checkbox', { name: /select row/i }));
    await user.click(screen.getByRole('button', { name: /dispatch now/i }));
    await waitFor(() => expect(screen.getByText(/dispatch failed/i)).toBeInTheDocument());
  });

  it('shows error toast when pause fails', async () => {
    const user = userEvent.setup();
    const services = makeServices({
      dispatcher: {
        getState: vi.fn().mockResolvedValue(makeDispatcherState({ running: true })),
        setRunning: vi.fn().mockRejectedValue(new Error('service error')),
      },
    });

    render(<DispatchView />, { wrapper: wrap(services) });
    await waitFor(() => screen.getByRole('button', { name: /pause dispatcher/i }));

    await user.click(screen.getByRole('button', { name: /pause dispatcher/i }));
    await waitFor(() =>
      expect(screen.getByText(/failed to update dispatcher/i)).toBeInTheDocument(),
    );
  });
});
