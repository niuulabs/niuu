import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMemo, useState } from 'react';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { SearchPage } from './SearchPage';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';
import { renderWithMimir } from '../testing/renderWithMimir';

const wrap = renderWithMimir;
const mockNavigate = vi.fn();

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual<typeof import('@tanstack/react-router')>(
    '@tanstack/react-router',
  );
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('SearchPage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
  });

  it('renders the search input', () => {
    wrap(<SearchPage />);
    expect(screen.getByRole('searchbox')).toBeInTheDocument();
  });

  it('renders all three mode toggle buttons', () => {
    wrap(<SearchPage />);
    expect(screen.getByRole('button', { name: /fts/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /semantic/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /hybrid/i })).toBeInTheDocument();
  });

  it('hybrid mode is active by default', () => {
    wrap(<SearchPage />);
    const hybridBtn = screen.getByRole('button', { name: /hybrid/i });
    expect(hybridBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking a mode button marks it active', () => {
    wrap(<SearchPage />);
    const ftsBtn = screen.getByRole('button', { name: /fts/i });
    fireEvent.click(ftsBtn);
    expect(ftsBtn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /hybrid/i })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('shows no results before typing', () => {
    wrap(<SearchPage />);
    expect(screen.queryByTestId('search-result')).not.toBeInTheDocument();
  });

  it('shows results after typing a matching query', async () => {
    wrap(<SearchPage />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'architecture' } });
    await waitFor(() => expect(screen.getAllByTestId('search-result').length).toBeGreaterThan(0));
  });

  it('shows empty message when no results match', async () => {
    wrap(<SearchPage />);
    fireEvent.change(screen.getByRole('searchbox'), {
      target: { value: 'xyzxyzxyz_no_match_at_all' },
    });
    await waitFor(() => expect(screen.getByText(/no results found/i)).toBeInTheDocument());
  });

  it('shows error state when the service throws', async () => {
    const failing: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        search: async () => {
          throw new Error('search service down');
        },
      },
    };
    wrap(<SearchPage />, failing);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'test' } });
    await waitFor(() => expect(screen.getByText('search service down')).toBeInTheDocument());
  });

  it('scopes the placeholder to the active mount', () => {
    wrap(<SearchPage />, undefined, { tweaks: { activeMount: 'local' } });
    expect(screen.getByPlaceholderText('Search pages in local…')).toBeInTheDocument();
  });

  it('passes the active mount to the search service', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const service: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        search,
      },
    };
    wrap(<SearchPage />, service, { tweaks: { activeMount: 'local' } });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'memory' } });
    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search).toHaveBeenLastCalledWith('memory', 'hybrid', 'local');
  });

  it('updates the search scope when the active mount changes at runtime', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const service: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        search,
      },
    };

    function Harness() {
      const [activeMount, setActiveMount] = useState<string>('all');
      const ctx = useMemo<PluginCtx>(
        () => ({
          tweaks: { activeMount },
          setTweak: (key, value) => {
            if (key === 'activeMount') setActiveMount(String(value));
          },
        }),
        [activeMount],
      );
      const client = useMemo(
        () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
        [],
      );
      return (
        <QueryClientProvider client={client}>
          <PluginCtxProvider value={ctx}>
            <ServicesProvider services={{ mimir: service }}>
              <button type="button" onClick={() => ctx.setTweak('activeMount', 'local')}>
                focus local
              </button>
              <SearchPage />
            </ServicesProvider>
          </PluginCtxProvider>
        </QueryClientProvider>
      );
    }

    wrap(<Harness />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'memory' } });
    await waitFor(() => expect(search).toHaveBeenLastCalledWith('memory', 'hybrid', undefined));

    fireEvent.click(screen.getByRole('button', { name: 'focus local' }));
    await waitFor(() => expect(search).toHaveBeenLastCalledWith('memory', 'hybrid', 'local'));
    expect(screen.getByPlaceholderText('Search pages in local…')).toBeInTheDocument();
  });

  it('each result shows title and path', async () => {
    wrap(<SearchPage />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'architecture' } });
    await waitFor(() => expect(screen.getAllByTestId('search-result').length).toBeGreaterThan(0));
    const first = screen.getAllByTestId('search-result')[0]!;
    // Title text is rendered inline (no dedicated testid)
    expect(first.textContent).toContain('Architecture');
    // Path is rendered in a mono line
    expect(first.textContent).toContain('/arch/overview');
  });

  it('each result shows a numeric score', async () => {
    wrap(<SearchPage />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'architecture' } });
    await waitFor(() => expect(screen.getAllByTestId('search-result').length).toBeGreaterThan(0));
    const first = screen.getAllByTestId('search-result')[0]!;
    expect(first.textContent).toMatch(/score \d+\.\d+/);
  });

  it('each result shows mount chips', async () => {
    wrap(<SearchPage />);
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'architecture' } });
    await waitFor(() => expect(screen.getAllByTestId('search-result').length).toBeGreaterThan(0));
    const first = screen.getAllByTestId('search-result')[0]!;
    // Mount chips render as .mm-chip spans — first result has mounts ['local', 'shared']
    expect(first.textContent).toContain('local');
    expect(first.textContent).toContain('shared');
  });

  it('clicking a result opens the pages route and stores the selected page path', async () => {
    const setTweak = vi.fn();
    wrap(<SearchPage />, undefined, { tweaks: {}, setTweak });

    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'architecture' } });
    await waitFor(() => expect(screen.getAllByTestId('search-result').length).toBeGreaterThan(0));

    fireEvent.click(screen.getAllByTestId('search-result')[0]!);

    expect(setTweak).toHaveBeenCalledWith('mimir.selectedPagePath', '/arch/overview');
    expect(mockNavigate).toHaveBeenCalledWith({ to: '/mimir/pages' });
  });
});
