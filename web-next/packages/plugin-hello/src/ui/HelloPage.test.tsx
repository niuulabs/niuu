import { describe, it, expect } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { HelloPage } from './HelloPage';
import { createMockHelloService } from '../adapters/mock';

function wrap(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ hello: createMockHelloService() }}>{ui}</ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('HelloPage', () => {
  it('renders the title and loading state then data', async () => {
    wrap(<HelloPage />);
    expect(screen.getByText('hello · smoke test')).toBeInTheDocument();
    expect(screen.getByText(/loading/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText('hello from the mock adapter')).toBeInTheDocument(),
    );
  });

  it('renders error state when the service throws', async () => {
    const failing = {
      listGreetings: async () => {
        throw new Error('boom');
      },
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ServicesProvider services={{ hello: failing }}>
          <HelloPage />
        </ServicesProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
  });
});
