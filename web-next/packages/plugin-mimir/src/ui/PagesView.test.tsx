import { describe, it, expect, vi } from 'vitest';
import { useMemo, useState } from 'react';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PluginCtxProvider, ServicesProvider, type PluginCtx } from '@niuulabs/plugin-sdk';
import { renderWithMimir as wrap } from '../testing/renderWithMimir';
import { PagesView } from './PagesView';
import { createMimirMockAdapter } from '../adapters/mock';
import type { IMimirService } from '../ports';

describe('PagesView', () => {
  it('renders the page tree sidebar', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getByRole('complementary', { name: /page tree/ })).toBeInTheDocument(),
    );
  });

  it('shows directory nodes from page paths', async () => {
    wrap(<PagesView />);
    // Mock has pages at /arch/*, /api/*, /infra/*
    await waitFor(() => expect(screen.getByText('arch/')).toBeInTheDocument());
    expect(screen.getByText('api/')).toBeInTheDocument();
    expect(screen.getByText('infra/')).toBeInTheDocument();
  });

  it('renders the page count badge', async () => {
    wrap(<PagesView />);
    // Mock listPages returns 10 MOCK_PAGES
    await waitFor(() => expect(screen.getByText('10')).toBeInTheDocument());
  });

  it('scopes the tree to the active mount', async () => {
    wrap(<PagesView />, undefined, { tweaks: { activeMount: 'platform' } });
    await waitFor(() => expect(screen.getByText('infra/')).toBeInTheDocument());
    expect(screen.getByText('platform mount tree')).toBeInTheDocument();
    expect(screen.queryByText('api/')).not.toBeInTheDocument();
    expect(screen.getByText('infra/')).toBeInTheDocument();
  });

  it('updates the tree when the active mount changes at runtime', async () => {
    const listPages = vi
      .fn<NonNullable<IMimirService['pages']['listPages']>>()
      .mockImplementation(async (options) => {
        const pages = await createMimirMockAdapter().pages.listPages(options);
        return pages;
      });

    const service: IMimirService = {
      ...createMimirMockAdapter(),
      pages: {
        ...createMimirMockAdapter().pages,
        listPages,
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
              <button type="button" onClick={() => ctx.setTweak('activeMount', 'platform')}>
                focus platform
              </button>
              <PagesView />
            </ServicesProvider>
          </PluginCtxProvider>
        </QueryClientProvider>
      );
    }

    wrap(<Harness />);
    await waitFor(() => expect(screen.getByText('arch/')).toBeInTheDocument());
    expect(listPages).toHaveBeenCalledWith(undefined);

    fireEvent.click(screen.getByRole('button', { name: 'focus platform' }));

    await waitFor(() => expect(screen.getByText('platform mount tree')).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByText('arch/')).not.toBeInTheDocument());
    expect(screen.getByText('infra/')).toBeInTheDocument();
    expect(listPages).toHaveBeenLastCalledWith({ mountName: 'platform' });
  });

  it('uses a requested page path from plugin tweaks', async () => {
    wrap(<PagesView />, undefined, { tweaks: { 'mimir.selectedPagePath': '/api/overview' } });
    await waitFor(() => expect(screen.getByText('API Design Guidelines')).toBeInTheDocument());
  });

  it('can collapse and expand the pages sidebar', async () => {
    function Harness() {
      const [tweaks, setTweaks] = useState<Record<string, unknown>>({});
      const ctx = useMemo<PluginCtx>(
        () => ({
          tweaks,
          setTweak: (key, value) => setTweaks((prev) => ({ ...prev, [key]: value })),
        }),
        [tweaks],
      );
      const client = useMemo(
        () => new QueryClient({ defaultOptions: { queries: { retry: false } } }),
        [],
      );

      return (
        <QueryClientProvider client={client}>
          <PluginCtxProvider value={ctx}>
            <ServicesProvider services={{ mimir: createMimirMockAdapter() }}>
              <PagesView />
            </ServicesProvider>
          </PluginCtxProvider>
        </QueryClientProvider>
      );
    }

    wrap(<Harness />);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /collapse pages sidebar/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /collapse pages sidebar/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /expand pages sidebar/i })).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: /expand pages sidebar/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /collapse pages sidebar/i })).toBeInTheDocument(),
    );
  });

  it('displays a page title and summary when a page is selected', async () => {
    wrap(<PagesView />);
    await waitFor(() => expect(screen.getByText('arch/')).toBeInTheDocument());
    // Click on "arch/" dir to expand (it opens by default at depth 0)
    // Then click on overview leaf — use [0] because /arch/overview and /api/overview both render "overview"
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() => expect(screen.getByText('Architecture Overview')).toBeInTheDocument());
  });

  it('renders zone blocks for the selected page', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() => expect(screen.getByText('Key facts')).toBeInTheDocument());
  });

  it('shows an edit button for each zone in idle state', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /edit .* zone/ }).length).toBeGreaterThan(0),
    );
  });

  it('enters edit mode when the edit button is clicked', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /edit key-facts zone/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /edit key-facts zone/ }));
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /zone edit area/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /save key-facts zone/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel edit/ })).toBeInTheDocument();
  });

  it('cancels edit when cancel is clicked', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /edit key-facts zone/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /edit key-facts zone/ }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /cancel edit/ })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /cancel edit/ }));
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: /zone edit area/ })).toBeNull(),
    );
  });

  it('renders the meta panel with page provenance', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('complementary', { name: /page metadata/ })).toBeInTheDocument(),
    );
    expect(screen.getByText('Provenance')).toBeInTheDocument();
  });

  // ── Layout toggle ────────────────────────────────────────────────────────

  it('renders the Structured and Split layout toggle buttons', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /structured/i, hidden: false }),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /split/i })).toBeInTheDocument();
  });

  it('defaults to structured layout (Structured button is pressed)', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /structured/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /structured/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(screen.getByRole('button', { name: /^split$/i })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
  });

  it('switches to split layout when Split is clicked', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^split$/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /^split$/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^split$/i })).toHaveAttribute(
        'aria-pressed',
        'true',
      ),
    );
    // Raw sources pane should appear
    expect(screen.getByLabelText('raw sources')).toBeInTheDocument();
  });

  // ── Action bar buttons ───────────────────────────────────────────────────

  it('marks unsupported page actions unavailable instead of exposing no-op controls', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /edit page/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /flag for review unavailable/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /promote confidence unavailable/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /cite page/i })).toBeInTheDocument();
  });

  it('Edit action bar button triggers zone edit for the first zone', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /edit page/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /edit page/i }));
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: /zone edit area/ })).toBeInTheDocument(),
    );
  });

  it('Cite button copies page path + title to clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /cite page/i })).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByRole('button', { name: /cite page/i }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0]![0]).toContain('/arch/overview');
  });

  // ── Broken wikilink warning in tree ─────────────────────────────────────

  it('shows a warning indicator on tree leaves with broken wikilinks', async () => {
    wrap(<PagesView />);
    // /infra/k8s has related: ['/infra/envoy', '/arch/overview']
    // '/infra/envoy' does not exist in the mock pages → broken link
    await waitFor(() => expect(screen.getByText('infra/')).toBeInTheDocument());
    // The k8s leaf should have a "broken wikilinks" indicator
    const brokenIndicators = screen.queryAllByLabelText(/page has broken wikilinks/i);
    expect(brokenIndicators.length).toBeGreaterThan(0);
  });
});
