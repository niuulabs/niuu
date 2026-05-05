import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { NotificationsSection } from './NotificationsSection';
import { createMockTyrSettingsService } from '../../adapters/mock';

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

const defaultServices = () => ({ 'tyr.settings': createMockTyrSettingsService() });

describe('NotificationsSection', () => {
  it('shows loading state initially', () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    expect(screen.getByText(/loading notification settings/i)).toBeInTheDocument();
  });

  it('renders form after loading', async () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByRole('form', { name: /notification settings form/i })).toBeInTheDocument(),
    );
  });

  it('shows section heading', async () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Notifications')).toBeInTheDocument());
  });

  it('shows error state when service throws', async () => {
    const failing = {
      getNotificationSettings: async () => {
        throw new Error('notif error');
      },
    };
    render(<NotificationsSection />, { wrapper: wrap({ 'tyr.settings': failing }) });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('notif error')).toBeInTheDocument();
  });

  it('shows event toggle rows', async () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Raid awaiting approval')).toBeInTheDocument());
    expect(screen.getByText('Raid merged')).toBeInTheDocument();
    expect(screen.getByText('Raid failed')).toBeInTheDocument();
    expect(screen.getByText('Saga complete')).toBeInTheDocument();
    expect(screen.getByText('Dispatcher error')).toBeInTheDocument();
  });

  it('shows "Saved" after successful submit', async () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByRole('form', { name: /notification settings form/i })).toBeInTheDocument(),
    );

    fireEvent.submit(screen.getByRole('form', { name: /notification settings form/i }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });

  it('shows channel selection label', async () => {
    render(<NotificationsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Notification channel')).toBeInTheDocument());
  });
});
