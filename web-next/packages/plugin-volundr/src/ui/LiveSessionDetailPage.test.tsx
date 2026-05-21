import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
import * as chatHooks from './hooks/useSkuldChat';
import {
  createMockVolundrService,
  createMockSessionStore,
  createMockMetricsStream,
} from '../adapters/mock';
import type { IVolundrService } from '../ports/IVolundrService';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IFileSystemPort } from '../ports/IFileSystemPort';
import type { ISessionStore } from '../ports/ISessionStore';
import type { VolundrSession } from '../models/volundr.model';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const navigate = vi.fn();

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

vi.mock('@xterm/xterm', () => ({
  Terminal: vi.fn().mockImplementation(() => ({
    open: vi.fn(),
    write: vi.fn(),
    dispose: vi.fn(),
    loadAddon: vi.fn(),
    onData: vi.fn().mockReturnValue({ dispose: vi.fn() }),
    options: {},
  })),
}));

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: vi.fn().mockImplementation(() => ({
    fit: vi.fn(),
    dispose: vi.fn(),
  })),
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
// Test data
// ---------------------------------------------------------------------------

const RUNNING_SESSION: VolundrSession = {
  id: 'test-session-id-1234',
  name: 'test-session',
  source: { type: 'git', repo: 'niuulabs/volundr', branch: 'main' },
  status: 'running',
  model: 'claude-sonnet-4-6',
  lastActive: Date.now() - 60_000,
  messageCount: 10,
  tokensUsed: 5000,
  hostname: 'skuld-test.local',
  instanceName: 'Guild Alpha',
  chatEndpoint: 'wss://skuld-test.local/session',
};

const STOPPED_SESSION: VolundrSession = {
  ...RUNNING_SESSION,
  status: 'stopped',
  hostname: undefined,
  chatEndpoint: undefined,
};

const STARTING_SESSION: VolundrSession = {
  ...RUNNING_SESSION,
  status: 'starting',
};

const ERROR_SESSION: VolundrSession = {
  ...RUNNING_SESSION,
  status: 'error',
  error: 'OOMKilled',
};

const originalFetch = global.fetch;

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function buildPtyStream(): IPtyStream {
  return {
    subscribe: vi.fn().mockReturnValue(() => {}),
    send: vi.fn(),
  };
}

function buildFilesystem(): IFileSystemPort {
  return {
    listTree: vi.fn().mockResolvedValue([]),
    expandDirectory: vi.fn().mockResolvedValue([]),
    readFile: vi.fn().mockResolvedValue(''),
  };
}

const SESSION_FEATURES = [
  {
    key: 'chat',
    label: 'Chat',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 10,
  },
  {
    key: 'terminal',
    label: 'Terminal',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 20,
  },
  {
    key: 'files',
    label: 'Files',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 40,
  },
  {
    key: 'chronicles',
    label: 'Chronicle',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 50,
  },
  {
    key: 'logs',
    label: 'Logs',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 60,
  },
];

function buildVolundrService(session: VolundrSession | null = RUNNING_SESSION): IVolundrService {
  const base = createMockVolundrService();
  return {
    ...base,
    getSession: vi.fn().mockResolvedValue(session),
    getFeatureModules: vi.fn().mockResolvedValue(SESSION_FEATURES),
    getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
    getChronicle: vi.fn().mockResolvedValue(null),
    getLogs: vi.fn().mockResolvedValue([]),
    getAggregatedLogs: vi.fn().mockResolvedValue({
      lines: [],
      participants: [],
    }),
    subscribeAggregatedLogs: vi.fn().mockReturnValue(() => {}),
  };
}

function buildSessionStore(session: VolundrSession | null = RUNNING_SESSION): ISessionStore {
  const base = createMockSessionStore();
  return {
    ...base,
    getSession: vi.fn().mockResolvedValue(
      session
        ? {
            id: session.id,
            name: session.name,
            state: session.status === 'running' ? 'active' : 'stopped',
            clusterId: 'local',
            events: [],
          }
        : null,
    ),
  };
}

