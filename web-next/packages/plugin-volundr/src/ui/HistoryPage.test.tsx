import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { HistoryPage } from './HistoryPage';
import { renderWithVolundr } from '../testing/renderWithVolundr';
import { createMockSessionStore } from '../adapters/mock';

// Link from TanStack Router requires a full router context which is provided
// by the Shell in production but not in unit tests. Stub it as a plain <a>
// so HistoryPage tests remain fast and self-contained.
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>();
  return {
    ...actual,
    Link: ({
      children,
      to,
      params,
      ...rest
    }: {
      children: React.ReactNode;
      to: string;
      params?: Record<string, string>;
      [key: string]: unknown;
    }) => {
      const href = to.replace(/\$(\w+)/g, (_, key: string) => params?.[key] ?? '');
      return (
        <a href={href} {...rest}>
          {children}
        </a>
      );
    },
  };
});

describe('HistoryPage', () => {
  it('renders the heading', () => {
    renderWithVolundr(<HistoryPage />);
    expect(screen.getByRole('heading', { name: /session history/i })).toBeInTheDocument();
  });

  it('shows loading state before sessions resolve', () => {
    const slowStore = {
      ...createMockSessionStore(),
      listSessions: () => new Promise<never>(() => {}),
    };
    renderWithVolundr(<HistoryPage />, { sessionStore: slowStore });
    expect(screen.getByText(/loading history/)).toBeInTheDocument();
  });

  it('renders only terminated/failed sessions', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    // Seed: ds-1(running), ds-2(idle), ds-3(provisioning) excluded; ds-4(failed),
    // ds-5(terminated), + 3 historical (ds-2 term, ds-3 failed, ds-4 term) = 5 total
    expect(screen.getAllByTestId('history-row').length).toBe(5);
  });

  it('shows session IDs in rows', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getByText('ds-2')).toBeInTheDocument());
    expect(screen.getByText('ds-3')).toBeInTheDocument();
    expect(screen.getByText('ds-4')).toBeInTheDocument(); // new active/failed
    expect(screen.getByText('ds-4b')).toBeInTheDocument(); // historical/terminated
    expect(screen.queryByText('ds-1')).not.toBeInTheDocument(); // running session excluded
  });

  it('shows outcome chips', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByText('terminated').length).toBeGreaterThan(0));
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);
  });

  it('shows persona names', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByText('skald').length).toBeGreaterThan(0));
    expect(screen.getAllByText('bard').length).toBeGreaterThan(0);
  });

  it('shows detail links', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() =>
      expect(screen.getAllByRole('link', { name: /details/i }).length).toBeGreaterThan(0),
    );
  });

  it('filters rows by raven ID', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    const ravnFilter = screen.getByLabelText(/raven id/i);
    fireEvent.change(ravnFilter, { target: { value: 'r2' } });
    // Historical ds-3 (r2/failed) + historical ds-4b (r2/terminated) = 2
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(2));
    expect(screen.queryByText('ds-2')).not.toBeInTheDocument();
    expect(screen.getByText('ds-3')).toBeInTheDocument();
    expect(screen.getByText('ds-4b')).toBeInTheDocument();
  });

  it('filters rows by persona name', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    const personaFilter = screen.getByLabelText(/persona/i);
    fireEvent.change(personaFilter, { target: { value: 'skald' } });
    // Only historical ds-2 has personaName 'skald'
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(1));
    expect(screen.getByText('ds-2')).toBeInTheDocument();
  });

  it('filters by outcome button — failed', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole('button', { name: 'failed' }));
    // New ds-4 (active/failed) + historical ds-3 (failed) = 2
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(2));
    expect(screen.getByText('ds-3')).toBeInTheDocument();
  });

  it('clicking All outcome button restores all rows', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    const totalCount = screen.getAllByTestId('history-row').length;
    fireEvent.click(screen.getByRole('button', { name: 'failed' }));
    await waitFor(() =>
      expect(screen.getAllByTestId('history-row').length).toBeLessThan(totalCount),
    );
    fireEvent.click(screen.getByRole('button', { name: 'All' }));
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(totalCount));
  });

  it('shows Clear filters button when a filter is active', async () => {
    renderWithVolundr(<HistoryPage />);
    expect(screen.queryByRole('button', { name: /clear filters/i })).not.toBeInTheDocument();
    const ravnFilter = screen.getByLabelText(/raven id/i);
    fireEvent.change(ravnFilter, { target: { value: 'r1' } });
    expect(screen.getByRole('button', { name: /clear filters/i })).toBeInTheDocument();
  });

  it('Clear filters button resets all filters', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBeGreaterThan(0));
    const totalCount = screen.getAllByTestId('history-row').length;
    const ravnFilter = screen.getByLabelText(/raven id/i);
    fireEvent.change(ravnFilter, { target: { value: 'r2' } });
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(2));
    fireEvent.click(screen.getByRole('button', { name: /clear filters/i }));
    await waitFor(() => expect(screen.getAllByTestId('history-row').length).toBe(totalCount));
  });

  it('shows error state when session store throws', async () => {
    const failStore = {
      ...createMockSessionStore(),
      listSessions: async () => {
        throw new Error('session store down');
      },
    };
    renderWithVolundr(<HistoryPage />, { sessionStore: failStore });
    await waitFor(() => expect(screen.getByText('session store down')).toBeInTheDocument());
  });

  it('shows empty state when no terminal sessions match', async () => {
    const emptyStore = {
      ...createMockSessionStore(),
      listSessions: async () => [],
    };
    renderWithVolundr(<HistoryPage />, { sessionStore: emptyStore });
    await waitFor(() =>
      expect(screen.getByText(/no terminated sessions match/i)).toBeInTheDocument(),
    );
  });

  it('shows saga IDs where available', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() => expect(screen.getByText('saga-auth')).toBeInTheDocument());
    expect(screen.getByText('saga-api')).toBeInTheDocument();
  });

  it('detail links point to /volundr/session/:id/archived', async () => {
    renderWithVolundr(<HistoryPage />);
    await waitFor(() =>
      expect(screen.getAllByRole('link', { name: /details/i }).length).toBeGreaterThan(0),
    );
    const links = screen.getAllByRole('link', { name: /details/i });
    // Verify all detail links use the correct archived route pattern
    for (const link of links) {
      expect(link.getAttribute('href')).toMatch(/^\/volundr\/session\/.+\/archived$/);
    }
  });
});
