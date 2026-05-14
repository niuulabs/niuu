import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TingTopbar } from './TingTopbar';
import { createMockDispatcherService } from '../adapters/mock';
import type { IDispatcherService } from '../ports';

// ---------------------------------------------------------------------------
// Router mock
// ---------------------------------------------------------------------------
let mockPathname = '/ting';
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
        <ServicesProvider services={{ 'ting.dispatcher': dispatcher }}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

describe('TingTopbar', () => {
  it('renders dispatcher stats on dashboard route', async () => {
    mockPathname = '/ting';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      expect(screen.getByTestId('ting-topbar')).toBeInTheDocument();
    });
  });

  it('shows dispatcher on chip when running', async () => {
    mockPathname = '/ting';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      expect(screen.getByTestId('ting-chip-dispatcher-on')).toBeInTheDocument();
    });
  });

  it('shows threshold value from dispatcher state', async () => {
    mockPathname = '/ting';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // Mock dispatcher threshold is 70 → displayed as 0.70
    await waitFor(() => {
      expect(screen.getByTestId('ting-chip-threshold-0.70')).toBeInTheDocument();
    });
  });

  it('shows concurrent runs value', async () => {
    mockPathname = '/ting/sagas';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // Mock maxConcurrentRuns is 5
    await waitFor(() => {
      expect(screen.getByTestId('ting-chip-concurrent-5')).toBeInTheDocument();
    });
  });

  it('shows settings breadcrumb on settings routes', async () => {
    mockPathname = '/ting/settings/personas';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    // SettingsTopbar renders "← Ting" button
    expect(screen.getByLabelText('Back to Ting')).toBeInTheDocument();
  });

  it('shows loading state when data is pending', () => {
    mockPathname = '/ting';
    const slow: IDispatcherService = {
      getState: () => new Promise(() => {}), // never resolves
      setRunning: async () => {},
      setThreshold: async () => {},
      setAutoContinue: async () => {},
      getLog: async () => [],
    };
    render(<TingTopbar />, { wrapper: wrap(slow) });
    expect(screen.getByTestId('ting-topbar')).toBeInTheDocument();
    expect(screen.getByTestId('ting-chip-dispatcher-…')).toBeInTheDocument();
  });
});