function wrap(
  sessionId: string,
  opts: {
    readOnly?: boolean;
    session?: VolundrSession | null;
    volundr?: Partial<IVolundrService>;
  } = {},
) {
  const session = opts.session === undefined ? RUNNING_SESSION : opts.session;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const volundr = { ...buildVolundrService(session), ...opts.volundr } as IVolundrService;
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          ptyStream: buildPtyStream(),
          filesystem: buildFilesystem(),
          sessionStore: buildSessionStore(session),
          metricsStream: createMockMetricsStream(),
        }}
      >
        <LiveSessionDetailPage sessionId={sessionId} readOnly={opts.readOnly} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('LiveSessionDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    navigate.mockReset();
    global.fetch = vi.fn(async (input: string | URL | Request) => {
      const url =
        typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;

      if (url.includes('/api/diff/files')) {
        return new Response(JSON.stringify({ files: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      if (url.includes('/api/diff?')) {
        return new Response(JSON.stringify({ filePath: 'README.md', hunks: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }

      return new Response('{}', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    global.fetch = originalFetch;
  });

  it('surfaces native workflow gates and resolves them through Volundr', async () => {
    const resolveWorkflowGate = vi.fn().mockResolvedValue({
      id: 'gate-1',
      node_id: 'spec-prd-gate',
      activation_id: 'activation-1',
      label: 'PRD approval gate',
      condition: 'Review the PRD and decide whether it is strong enough to unlock SRD drafting.',
      status: 'approved',
      pending_behavior: 'help_needed',
      approvers: ['human'],
      auto_forward_after: '30m',
      requested_at: '2026-05-20T12:00:00Z',
      updated_at: '2026-05-20T12:02:00Z',
      triggered_by_event_type: 'spec.prd.ready_for_gate',
      approval_event_type: 'spec.prd.approved',
      changes_requested_event_type: 'spec.prd.changes_requested',
      attempt: 1,
      decision: 'APPROVE',
      notes: 'The scope is right; proceed.',
      source: 'human',
      summary: 'PRD approved by human reviewer.',
    });
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue({
      messages: [],
      streamingContent: undefined,
      streamingParts: undefined,
      streamingModel: undefined,
      connected: true,
      historyLoaded: true,
      participants: new Map(),
      meshEvents: [],
      agentEvents: new Map(),
      pendingPermissions: [],
      capabilities: {},
      sendMessage: vi.fn(),
      sendDirectedMessages: vi.fn(),
      respondToPermission: vi.fn(),
      sendInterrupt: vi.fn(),
      sendSetModel: vi.fn(),
      sendSetThinkingTokens: vi.fn(),
      sendRewindFiles: vi.fn(),
      sendSetInternalVisibility: vi.fn(),
      clearMessages: vi.fn(),
    });

    wrap('test-session-id-1234', {
      volundr: {
        getWorkflowGates: vi.fn().mockResolvedValue([
          {
            id: 'gate-1',
            node_id: 'spec-prd-gate',
            activation_id: 'activation-1',
            label: 'PRD approval gate',
            condition:
              'Review the PRD and decide whether it is strong enough to unlock SRD drafting.',
            status: 'pending',
            pending_behavior: 'help_needed',
            approvers: ['human'],
            auto_forward_after: '30m',
            requested_at: '2026-05-20T12:00:00Z',
            updated_at: '2026-05-20T12:00:00Z',
            triggered_by_event_type: 'spec.prd.ready_for_gate',
            approval_event_type: 'spec.prd.approved',
            changes_requested_event_type: 'spec.prd.changes_requested',
            attempt: 1,
            notes: '',
            source: 'workflow',
            summary: 'Please approve or request changes on the PRD.',
          },
        ]),
        resolveWorkflowGate,
      },
    });

    await screen.findByTestId('live-session-detail-page');
    await screen.findByText('Human Gate Requested');
    expect(screen.getByText(/Please approve or request changes on the PRD/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Reply Notes'), {
      target: { value: 'The scope is right; proceed.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }));

    await waitFor(() => {
      expect(resolveWorkflowGate).toHaveBeenCalledWith('test-session-id-1234', 'gate-1', {
        decision: 'APPROVE',
        notes: 'The scope is right; proceed.',
        source: 'human',
      });
    });
    await waitFor(() => {
      expect(screen.queryByText('Human Gate Requested')).not.toBeInTheDocument();
    });
  });

  it('hides the gate card once the latest gate verdict is already resolved', async () => {
    vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue({
      messages: [],
      streamingContent: undefined,
      streamingParts: undefined,
      streamingModel: undefined,
      connected: true,
      historyLoaded: true,
      participants: new Map(),
      meshEvents: [],
      agentEvents: new Map(),
      pendingPermissions: [],
      capabilities: {},
      sendMessage: vi.fn(),
      sendDirectedMessages: vi.fn(),
      respondToPermission: vi.fn(),
      sendInterrupt: vi.fn(),
      sendSetModel: vi.fn(),
      sendSetThinkingTokens: vi.fn(),
      sendRewindFiles: vi.fn(),
      sendSetInternalVisibility: vi.fn(),
      clearMessages: vi.fn(),
    });

    wrap('test-session-id-1234', {
      volundr: {
        getWorkflowGates: vi.fn().mockResolvedValue([
          {
            id: 'gate-1',
            node_id: 'spec-prd-gate',
            activation_id: 'activation-1',
            label: 'PRD approval gate',
            condition: 'Review the PRD',
            status: 'approved',
            pending_behavior: 'help_needed',
            approvers: ['human'],
            auto_forward_after: '30m',
            requested_at: '2026-05-20T12:00:00Z',
            updated_at: '2026-05-20T12:01:00Z',
            triggered_by_event_type: 'spec.prd.ready_for_gate',
            approval_event_type: 'spec.prd.approved',
            changes_requested_event_type: 'spec.prd.changes_requested',
            attempt: 1,
            decision: 'APPROVE',
            notes: '',
            source: 'human',
            summary: 'PRD approved by human reviewer.',
          },
        ]),
      },
    });

    await screen.findByTestId('live-session-detail-page');
    expect(screen.queryByText('Human Gate Requested')).not.toBeInTheDocument();
  });

  describe('loading and error states', () => {
    it('shows loading state initially', () => {
      wrap('test-session-id-1234');
      expect(screen.getByText('Loading session…')).toBeInTheDocument();
    });

    it('resolves past loading into main content', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
    });
  });

  describe('header rendering', () => {
    it('shows session name', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getAllByText('test-session').length).toBeGreaterThanOrEqual(1);
    });

    it('shows session id chip', async () => {
      wrap('test-session-id-1234');
      const chip = await screen.findByTestId('session-id-label');
      expect(chip).toBeInTheDocument();
    });

    it('shows model label', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Claude Sonnet 4.6')).toBeInTheDocument();
    });

    it('shows repo and branch for git source', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('niuulabs/volundr')).toBeInTheDocument();
      expect(screen.getByText('@main')).toBeInTheDocument();
    });

    it('shows Archived badge in read-only mode', async () => {
      wrap('test-session-id-1234', { readOnly: true });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Archived')).toBeInTheDocument();
    });

    it('does not show Archived badge in normal mode', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.queryByText('Archived')).not.toBeInTheDocument();
    });
  });

  describe('status rendering', () => {
    it('renders running session with status dot', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      // Status dot should have the brand class for running
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-bg-brand');
      expect(dot).toBeInTheDocument();
    });

    it('renders stopped session with faint dot', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-bg-text-faint');
      expect(dot).toBeInTheDocument();
    });

    it('renders starting session with sky dot', async () => {
      wrap('test-session-id-1234', { session: STARTING_SESSION });
      await screen.findByTestId('live-session-detail-page');
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-bg-sky-400');
      expect(dot).toBeInTheDocument();
    });

    it('renders error session with rose dot', async () => {
      wrap('test-session-id-1234', { session: ERROR_SESSION });
      await screen.findByTestId('live-session-detail-page');
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-bg-rose-400');
      expect(dot).toBeInTheDocument();
    });
  });

  describe('tabs', () => {
    it('renders all tab buttons', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('tab', { name: /Chat/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Terminal/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Diffs/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Files/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Chronicle/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Logs/i })).toBeInTheDocument();
    });

    it('switches to logs tab on click', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await waitFor(() => {
        expect(screen.getByTestId('live-logs-tab')).toBeInTheDocument();
      });
    });

    it('switches to diffs tab on click', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Diffs/i }));
      await waitFor(() => {
        expect(screen.getByTestId('diffs-tab')).toBeInTheDocument();
      });
    });

    it('shows a starting message in the terminal tab while a session boots', async () => {
      wrap('test-session-id-1234', { session: STARTING_SESSION });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Terminal/i }));
      await waitFor(() => {
        expect(screen.getByText('Session is starting…')).toBeInTheDocument();
      });
    });

    it('shows a stopped message in the terminal tab when no live terminal is available', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Terminal/i }));
      await waitFor(() => {
        expect(screen.getByText('Start the session to access terminal.')).toBeInTheDocument();
      });
    });

    it('renders diff file metadata and an empty diff state', async () => {
      vi.mocked(global.fetch).mockImplementation(async (input: string | URL | Request) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;

        if (url.includes('/api/diff/files')) {
          return new Response(
            JSON.stringify({
              files: [{ path: 'README.md', status: 'mod', ins: 2, del: 1 }],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          );
        }

        if (url.includes('/api/diff?')) {
          return new Response(JSON.stringify({ filePath: 'README.md', hunks: [] }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        return new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      });

      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Diffs/i }));
      const fileButton = await screen.findByText('README.md');
      expect(screen.getByText('+2')).toBeInTheDocument();
      expect(screen.getByText('-1')).toBeInTheDocument();

      fireEvent.click(fileButton);

      await waitFor(() => {
        expect(screen.getByText('No changes in this file')).toBeInTheDocument();
      });
    });

    it('shows a diff error when loading a selected file fails', async () => {
      vi.mocked(global.fetch).mockImplementation(async (input: string | URL | Request) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;

        if (url.includes('/api/diff/files')) {
          return new Response(
            JSON.stringify({
              files: [{ path: 'README.md', status: 'new', ins: 1, del: 0 }],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          );
        }

        if (url.includes('/api/diff?')) {
          return new Response(null, { status: 500 });
        }

        return new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      });

      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Diffs/i }));
      fireEvent.click(await screen.findByText('README.md'));

      await waitFor(() => {
        expect(
          screen.getByText('Failed to load diff: Failed to fetch diff: 500'),
        ).toBeInTheDocument();
      });
    });

    it('renders diff hunks and resets selection when switching diff base', async () => {
      vi.mocked(global.fetch).mockImplementation(async (input: string | URL | Request) => {
        const url =
          typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;

        if (url.includes('/api/diff/files')) {
          const params = new URL(url).searchParams;
          const base = params.get('base');
          const files =
            base === 'default-branch'
              ? [{ path: 'src/app.ts', status: 'remove', ins: 0, del: 4 }]
              : [{ path: 'src/app.ts', status: 'mod', ins: 3, del: 1 }];
          return new Response(JSON.stringify({ files }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }

        if (url.includes('/api/diff?')) {
          return new Response(
            JSON.stringify({
              filePath: 'src/app.ts',
              hunks: [
                {
                  oldStart: 1,
                  oldCount: 2,
                  newStart: 1,
                  newCount: 3,
                  lines: [
                    { type: 'context', content: 'const ready = true;', oldLine: 1, newLine: 1 },
                    { type: 'remove', content: 'console.log("old");', oldLine: 2 },
                    { type: 'add', content: 'console.log("new");', newLine: 2 },
                    { type: 'add', content: '', newLine: 3 },
                  ],
                },
              ],
            }),
            {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            },
          );
        }

        return new Response('{}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      });

      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Diffs/i }));
      fireEvent.click(await screen.findByText('src/app.ts'));

      await waitFor(() => {
        expect(screen.getByText('@@ -1,2 +1,3 @@')).toBeInTheDocument();
      });
      expect(screen.getByText('console.log("old");')).toBeInTheDocument();
      expect(screen.getByText('console.log("new");')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /default branch/i }));

      await waitFor(() => {
        expect(screen.getByText('Select a file to view changes')).toBeInTheDocument();
      });
      expect(screen.getByText('-4')).toBeInTheDocument();
    });

    it('shows the empty diff state when a session has no live endpoint', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Diffs/i }));

      await waitFor(() => {
        expect(screen.getByText('Select a file to view changes')).toBeInTheDocument();
      });
      expect(screen.getAllByText('changed files').length).toBeGreaterThanOrEqual(1);
      expect(screen.queryByText('Loading files...')).not.toBeInTheDocument();
    });

    it('renders a saved transcript for stopped sessions', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getConversationHistory: vi.fn().mockResolvedValue({
            turns: [
              {
                id: 'turn-user-1',
                role: 'user',
                content: 'Can you summarize the last run?',
                created_at: '2026-05-15T18:00:00Z',
              },
              {
                id: 'turn-assistant-1',
                role: 'assistant',
                content: 'It finished cleanly and archived the workspace.',
                created_at: '2026-05-15T18:00:05Z',
                participant_meta: {
                  peer_id: 'flock-coder',
                  persona: 'coder',
                  display_name: 'Coder',
                  participant_type: 'ravn',
                  color: 'brand',
                },
              },
            ],
          }),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Can you summarize the last run?')).toBeInTheDocument();
      expect(
        screen.getByText('It finished cleanly and archived the workspace.'),
      ).toBeInTheDocument();
      expect(screen.queryByText('Start the session to chat.')).not.toBeInTheDocument();
    });

    it('shows an archive-aware chronicle empty state for stopped sessions', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Chronicle/i }));
      await waitFor(() => {
        expect(screen.getByText('No saved chronicle yet.')).toBeInTheDocument();
      });
      expect(
        screen.queryByText('Start the session to view its chronicle.'),
      ).not.toBeInTheDocument();
    });
  });

  describe('logs tab', () => {
    it('renders participant chips from aggregated logs', async () => {
      const service = buildVolundrService();
      vi.mocked(service.getAggregatedLogs).mockResolvedValue({
        participants: [
          { id: 'skuld', label: 'Skuld', kind: 'broker' },
          { id: 'coder', label: 'Coder', kind: 'ravn' },
        ],
        lines: [
          {
            id: 'log-1',
            sessionId: 'test-session-id-1234',
            timestamp: Date.now(),
            level: 'info',
            participant: 'skuld',
            participantLabel: 'Skuld',
            participantKind: 'broker',
            source: 'skuld.broker',
            message: 'Workflow trigger dispatched',
            sequence: 1,
            stream: '.skuld.log',
          },
        ],
      });

      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <ServicesProvider
            services={{
              bifrost: createMockBifrostService(),
              volundr: service,
              ptyStream: buildPtyStream(),
              filesystem: buildFilesystem(),
              sessionStore: buildSessionStore(),
              metricsStream: createMockMetricsStream(),
            }}
          >
            <LiveSessionDetailPage sessionId="test-session-id-1234" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await screen.findByTestId('live-logs-toolbar');
      expect(screen.getByTestId('log-participant-skuld')).toBeInTheDocument();
      expect(screen.getByTestId('log-participant-coder')).toBeInTheDocument();
      expect(screen.getByText('Workflow trigger dispatched')).toBeInTheDocument();
    });

    it('filters rows when a participant chip is selected', async () => {
      const service = buildVolundrService();
      vi.mocked(service.getAggregatedLogs).mockResolvedValue({
        participants: [
          { id: 'skuld', label: 'Skuld', kind: 'broker' },
          { id: 'coder', label: 'Coder', kind: 'ravn' },
        ],
        lines: [
          {
            id: 'log-1',
            sessionId: 'test-session-id-1234',
            timestamp: Date.now(),
            level: 'info',
            participant: 'skuld',
            participantLabel: 'Skuld',
            participantKind: 'broker',
            source: 'skuld.broker',
            message: 'Broker line',
            sequence: 1,
            stream: '.skuld.log',
          },
          {
            id: 'log-2',
            sessionId: 'test-session-id-1234',
            timestamp: Date.now() + 1,
            level: 'error',
            participant: 'coder',
            participantLabel: 'Coder',
            participantKind: 'ravn',
            source: 'ravn.adapters.llm.openai',
            message: 'Coder line',
            sequence: 2,
            stream: 'logs/coder.log',
          },
        ],
      });

      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <ServicesProvider
            services={{
              bifrost: createMockBifrostService(),
              volundr: service,
              ptyStream: buildPtyStream(),
              filesystem: buildFilesystem(),
              sessionStore: buildSessionStore(),
              metricsStream: createMockMetricsStream(),
            }}
          >
            <LiveSessionDetailPage sessionId="test-session-id-1234" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await screen.findByText('Broker line');
      fireEvent.click(screen.getByTestId('log-participant-coder'));
      await waitFor(() => {
        expect(screen.queryByText('Broker line')).not.toBeInTheDocument();
        expect(screen.getByText('Coder line')).toBeInTheDocument();
      });
    });
  });

  describe('action buttons', () => {
    it('shows Stop button for running session', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /Stop/i })).toBeInTheDocument();
    });

    it('shows Start button for stopped session', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /Start/i })).toBeInTheDocument();
    });

    it('shows delete button', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByTitle(/Delete/i)).toBeInTheDocument();
    });

    it('opens a centered delete dialog with visible cleanup options and submits them', async () => {
      const service = buildVolundrService();
      service.deleteSession = vi.fn().mockResolvedValue(undefined);
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      render(
        <QueryClientProvider client={client}>
          <ServicesProvider
            services={{
              bifrost: createMockBifrostService(),
              volundr: service,
              ptyStream: buildPtyStream(),
              filesystem: buildFilesystem(),
              sessionStore: buildSessionStore(),
              metricsStream: createMockMetricsStream(),
            }}
          >
            <LiveSessionDetailPage sessionId="test-session-id-1234" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Delete session/i));

      const dialog = screen.getByRole('dialog');
      expect(dialog).toBeInTheDocument();
      expect(screen.getByTestId('cleanup-workspace_storage')).toBeInTheDocument();
      expect(screen.getByTestId('cleanup-chronicles')).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('cleanup-workspace_storage'));
      fireEvent.click(screen.getByTestId('cleanup-chronicles'));

      expect(screen.getByTestId('cleanup-workspace_storage')).toBeChecked();
      expect(screen.getByTestId('cleanup-chronicles')).toBeChecked();

      fireEvent.click(screen.getByRole('button', { name: /^delete$/i }));

      await waitFor(() => {
        expect(service.deleteSession).toHaveBeenCalledWith('test-session-id-1234', [
          'workspace_storage',
          'chronicles',
        ]);
      });
      await waitFor(() => {
        expect(navigate).toHaveBeenCalledWith({ to: '/volundr/sessions', replace: true });
      });
    });
  });

  describe('local mount source', () => {
    it('shows path for local mount source', async () => {
      const localSession: VolundrSession = {
        ...RUNNING_SESSION,
        source: { type: 'local_mount', path: '/home/user/project' },
      };
      wrap('test-session-id-1234', { session: localSession });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('/home/user/project')).toBeInTheDocument();
    });
  });

  describe('header metrics', () => {
    it('shows Active metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Active')).toBeInTheDocument();
    });

    it('shows Msgs metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Msgs')).toBeInTheDocument();
    });

    it('shows Tokens metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Tokens')).toBeInTheDocument();
    });

    it('shows Forge metric with the instance name', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Forge')).toBeInTheDocument();
      expect(screen.getByText('Guild Alpha')).toBeInTheDocument();
    });
  });
});
