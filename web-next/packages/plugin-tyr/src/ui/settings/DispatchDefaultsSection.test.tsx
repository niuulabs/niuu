import { describe, it, expect } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { DispatchDefaultsSection } from './DispatchDefaultsSection';
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

describe('DispatchDefaultsSection', () => {
  it('shows loading state initially', () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    expect(screen.getByText(/loading dispatch defaults/i)).toBeInTheDocument();
  });

  it('renders form with default values after loading', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => {
      expect(screen.getByRole('form', { name: /dispatch rules form/i })).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('70')).toBeInTheDocument();
    expect(screen.getByDisplayValue('3')).toBeInTheDocument();
    expect(screen.getByDisplayValue('10')).toBeInTheDocument();
  });

  it('shows error state when service throws', async () => {
    const failing = {
      getDispatchDefaults: async () => {
        throw new Error('service unavailable');
      },
    };
    render(<DispatchDefaultsSection />, { wrapper: wrap({ 'tyr.settings': failing }) });
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByText('service unavailable')).toBeInTheDocument();
  });

  it('shows section heading', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Dispatch rules')).toBeInTheDocument());
  });

  it('shows validation error for out-of-range confidence threshold', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/confidence threshold/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/confidence threshold/i);
    fireEvent.change(input, { target: { value: '150' } });
    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));

    await waitFor(() => expect(screen.getByText(/between 0 and 100/i)).toBeInTheDocument());
  });

  it('shows validation error for zero max concurrent raids', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/max concurrent raids/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/max concurrent raids/i);
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));

    await waitFor(() => expect(screen.getByText(/at least 1/i)).toBeInTheDocument());
  });

  it('shows validation error for zero batch size', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/batch size/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/batch size/i);
    fireEvent.change(input, { target: { value: '0' } });
    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));

    await waitFor(() => expect(screen.getByText(/at least 1/i)).toBeInTheDocument());
  });

  it('shows "Saved" confirmation after successful submission', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() =>
      expect(screen.getByRole('form', { name: /dispatch rules form/i })).toBeInTheDocument(),
    );

    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));
    await waitFor(() => expect(screen.getByText('Saved')).toBeInTheDocument());
  });

  it('renders retry policy section', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByText('Retry Policy')).toBeInTheDocument());
    expect(screen.getByLabelText(/max retries per raid/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/retry delay/i)).toBeInTheDocument();
  });

  it('shows negative retry delay error', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/retry delay/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/retry delay/i);
    fireEvent.change(input, { target: { value: '-5' } });
    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));

    await waitFor(() => expect(screen.getByText(/cannot be negative/i)).toBeInTheDocument());
  });

  it('shows negative max retries error', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/max retries per raid/i)).toBeInTheDocument());

    const input = screen.getByLabelText(/max retries per raid/i);
    fireEvent.change(input, { target: { value: '-1' } });
    fireEvent.submit(screen.getByRole('form', { name: /dispatch rules form/i }));

    await waitFor(() => expect(screen.getByText(/cannot be negative/i)).toBeInTheDocument());
  });

  it('renders quiet hours field', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/quiet hours/i)).toBeInTheDocument());
    expect(screen.getByDisplayValue('22:00–07:00 UTC')).toBeInTheDocument();
  });

  it('renders escalate after field', async () => {
    render(<DispatchDefaultsSection />, { wrapper: wrap(defaultServices()) });
    await waitFor(() => expect(screen.getByLabelText(/escalate after/i)).toBeInTheDocument());
    expect(screen.getByDisplayValue('30m')).toBeInTheDocument();
  });
});
