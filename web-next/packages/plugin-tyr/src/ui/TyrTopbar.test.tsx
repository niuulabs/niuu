import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TyrTopbar } from './TyrTopbar';
import { createMockDispatcherService } from '../adapters/mock';
import type { IDispatcherService } from '../ports';

// ---------------------------------------------------------------------------
// Router mock
// ---------------------------------------------------------------------------
let mockPathname = '/tyr';
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { pathname: mockPathname } }),
  useRouter: () => ({
    navigate: vi.fn(),
  }),
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function wrap(dispatcher: IDispatcherService) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ 'tyr.dispatcher': dispatcher }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('TyrTopbar', () => {
  it('renders dispatcher stats on dashboard route', async () => {
    mockPathname = '/tyr';
    render(<TyrTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      expect(screen.getByTestId('tyr-topbar')).toBeInTheDocument();
    });
  });

  it('shows dispatcher on chip when running', async () => {
    mockPathname = '/tyr';
    render(<TyrTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      expect(screen.getByTestId('tyr-chip-dispatcher-on')).toBeInTheDocument();
    });
  });

  it('shows threshold value from dispatcher state', async () => {
    mockPathname = '/tyr';
    render(<TyrTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // Mock dispatcher threshold is 70 → displayed as 0.70
    await waitFor(() => {
      expect(screen.getByTestId('tyr-chip-threshold-0.70')).toBeInTheDocument();
    });
  });

  it('shows concurrent raids value', async () => {
    mockPathname = '/tyr/sagas';
    render(<TyrTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // Mock maxConcurrentRaids is 5
    await waitFor(() => {
      expect(screen.getByTestId('tyr-chip-concurrent-5')).toBeInTheDocument();
    });
  });

  it('shows settings breadcrumb on settings routes', async () => {
    mockPathname = '/tyr/settings/personas';
    render(<TyrTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // SettingsTopbar renders "← Tyr" button
    expect(screen.getByLabelText('Back to Tyr')).toBeInTheDocument();
  });

  it('shows loading state when data is pending', () => {
    mockPathname = '/tyr';
    const slow: IDispatcherService = {
      getState: () => new Promise(() => {}), // never resolves
      setRunning: async () => {},
      setThreshold: async () => {},
      setAutoContinue: async () => {},
      getLog: async () => [],
    };
    render(<TyrTopbar />, { wrapper: wrap(slow) });
    expect(screen.getByTestId('tyr-topbar')).toBeInTheDocument();
    expect(screen.getByTestId('tyr-chip-dispatcher-…')).toBeInTheDocument();
  });
});
