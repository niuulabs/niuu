import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { TyrFooter } from './TyrFooter';
import { createMockDispatcherService } from '../adapters/mock';
import type { IDispatcherService } from '../ports';

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

describe('TyrFooter', () => {
  it('renders the footer container', () => {
    render(<TyrFooter />, { wrapper: wrap(createMockDispatcherService()) });
    expect(screen.getByTestId('tyr-footer')).toBeInTheDocument();
  });

  it('shows api status as connected when data loads', async () => {
    render(<TyrFooter />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-api');
      expect(chip.textContent).toContain('connected');
    });
  });

  it('shows dispatcher active status', async () => {
    render(<TyrFooter />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-dispatcher');
      expect(chip.textContent).toContain('active');
    });
  });

  it('shows threshold value', async () => {
    render(<TyrFooter />, { wrapper: wrap(createMockDispatcherService()) });
    // Mock threshold is 70 → 0.70
    await waitFor(() => {
      const chip = screen.getByTestId('footer-chip-threshold');
      expect(chip.textContent).toContain('0.70');
    });
  });

  it('shows connecting when data is pending', () => {
    const slow: IDispatcherService = {
      getState: () => new Promise(() => {}),
      setRunning: async () => {},
      setThreshold: async () => {},
      setAutoContinue: async () => {},
      getLog: async () => [],
    };
    render(<TyrFooter />, { wrapper: wrap(slow) });
    const chip = screen.getByTestId('footer-chip-api');
    expect(chip.textContent).toContain('connecting');
  });
});
