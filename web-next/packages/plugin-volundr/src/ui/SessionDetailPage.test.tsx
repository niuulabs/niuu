import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { SessionDetailPage } from './SessionDetailPage';
import {
  createMockFileSystemPort,
  createMockSessionStore,
  createMockMetricsStream,
} from '../adapters/mock';
import * as mockChatData from '../testing/mockChatData';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type { ISessionStore } from '../ports/ISessionStore';
import type { IMetricsStream } from '../ports/IMetricsStream';
import type { Session } from '../domain/session';

// ---------------------------------------------------------------------------
// Mock xterm (no Canvas in jsdom)
// ---------------------------------------------------------------------------

vi.mock('@xterm/xterm', () => ({
  Terminal: class MockXtermTerminal {
    open = vi.fn();
    write = vi.fn();
    dispose = vi.fn();
    loadAddon = vi.fn();
    onData = vi.fn().mockReturnValue({ dispose: vi.fn() });
    options = {};
  },
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit = vi.fn();
    dispose = vi.fn();
  },
}));

vi.mock('shiki', () => ({
  codeToHtml: vi.fn().mockResolvedValue('<pre><code>highlighted</code></pre>'),
}));

class ResizeObserverStub {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
}
vi.stubGlobal('ResizeObserver', ResizeObserverStub);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildPtyStream(overrides?: Partial<IPtyStream>): IPtyStream {
  return {
    subscribe: vi.fn().mockReturnValue(() => {}),
    send: vi.fn(),
    ...overrides,
  };
}

function buildFilesystem(overrides?: Partial<IFileSystemPort>): IFileSystemPort {
  const base = createMockFileSystemPort();
  return { ...base, ...overrides };
}

function wrap(
  ui: React.ReactNode,
  {
    ptyStream = buildPtyStream(),
    filesystem = buildFilesystem(),
    sessionStore = createMockSessionStore(),
    metricsStream = createMockMetricsStream(),
  }: {
    ptyStream?: IPtyStream;
    filesystem?: IFileSystemPort;
    sessionStore?: ISessionStore;
    metricsStream?: IMetricsStream;
  } = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider services={{ ptyStream, filesystem, sessionStore, metricsStream }}>
        {ui}
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 'ds-1',
    ravnId: 'r1',
    personaName: 'skald',
    templateId: 'tpl-default',
    clusterId: 'cl-eitri',
    state: 'running',
    startedAt: '2026-05-31T10:00:00.000Z',
    readyAt: '2026-05-31T10:01:00.000Z',
    lastActivityAt: '2026-05-31T10:59:00.000Z',
    connectionType: 'cli',
    tokensIn: 4200,
    tokensOut: 1800,
    costCents: 8,
    preview: 'Refactoring auth middleware',
    resources: {
      cpuRequest: 1,
      cpuLimit: 2,
      cpuUsed: 0.4,
      memRequestMi: 512,
      memLimitMi: 1024,
      memUsedMi: 320,
      gpuCount: 0,
      diskUsedMi: 2048,
      diskLimitMi: 10240,
    },
    files: { added: 3, modified: 7, deleted: 1 },
    env: {},
    events: [
      { ts: '2026-05-31T10:00:00.000Z', kind: 'requested', body: 'session requested' },
      { ts: '2026-05-31T10:01:00.000Z', kind: 'ready', body: 'pod ready' },
    ],
    ...overrides,
  };
}

