/**
 * TingPage is a thin re-export of DashboardPage.
 * DashboardPage has its own comprehensive test suite.
 * This file verifies the re-export contract so the /ting route still works.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TingPage } from './TingPage';
import { createMockTingService, createMockDispatcherService } from '../adapters/mock';

vi.mock('@tanstack/react-router', () => ({
  useNavigate: vi.fn().mockReturnValue(vi.fn()),
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

const defaultServices = {
  ting: createMockTingService(),
  'ting.dispatcher': createMockDispatcherService(),
};

describe('TingPage (DashboardPage alias)', () => {
  it('renders the saga stream section', async () => {
    render(<TingPage />, { wrapper: wrap(defaultServices) });
    await waitFor(() => expect(screen.getByText('Saga stream')).toBeInTheDocument());
  });

  it('renders KPI cards', async () => {
    render(<TingPage />, { wrapper: wrap(defaultServices) });
    await waitFor(() => expect(screen.getByText('Active sagas')).toBeInTheDocument());
    expect(screen.getByText('Active runs')).toBeInTheDocument();
  });

  it('renders the live flock and event feed sections', async () => {
    render(<TingPage />, { wrapper: wrap(defaultServices) });
    await waitFor(() => expect(screen.getByText('Live flock')).toBeInTheDocument());
    expect(screen.getByText('Event feed')).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing = {
      ting: {
        getSagas: async () => {
          throw new Error('ting unavailable');
        },
      },
      'ting.dispatcher': createMockDispatcherService(),
    };
    render(<TingPage />, { wrapper: wrap(failing) });
    await waitFor(() => expect(screen.getByText('ting unavailable')).toBeInTheDocument());
  });
});
