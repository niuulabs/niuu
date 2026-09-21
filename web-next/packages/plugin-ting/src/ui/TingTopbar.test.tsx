import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  it('keeps detailed dispatcher stats in a compact status popover', async () => {
    const user = userEvent.setup();
    mockPathname = '/ting';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    const trigger = await screen.findByRole('button', { name: 'Dispatcher status: active' });
    expect(screen.getByTestId('ting-chip-dispatcher-active')).toBeInTheDocument();
    expect(document.getElementById('ting-dispatcher-status')).not.toBeVisible();

    await user.click(trigger);

    expect(screen.getByRole('dialog', { name: 'Dispatcher status' })).toBeVisible();
    expect(screen.getByText('Threshold')).toBeVisible();
    expect(screen.getByText('0.70')).toBeVisible();
    expect(screen.getByText('Concurrent runs')).toBeVisible();
    expect(screen.getByText('5')).toBeVisible();
  });

  it('keeps dispatcher stats even on legacy settings paths', async () => {
    mockPathname = '/ting/settings/personas';
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    await waitFor(() => {
      expect(screen.getByTestId('ting-topbar')).toBeInTheDocument();
    });
    expect(screen.queryByLabelText('Back to Ting')).not.toBeInTheDocument();
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
    expect(screen.getByTestId('ting-chip-dispatcher-loading')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dispatcher status: loading' })).toBeInTheDocument();
  });

  it('shows the paused dispatcher details', async () => {
    const user = userEvent.setup();
    const stopped: IDispatcherService = {
      ...createMockDispatcherService(),
      getState: async () => ({
        id: '00000000-0000-0000-0000-000000000999',
        running: false,
        threshold: 55,
        maxConcurrentRuns: 2,
        autoContinue: false,
        updatedAt: '2026-07-11T12:00:00Z',
      }),
    };
    render(<TingTopbar />, { wrapper: wrap(stopped) });
    const trigger = await screen.findByRole('button', { name: 'Dispatcher status: paused' });
    expect(screen.getByTestId('ting-chip-dispatcher-paused')).toBeInTheDocument();
    await user.click(trigger);
    expect(screen.getByText('0.55')).toBeVisible();
    expect(screen.getByText('2')).toBeVisible();
  });

  it('dismisses the popover with Escape and returns focus', async () => {
    const user = userEvent.setup();
    render(<TingTopbar />, { wrapper: wrap(createMockDispatcherService()) });
    const trigger = await screen.findByRole('button', { name: 'Dispatcher status: active' });
    await user.click(trigger);
    expect(screen.getByRole('dialog', { name: 'Dispatcher status' })).toBeVisible();

    await user.keyboard('{Escape}');

    expect(document.getElementById('ting-dispatcher-status')).not.toBeVisible();
    expect(trigger).toHaveFocus();
  });

  it('dismisses the popover after an outside pointer action', async () => {
    const user = userEvent.setup();
    render(
      <>
        <TingTopbar />
        <button type="button">Outside</button>
      </>,
      { wrapper: wrap(createMockDispatcherService()) },
    );
    const trigger = await screen.findByRole('button', { name: 'Dispatcher status: active' });
    await user.click(trigger);
    await user.click(screen.getByRole('button', { name: 'Outside' }));
    expect(document.getElementById('ting-dispatcher-status')).not.toBeVisible();
  });
});