function buildSessionStoreWithSession(session: Session | null): ISessionStore {
  return {
    ...createMockSessionStore(),
    getSession: vi.fn().mockResolvedValue(session),
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SessionDetailPage', () => {
  // ─── Header ─────────────────────────────────────────────
  describe('header', () => {
    it('renders the session header with session info after loading', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('session-header')).toBeInTheDocument());
      expect(screen.getByTestId('session-name')).toHaveTextContent('skald');
      expect(screen.getByTestId('session-id-label')).toHaveTextContent('ds-1');
    });

    it('shows lifecycle badge in the header', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('session-header')).toBeInTheDocument());
      // LifecycleBadge renders the state text "running"
      expect(screen.getAllByText('running').length).toBeGreaterThan(0);
    });

    it('shows the archived badge in readOnly mode', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" readOnly />);
      await waitFor(() => expect(screen.getByTestId('session-archived-badge')).toBeInTheDocument());
    });

    it('does NOT show the archived badge in interactive mode', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('session-header')).toBeInTheDocument());
      expect(screen.queryByTestId('session-archived-badge')).not.toBeInTheDocument();
    });

    it('renders session stats in the header', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('session-stats')).toBeInTheDocument());
      const stats = screen.getAllByTestId('stat');
      expect(stats.length).toBeGreaterThanOrEqual(2);
    });

    it('renders source label and cluster chip', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('source-label')).toBeInTheDocument());
      expect(screen.getByTestId('cluster-chip')).toBeInTheDocument();
    });

    it('toggles resources row when clicking resources button', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('resources-toggle')).toBeInTheDocument());

      // Resources row should not be visible initially
      expect(screen.queryByTestId('resources-row')).not.toBeInTheDocument();

      // Click to show resources
      fireEvent.click(screen.getByTestId('resources-toggle'));
      expect(screen.getByTestId('resources-row')).toBeInTheDocument();

      // Click to hide resources
      fireEvent.click(screen.getByTestId('resources-toggle'));
      expect(screen.queryByTestId('resources-row')).not.toBeInTheDocument();
    });

    it('renders meters in the resources row', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('resources-toggle')).toBeInTheDocument());
      fireEvent.click(screen.getByTestId('resources-toggle'));
      const resourcesRow = screen.getByTestId('resources-row');
      const meters = within(resourcesRow).getAllByTestId('meter');
      expect(meters.length).toBeGreaterThanOrEqual(2);
    });

    it('renders disk meter in the resources row when disk data present', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('resources-toggle')).toBeInTheDocument());
      fireEvent.click(screen.getByTestId('resources-toggle'));
      const resourcesRow = screen.getByTestId('resources-row');
      // ds-1 has diskUsedMi and diskLimitMi set
      const meters = within(resourcesRow).getAllByTestId('meter');
      expect(meters.length).toBeGreaterThanOrEqual(3);
    });

    it('renders file change summary when session has file stats', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('file-change-summary')).toBeInTheDocument());
      expect(screen.getByTestId('files-added')).toBeInTheDocument();
      expect(screen.getByTestId('files-modified')).toBeInTheDocument();
      expect(screen.getByTestId('files-deleted')).toBeInTheDocument();
    });

    it('renders saga link and omits file summary when a session has no file stats', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(
          makeSession({
            sagaId: 'SAGA-42',
            files: undefined,
          }),
        ),
      });
      await waitFor(() => expect(screen.getByTestId('session-header')).toBeInTheDocument());
      expect(screen.getByTestId('session-issue-link')).toHaveTextContent('SAGA-42');
      expect(screen.queryByTestId('file-change-summary')).not.toBeInTheDocument();
    });

    it('renders gpu usage and omits disk meter when disk metrics are unavailable', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(
          makeSession({
            resources: {
              cpuRequest: 2,
              cpuLimit: 4,
              cpuUsed: 1,
              memRequestMi: 2048,
              memLimitMi: 4096,
              memUsedMi: 1024,
              gpuCount: 2,
            },
          }),
        ),
      });
      await waitFor(() => expect(screen.getByTestId('resources-toggle')).toBeInTheDocument());
      fireEvent.click(screen.getByTestId('resources-toggle'));
      expect(screen.getByText('gpu: 2')).toBeInTheDocument();
      const resourcesRow = screen.getByTestId('resources-row');
      const meters = within(resourcesRow).getAllByTestId('meter');
      expect(meters).toHaveLength(2);
    });
  });

  // ─── Tab bar ────────────────────────────────────────────
  describe('tab bar', () => {
    it('renders all six tab buttons', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      expect(screen.getByTestId('tab-chat')).toBeInTheDocument();
      expect(screen.getByTestId('tab-terminal')).toBeInTheDocument();
      expect(screen.getByTestId('tab-diffs')).toBeInTheDocument();
      expect(screen.getByTestId('tab-files')).toBeInTheDocument();
      expect(screen.getByTestId('tab-chronicle')).toBeInTheDocument();
      expect(screen.getByTestId('tab-logs')).toBeInTheDocument();
    });

    it('defaults to the chat tab', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      expect(screen.getByTestId('tab-chat')).toHaveAttribute('aria-selected', 'true');
    });

    it('respects initialTab prop', () => {
      wrap(<SessionDetailPage sessionId="ds-1" initialTab="terminal" />);
      expect(screen.getByTestId('tab-terminal')).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByTestId('tab-chat')).toHaveAttribute('aria-selected', 'false');
    });

    it('switches tabs on click', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);

      fireEvent.click(screen.getByTestId('tab-terminal'));
      expect(screen.getByTestId('tab-terminal')).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByTestId('tab-chat')).toHaveAttribute('aria-selected', 'false');

      fireEvent.click(screen.getByTestId('tab-diffs'));
      expect(screen.getByTestId('tab-diffs')).toHaveAttribute('aria-selected', 'true');

      fireEvent.click(screen.getByTestId('tab-chronicle'));
      expect(screen.getByTestId('tab-chronicle')).toHaveAttribute('aria-selected', 'true');

      fireEvent.click(screen.getByTestId('tab-logs'));
      expect(screen.getByTestId('tab-logs')).toHaveAttribute('aria-selected', 'true');
    });

    it('uses role="tablist" on the tab container', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      expect(screen.getByRole('tablist')).toBeInTheDocument();
    });
  });

  // ─── Chat tab ───────────────────────────────────────────
  describe('chat tab', () => {
    it('renders the 3-column chat layout', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-tab')).toBeInTheDocument());
      expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument();
      expect(screen.getByTestId('chat-stream')).toBeInTheDocument();
      expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument();
    });

    it('renders ravn peer cards in the MeshSidebar', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument());
      const sidebar = screen.getByTestId('mesh-sidebar');
      // MeshSidebar filters by participantType === 'ravn', so only ravn + reviewer (2)
      const peerCards = within(sidebar).getAllByTestId(/^peer-card-/);
      expect(peerCards.length).toBe(2);
    });

    it('peer cards show persona and status', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument());
      const sidebar = screen.getByTestId('mesh-sidebar');
      // skald appears as displayName (persona) in the ravn peer card
      expect(within(sidebar).getByText(/skald/)).toBeInTheDocument();
      expect(within(sidebar).getByText(/Reviewer/)).toBeInTheDocument();
    });

    it('clicking a peer card toggles focus filter', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument());

      const sidebar = screen.getByTestId('mesh-sidebar');
      const peerCards = within(sidebar).getAllByTestId(/^peer-card-/);

      // Click on the first ravn peer to select
      fireEvent.click(peerCards[0]!);
      expect(peerCards[0]).toHaveClass('niuu-chat-peer-card--selected');

      // Click again to deselect
      fireEvent.click(peerCards[0]!);
      expect(peerCards[0]).not.toHaveClass('niuu-chat-peer-card--selected');
    });

    it('renders chat messages in the ChatStream', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

      const userTurns = screen.getAllByTestId('chat-turn-user');
      expect(userTurns.length).toBeGreaterThanOrEqual(1);

      const assistantTurns = screen.getAllByTestId('chat-turn-assistant');
      expect(assistantTurns.length).toBeGreaterThanOrEqual(1);
    });

    it('renders tool run blocks', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

      const toolRuns = screen.getAllByTestId('tool-run');
      expect(toolRuns.length).toBeGreaterThanOrEqual(1);
    });

    it('renders thinking blocks', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

      const thinkingBlocks = screen.getAllByTestId('thinking-block');
      expect(thinkingBlocks.length).toBeGreaterThanOrEqual(1);
    });

    it('renders outcome cards inline', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

      const outcomeCards = screen.getAllByTestId('outcome-card');
      expect(outcomeCards.length).toBeGreaterThanOrEqual(1);
    });

    it('renders mesh cascade events', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument());

      const cascadeEvents = screen.getAllByTestId('cascade-event');
      expect(cascadeEvents.length).toBe(3); // outcome + delegation + notification
    });

    it('mesh cascade filter buttons work', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument());

      // Click outcomes filter
      fireEvent.click(screen.getByTestId('cascade-filter-outcome'));
      const filtered = screen.getAllByTestId('cascade-event');
      expect(filtered.length).toBe(1); // only the outcome event

      // Click all filter
      fireEvent.click(screen.getByTestId('cascade-filter-all'));
      const all = screen.getAllByTestId('cascade-event');
      expect(all.length).toBe(3);
    });

    it('mesh cascade shows delegation events when filtered', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument());

      fireEvent.click(screen.getByTestId('cascade-filter-mesh_message'));
      const delegations = screen.getAllByTestId('cascade-event');
      expect(delegations.length).toBe(1);
    });

    it('mesh cascade shows notification events when filtered', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument());

      fireEvent.click(screen.getByTestId('cascade-filter-notification'));
      const notifications = screen.getAllByTestId('cascade-event');
      expect(notifications.length).toBe(1);
    });

    it('collapses and expands both chat side rails', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument());

      fireEvent.click(screen.getByText('‹'));
      const collapsedSidebar = screen.getByTestId('mesh-sidebar');
      expect(collapsedSidebar).toBeInTheDocument();
      expect(within(collapsedSidebar).queryByTestId(/^peer-card-/)).not.toBeInTheDocument();

      fireEvent.click(within(collapsedSidebar).getByText('›'));
      expect(await screen.findByTestId('peer-card-ravn-r1')).toBeInTheDocument();

      const cascade = screen.getByTestId('mesh-cascade');
      fireEvent.click(within(cascade).getByText('›'));
      expect(within(screen.getByTestId('mesh-cascade')).queryByTestId('cascade-filter-all')).not.toBeInTheDocument();

      fireEvent.click(within(screen.getByTestId('mesh-cascade')).getByText('‹'));
      expect(await screen.findByTestId('cascade-filter-all')).toBeInTheDocument();
    });

    it('expands thinking and tool-run blocks to show details', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

      fireEvent.click(screen.getAllByTestId('thinking-block')[0]!.querySelector('button')!);
      expect(screen.getByText(/I need to create a JWT validation handler/)).toBeInTheDocument();

      const toolRun = screen.getAllByTestId('tool-run')[0]!;
      fireEvent.click(toolRun.querySelector('button')!);
      expect(within(toolRun).getAllByText('write_file').length).toBeGreaterThan(0);
      expect(within(toolRun).getByText('45ms')).toBeInTheDocument();
    });

    it('shows an empty filtered chat stream when the focused peer has no grouped turns', async () => {
      const session = makeSession();
      const room = mockChatData.buildMockRoom(session);
      const turns = [
        {
          id: 'user-only',
          role: 'user' as const,
          peerId: 'human',
          content: 'Broadcast only',
          ts: Date.now(),
        },
      ];
      vi.spyOn(mockChatData, 'buildMockRoom').mockReturnValue(room);
      vi.spyOn(mockChatData, 'buildMockTurns').mockReturnValue(turns);

      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(session),
      });
      await waitFor(() => expect(screen.getByTestId('mesh-sidebar')).toBeInTheDocument());

      fireEvent.click(screen.getByTestId('peer-card-ravn-r1'));
      expect(screen.getByText('no messages yet')).toBeInTheDocument();
    });

    it('renders empty cascade state and fallback chat connection copy from mocked room branches', async () => {
      const session = makeSession();
      const room = { ...mockChatData.buildMockRoom(session), meshEvents: [] };
      vi.spyOn(mockChatData, 'buildMockRoom').mockReturnValue(room);
      vi.spyOn(mockChatData, 'buildMockTurns').mockReturnValue([]);

      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(session),
      });
      await waitFor(() => expect(screen.getByTestId('mesh-cascade')).toBeInTheDocument());

      fireEvent.click(screen.getByTestId('cascade-filter-notification'));
      expect(screen.getByText('no events')).toBeInTheDocument();
      expect(screen.getByText('no messages yet')).toBeInTheDocument();

      vi.spyOn(mockChatData, 'buildMockRoom').mockReturnValue(null as never);
      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(session),
      });
      expect(await screen.findByText('Chat — requires live connection')).toBeInTheDocument();
    });

    it('renders alternative outcome verdict styling branches', async () => {
      const session = makeSession();
      const room = mockChatData.buildMockRoom(session);
      vi.spyOn(mockChatData, 'buildMockRoom').mockReturnValue(room);
      vi.spyOn(mockChatData, 'buildMockTurns').mockReturnValue([
        {
          id: 'assistant-blocked',
          role: 'assistant',
          peerId: room.participants[1]!.peerId,
          content: 'Blocked on follow-up review.',
          ts: Date.now(),
          outcome: {
            verdict: 'blocked',
            eventType: 'review',
            summary: 'Needs follow-up',
          },
        },
      ]);

      wrap(<SessionDetailPage sessionId="ds-1" />, {
        sessionStore: buildSessionStoreWithSession(session),
      });
      await waitFor(() => expect(screen.getByTestId('outcome-card')).toBeInTheDocument());
      expect(screen.getByText('blocked')).toBeInTheDocument();
      expect(screen.getByText('Needs follow-up')).toBeInTheDocument();
    });
  });

  // ─── Terminal tab ───────────────────────────────────────
  describe('terminal tab', () => {
    it('switches to the terminal tab on click', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-terminal'));
      expect(screen.getByTestId('tab-terminal')).toHaveAttribute('aria-selected', 'true');
      await waitFor(() => expect(screen.getByTestId('terminal-container')).toBeInTheDocument());
    });

    it('renders terminal workspace tabs and can add another tab', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" initialTab="terminal" />);
      expect(screen.getByTestId('terminal-tab-main')).toBeInTheDocument();
      expect(screen.getByTestId('terminal-tab-tests')).toBeInTheDocument();
      expect(screen.getByTestId('terminal-tab-view-io')).toBeInTheDocument();
      fireEvent.click(screen.getByTestId('terminal-tab-add'));
      expect(screen.getByTestId('terminal-tab-term-4')).toBeInTheDocument();
    });

    it('switches terminal tabs and shows the active terminal identity', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" initialTab="terminal" />);
      fireEvent.click(screen.getByTestId('terminal-tab-view-io'));
      expect(screen.getByTestId('terminal-panel-view-io')).toBeInTheDocument();
      expect(screen.getByText('io@ds-1')).toBeInTheDocument();
    });
  });

  // ─── Diffs tab ──────────────────────────────────────────
  describe('diffs tab', () => {
    it('renders two-pane diffs layout', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-diffs'));
      expect(screen.getByTestId('diffs-tab')).toBeInTheDocument();
      expect(screen.getByTestId('diff-file-list')).toBeInTheDocument();
      expect(screen.getByTestId('diff-viewer')).toBeInTheDocument();
    });

    it('renders diff file list entries', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-diffs'));
      const modFiles = screen.getAllByTestId('diff-file-mod');
      const newFiles = screen.getAllByTestId('diff-file-new');
      const delFiles = screen.getAllByTestId('diff-file-del');
      expect(modFiles.length + newFiles.length + delFiles.length).toBeGreaterThan(0);
    });

    it('switches diff viewer on file click', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-diffs'));
      const fileList = screen.getByTestId('diff-file-list');
      const fileButtons = fileList.querySelectorAll('button');
      if (fileButtons.length > 1) {
        fireEvent.click(fileButtons[1]!);
        expect(screen.getByTestId('diff-viewer')).toBeInTheDocument();
      }
    });

    it('renders deleted-file diff hunks', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-diffs'));
      fireEvent.click(screen.getAllByTestId('diff-file-del')[0]!);
      expect(screen.getByText('export class LegacyAuth {')).toBeInTheDocument();
      expect(screen.getByText(/deprecated/)).toBeInTheDocument();
    });
  });

  // ─── Files tab ──────────────────────────────────────────
  describe('files tab', () => {
    it('switches to the files tab on click', async () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-files'));
      expect(screen.getByTestId('tab-files')).toHaveAttribute('aria-selected', 'true');
      await waitFor(() =>
        expect(screen.getByTestId('session-files-workspace')).toBeInTheDocument(),
      );
      expect(screen.getByRole('button', { name: 'upload' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'download' })).toBeInTheDocument();
    });
  });

  // ─── Chronicle tab ─────────────────────────────────────
  describe('chronicle tab', () => {
    it('renders chronicle timeline', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-chronicle'));
      expect(screen.getByTestId('chronicle-tab')).toBeInTheDocument();
      expect(screen.getByTestId('chronicle-timeline')).toBeInTheDocument();
    });

    it('renders chronicle summary stats', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-chronicle'));
      expect(screen.getByTestId('chronicle-summary')).toBeInTheDocument();
    });

    it('renders chronicle event rows', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-chronicle'));
      const gitEvents = screen.getAllByTestId('chronicle-event-git');
      expect(gitEvents.length).toBeGreaterThan(0);
    });

    it('renders chronicle badges and tail running state', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-chronicle'));
      expect(screen.getAllByText('exit 0').length).toBeGreaterThan(0);
      expect(screen.getByText('running…')).toBeInTheDocument();
      expect(screen.getByText('TAIL')).toBeInTheDocument();
    });
  });

  // ─── Logs tab ──────────────────────────────────────────
  describe('logs tab', () => {
    it('renders logs terminal container', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-logs'));
      expect(screen.getByTestId('logs-tab')).toBeInTheDocument();
      expect(screen.getByTestId('logs-body')).toBeInTheDocument();
    });

    it('renders log filter buttons', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-logs'));
      expect(screen.getByTestId('log-filter-all')).toBeInTheDocument();
      expect(screen.getByTestId('log-filter-error')).toBeInTheDocument();
      expect(screen.getByTestId('log-filter-warn')).toBeInTheDocument();
    });

    it('filters log lines by level', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-logs'));

      fireEvent.click(screen.getByTestId('log-filter-warn'));
      const warnLines = screen.getAllByTestId('log-line-warn');
      expect(warnLines.length).toBeGreaterThan(0);
      expect(screen.queryByTestId('log-line-debug')).not.toBeInTheDocument();
    });

    it('shows the empty log state when filtering to a missing level', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      fireEvent.click(screen.getByTestId('tab-logs'));
      fireEvent.click(screen.getByTestId('log-filter-error'));
      expect(screen.getByText('no log entries')).toBeInTheDocument();
    });
  });

  // ─── Loading / Error states ────────────────────────────
  describe('loading and error states', () => {
    it('shows loading state when session is loading', () => {
      const slowStore: ISessionStore = {
        ...createMockSessionStore(),
        getSession: vi.fn(() => new Promise(() => {})), // never resolves
      };
      wrap(<SessionDetailPage sessionId="ds-1" />, { sessionStore: slowStore });
      expect(screen.getByText(/Loading session/)).toBeInTheDocument();
    });

    it('shows error state when session store rejects', async () => {
      const failingStore: ISessionStore = {
        ...createMockSessionStore(),
        getSession: vi.fn().mockRejectedValue(new Error('store error')),
      };
      wrap(<SessionDetailPage sessionId="ds-1" />, { sessionStore: failingStore });
      await waitFor(() => expect(screen.getByText('Failed to load session')).toBeInTheDocument());
    });

    it('shows error message from the store error', async () => {
      const failingStore: ISessionStore = {
        ...createMockSessionStore(),
        getSession: vi.fn().mockRejectedValue(new Error('connection lost')),
      };
      wrap(<SessionDetailPage sessionId="ds-1" />, { sessionStore: failingStore });
      await waitFor(() => expect(screen.getByText('connection lost')).toBeInTheDocument());
    });
  });

  // ─── Edge cases ────────────────────────────────────────
  describe('edge cases', () => {
    it('renders with unknown session id', () => {
      wrap(<SessionDetailPage sessionId="unknown-session" />);
      expect(screen.getByTestId('session-detail-page')).toBeInTheDocument();
      expect(screen.getByTestId('tab-chat')).toBeInTheDocument();
    });

    it('shows the archived placeholder badge when the session is absent in read-only mode', () => {
      wrap(<SessionDetailPage sessionId="unknown-session" readOnly />);
      expect(screen.getByTestId('session-archived-badge')).toBeInTheDocument();
    });

    it('all tabs use proper ARIA attributes', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      const tabs = screen.getAllByRole('tab');
      expect(tabs).toHaveLength(6);
      tabs.forEach((tab) => {
        expect(tab).toHaveAttribute('aria-selected');
      });
    });

    it('renders the active tab panel with role="tabpanel"', () => {
      wrap(<SessionDetailPage sessionId="ds-1" />);
      const panels = screen.getAllByRole('tabpanel');
      expect(panels.length).toBe(1);
    });
  });
});
