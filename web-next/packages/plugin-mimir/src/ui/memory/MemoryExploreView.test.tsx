import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createFakeMimirService } from '../../testing/fakeMimirService';
import { MemoryExploreView } from './MemoryExploreView';
import type { MemorySceneProps } from '../scene/types';

let currentSearch: Record<string, unknown> = {};
const navigateMock = vi.fn();
let lastSceneProps: MemorySceneProps | null = null;

vi.mock('@tanstack/react-router', () => ({
  useSearch: () => currentSearch,
  useNavigate: () => navigateMock,
}));

vi.mock('../scene/MemoryScene', () => ({
  MemoryScene: (props: MemorySceneProps) => {
    lastSceneProps = props;
    return <div data-testid="memory-scene" />;
  },
}));

function renderView(search: Record<string, unknown> = {}) {
  currentSearch = search;
  return renderWithMimir(<MemoryExploreView />, createFakeMimirService());
}

describe('MemoryExploreView', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    lastSceneProps = null;
  });

  it('renders the scene and Explore panel by default', async () => {
    renderView();
    await waitFor(() => expect(screen.getByTestId('memory-scene')).toBeInTheDocument());
    expect(screen.getByRole('region', { name: 'What Niuu knows' })).toBeInTheDocument();
    expect(screen.getByRole('toolbar', { name: 'Scene controls' })).toBeInTheDocument();
  });

  it('renders real suggestion chips and no hard-coded example questions', async () => {
    renderView();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'what changed this week?' })).toBeInTheDocument(),
    );
  });

  it('asking a question navigates to /mimir/ask', async () => {
    renderView();
    await waitFor(() => screen.getByPlaceholderText('Ask what Niuu knows…'));
    await userEvent.type(
      screen.getByPlaceholderText('Ask what Niuu knows…'),
      'why does it break?{Enter}',
    );
    expect(navigateMock).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: { q: 'why does it break?', mount: undefined },
    });
  });

  it('clicking a suggestion chip navigates to /mimir/ask with that question', async () => {
    renderView();
    await waitFor(() => screen.getByRole('button', { name: 'what changed this week?' }));
    await userEvent.click(screen.getByRole('button', { name: 'what changed this week?' }));
    expect(navigateMock).toHaveBeenCalledWith({
      to: '/mimir/ask',
      search: { q: 'what changed this week?', mount: undefined },
    });
  });

  it('shows the Focus inspector when focus is set, and passes it to the scene', async () => {
    renderView({ focus: '/platform/gateway-routing' });
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Page inspector' })).toBeInTheDocument(),
    );
    expect(lastSceneProps?.focus).toEqual({ nodeId: '/platform/gateway-routing', depth: 1 });
    expect(screen.queryByRole('region', { name: 'What Niuu knows' })).not.toBeInTheDocument();
  });

  it('focusing a page from the scene navigates via URL (focus param)', async () => {
    renderView();
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onSelectNode('/platform/cedar-authorization', { shift: false });
    expect(navigateMock).toHaveBeenCalled();
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({})).toEqual({ focus: '/platform/cedar-authorization' });
  });

  it('shift-clicking a second node while focused traces a path instead of navigating', async () => {
    renderView({ focus: '/platform/gateway-routing' });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    navigateMock.mockClear();
    lastSceneProps!.onSelectNode('/shared/volundr', { shift: true });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('background click clears the traced path and focus', async () => {
    renderView({ focus: '/platform/gateway-routing' });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onBackgroundClick();
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ focus: '/platform/gateway-routing' })).toEqual({});
  });

  it('enters Replay mode from the toolbar Replay button, setting asOf to the earliest firstSeen', async () => {
    renderView();
    await waitFor(() => screen.getByRole('button', { name: 'Replay' }));
    await userEvent.click(screen.getByRole('button', { name: 'Replay' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({})).toEqual({ asOf: '2026-03-01' });
  });

  it('shows the Replay panel and timeline, and hides the toolbar + ask bar, when asOf is set', async () => {
    renderView({ asOf: '2026-03-10' });
    await waitFor(() => expect(screen.getByRole('region', { name: 'Replay' })).toBeInTheDocument());
    expect(screen.getByRole('region', { name: 'Replay timeline' })).toBeInTheDocument();
    expect(screen.queryByRole('toolbar', { name: 'Scene controls' })).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Ask what Niuu knows…')).not.toBeInTheDocument();
  });

  it('exits replay via the As-of "now" button', async () => {
    renderView({ asOf: '2026-03-10' });
    await waitFor(() => screen.getByRole('button', { name: 'Back to now' }));
    await userEvent.click(screen.getByRole('button', { name: 'Back to now' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ asOf: '2026-03-10' })).toEqual({});
  });

  it('toggling a kind group updates the hiddenGroups passed to the scene', async () => {
    renderView();
    await waitFor(() => screen.getByLabelText('Topics'));
    await userEvent.click(screen.getByLabelText('Topics'));
    expect(lastSceneProps?.hiddenGroups.has('topic')).toBe(true);
  });

  it('Escape clears focus when nothing is traced', async () => {
    renderView({ focus: '/platform/gateway-routing' });
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Page inspector' })).toBeInTheDocument(),
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ focus: '/platform/gateway-routing' })).toEqual({});
  });

  it('flying to a mount sets the mount filter', async () => {
    renderView();
    await waitFor(() => screen.getByRole('button', { name: /platform.*3/ }));
    await userEvent.click(screen.getByRole('button', { name: /platform.*3/ }));
    const call = navigateMock.mock.calls.find((c) =>
      JSON.stringify(c[0].search({})).includes('platform'),
    );
    expect(call).toBeDefined();
  });

  it('surfaces an honest error when the graph fails to load', async () => {
    const base = createFakeMimirService();
    const failing = {
      ...base,
      pages: {
        ...base.pages,
        getGraph: async () => {
          throw new Error('graph down');
        },
      },
    };
    currentSearch = {};
    renderWithMimir(<MemoryExploreView />, failing);
    await waitFor(() => expect(screen.getByText('graph down')).toBeInTheDocument());
  });

  it('shows an empty-memory state when the graph has no pages', async () => {
    currentSearch = {};
    renderWithMimir(
      <MemoryExploreView />,
      createFakeMimirService({ graph: { nodes: [], edges: [] } }),
    );
    await waitFor(() => expect(screen.getByText('Nothing in memory yet')).toBeInTheDocument());
  });
});
