import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createMimirMockAdapter } from '../../adapters/mock';
import type { IMimirService } from '../../ports';
import { AskMemoryPage } from './AskMemoryPage';

const mockNavigate = vi.fn();
const mockSearch = vi.hoisted(() => ({
  current: {} as { q?: string; mount?: string; path?: string },
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => mockSearch.current,
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

describe('AskMemoryPage', () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    mockSearch.current = {};
  });

  it('suggests questions when nothing has been asked', () => {
    renderWithMimir(<AskMemoryPage />);
    expect(screen.getByText('Ask memory a question')).toBeInTheDocument();
    expect(screen.getByText('how do we deploy to Kubernetes?')).toBeInTheDocument();
    expect(screen.queryByTestId('memory-results')).not.toBeInTheDocument();
  });

  it('asks an example question through the URL', () => {
    renderWithMimir(<AskMemoryPage />);
    fireEvent.click(screen.getByText('how do we deploy to Kubernetes?'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: { q: 'how do we deploy to Kubernetes?', mount: undefined },
    });
  });

  it('prefills the box from the question in the URL', () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    expect(screen.getByLabelText('Ask memory')).toHaveValue('architecture');
  });

  it('says how many pages answer, in which mode, over which mounts', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    await screen.findByTestId('memory-results');
    expect(screen.getByText(/pages answer/)).toHaveTextContent('hybrid (keyword + meaning)');
    expect(screen.getByText(/pages answer/)).toHaveTextContent('searched: every mount');
  });

  it('lists the answering pages with path, score and summary', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const results = await screen.findByTestId('memory-results');
    const cards = within(results).getAllByTestId('memory-result');
    expect(cards.length).toBeGreaterThan(1);
    expect(cards[0]).toHaveTextContent('Architecture Overview');
    expect(cards[0]).toHaveTextContent('/arch/overview');
    expect(cards[0]).toHaveTextContent('0.95');
  });

  it('selects the first result until the URL says otherwise', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const cards = within(await screen.findByTestId('memory-results')).getAllByTestId(
      'memory-result',
    );
    expect(cards[0]).toHaveAttribute('aria-pressed', 'true');
    expect(cards[1]).toHaveAttribute('aria-pressed', 'false');
  });

  it('puts the chosen result in the URL', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const cards = within(await screen.findByTestId('memory-results')).getAllByTestId(
      'memory-result',
    );
    fireEvent.click(cards[1]!);
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: expect.objectContaining({ q: 'architecture' }),
    });
  });

  it('narrows the search to one mount', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    fireEvent.click(await screen.findByTestId('memory-mount-filter-shared'));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: { q: 'architecture', mount: 'shared' },
    });
  });

  it('counts the facts a page carries once it is loaded', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const results = await screen.findByTestId('memory-results');
    await waitFor(() => expect(results).toHaveTextContent('3 facts'));
  });

  it('quotes the facts verbatim with the page they came from', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const panel = await screen.findByTestId('memory-facts');
    expect(panel).toHaveTextContent('quoted from the pages on the left, as written');
    await waitFor(() =>
      expect(panel).toHaveTextContent('Hexagonal architecture with ports and adapters'),
    );
    expect(panel).toHaveTextContent('Architecture Overview · fact 1');
  });

  it('warns when a quoted fact is weakening and offers the revision', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    const warning = await screen.findByTestId('memory-facts-warning');
    expect(warning).toHaveTextContent('One fact behind this answer is weakening');
    fireEvent.click(within(warning).getByTestId('memory-revise-0'));
    expect(screen.getByLabelText(`Revise: ${WEAK_FACT}`)).toBeInTheDocument();
  });

  it('offers the page and the resident that keeps it', async () => {
    mockSearch.current = { q: 'architecture' };
    renderWithMimir(<AskMemoryPage />);
    expect(await screen.findByTestId('memory-open-page')).toHaveAttribute('href', '/mimir/read');
    expect(screen.getByTestId('memory-ask-resident')).toHaveAttribute('href', '/ravn/residents');
  });

  it('points at the realm when the answer comes from a realm mount', async () => {
    mockSearch.current = { q: 'architecture', mount: 'realm-aurora' };
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      pages: {
        ...svc.pages,
        search: async () => [
          {
            path: '/arch/overview',
            title: 'Architecture Overview',
            summary: 'High-level view.',
            category: 'arch',
            type: 'topic',
            confidence: 'high',
            score: 0.9,
            mounts: ['realm-aurora'],
          },
        ],
      },
    };
    renderWithMimir(<AskMemoryPage />, service);
    expect(await screen.findByTestId('memory-ask-resident')).toHaveAttribute(
      'href',
      '/realms/aurora',
    );
  });

  it('says so when nothing answers', async () => {
    mockSearch.current = { q: 'zzzz-nothing' };
    renderWithMimir(<AskMemoryPage />);
    expect(await screen.findByText('No page answers that yet')).toBeInTheDocument();
  });

  it('reports a failed search', async () => {
    mockSearch.current = { q: 'architecture' };
    const svc = createMimirMockAdapter();
    const service: IMimirService = {
      ...svc,
      pages: {
        ...svc.pages,
        search: async () => {
          throw new Error('search backend down');
        },
      },
    };
    renderWithMimir(<AskMemoryPage />, service);
    expect(await screen.findByText('search backend down')).toBeInTheDocument();
  });
});
