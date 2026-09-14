import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createMimirMockAdapter } from '../../adapters/mock';
import type { IMimirService } from '../../ports';
import { MemoryHomePage } from './MemoryHomePage';

const mockNavigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => ({}),
  Link: ({
    to,
    children,
    search: _search,
    params: _params,
    ...rest
  }: {
    to: string;
    children: ReactNode;
    search?: unknown;
    params?: unknown;
  }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

const WEAK_FACT = 'Six cognitive regions (Sköll, Hati, Sága, Móði, Váli, Víðarr)';
const STALE_FACT = 'Hexagonal adapter pattern for all infrastructure';

describe('MemoryHomePage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
  });

  it('leads with the question the screen answers', async () => {
    renderWithMimir(<MemoryHomePage />);
    expect(screen.getByRole('heading', { name: 'What does Niuu remember?' })).toBeInTheDocument();
    expect(screen.getByText(/Residents write what they learn here/)).toBeInTheDocument();
  });

  it('counts pages and sources across the mounts', async () => {
    renderWithMimir(<MemoryHomePage />);
    // 412 + 1180 + 342 + 184 pages, 185 + 842 + 218 + 128 sources
    expect(await screen.findByText('2,118 pages')).toBeInTheDocument();
    expect(await screen.findByText('1,373 sources')).toBeInTheDocument();
  });

  it('shows the health score and that search is hybrid', async () => {
    renderWithMimir(<MemoryHomePage />);
    expect(await screen.findByText(/^health /)).toBeInTheDocument();
    expect(screen.getByText('hybrid search')).toBeInTheDocument();
  });

  it('renders one card per memory store', async () => {
    renderWithMimir(<MemoryHomePage />);
    expect(await screen.findByTestId('memory-mount-local')).toBeInTheDocument();
    expect(screen.getByTestId('memory-mount-shared')).toBeInTheDocument();
    expect(screen.getByTestId('memory-mount-platform')).toBeInTheDocument();
    expect(screen.getByTestId('memory-mount-forge')).toBeInTheDocument();
  });

  it('shows pages and sources on a mount card', async () => {
    renderWithMimir(<MemoryHomePage />);
    const card = await screen.findByTestId('memory-mount-local');
    expect(card).toHaveTextContent('412');
    expect(card).toHaveTextContent('185');
    expect(card).toHaveTextContent(/last written/);
  });

  it('links a realm mount to its realm', async () => {
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      mounts: {
        ...svc.mounts,
        listMounts: async () => [
          {
            name: 'realm-aurora',
            role: 'domain',
            host: 'kb',
            url: '',
            priority: 1,
            categories: null,
            status: 'healthy',
            pages: 12,
            sources: 3,
            lintIssues: 0,
            lastWrite: '2026-04-19T09:00:00Z',
            embedding: 'fts',
            sizeKb: 10,
            desc: '',
          },
        ],
      },
    };
    renderWithMimir(<MemoryHomePage />, service);
    const link = await screen.findByRole('link', { name: 'aurora' });
    expect(link).toHaveAttribute('href', '/realms/aurora');
  });

  it('merges the write and activity feeds into plain labels', async () => {
    renderWithMimir(<MemoryHomePage />);
    const feed = await screen.findByTestId('memory-feed');
    await waitFor(() => expect(feed).toHaveTextContent('fact added'));
    expect(feed).toHaveTextContent('source ingested');
    expect(feed).toHaveTextContent('ravn-fjolnir');
  });

  it('lists only the beliefs whose proof is going cold', async () => {
    renderWithMimir(<MemoryHomePage />);
    const beliefs = await screen.findByTestId('memory-beliefs');
    await waitFor(() => expect(beliefs).toHaveTextContent(WEAK_FACT));
    expect(beliefs).toHaveTextContent(STALE_FACT);
    expect(beliefs).not.toHaveTextContent('Hexagonal architecture with ports and adapters');
  });

  it('stamps each belief with its proof count and trend', async () => {
    renderWithMimir(<MemoryHomePage />);
    await screen.findByTestId('memory-beliefs');
    await waitFor(() => expect(screen.getAllByTestId('proof-pill').length).toBeGreaterThan(0));
    const pills = screen.getAllByTestId('proof-pill');
    expect(pills.map((pill) => pill.dataset['trend'])).toContain('weakening');
  });

  it('hands the question to the ask page', async () => {
    renderWithMimir(<MemoryHomePage />);
    fireEvent.change(screen.getByLabelText('Ask memory'), {
      target: { value: 'how do we deploy?' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Ask memory' }));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: { q: 'how do we deploy?' },
    });
  });

  it('does not ask an empty question', async () => {
    renderWithMimir(<MemoryHomePage />);
    expect(screen.getByRole('button', { name: 'Ask memory' })).toBeDisabled();
  });

  it('revising a belief takes it off the list', async () => {
    renderWithMimir(<MemoryHomePage />);
    const beliefs = await screen.findByTestId('memory-beliefs');
    await waitFor(() => expect(beliefs).toHaveTextContent(WEAK_FACT));

    fireEvent.click(await screen.findByTestId('memory-revise-0'));
    fireEvent.change(screen.getByLabelText(`Revise: ${WEAK_FACT}`), {
      target: { value: 'Six regions, checked again today' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision' }));

    await waitFor(() => expect(beliefs).not.toHaveTextContent(WEAK_FACT));
  });

  it('shows the write error inline and keeps the belief', async () => {
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      pages: {
        ...svc.pages,
        revisePage: async () => {
          throw new Error('Not authorised to write');
        },
      },
    };
    renderWithMimir(<MemoryHomePage />, service);
    const beliefs = await screen.findByTestId('memory-beliefs');
    await waitFor(() => expect(beliefs).toHaveTextContent(WEAK_FACT));

    fireEvent.click(await screen.findByTestId('memory-revise-0'));
    fireEvent.change(screen.getByLabelText(`Revise: ${WEAK_FACT}`), {
      target: { value: 'something else' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save revision' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Not authorised to write');
    expect(beliefs).toHaveTextContent(WEAK_FACT);
  });

  it('reports a failure to read the memory stores', async () => {
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      mounts: {
        ...svc.mounts,
        listMounts: async () => {
          throw new Error('mount registry down');
        },
      },
    };
    renderWithMimir(<MemoryHomePage />, service);
    expect(await screen.findByText('mount registry down')).toBeInTheDocument();
  });
});
