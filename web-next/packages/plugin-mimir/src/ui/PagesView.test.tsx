import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { renderWithMimir as wrap } from '../testing/renderWithMimir';
import { PagesView } from './PagesView';

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

  it('renders the action bar with Edit, Flag, Promote confidence, and Cite buttons', async () => {
    wrap(<PagesView />);
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /overview/ }).length).toBeGreaterThan(0),
    );
    fireEvent.click(screen.getAllByRole('button', { name: /overview/ })[0]);
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /edit page/i })).toBeInTheDocument(),
    );
    expect(screen.getByRole('button', { name: /flag for review/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /promote confidence/i })).toBeInTheDocument();
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
