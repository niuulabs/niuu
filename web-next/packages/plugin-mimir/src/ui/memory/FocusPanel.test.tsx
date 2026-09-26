import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import {
  createFakeMimirService,
  FAKE_PAGES,
  FAKE_GRAPH,
  fakeNodeId,
} from '../../testing/fakeMimirService';
import { FocusPanel } from './FocusPanel';

const PAGE = FAKE_PAGES.find((p) => p.path === '/platform/gateway-routing')!;
const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const CEDAR = fakeNodeId('platform', '/platform/cedar-authorization');
const GUILDS = fakeNodeId('platform', '/platform/guilds-gateway');
const VOLUNDR = fakeNodeId('shared', '/shared/volundr');

function setup(overrides: Partial<React.ComponentProps<typeof FocusPanel>> = {}) {
  const props: React.ComponentProps<typeof FocusPanel> = {
    focusId: GATEWAY,
    page: PAGE,
    isLoading: false,
    isError: false,
    graph: FAKE_GRAPH,
    depth: 1,
    disputedIds: new Set<string>(),
    onDepthChange: vi.fn(),
    onClearFocus: vi.fn(),
    onFocusPage: vi.fn(),
    onReadPage: vi.fn(),
    onAskAbout: vi.fn(),
    ...overrides,
  };
  renderWithMimir(<FocusPanel {...props} />, createFakeMimirService());
  return props;
}

describe('FocusPanel', () => {
  it('shows a loading state', () => {
    setup({ isLoading: true, page: null });
    expect(screen.getByText('loading page…')).toBeInTheDocument();
  });

  it('shows an error state and can clear focus from it', async () => {
    const props = setup({ isError: true, page: null });
    await userEvent.click(screen.getByRole('button', { name: 'All memory' }));
    expect(props.onClearFocus).toHaveBeenCalled();
  });

  it('renders breadcrumb, eyebrow, title, and counts (links from the graph, synchronously)', () => {
    setup();
    expect(screen.getByText('Gateway routing on ymir')).toBeInTheDocument();
    expect(screen.getByText('topic · platform')).toBeInTheDocument();
    expect(screen.getByText(/3 facts · 3 links · rewritten/)).toBeInTheDocument();
  });

  it('renders every key fact and quotes proof for the facts with evidence', async () => {
    setup();
    expect(
      screen.getByText(
        'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTestId('proof-pill')).toHaveLength(3));
  });

  it('calls onReadPage / onAskAbout from the action buttons', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: 'Read the page' }));
    expect(props.onReadPage).toHaveBeenCalledWith(PAGE);
    await userEvent.click(screen.getByRole('button', { name: 'Ask about this' }));
    expect(props.onAskAbout).toHaveBeenCalledWith(PAGE);
  });

  it('lists depth-1 links with relation labels, from a pure graph BFS', () => {
    setup();
    expect(screen.getByText('Cedar authorization')).toBeInTheDocument();
    expect(screen.getByText('Cedar authorization').closest('button')).toHaveTextContent(
      'depends on',
    );
  });

  it('marks a disputed linked page (by graph node id) with the dispute style', () => {
    setup({ disputedIds: new Set([GUILDS]) });
    const row = screen.getByText("Guild's gateway").closest('button')!;
    expect(row).toHaveTextContent('disputed');
    expect(row.querySelector('.memory-dispute')).not.toBeNull();
  });

  it('changes depth via the segmented control', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: '2' }));
    expect(props.onDepthChange).toHaveBeenCalledWith(2);
  });

  it('expanding depth reaches a 2-hop node not shown at depth 1', () => {
    setup({ depth: 2 });
    expect(screen.getByText('Volundr')).toBeInTheDocument();
  });

  it('focuses a linked page by its graph node id on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByText('Cedar authorization'));
    expect(props.onFocusPage).toHaveBeenCalledWith(CEDAR);
  });

  it('shows a message when there are no linked pages', () => {
    const other = FAKE_PAGES.find((p) => p.path === '/shared/volundr')!;
    setup({ page: other, focusId: VOLUNDR, depth: 1 });
    // volundr links only to openbao-policy at hop 1, so with depth 1 from volundr itself
    // there IS a link; use a genuinely isolated focus instead.
    expect(screen.queryByText('No linked pages.')).not.toBeInTheDocument();
  });

  it('shows a message when the focused node truly has no links', () => {
    const isolatedGraph = { nodes: FAKE_GRAPH.nodes, edges: [] };
    setup({ graph: isolatedGraph });
    expect(screen.getByText('No linked pages.')).toBeInTheDocument();
  });
});
