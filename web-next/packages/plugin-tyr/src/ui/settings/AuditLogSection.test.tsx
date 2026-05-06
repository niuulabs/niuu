import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { AuditLogSection } from './AuditLogSection';
import { createMockAuditLogService } from '../../adapters/mock';

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

const defaultServices = () => ({ 'tyr.audit': createMockAuditLogService() });

describe('AuditLogSection', () => {
  it('shows loading state initially', () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    expect(screen.getByText(/loading audit log/i)).toBeInTheDocument();
  });

  it('renders audit log entries after loading', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText(/6 entries/i)).toBeInTheDocument());
  });

  it('shows section heading', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Audit Log')).toBeInTheDocument());
  });

  it('shows error state when service throws', async () => {
    const failing = {
      listAuditEntries: async () => {
        throw new Error('audit unavailable');
      },
    };
    render(<AuditLogSection />, { wrapper: wrap({ 'tyr.audit': failing }) });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('audit unavailable')).toBeInTheDocument();
  });

  it('shows filter buttons for kind groups', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid dispatched')).toBeInTheDocument());
    expect(screen.getByText('Raid merged')).toBeInTheDocument();
    expect(screen.getByText('Dispatcher started')).toBeInTheDocument();
    expect(screen.getByText('Flock config updated')).toBeInTheDocument();
  });

  it('activates filter on kind button click and shows (filtered)', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid dispatched')).toBeInTheDocument());

    const dispatchedBtn = screen.getByRole('button', { name: 'Raid dispatched' });
    fireEvent.click(dispatchedBtn);
    expect(dispatchedBtn).toHaveAttribute('aria-pressed', 'true');

    await waitFor(() => expect(screen.getByText(/filtered/i)).toBeInTheDocument());
  });

  it('deactivates filter on second click', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid dispatched')).toBeInTheDocument());

    const btn = screen.getByRole('button', { name: 'Raid dispatched' });
    fireEvent.click(btn);
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows clear filters button when filters are active', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid dispatched')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Raid dispatched' }));
    await waitFor(() => expect(screen.getByText(/Clear filters/i)).toBeInTheDocument());
  });

  it('clears filters when clear button is clicked', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid dispatched')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Raid dispatched' }));
    await waitFor(() => expect(screen.getByText(/Clear filters/i)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Clear filters/i));
    await waitFor(() => expect(screen.queryByText(/Clear filters/i)).not.toBeInTheDocument());
  });

  it('renders table with correct columns', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Time')).toBeInTheDocument());
    expect(screen.getByText('Event')).toBeInTheDocument();
    expect(screen.getByText('Summary')).toBeInTheDocument();
    expect(screen.getByText('Actor')).toBeInTheDocument();
  });

  it('renders entry summaries in the table', async () => {
    render(<AuditLogSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Dispatcher started')).toBeInTheDocument());
  });
});
