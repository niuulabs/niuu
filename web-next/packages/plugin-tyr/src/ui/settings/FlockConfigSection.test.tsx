import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { FlockConfigSection } from './FlockConfigSection';
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

describe('FlockConfigSection', () => {
  it('shows loading state initially', () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    expect(screen.getByText(/loading flock config/i)).toBeInTheDocument();
  });

  it('renders form with seed values after loading', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByRole('form', { name: /flock configuration form/i })).toBeInTheDocument(),
    );
    expect(screen.getByDisplayValue('Niuu Core')).toBeInTheDocument();
    expect(screen.getByDisplayValue('main')).toBeInTheDocument();
    expect(screen.getByDisplayValue('linear')).toBeInTheDocument();
    expect(screen.getByDisplayValue('5')).toBeInTheDocument();
  });

  it('shows section heading', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Flock Config')).toBeInTheDocument());
  });

  it('shows error state when service throws', async () => {
    const failing = {
      getFlockConfig: async () => {
        throw new Error('flock error');
      },
    };
    render(<FlockConfigSection />, { wrapper: wrap({ 'tyr.settings': failing }) });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('flock error')).toBeInTheDocument();
  });

  it('shows validation error when flockName is empty', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/flock name/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/flock name/i);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.submit(screen.getByRole('form', { name: /flock configuration form/i }));

    await waitFor(() => expect(screen.getByText(/required/i)).toBeInTheDocument());
  });

  it('shows validation error when defaultBaseBranch is empty', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/default base branch/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/default base branch/i);
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.submit(screen.getByRole('form', { name: /flock configuration form/i }));

    await waitFor(() => expect(screen.getByText(/required/i)).toBeInTheDocument());
  });

  it('shows "Saved" after successful submit', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByRole('form', { name: /flock configuration form/i })).toBeInTheDocument(),
    );

    fireEvent.submit(screen.getByRole('form', { name: /flock configuration form/i }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });

  it('shows max active sagas field', async () => {
    render(<FlockConfigSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/max active sagas/i)).toBeInTheDocument());
  });
});
