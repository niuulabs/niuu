import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { VolundrPage } from './VolundrPage';
import {
  createMockVolundrService,
  createMockClusterAdapter,
  createMockSessionStore,
} from '../adapters/mock';

// Mock TanStack Router navigate
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}));

function wrap(
  service = createMockVolundrService(),
  clusterAdapter = createMockClusterAdapter(),
  sessionStore = createMockSessionStore(),
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr: service, clusterAdapter, sessionStore }}>
        <VolundrPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('VolundrPage (Overview)', () => {
  it('renders the page title', () => {
    wrap();
    expect(screen.getByText('Völundr · session forge')).toBeInTheDocument();
  });

  it('renders the rune glyph', () => {
    wrap();
    expect(screen.getByText('ᚲ')).toBeInTheDocument();
  });

  it('renders KPI cards once data loads', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('active')).toBeInTheDocument());
    // 'idle' may appear multiple times (KPI label + lifecycle badge); getAllByText handles that.
    expect(screen.getAllByText('idle').length).toBeGreaterThan(0);
    expect(screen.getByText('total CPU')).toBeInTheDocument();
    expect(screen.getByText('total mem')).toBeInTheDocument();
    expect(screen.getByText('GPU')).toBeInTheDocument();
    expect(screen.getByText('provisioning queue')).toBeInTheDocument();
  });

  it('renders the cluster health section', async () => {
    wrap();
    // 'Eitri' appears in both the cluster card and the active sessions table (clusterId).
    await waitFor(() => expect(screen.getAllByText('Eitri').length).toBeGreaterThan(0));
    expect(screen.getAllByTestId('cluster-card').length).toBeGreaterThan(0);
  });

  it('renders an active sessions section', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Active sessions')).toBeInTheDocument());
  });

  it('shows "no active sessions" when no running/idle sessions exist', async () => {
    const store = createMockSessionStore();
    const emptyStore = {
      ...store,
      listSessions: vi.fn().mockResolvedValue([]),
    };
    wrap(createMockVolundrService(), createMockClusterAdapter(), emptyStore);
    await waitFor(() => expect(screen.getByTestId('no-active-sessions')).toBeInTheDocument());
  });

  it('shows loading state before stats resolve', () => {
    const slowService = {
      ...createMockVolundrService(),
      getSessions: () => new Promise(() => {}),
      getStats: () => new Promise(() => {}),
    };
    wrap(slowService);
    expect(screen.getByText(/loading sessions/i)).toBeInTheDocument();
  });

  it('shows stats footer with token counts', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText(/Tokens today/)).toBeInTheDocument());
  });

  it('shows cluster health grid', async () => {
    wrap();
    await waitFor(() => expect(screen.getByText('Cluster health')).toBeInTheDocument());
  });
});
