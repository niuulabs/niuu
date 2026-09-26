import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import { createFakeMimirService, fakeNodeId } from '../../testing/fakeMimirService';
import { MemoryExploreView } from './MemoryExploreView';
import type { MemorySceneProps } from '../scene/types';

const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
const CEDAR = fakeNodeId('platform', '/platform/cedar-authorization');
const GUILDS = fakeNodeId('platform', '/platform/guilds-gateway');

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

function renderView(search: Record<string, unknown> = {}, service = createFakeMimirService()) {
  currentSearch = search;
  return renderWithMimir(<MemoryExploreView />, service);
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

  it('asking a question sets the q param instead of navigating away to /mimir/ask', async () => {
    renderView();
    await waitFor(() => screen.getByPlaceholderText('Ask what Niuu knows…'));
    await userEvent.type(
      screen.getByPlaceholderText('Ask what Niuu knows…'),
      'why does it break?{Enter}',
    );
    expect(navigateMock).toHaveBeenCalled();
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.to).toBe('/mimir');
    expect(call.search({})).toEqual({ q: 'why does it break?' });
  });

  it('clicking a suggestion chip sets q to that question', async () => {
    renderView();
    await waitFor(() => screen.getByRole('button', { name: 'what changed this week?' }));
    await userEvent.click(screen.getByRole('button', { name: 'what changed this week?' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({})).toEqual({ q: 'what changed this week?' });
  });

  it('shows the Focus inspector for a graph node id, resolving it to the right (path, mount) — not treating the id as a path', async () => {
    renderView({ focus: GATEWAY });
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Page inspector' })).toBeInTheDocument(),
    );
    // Only resolves if the node id was correctly mapped to its (path, mount) before calling getPage.
    expect(await screen.findByText('Gateway routing on ymir')).toBeInTheDocument();
    expect(lastSceneProps?.focus).toEqual({ nodeId: GATEWAY, depth: 1 });
    expect(screen.queryByRole('region', { name: 'What Niuu knows' })).not.toBeInTheDocument();
  });

  it('marks a lint L02 (contradiction) issue as disputed by its graph node id, from the graph, not the page path', async () => {
    renderView();
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    expect(lastSceneProps?.disputedIds.has(GUILDS)).toBe(true);
    expect(lastSceneProps?.disputedIds.has('/platform/guilds-gateway')).toBe(false);
    expect(lastSceneProps?.disputedIds.size).toBe(1);
  });

  it('shows the disputed linked page in the Focus inspector, proving disputedIds reaches it correctly', async () => {
    renderView({ focus: GATEWAY });
    await waitFor(() => screen.getByText("Guild's gateway"));
    const row = screen.getByText("Guild's gateway").closest('button')!;
    expect(row).toHaveTextContent('disputed');
  });

  it('focusing a page from the scene navigates via URL (focus param, node id)', async () => {
    renderView();
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onSelectNode(CEDAR, { shift: false });
    expect(navigateMock).toHaveBeenCalled();
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({})).toEqual({ focus: CEDAR });
  });

  it('shift-clicking a second node while focused traces a path instead of navigating', async () => {
    renderView({ focus: GATEWAY });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    navigateMock.mockClear();
    lastSceneProps!.onSelectNode(fakeNodeId('shared', '/shared/volundr'), { shift: true });
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('background click clears the traced path and focus', async () => {
    renderView({ focus: GATEWAY });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onBackgroundClick();
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ focus: GATEWAY })).toEqual({});
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
    renderView({ focus: GATEWAY });
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Page inspector' })).toBeInTheDocument(),
    );
    fireEvent.keyDown(window, { key: 'Escape' });
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ focus: GATEWAY })).toEqual({});
  });

  it('Escape clears the question when nothing is focused or traced', async () => {
    renderView({ q: 'gateway' });
    await waitFor(() => screen.getByRole('region', { name: 'How it answered' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ q: 'gateway' })).toEqual({});
  });

  it("flying to a mount issues a camera fly-to command with that mount's node ids, and does not change the mount filter", async () => {
    renderView();
    await waitFor(() => screen.getByRole('button', { name: /platform.*3/ }));
    await userEvent.click(screen.getByRole('button', { name: /platform.*3/ }));
    expect(navigateMock).not.toHaveBeenCalled();
    expect(lastSceneProps?.camera).toMatchObject({
      kind: 'fly-to',
      nodeIds: expect.arrayContaining([GATEWAY, CEDAR, GUILDS]),
    });
  });

  it('shows a "Scoped to <mount>" chip when mount is set, which clears it', async () => {
    renderView({ mount: 'platform' });
    await waitFor(() => screen.getByText('Scoped to platform'));
    await userEvent.click(screen.getByRole('button', { name: 'Show all memory' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ mount: 'platform' })).toEqual({});
  });

  it('answers a question inline in the scene, with a bottom answer card of numbered verbatim facts', async () => {
    renderView({ q: 'gateway' });
    await waitFor(() => screen.getByRole('region', { name: 'How it answered' }));
    expect(screen.getByTestId('memory-ask-answer-card')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.getByText(
          'The route tables live in values-cedar.yaml, which wins over values-niuu.yaml.',
        ),
      ).toBeInTheDocument(),
    );
    await waitFor(() => expect(lastSceneProps?.answers.length).toBeGreaterThan(0));
    expect(lastSceneProps?.answers[0]).toEqual({ nodeId: GATEWAY, n: 1 });
  });

  it('shows "Memory has nothing on …" and an unanswered scene question when a question gets no results', async () => {
    renderView({ q: 'zzz-nothing-matches-zzz' });
    await waitFor(() =>
      expect(
        screen.getByText('Memory has nothing on "zzz-nothing-matches-zzz".'),
      ).toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(
        lastSceneProps?.questions.some(
          (q) => q.label === 'zzz-nothing-matches-zzz' && q.nearNodeId === null,
        ),
      ).toBe(true),
    );
  });

  it('"Follow up" clears the question and refocuses the ask bar', async () => {
    renderView({ q: 'gateway' });
    await waitFor(() => screen.getByRole('button', { name: 'Follow up' }));
    await userEvent.click(screen.getByRole('button', { name: 'Follow up' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.search({ q: 'gateway' })).toEqual({});
  });

  it('"Ask about this" from the Focus inspector sets q to the page title, not a navigation away', async () => {
    renderView({ focus: GATEWAY });
    await waitFor(() => screen.getByRole('button', { name: 'Ask about this' }));
    await userEvent.click(screen.getByRole('button', { name: 'Ask about this' }));
    const call = navigateMock.mock.calls.at(-1)![0];
    expect(call.to).toBe('/mimir');
    expect(call.search({ focus: GATEWAY })).toEqual({
      focus: GATEWAY,
      q: 'Gateway routing on ymir',
    });
  });

  it('shows a real, working Add a source form in the empty-memory state, not just text', async () => {
    currentSearch = {};
    renderWithMimir(
      <MemoryExploreView />,
      createFakeMimirService({ graph: { nodes: [], edges: [] } }),
    );
    await waitFor(() => expect(screen.getByText('Nothing in memory yet')).toBeInTheDocument());
    expect(screen.getByLabelText('Source URL')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add' })).toBeInTheDocument();
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
});
