import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { RavensPage } from './RavensPage';
import {
  createMockRavenStream,
  createMockBudgetStream,
  createMockTriggerStore,
  createMockSessionStream,
} from '../adapters/mock';

function makeServices(overrides?: Record<string, unknown>) {
  return {
    'ravn.ravens': createMockRavenStream(),
    'ravn.budget': createMockBudgetStream(),
    'ravn.triggers': createMockTriggerStore(),
    'ravn.sessions': createMockSessionStream(),
    ...overrides,
  };
}

function wrap(services = makeServices()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <ServicesProvider services={services}>{children}</ServicesProvider>
      </QueryClientProvider>
    );
  };
}

beforeEach(() => {
  localStorage.clear();
});

describe('RavensPage', () => {
  it('shows loading state initially', () => {
    const slow = { listRavens: () => new Promise(() => undefined) };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': slow })) });
    expect(screen.getByTestId('ravens-loading')).toBeInTheDocument();
  });

  it('shows error state when ravens service fails', async () => {
    const failing = { listRavens: () => Promise.reject(new Error('load failed')) };
    render(<RavensPage />, { wrapper: wrap(makeServices({ 'ravn.ravens': failing })) });
    await waitFor(() => expect(screen.getByTestId('ravens-error')).toBeInTheDocument());
    expect(screen.getByText(/load failed/i)).toBeInTheDocument();
  });

  it('renders the split fleet layout controls', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('ravens-page')).toBeInTheDocument());
    expect(screen.getByTestId('ravens-sidebar')).toBeInTheDocument();
    expect(screen.getByTestId('ravens-search')).toBeInTheDocument();
    expect(screen.getByTestId('grouping-selector')).toBeInTheDocument();
    expect(screen.getByTestId('layout-split')).toBeInTheDocument();
  });

  it('selects a ravn by default and shows its detail pane', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('ravn-detail')).toBeInTheDocument());
    expect(screen.getAllByText(/sindri/i).length).toBeGreaterThan(0);
  });

  it('filters the fleet list from the left rail search', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByTestId('ravens-search'), { target: { value: 'muninn' } });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row')).toHaveLength(1));
    expect(screen.getByText('muninn')).toBeInTheDocument();
  });

  it('switches grouping from the segmented control and persists it', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getByTestId('group-btn-state')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('group-btn-state'));

    await waitFor(() => expect(screen.getByText('Active')).toBeInTheDocument());
    expect(localStorage.getItem('ravn.ravens.group')).toBe('"state"');
  });

  it('switches the selected ravn when a different list row is clicked', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() => expect(screen.getAllByTestId('ravn-list-row').length).toBeGreaterThan(1));
    const muninnRow = screen
      .getAllByTestId('ravn-list-row')
      .find((row) => within(row).queryByText('muninn'));

    expect(muninnRow).toBeTruthy();
    if (muninnRow) fireEvent.click(muninnRow);

    await waitFor(() => expect(screen.getAllByText('muninn').length).toBeGreaterThan(0));
  });

  it('collapses and expands the fleet sidebar', async () => {
    render(<RavensPage />, { wrapper: wrap() });

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /collapse ravens sidebar/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /collapse ravens sidebar/i }));
    expect(screen.getByRole('button', { name: /expand ravens sidebar/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand ravens sidebar/i }));
    expect(screen.getByRole('button', { name: /collapse ravens sidebar/i })).toBeInTheDocument();
  });
});
