import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { OverviewPage } from './OverviewPage';
import {
  createMockRavenStream,
  createMockTriggerStore,
  createMockSessionStream,
  createMockBudgetStream,
} from '../adapters/mock';
import type { Ravn } from '../domain/ravn';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    'ravn.budget': createMockBudgetStream(),
    ...overrides,
  };
}

function wrap(services = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('OverviewPage', () => {
  it('shows loading state initially', () => {
    const slow = { listRavens: () => new Promise(() => undefined) };
    render(<OverviewPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': slow })) });
    expect(screen.getByTestId('overview-loading')).toBeInTheDocument();
  });

  it('renders after loading', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeInTheDocument());
  });

  it('renders 4 KPI cards', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('overview-page')).toBeInTheDocument());
    expect(screen.getByTestId('kpi-ravens')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-sessions')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-spend')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-triggers')).toBeInTheDocument();
  });

  it('shows raven total count', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('kpi-ravens')).toBeInTheDocument());
    // Mock has 12 ravens
    const ravensKpi = screen.getByTestId('kpi-ravens');
    expect(ravensKpi.textContent).toContain('12');
  });

  it('renders the active ravens list', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('active-ravens-list')).toBeInTheDocument());
    const rows = screen.getAllByTestId('active-ravn-row');
    expect(rows.length).toBe(7);
  });

  it('renders persona avatars in active ravens rows', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('active-ravens-list')).toBeInTheDocument());
    // Active ravens in mock data have role/letter — avatars should be rendered
    const rows = screen.getAllByTestId('active-ravn-row');
    expect(rows.length).toBeGreaterThan(0);
    // PersonaAvatar renders a span with aria-label containing "persona"
    const avatars = screen.getAllByLabelText(/persona/i);
    expect(avatars.length).toBeGreaterThan(0);
  });

  it('renders the fleet sparkline widget', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('fleet-sparkline')).toBeInTheDocument());
  });

  it('renders the top spenders list', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('top-spenders-list')).toBeInTheDocument());
  });

  it('renders location bars section when ravens have locations', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('location-bars')).toBeInTheDocument());
  });

  it('shows location rows for each unique location', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('location-bars')).toBeInTheDocument());
    const rows = screen.getAllByTestId('location-row');
    // Active ravens currently span three locations.
    expect(rows.length).toBe(3);
  });

  it('renders the recent activity log section', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('activity-log')).toBeInTheDocument());
  });

  it('renders up to 9 activity log rows', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('activity-log')).toBeInTheDocument());
    const rows = screen.getAllByTestId('activity-log-row');
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.length).toBeLessThanOrEqual(9);
  });

  it('shows error state when ravens service fails', async () => {
    const failing = { listRavens: () => Promise.reject(new Error('fleet offline')) };
    render(<OverviewPage />, {
      wrapper: wrap(makeServices({ 'ravn.ravens': failing })),
    });
    await waitFor(() => expect(screen.getByTestId('overview-error')).toBeInTheDocument());
    expect(screen.getByText(/fleet offline/i)).toBeInTheDocument();
  });

  it('shows "No active ravens" when all ravens are idle', async () => {
    const idleRavn: Ravn = {
      id: 'aaaaaaaa-0000-4000-8000-000000000001',
      personaName: 'idle-ravn',
      status: 'idle',
      model: 'claude-sonnet-4-6',
      createdAt: '2026-04-15T09:00:00Z',
    };
    const noActive = {
      listRavens: () => Promise.resolve([idleRavn]),
      getRaven: (_id: string) => Promise.resolve(idleRavn),
    };
    render(<OverviewPage />, {
      wrapper: wrap(makeServices({ 'ravn.ravens': noActive })),
    });
    await waitFor(() => expect(screen.getByText(/no active ravens/i)).toBeInTheDocument());
  });

  it('shows open session count in KPI', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('kpi-sessions')).toBeInTheDocument());
    // 10 running sessions in mock data
    const sessionsKpi = screen.getByTestId('kpi-sessions');
    expect(sessionsKpi.textContent).toContain('10');
  });

  it('shows spend KPI', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('kpi-spend')).toBeInTheDocument());
    expect(screen.getByTestId('kpi-spend').textContent).toContain('$');
  });

  it('shows the expanded active trigger count', async () => {
    render(<OverviewPage />, { wrapper: wrap() });
    await waitFor(() => expect(screen.getByTestId('kpi-triggers')).toBeInTheDocument());
    expect(screen.getByTestId('kpi-triggers').textContent).toContain('9');
  });
});
