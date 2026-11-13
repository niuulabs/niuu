import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithMimir } from '../../testing/renderWithMimir';
import {
  createFakeMimirService,
  fakeNodeId,
  FAKE_GRAPH,
  KG,
  BOOKING_LAYER,
} from '../../testing/fakeMimirService';
import { MemoryExploreView } from './MemoryExploreView';
import type { MemorySceneProps } from '../scene/types';
import type { IMimirService } from '../../ports';

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

/** The search params the last navigation produced from `prev`. */
function lastSearch(prev: Record<string, unknown> = currentSearch): Record<string, unknown> {
  const call = navigateMock.mock.calls.at(-1)![0];
  expect(call.to).toBe('/mimir');
  return call.search(prev);
}

async function searchBox() {
  return screen.findByRole('combobox', { name: 'Search the knowledge graph' });
}

describe('MemoryExploreView — search-first', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    lastSceneProps = null;
  });

  it('renders the scene in the heads-up display with the knowledge graph and its layer names', async () => {
    renderView();
    await waitFor(() => expect(screen.getByTestId('memory-scene')).toBeInTheDocument());
    expect(lastSceneProps?.colourBy).toBe('hud');
    expect(lastSceneProps?.graph.nodes.some((n) => n.id === KG.cutover)).toBe(true);
    expect(lastSceneProps?.layerNames.get(BOOKING_LAYER)).toBe('Booking platform');
    expect(lastSceneProps?.kinds).toBeNull();
    expect(screen.getByText('drag to orbit · click to fly · type to search')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
      '10 items across 2 layers · type a name to fly there',
    );
    expect(screen.getByText('1 unresolved link')).toBeInTheDocument();
  });

  it('typing a name lists the matches with kind and layer, and Enter flies to the best one', async () => {
    renderView();
    const box = await searchBox();
    await userEvent.type(box, 'booking cut');
    const list = screen.getByRole('listbox');
    const options = within(list).getAllByRole('option');
    expect(options[0]).toHaveTextContent('Booking cut-over');
    expect(options[0]).toHaveTextContent('DECISION · BOOKING PLATFORM');
    await userEvent.keyboard('{Enter}');
    expect(lastSearch({})).toEqual({ focus: KG.cutover });
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });

  it('arrow keys pick another match, and clicking one flies there too', async () => {
    renderView();
    const box = await searchBox();
    await userEvent.type(box, 'payments');
    const options = within(screen.getByRole('listbox')).getAllByRole('option');
    expect(options.map((o) => o.textContent)).toEqual([
      expect.stringContaining('Payments freeze'),
      expect.stringContaining('Payments ledger'),
    ]);
    await userEvent.keyboard('{ArrowDown}{ArrowUp}{ArrowDown}{Enter}');
    expect(lastSearch({})).toEqual({ focus: KG.ledger });

    await userEvent.clear(box);
    await userEvent.type(box, 'ines');
    fireEvent.mouseDown(screen.getByRole('option', { name: /Ines Moreau/ }));
    expect(lastSearch({})).toEqual({ focus: KG.ines });
  });

  it('Escape closes the match list without stepping back out of the focus', async () => {
    renderView({ focus: KG.cutover });
    const box = await searchBox();
    await userEvent.type(box, 'ines');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    navigateMock.mockClear();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('a name that matches nothing is asked as a question instead', async () => {
    renderView();
    const box = await searchBox();
    await userEvent.type(box, 'why did the cut-over move?{Enter}');
    expect(lastSearch({})).toEqual({ q: 'why did the cut-over move?' });
  });

  it('searching for the node already in focus re-flies the camera to it, centred', async () => {
    renderView({ focus: KG.cutover });
    const box = await searchBox();
    await userEvent.type(box, 'Booking cut-over{Enter}');
    expect(navigateMock).not.toHaveBeenCalled();
    expect(lastSceneProps?.camera).toMatchObject({ kind: 'fly-to', centre: KG.cutover });
    const camera = lastSceneProps!.camera as { nodeIds: string[] };
    expect(camera.nodeIds).toEqual(expect.arrayContaining([KG.cutover, KG.ines, KG.lin501]));
  });
});

