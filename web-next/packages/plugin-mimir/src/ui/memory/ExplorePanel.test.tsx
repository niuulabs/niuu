import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import {
  createFakeMimirService,
  FAKE_GRAPH,
  FAKE_LIVE_ACTIVITY,
  fakeNodeId,
} from '../../testing/fakeMimirService';

const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const CEDAR = fakeNodeId('platform', '/platform/cedar-authorization');
import { ExplorePanel } from './ExplorePanel';

function setup(overrides: Partial<React.ComponentProps<typeof ExplorePanel>> = {}) {
  const props: React.ComponentProps<typeof ExplorePanel> = {
    graph: FAKE_GRAPH,
    liveActivity: FAKE_LIVE_ACTIVITY,
    liveActivityIsError: false,
    onFocus: vi.fn(),
    onFlyToMount: vi.fn(),
    ...overrides,
  };
  renderWithMimir(<ExplorePanel {...props} />, createFakeMimirService());
  return props;
}

describe('ExplorePanel', () => {
  it('renders the stats line', () => {
    setup();
    expect(screen.getByText(/5 pages · 4 links · 2 instances/)).toBeInTheDocument();
  });

  it('uses the singular for one page, link and instance', () => {
    const [first, second] = FAKE_GRAPH.nodes;
    setup({
      graph: {
        nodes: [first!, { ...second!, mount: first!.mount }],
        edges: [{ source: first!.id, target: second!.id, type: 'depends_on' }],
      },
    });
    expect(screen.getByText(/2 pages · 1 link · 1 instance$/)).toBeInTheDocument();
  });

  it('counts fly-to pages per mount from the graph itself', () => {
    setup();
    const counts = new Map<string, number>();
    for (const node of FAKE_GRAPH.nodes) counts.set(node.mount, (counts.get(node.mount) ?? 0) + 1);
    for (const [mount, pages] of counts) {
      const row = screen.getByRole('button', { name: new RegExp(`^${mount}\\s*${pages}$`) });
      expect(row).toBeInTheDocument();
    }
  });

  it('filters pages by title as the user types and focuses on Enter', async () => {
    const props = setup();
    await userEvent.type(screen.getByLabelText('Find a page'), 'gateway');
    expect(screen.getByRole('option', { name: 'Gateway routing on ymir' })).toBeInTheDocument();
    await userEvent.keyboard('{Enter}');
    expect(props.onFocus).toHaveBeenCalledWith(GATEWAY);
  });

  it('focuses a page from the find list on click', async () => {
    const props = setup();
    await userEvent.type(screen.getByLabelText('Find a page'), 'cedar');
    await userEvent.click(screen.getByRole('option', { name: 'Cedar authorization' }));
    expect(props.onFocus).toHaveBeenCalledWith(CEDAR);
  });

  it('renders fly-to mounts with page counts and flies to one on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: /platform.*3/ }));
    expect(props.onFlyToMount).toHaveBeenCalledWith('platform');
  });

  it('renders most-connected pages and focuses on click', async () => {
    const props = setup();
    await userEvent.click(screen.getByRole('button', { name: /Gateway routing on ymir.*3/ }));
    expect(props.onFocus).toHaveBeenCalledWith(GATEWAY);
  });

  it('renders the right-now feed with resolved page titles', () => {
    setup();
    expect(screen.getByText('muninn is reading Gateway routing on ymir')).toBeInTheDocument();
    expect(screen.getByText('Someone wrote to OpenBao policy')).toBeInTheDocument();
  });

  it('shows an empty message when there is no live activity', () => {
    setup({ liveActivity: [] });
    expect(screen.getByText('Nothing happening right now.')).toBeInTheDocument();
  });

  it('shows a loading state while live activity is not yet loaded', () => {
    setup({ liveActivity: undefined });
    expect(screen.getByText('loading activity…')).toBeInTheDocument();
  });

  it('shows an honest error indicator when the live feed fails', () => {
    setup({ liveActivityIsError: true, liveActivity: undefined });
    expect(screen.getByRole('alert')).toHaveTextContent('Live activity is unavailable right now.');
  });

  it('ingests a URL through the Add a source form', async () => {
    setup();
    await userEvent.type(screen.getByLabelText('Source URL'), 'https://example.com/doc');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(screen.getByText('Added "https://example.com/doc".')).toBeInTheDocument(),
    );
  });

  it('shows an honest error when ingest fails', async () => {
    renderWithMimir(
      <ExplorePanel
        graph={FAKE_GRAPH}
        liveActivity={FAKE_LIVE_ACTIVITY}
        liveActivityIsError={false}
        onFocus={vi.fn()}
        onFlyToMount={vi.fn()}
      />,
      createFakeMimirService({ ingestShouldFail: true }),
    );
    await userEvent.type(screen.getByLabelText('Source URL'), 'https://example.com/doc');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(screen.getByText('ingest failed: fetch refused')).toBeInTheDocument(),
    );
  });
});
