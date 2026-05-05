import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { CredentialsPage } from './CredentialsPage';
import { createMockVolundrService } from '../adapters/mock';

function wrap(service = createMockVolundrService()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ volundr: service }}>
        <CredentialsPage />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

describe('CredentialsPage', () => {
  it('renders the page header', () => {
    wrap();
    expect(screen.getAllByText('Credentials')).toHaveLength(2);
    expect(screen.getByText(/secrets injected into pods/i)).toBeInTheDocument();
  });

  it('renders new credential button', () => {
    wrap();
    expect(screen.getByTestId('new-credential-btn')).toBeInTheDocument();
    expect(screen.getByText(/new credential/i)).toBeInTheDocument();
  });

  it('renders the grouped sidebar and credentials table by default', async () => {
    wrap();
    await waitFor(() => expect(screen.getByTestId('credentials-table')).toBeInTheDocument());
    expect(screen.getByTestId('credentials-sidebar')).toBeInTheDocument();
    expect(screen.getAllByText('anthropic-key')).toHaveLength(2);
    expect(screen.getByText('LINEAR_REFRESH')).toBeInTheDocument();
    expect(screen.getByText('template:mimir-embeddings')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    const slowService = {
      ...createMockVolundrService(),
      getCredentials: () => new Promise(() => {}),
    };
    wrap(slowService);
    expect(screen.getByTestId('credentials-loading')).toBeInTheDocument();
  });

  it('shows error state', async () => {
    const errorService = {
      ...createMockVolundrService(),
      getCredentials: () => Promise.reject(new Error('fail')),
    };
    wrap(errorService);
    await waitFor(() => expect(screen.getByTestId('credentials-error')).toBeInTheDocument());
  });

  it('shows empty state when no credentials', async () => {
    const emptyService = {
      ...createMockVolundrService(),
      getCredentials: vi.fn().mockResolvedValue([]),
    };
    wrap(emptyService);
    await waitFor(() => expect(screen.getByTestId('no-credentials')).toBeInTheDocument());
  });

  it('renders credentials table with custom data', async () => {
    const serviceWithCreds = {
      ...createMockVolundrService(),
      getCredentials: vi.fn().mockResolvedValue([
        {
          id: 'cr-1',
          name: 'anthropic-key',
          secretType: 'api_key',
          keys: ['ANTHROPIC_API_KEY'],
          scope: 'global',
          used: 12,
          metadata: {},
          createdAt: '2026-01-01',
          updatedAt: '2d ago',
        },
      ]),
    };
    wrap(serviceWithCreds);
    await waitFor(() => expect(screen.getByTestId('credentials-table')).toBeInTheDocument());
    const table = within(screen.getByTestId('credentials-table'));
    expect(table.getByText('anthropic-key')).toBeInTheDocument();
    expect(table.getByText('api key')).toBeInTheDocument();
    expect(table.getByText('ANTHROPIC_API_KEY')).toBeInTheDocument();
    expect(table.getByText('12')).toBeInTheDocument();
  });
});