describe('MemoryExploreView — focused item', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    lastSceneProps = null;
  });

  it('shows the heads-up card: kind and layer caption, title, summary, this week, actions', async () => {
    renderView({ focus: KG.cutover });
    const card = await screen.findByRole('complementary', { name: 'Focused' });
    expect(within(card).getByText('decision · Booking platform')).toHaveClass('niuu-hud-caption');
    expect(within(card).getByRole('heading', { name: 'Booking cut-over' })).toBeInTheDocument();
    expect(
      within(card).getByText('Bookings move from the legacy API to booking-api v2.'),
    ).toBeInTheDocument();
    const thisWeek = within(card).getByText(/^This week: LIN-501 moved it/);
    expect(thisWeek).toHaveClass('niuu-hud-recent');
    expect(within(card).getByRole('button', { name: 'Open the page' })).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: 'Trace a path to…' })).toBeInTheDocument();
    expect(lastSceneProps?.focus).toEqual({ nodeId: KG.cutover, depth: 1 });
  });

  it('says where it flew and how much is around it', async () => {
    renderView({ focus: KG.cutover });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'Flew to Booking cut-over · 7 connections · 1 more nearby',
      ),
    );
  });

  it('a page with no summary and no change says so plainly, with no "this week" line', async () => {
    renderView({ focus: KG.ola });
    const card = await screen.findByRole('complementary', { name: 'Focused' });
    expect(within(card).getByText('No summary written yet.')).toBeInTheDocument();
    expect(within(card).queryByText(/This week/)).not.toBeInTheDocument();
    expect(within(card).getByText('person · Org')).toBeInTheDocument();
  });

  it('Open the page goes to the reader for that page in its layer', async () => {
    renderView({ focus: KG.cutover });
    await userEvent.click(await screen.findByRole('button', { name: 'Open the page' }));
    expect(navigateMock).toHaveBeenCalledWith({
      to: '/mimir/read',
      search: { path: 'projects/booking-cutover', mount: BOOKING_LAYER },
    });
  });

  it('Trace a path to… turns the search box into a trace, and picking an end lights the path', async () => {
    renderView({ focus: KG.ines });
    // The name just flown to does not carry over into the trace box.
    await userEvent.type(await searchBox(), 'Ines');
    await userEvent.click(await screen.findByRole('button', { name: 'Trace a path to…' }));
    const trace = screen.getByRole('combobox', { name: 'Trace a path to' });
    expect(trace).toHaveFocus();
    expect(trace).toHaveValue('');
    expect(trace).toHaveAttribute('placeholder', 'Trace a path from Ines Moreau to…');
    expect(screen.getByRole('status')).toHaveTextContent(
      'Pick where a path from Ines Moreau should go',
    );
    await userEvent.type(trace, 'ledger{Enter}');
    await waitFor(() =>
      expect(lastSceneProps?.path).toEqual([KG.ines, KG.cutover, KG.freeze, KG.ledger]),
    );
    expect(screen.getByRole('status')).toHaveTextContent(
      'Path · Ines Moreau → Booking cut-over → Payments freeze → Payments ledger · 3 hops',
    );
    // The focus never moved.
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('a trace to something unconnected says there is no path', async () => {
    renderView({ focus: KG.ines });
    await userEvent.click(await screen.findByRole('button', { name: 'Trace a path to…' }));
    await userEvent.type(screen.getByRole('combobox', { name: 'Trace a path to' }), 'ola{Enter}');
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'No path from Ines Moreau to Ola Nordmann',
      ),
    );
    expect(lastSceneProps?.path).toBeNull();
  });

  it('Escape in the trace box cancels the trace, and pressing the card button again does too', async () => {
    renderView({ focus: KG.ines });
    const button = await screen.findByRole('button', { name: 'Trace a path to…' });
    await userEvent.click(button);
    expect(button).toHaveAttribute('aria-pressed', 'true');
    await userEvent.keyboard('{Escape}');
    expect(
      screen.getByRole('combobox', { name: 'Search the knowledge graph' }),
    ).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
    await userEvent.click(button);
    await userEvent.click(button);
    expect(button).toHaveAttribute('aria-pressed', 'false');
  });

  it('shift-click on another node in the scene traces the path from the focus', async () => {
    renderView({ focus: KG.ines });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onSelectNode(KG.lin501, { shift: true });
    await waitFor(() => expect(lastSceneProps?.path).toEqual([KG.ines, KG.cutover, KG.lin501]));
  });

  it('clicking a node flies to it; clicking empty space lets go', async () => {
    renderView({ focus: KG.ines });
    await waitFor(() => expect(lastSceneProps).not.toBeNull());
    lastSceneProps!.onSelectNode(KG.runbook, { shift: false });
    expect(lastSearch()).toEqual({ focus: KG.runbook });
    lastSceneProps!.onBackgroundClick();
    expect(lastSearch()).toEqual({});
  });

  it('Escape steps back out of the focus', async () => {
    renderView({ focus: KG.cutover });
    await screen.findByRole('complementary', { name: 'Focused' });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(lastSearch()).toEqual({});
  });

  it('a focus on something not in the readable memory says so', async () => {
    renderView({ focus: 'project:gone/pages/x' });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        'That item is not in the memory you can read',
      ),
    );
    expect(screen.queryByRole('complementary', { name: 'Focused' })).not.toBeInTheDocument();
  });
});

