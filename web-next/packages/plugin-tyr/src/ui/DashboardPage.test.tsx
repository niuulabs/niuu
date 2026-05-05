import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DashboardPage } from './DashboardPage';
import { createMockTyrService, createMockDispatcherService } from '../adapters/mock';
import type { Saga } from '../domain/saga';

// ---------------------------------------------------------------------------
// Router mock — DashboardPage calls useNavigate() and Link for navigation
// ---------------------------------------------------------------------------
const mockNavigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children }: { to: string; className?: string; children?: unknown }) =>
    children as unknown as JSX.Element | null,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
  tyr: createMockTyrService(),
  'tyr.dispatcher': createMockDispatcherService(),
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('DashboardPage', () => {
  it('shows loading state initially', () => {
    const slowSvc = {
      tyr: { getSagas: () => new Promise(() => undefined), getPhases: () => Promise.resolve([]) },
      'tyr.dispatcher': { getState: () => new Promise(() => undefined) },
    };
    render(<DashboardPage />, { wrapper: wrap(slowSvc) });
    expect(screen.getByText(/Loading dashboard/i)).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failingSvc = {
      tyr: {
        getSagas: async () => {
          throw new Error('network error');
        },
        getPhases: () => Promise.resolve([]),
      },
      'tyr.dispatcher': createMockDispatcherService(),
    };
    render(<DashboardPage />, { wrapper: wrap(failingSvc) });
    await waitFor(() => expect(screen.getByText('network error')).toBeInTheDocument());
  });

  it('renders KPI cards with correct labels', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Active sagas')).toBeInTheDocument());
    expect(screen.getByText('Active raids')).toBeInTheDocument();
    expect(screen.getByText('Awaiting review')).toBeInTheDocument();
    expect(screen.getByText(/Merged/)).toBeInTheDocument();
  });

  it('renders saga stream section', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Saga stream')).toBeInTheDocument());
    expect(screen.getByText('View all')).toBeInTheDocument();
  });

  it('shows active saga names from seed data', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByText('Flokk subscription validation')).toBeInTheDocument(),
    );
  });

  it('renders live flock section', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Live flock')).toBeInTheDocument());
    expect(screen.getByText('Raid mesh')).toBeInTheDocument();
  });

  it('renders event feed section', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
  });

  it('renders throughput section with sparklines', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Throughput')).toBeInTheDocument());
    expect(screen.getByText('Raids completed / hour')).toBeInTheDocument();
    expect(screen.getByText('Saga confidence')).toBeInTheDocument();
  });

  it('clicking a saga card calls navigate with the saga ID', async () => {
    mockNavigate.mockClear();
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByText('Flokk subscription validation')).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText('Flokk subscription validation').closest('[role="button"]')!);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/tyr/sagas/$sagaId',
      params: { sagaId: '00000000-0000-0000-0000-000000000004' },
    });
  });

  it('shows no saga cards when all sagas are complete', async () => {
    const completeSvc = {
      tyr: {
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
          },
        ],
        getPhases: async () => [],
      },
      'tyr.dispatcher': createMockDispatcherService(),
    };
    render(<DashboardPage />, { wrapper: wrap(completeSvc) });
    // Saga stream section exists but no saga cards
    await waitFor(() => expect(screen.getByText('Saga stream')).toBeInTheDocument());
    expect(screen.queryByText('Done Saga')).not.toBeInTheDocument();
  });

  it('renders View all button that navigates to sagas page', async () => {
    mockNavigate.mockClear();
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('View all')).toBeInTheDocument());
    fireEvent.click(screen.getByText('View all'));
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/tyr/sagas' });
  });

  // ---------------------------------------------------------------------------
  // Dispatcher stats bar
  // ---------------------------------------------------------------------------

  it('renders dispatcher stats bar when state is available', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByTestId('tyr-dispatcher-stats')).toBeInTheDocument());
    const bar = screen.getByTestId('tyr-dispatcher-stats');
    expect(bar).toHaveTextContent('dispatcher');
    expect(bar).toHaveTextContent('on');
    expect(bar).toHaveTextContent('threshold');
    expect(bar).toHaveTextContent('0.70');
    expect(bar).toHaveTextContent('concurrent');
    expect(bar).toHaveTextContent('2/5');
  });

  it('does not render dispatcher stats bar while dispatcher is loading', () => {
    const slowDispatcher = {
      tyr: createMockTyrService(),
      'tyr.dispatcher': { getState: () => new Promise(() => undefined) },
    };
    render(<DashboardPage />, { wrapper: wrap(slowDispatcher) });
    expect(screen.queryByTestId('tyr-dispatcher-stats')).not.toBeInTheDocument();
  });

  it('shows dispatcher off when running is false', async () => {
    const offDispatcher = {
      tyr: createMockTyrService(),
      'tyr.dispatcher': {
        getState: async () => ({
          id: 'test',
          running: false,
          threshold: 80,
          maxConcurrentRaids: 5,
          autoContinue: false,
          updatedAt: '2026-01-01T00:00:00Z',
        }),
      },
    };
    render(<DashboardPage />, { wrapper: wrap(offDispatcher) });
    await waitFor(() => expect(screen.getByTestId('tyr-dispatcher-stats')).toBeInTheDocument());
    expect(screen.getByTestId('tyr-dispatcher-stats')).toHaveTextContent('off');
    expect(screen.getByTestId('tyr-dispatcher-stats')).toHaveTextContent('0.80');
    expect(screen.getByTestId('tyr-dispatcher-stats')).toHaveTextContent('2/5');
  });

  // ---------------------------------------------------------------------------
  // Event feed link buttons
  // ---------------------------------------------------------------------------

  it('renders link buttons for every event feed row', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
    const linkButtons = screen.getAllByRole('button', { name: /↗|Open|No linked/ });
    // 6 feed rows → 6 link buttons
    expect(linkButtons.length).toBeGreaterThanOrEqual(6);
  });

  it('feed link buttons are enabled when saga trackerIds match feed subjects', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());
    // Seed sagas have trackerIds NIU-214, NIU-199, NIU-183, NIU-148 — match feed subjects
    const enabledButtons = screen
      .getAllByTitle(/Open NIU-/)
      .filter((el) => el.tagName === 'BUTTON');
    expect(enabledButtons.length).toBeGreaterThanOrEqual(1);
    enabledButtons.forEach((btn) => expect(btn).not.toBeDisabled());
  });

  it('feed link button navigates to the matched saga', async () => {
    mockNavigate.mockClear();
    const sagaId = 'saga-with-niu-214';
    const matchingSvc = {
      tyr: {
        getSagas: async (): Promise<Saga[]> => [
          {
            id: sagaId,
            trackerId: 'NIU-214',
            trackerType: 'linear',
            slug: 'niu-214',
            name: 'NIU-214 Saga',
            repos: [],
            featureBranch: 'feat/niu-214',
            status: 'active',
            confidence: 80,
            createdAt: '2026-01-01T00:00:00Z',
            phaseSummary: { total: 1, completed: 0 },
          },
        ],
        getPhases: async () => [],
      },
      'tyr.dispatcher': createMockDispatcherService(),
    };
    render(<DashboardPage />, { wrapper: wrap(matchingSvc) });
    await waitFor(() => expect(screen.getByText('Event feed')).toBeInTheDocument());

    // NIU-214 matches subjects "NIU-214.2" and "NIU-214.3"
    const enabledButtons = screen.getAllByTitle('Open NIU-214');
    expect(enabledButtons.length).toBe(2);
    expect(enabledButtons[0]).not.toBeDisabled();

    fireEvent.click(enabledButtons[0]!);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/tyr/sagas/$sagaId',
      params: { sagaId },
    });
  });

  // ---------------------------------------------------------------------------
  // Toast on View all
  // ---------------------------------------------------------------------------

  it('View all button shows toast notification', async () => {
    render(<DashboardPage />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('View all')).toBeInTheDocument());
    fireEvent.click(screen.getByText('View all'));
    await waitFor(() => expect(screen.getByText('Navigating to Sagas')).toBeInTheDocument());
  });
});
