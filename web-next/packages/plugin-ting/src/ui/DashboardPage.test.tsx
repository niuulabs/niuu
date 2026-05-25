import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DashboardPage } from './DashboardPage';
import { createMockTingService, createMockDispatcherService } from '../adapters/mock';
import type { Saga } from '../domain/saga';

const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children }: { to: string; className?: string; children?: unknown }) =>
    children as unknown as JSX.Element | null,
}));

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

const defaultServices = () => ({
  ting: createMockTingService(),
  'ting.dispatcher': createMockDispatcherService(),
});

describe('DashboardPage', () => {
  it('shows loading state initially', () => {
    const slowSvc = {
      ting: { getSagas: () => new Promise(() => undefined), getPhases: () => Promise.resolve([]) },
      'ting.dispatcher': {
        getState: () => new Promise(() => undefined),
        getActivityLog: () => Promise.resolve([]),
      },
    };
    render(<DashboardPage />, { wrapper: wrap(slowSvc) });
    expect(screen.getByText(/Loading dashboard/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failingSvc = {
      ting: {
        getSagas: async () => {
          throw new Error('network error');
        },
        getPhases: () => Promise.resolve([]),
      },
      'ting.dispatcher': createMockDispatcherService(),
    };
    render(<DashboardPage />, { wrapper: wrap(failingSvc) });
    await waitFor(() => expect(screen.getByText('network error')).toBeInTheDocument());
  });

  it('renders KPI cards with real-data labels', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Active sagas')).toBeInTheDocument());
    expect(screen.getByText('Active runs')).toBeInTheDocument();
    expect(screen.getByText('Awaiting review')).toBeInTheDocument();
    expect(screen.getByText('Merged · 24h')).toBeInTheDocument();
    expect(screen.getByText('Confidence overview')).toBeInTheDocument();
  });

  it('renders active saga names from the service', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByText('Flokk subscription validation')).toBeInTheDocument(),
    );
  });

  it('renders the live flock and throughput sections', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Live flock')).toBeInTheDocument());
    expect(screen.getByText('Run mesh')).toBeInTheDocument();
    expect(screen.getByText('Throughput')).toBeInTheDocument();
    expect(screen.getByText('Runs completed / hour')).toBeInTheDocument();
  });

  it('renders the dispatcher activity feed instead of placeholder rows', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
    expect(screen.getByText('running · dispatch')).toBeInTheDocument();
    expect(screen.getByText('NIU-214.2')).toBeInTheDocument();
  });

  it('falls back to saga updates when the dispatcher activity log is empty', async () => {
    const quietDispatcher = {
      ...createMockDispatcherService(),
      getActivityLog: async () => [],
    };
    render(<DashboardPage />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatcher': quietDispatcher }),
    });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
    expect(
      screen.getAllByText(/created · awaiting decomposition|stages committed|running ·/i).length,
    ).toBeGreaterThan(0);
  });

  it('shows an honest empty state when there is no recent activity at all', async () => {
    const quietDispatcher = {
      ...createMockDispatcherService(),
      getActivityLog: async () => [],
    };
    const quietTing = {
      ting: {
        getSagas: async (): Promise<Saga[]> => [],
        getPhases: async () => [],
      },
      'ting.dispatcher': quietDispatcher,
    };
    render(<DashboardPage />, { wrapper: wrap(quietTing) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
    expect(screen.getByText(/No recent Ting activity yet/i)).toBeInTheDocument();
  });

  it('clicking a saga card navigates to the saga detail page', async () => {
    mockNavigate.mockClear();
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByText('Flokk subscription validation')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Flokk subscription validation').closest('[role="button"]')!);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/ting/sagas/$sagaId',
      params: { sagaId: '00000000-0000-0000-0000-000000000004' },
    });
  });

  it('view all navigates to the sagas page and shows a toast', async () => {
    mockNavigate.mockClear();
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('View all')).toBeInTheDocument());
    fireEvent.click(screen.getByText('View all'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/ting/sagas' });
    await waitFor(() => expect(screen.getByText('Navigating to Sagas')).toBeInTheDocument());
  });

  it('renders the dispatcher stats bar when state is available', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByTestId('ting-dispatcher-stats')).toBeInTheDocument());
    const bar = screen.getByTestId('ting-dispatcher-stats');
    expect(bar).toHaveTextContent('dispatcher');
    expect(bar).toHaveTextContent('on');
    expect(bar).toHaveTextContent('threshold');
    expect(bar).toHaveTextContent('70.00');
  });

  it('shows dispatcher off when running is false', async () => {
    const offDispatcher = {
      ...createMockDispatcherService(),
      getState: async () => ({
        id: 'test',
        running: false,
        threshold: 0.8,
        maxConcurrentRuns: 5,
        autoContinue: false,
        updatedAt: '2026-01-01T00:00:00Z',
      }),
    };
    render(<DashboardPage />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatcher': offDispatcher }),
    });
    await waitFor(() => expect(screen.getByTestId('ting-dispatcher-stats')).toBeInTheDocument());
    expect(screen.getByTestId('ting-dispatcher-stats')).toHaveTextContent('off');
    expect(screen.getByTestId('ting-dispatcher-stats')).toHaveTextContent('0.80');
  });

  it('hides active-only saga cards when every saga is terminal', async () => {
    const completeSvc = {
      ting: {
        getSagas: async (): Promise<Saga[]> => [
          {
            id: '1',
            trackerId: 'NIU-1',
            trackerType: 'linear',
            slug: 'done-saga',
            name: 'Done Saga',
            repos: [],
            featureBranch: 'feat/done',
            status: 'complete',
            confidence: 95,
            createdAt: '2026-01-01T00:00:00Z',
            phaseSummary: { total: 1, completed: 1 },
            baseBranch: 'main',
          },
        ],
        getPhases: async () => [],
      },
      'ting.dispatcher': createMockDispatcherService(),
    };
    render(<DashboardPage />, { wrapper: wrap(completeSvc) });
    await waitFor(() => expect(screen.getByText('Saga stream')).toBeInTheDocument());
    expect(screen.queryByText('Done Saga')).not.toBeInTheDocument();
  });

  it('renders confidence and saga completion events in the activity feed', async () => {
    const eventfulDispatcher = {
      ...createMockDispatcherService(),
      getActivityLog: async () => [
        {
          id: 'evt-confidence',
          timestamp: '2026-05-25T12:00:00Z',
          event: 'confidence.updated',
          data: {
            event_type: 'confidence_updated',
            delta: -0.12,
            score_after: 0.58,
            tracker_id: 'NIU-902',
          },
        },
        {
          id: 'evt-saga-complete',
          timestamp: '2026-05-25T11:59:00Z',
          event: 'saga.completed',
          data: {
            tracker_id: 'NIU-903',
            status: 'merged',
          },
        },
      ],
      getState: async () => null,
    };

    render(<DashboardPage />, {
      wrapper: wrap({ ting: createMockTingService(), 'ting.dispatcher': eventfulDispatcher }),
    });

    await waitFor(() => expect(screen.getByText(/confidence updated · -0.12 · → 0.58/i)).toBeInTheDocument());
    expect(screen.getByText('NIU-902')).toBeInTheDocument();
    expect(screen.getByText(/^completed$/i)).toBeInTheDocument();
    expect(screen.queryByTestId('ting-dispatcher-stats')).not.toBeInTheDocument();
  });

  it('renders fallback feed rows from terminal runs when dispatcher activity is quiet', async () => {
    const quietDispatcher = {
      ...createMockDispatcherService(),
      getActivityLog: async () => [],
    };
    const phasefulTing = {
      getSagas: async (): Promise<Saga[]> => [
        {
          id: 'done-1',
          trackerId: 'NIU-404',
          trackerType: 'linear',
          slug: 'terminal-saga',
          name: 'Terminal Saga',
          repos: [],
          featureBranch: 'feat/terminal',
          status: 'active',
          confidence: 61,
          createdAt: '2026-05-25T08:00:00Z',
          phaseSummary: { total: 2, completed: 1 },
          baseBranch: 'main',
        },
      ],
      getPhases: async () => [
        {
          id: 'phase-1',
          sagaId: 'done-1',
          name: 'Validate',
          order: 1,
          status: 'complete',
          runs: [
            {
              id: 'run-merged',
              phaseId: 'phase-1',
              sagaId: 'done-1',
              name: 'Publisher',
              status: 'merged',
              persona: 'publisher',
              updatedAt: '2026-05-25T11:00:00Z',
              createdAt: '2026-05-25T10:30:00Z',
              confidence: 0.91,
            },
          ],
        },
      ],
    };

    render(<DashboardPage />, {
      wrapper: wrap({ ting: phasefulTing, 'ting.dispatcher': quietDispatcher }),
    });

    await waitFor(() => expect(screen.getByText(/merged · Publisher/i)).toBeInTheDocument());
    expect(screen.getAllByText('NIU-404').length).toBeGreaterThan(1);
  });
});