describe('MemoryExploreView — filters, replay and controls', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    lastSceneProps = null;
  });

  it('Everything / People / Decisions / Tickets set the kinds the scene keeps lit', async () => {
    renderView();
    const group = await screen.findByRole('group', { name: 'Show' });
    expect(within(group).getByRole('button', { name: 'Everything' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await userEvent.click(within(group).getByRole('button', { name: 'Tickets' }));
    expect(lastSearch({})).toEqual({ show: 'tickets' });
    await userEvent.click(within(group).getByRole('button', { name: 'Everything' }));
    expect(lastSearch({ show: 'tickets' })).toEqual({});
  });

  it('reads the filter from the URL into the scene', async () => {
    renderView({ show: 'people' });
    await waitFor(() => expect(lastSceneProps?.kinds).toEqual(new Set(['person'])));
    expect(screen.getByRole('button', { name: 'People' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('the scrubber reads AS OF today at rest and dragging it back replays an earlier day', async () => {
    renderView();
    const timeline = await screen.findByRole('region', { name: 'Replay timeline' });
    const today = new Date().toISOString().slice(0, 10);
    const todayLabel = new Date(`${today}T00:00:00Z`).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      timeZone: 'UTC',
    });
    expect(within(timeline).getByText(`As of ${todayLabel}`)).toBeInTheDocument();
    expect(within(timeline).getByText('Drag back to replay')).toBeInTheDocument();
    const range = within(timeline).getByLabelText('Show memory as of a date') as HTMLInputElement;
    expect(range.value).toBe(range.max);
    fireEvent.change(range, { target: { value: '0' } });
    const earliest = new Date(Date.now() - 90 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    expect(lastSearch({})).toEqual({ asOf: earliest });
    expect(lastSceneProps?.asOf).toBeNull();
  });

  it('a replayed day reaches the scene as the end of that day, with play and back-to-now', async () => {
    renderView({ asOf: '2026-11-01' });
    await waitFor(() => expect(lastSceneProps?.asOf).toBe('2026-11-01T23:59:59.999Z'));
    const timeline = screen.getByRole('region', { name: 'Replay timeline' });
    expect(within(timeline).getByText('As of 1 Nov')).toBeInTheDocument();
    await userEvent.click(within(timeline).getByRole('button', { name: 'Back to now' }));
    expect(lastSearch()).toEqual({});
  });

  it('shows the focused item’s dated history as marks on the scrubber', async () => {
    renderView({ focus: KG.cutover });
    const marks = await screen.findByRole('list', { name: 'What happened to the focused item' });
    const labels = within(marks)
      .getAllByRole('button')
      .map((b) => b.textContent);
    expect(labels.some((l) => l?.endsWith('· first seen, planned in'))).toBe(true);
    expect(labels.some((l) => l?.endsWith('· owns'))).toBe(true);
    const today = within(marks).getByRole('button', { name: /· moved$/ });
    expect(today).toHaveClass('niuu-hud-mark--recent');
    // Today's mark is "now", not a replay.
    await userEvent.click(today);
    expect(lastSearch({ focus: KG.cutover })).toEqual({ focus: KG.cutover });
  });

  it('plays a replay forward day by day and stops at now', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    renderView({ asOf: yesterday });
    const play = await screen.findByRole('button', { name: 'Play' });
    fireEvent.click(play);
    await vi.advanceTimersByTimeAsync(500);
    expect(lastSearch()).toEqual({});
    vi.useRealTimers();
  });

  it('Fit, 3D and 2D drive the camera and the view', async () => {
    renderView();
    const toolbar = await screen.findByRole('toolbar', { name: 'Scene controls' });
    await userEvent.click(within(toolbar).getByRole('button', { name: 'Fit' }));
    expect(lastSceneProps?.camera).toMatchObject({ kind: 'fit' });
    await userEvent.click(within(toolbar).getByRole('button', { name: '2D' }));
    expect(lastSearch({})).toEqual({ view: '2d' });
    expect(within(toolbar).getByRole('button', { name: '3D' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

describe('MemoryExploreView — ask, empty and failing memory', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    lastSceneProps = null;
  });

  it('answers a question inline in the scene, with the bottom answer card', async () => {
    const GATEWAY = fakeNodeId('platform', '/platform/gateway-routing');
    const service = createFakeMimirService({
      knowledgeGraph: {
        ...FAKE_GRAPH,
        layers: [
          { id: 'platform', name: 'Platform' },
          { id: 'shared', name: 'Shared' },
        ],
        unresolved: [],
      },
    });
    renderView({ q: 'gateway' }, service);
    await waitFor(() => expect(screen.getByTestId('memory-ask-answer-card')).toBeInTheDocument());
    await waitFor(() => expect(lastSceneProps?.answers.length).toBeGreaterThan(0));
    expect(lastSceneProps?.answers[0]).toEqual({ nodeId: GATEWAY, n: 1 });
  });

  it('shows a real, working Add a source form in the empty-memory state', async () => {
    renderView(
      {},
      createFakeMimirService({
        knowledgeGraph: { nodes: [], edges: [], layers: [], unresolved: [] },
      }),
    );
    await waitFor(() => expect(screen.getByText('Nothing in memory yet')).toBeInTheDocument());
    expect(screen.getByLabelText('Source URL')).toBeInTheDocument();
    expect(screen.queryByText(/unresolved/)).not.toBeInTheDocument();
  });

  it('surfaces the gateway’s error when a layer cannot be read', async () => {
    const base = createFakeMimirService();
    const failing: IMimirService = {
      ...base,
      pages: {
        ...base.pages,
        getKnowledgeGraph: async () => {
          throw new Error('layer project:booking could not be read');
        },
      },
    };
    renderView({}, failing);
    await waitFor(() =>
      expect(screen.getByText('layer project:booking could not be read')).toBeInTheDocument(),
    );
  });
});
