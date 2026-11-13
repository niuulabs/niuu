import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createMimirMockAdapter } from '../../adapters/mock';
import type { IMimirService } from '../../ports';
import { MemoryPagePage } from './MemoryPagePage';
import { encodeNodeId } from '../../domain/graphIndex';

const mockNavigate = vi.fn();
const mockSearch = vi.hoisted(() => ({
  current: {} as { path?: string; mount?: string },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => mockSearch.current,
  Link: ({
    to,
    children,
    search,
    params: _params,
    ...rest
  }: {
    to: string;
    children: ReactNode;
    search?: unknown;
    params?: unknown;
  }) => (
    <a href={to} data-search={search ? JSON.stringify(search) : undefined} {...rest}>
      {children}
    </a>
  ),
}));

const ARCH = '/arch/overview';
const setTweak = vi.fn();

describe('MemoryPagePage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    setTweak.mockReset();
    mockSearch.current = { path: ARCH, mount: 'shared' };
  });

  it('says so when the link carries no page', async () => {
    mockSearch.current = {};
    renderWithMimir(<MemoryPagePage />);
    expect(await screen.findByText('No page named')).toBeInTheDocument();
  });

  it('says so when nothing is written at the path', async () => {
    mockSearch.current = { path: '/nope' };
    renderWithMimir(<MemoryPagePage />);
    expect(await screen.findByText('No such page')).toBeInTheDocument();
  });

  it('reports a failure to open the page', async () => {
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      pages: {
        ...svc.pages,
        getPage: async () => {
          throw new Error('store unreachable');
        },
      },
    };
    renderWithMimir(<MemoryPagePage />, service);
    expect(await screen.findByText('store unreachable')).toBeInTheDocument();
  });

  it('the Memory back link carries this page as a focus (graph node id), not a bare path', async () => {
    renderWithMimir(<MemoryPagePage />);
    const link = await screen.findByRole('link', { name: /Memory/ });
    expect(link).toHaveAttribute('href', '/mimir');
    expect(link).toHaveAttribute(
      'data-search',
      JSON.stringify({ focus: encodeNodeId('shared', ARCH) }),
    );
  });

  it('shows the breadcrumb, title, type and confidence', async () => {
    renderWithMimir(<MemoryPagePage />);
    expect(
      await screen.findByRole('heading', { name: 'Architecture Overview' }),
    ).toBeInTheDocument();
    expect(screen.getByText('shared / arch / overview')).toBeInTheDocument();
    expect(screen.getByText('topic')).toBeInTheDocument();
    expect(screen.getByText('high confidence')).toBeInTheDocument();
  });

  it('counts facts, sources and evidence entries', async () => {
    renderWithMimir(<MemoryPagePage />);
    await screen.findByRole('heading', { name: 'Architecture Overview' });
    expect(screen.getByTestId('memory-read-page')).toHaveTextContent(
      '3 facts · 2 sources · 3 evidence entries',
    );
  });

  it('lists what we believe', async () => {
    renderWithMimir(<MemoryPagePage />);
    const zone = await screen.findByTestId('memory-facts-zone');
    expect(zone).toHaveTextContent('Hexagonal architecture with ports and adapters');
  });

  it('renders the relationships with their notes', async () => {
    renderWithMimir(<MemoryPagePage />);
    const zone = await screen.findByTestId('memory-relationships');
    expect(zone).toHaveTextContent('[[api/overview]]');
    expect(zone).toHaveTextContent('the API rules it implies');
  });

  it('opens a related page from a wikilink', async () => {
    renderWithMimir(<MemoryPagePage />);
    const zone = await screen.findByTestId('memory-relationships');
    await waitFor(() =>
      expect(within(zone).getByRole('button', { name: 'navigate to api/overview' })).toBeEnabled(),
    );
    fireEvent.click(within(zone).getByRole('button', { name: 'navigate to api/overview' }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/mimir/read',
      search: { path: '/api/overview', mount: 'shared' },
    });
  });

  it('shows the standing assessment', async () => {
    renderWithMimir(<MemoryPagePage />);
    expect(await screen.findByTestId('memory-assessment')).toHaveTextContent(
      'Architecture is sound and well-documented',
    );
  });

  it('links to the full graph focused on this page', async () => {
    renderWithMimir(<MemoryPagePage />);
    const around = await screen.findByTestId('memory-around');
    const graphLink = within(around).getByRole('link', { name: /open the graph/ });
    expect(graphLink).toHaveAttribute('href', '/mimir');
    expect(graphLink).toHaveAttribute(
      'data-search',
      JSON.stringify({ focus: encodeNodeId('shared', ARCH) }),
    );
  });

  it('shows the evidence trail newest first, marked append-only', async () => {
    renderWithMimir(<MemoryPagePage />);
    const evidence = await screen.findByTestId('memory-evidence');
    expect(evidence).toHaveTextContent('append-only · never edited');
    expect(evidence).toHaveTextContent('2026-04-18');
    expect(evidence).toHaveTextContent('Source: src-001');
  });

  it('lists the sources the page was compiled from', async () => {
    renderWithMimir(<MemoryPagePage />);
    const evidence = await screen.findByTestId('memory-evidence');
    await waitFor(() => expect(evidence).toHaveTextContent('compiled from'));
    expect(evidence).toHaveTextContent('Niuu Platform Architecture — internal wiki');
  });

  it('asks about this page with its title, answered inline in the Memory scene now', async () => {
    renderWithMimir(<MemoryPagePage />);
    const askLink = await screen.findByTestId('memory-ask-about');
    expect(askLink).toHaveAttribute('href', '/mimir');
    expect(askLink).toHaveAttribute(
      'data-search',
      JSON.stringify({ q: 'Architecture Overview', mount: 'shared' }),
    );
  });

  it('has no dangling Edit button — the Memory scene has no separate page editor', async () => {
    renderWithMimir(<MemoryPagePage />);
    await screen.findByRole('heading', { name: 'Architecture Overview' });
    expect(screen.queryByTestId('memory-edit-page')).not.toBeInTheDocument();
  });

  it('"open the graph" focuses this page in the Memory scene, by graph node id', async () => {
    renderWithMimir(<MemoryPagePage />);
    const link = await screen.findByText('open the graph ›');
    expect(link).toHaveAttribute('href', '/mimir');
    expect(link).toHaveAttribute(
      'data-search',
      JSON.stringify({ focus: encodeNodeId('shared', ARCH) }),
    );
  });

  it('renders nothing for the zones a page does not have', async () => {
    mockSearch.current = { path: '/concepts/drive-loop' };
    renderWithMimir(<MemoryPagePage />);
    await screen.findByRole('heading', { name: 'Drive loop' });
    expect(screen.queryByTestId('memory-facts-zone')).not.toBeInTheDocument();
    expect(screen.queryByTestId('memory-relationships')).not.toBeInTheDocument();
    expect(screen.queryByTestId('memory-assessment')).not.toBeInTheDocument();
    expect(screen.getByTestId('memory-evidence')).toHaveTextContent(
      'No evidence has been recorded for this page yet.',
    );
  });

  it('drops the mount from the breadcrumb when the link carries none', async () => {
    mockSearch.current = { path: '/concepts/drive-loop' };
    renderWithMimir(<MemoryPagePage />);
    expect(await screen.findByText('concepts / drive-loop')).toBeInTheDocument();
  });

  it('leaves out the source list when the page was compiled from none', async () => {
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      pages: { ...svc.pages, getPageSources: async () => [] },
    };
    renderWithMimir(<MemoryPagePage />, service);
    const evidence = await screen.findByTestId('memory-evidence');
    await waitFor(() => expect(evidence).not.toHaveTextContent('compiled from'));
  });
});
