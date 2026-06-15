import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { createMockBifrostService } from '@niuulabs/plugin-bifrost';
import { LiveSessionDetailPage, buildTelemetryTimelineRows } from './LiveSessionDetailPage';
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

const ARCHIVED_SESSION: VolundrSession = {
  ...STOPPED_SESSION,
  status: 'archived',
};

const STARTING_SESSION: VolundrSession = {
  ...RUNNING_SESSION,
  status: 'starting',
};

const TELEMETRY_TRACE = {
  traceId: 'trace-1',
  sessionId: RUNNING_SESSION.id,
  startedAt: '2026-05-23T09:25:01Z',
  endedAt: '2026-05-23T09:40:07Z',
  durationMs: 906_000,
  spans: [
    {
      id: 'root',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: null,
      kind: 'session.lifecycle',
      name: RUNNING_SESSION.name,
      status: 'completed',
      startedAt: '2026-05-23T09:25:01Z',
      endedAt: '2026-05-23T09:40:07Z',
      durationMs: 906_000,
      actorType: 'system',
      actorId: RUNNING_SESSION.id,
      actorLabel: RUNNING_SESSION.name,
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'workflow',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'root',
      kind: 'session.workflow',
      name: 'execution',
      status: 'completed',
      startedAt: '2026-05-23T09:29:12Z',
      endedAt: '2026-05-23T09:37:52Z',
      durationMs: 520_000,
      actorType: 'workflow',
      actorId: 'coordinator',
      actorLabel: 'coordinator',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'tool',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'workflow',
      kind: 'tool.call',
      name: 'Write',
      status: 'completed',
      startedAt: '2026-05-23T09:31:00Z',
      endedAt: '2026-05-23T09:31:21Z',
      durationMs: 21_000,
      actorType: 'assistant',
      actorId: 'coordinator',
      actorLabel: 'coordinator',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-1',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'workflow',
      kind: 'turn.assistant',
      name: 'draft response',
      status: 'completed',
      startedAt: '2026-05-23T09:29:12Z',
      endedAt: '2026-05-23T09:29:51Z',
      durationMs: 39_000,
      actorType: 'assistant',
      actorId: 'coordinator',
      actorLabel: 'planner',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-1-tool',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-1',
      kind: 'tool.call',
      name: 'Search',
      status: 'completed',
      startedAt: '2026-05-23T09:29:18Z',
      endedAt: '2026-05-23T09:29:26Z',
      durationMs: 8_000,
      actorType: 'assistant',
      actorId: 'coordinator',
      actorLabel: 'planner',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-1-idle',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-1',
      kind: 'wait.idle',
      name: 'Operator away',
      status: 'completed',
      startedAt: '2026-05-23T09:29:26Z',
      endedAt: '2026-05-23T09:29:38Z',
      durationMs: 12_000,
      actorType: 'assistant',
      actorId: 'coordinator',
      actorLabel: 'planner',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-2',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'workflow',
      kind: 'turn.peer',
      name: 'execute patch',
      status: 'completed',
      startedAt: '2026-05-23T09:31:30Z',
      endedAt: '2026-05-23T09:33:50Z',
      durationMs: 140_000,
      actorType: 'peer',
      actorId: 'worker',
      actorLabel: 'execution',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-2-tool',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-2',
      kind: 'terminal.command',
      name: 'npm test',
      status: 'completed',
      startedAt: '2026-05-23T09:32:10Z',
      endedAt: '2026-05-23T09:32:48Z',
      durationMs: 38_000,
      actorType: 'peer',
      actorId: 'worker',
      actorLabel: 'execution',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'turn-2-wait',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-2',
      kind: 'wait.permission',
      name: 'Await approval',
      status: 'completed',
      startedAt: '2026-05-23T09:33:00Z',
      endedAt: '2026-05-23T09:33:12Z',
      durationMs: 12_000,
      actorType: 'peer',
      actorId: 'worker',
      actorLabel: 'execution',
      sourceService: 'skuld',
      attributes: {},
    },
  ],
  lanes: [
    { key: 'system', label: 'system', kind: 'system' },
    { key: 'assistant', label: 'coordinator', kind: 'assistant' },
  ],
};

const TELEMETRY_SUMMARY = {
  totalDurationMs: 906_000,
  provisioningDurationMs: 0,
  setupDurationMs: 0,
  workflowDurationMs: 660_000,
  publishDurationMs: 21_000,
  cleanupDurationMs: 0,
  activeExecutionDurationMs: 556_000,
  waitingDurationMs: 349_000,
  turnCount: 2,
  toolCallCount: 1,
  longestSpan: TELEMETRY_TRACE.spans[0],
};

const BLOCKED_TELEMETRY_TRACE = {
  ...TELEMETRY_TRACE,
  spans: [
    TELEMETRY_TRACE.spans[0],
    TELEMETRY_TRACE.spans[1],
    {
      id: 'peer-blocked',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'root',
      kind: 'turn.peer',
      name: 'Handle mesh outcome',
      status: 'completed',
      startedAt: '2026-05-23T09:30:00Z',
      endedAt: '2026-05-23T09:38:00Z',
      durationMs: 480_000,
      actorType: 'peer',
      actorId: 'flock-coordinator',
      actorLabel: 'coordinator',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'peer-wait',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'peer-blocked',
      kind: 'wait.permission',
      name: 'Await approval',
      status: 'completed',
      startedAt: '2026-05-23T09:35:00Z',
      endedAt: '2026-05-23T09:35:06Z',
      durationMs: 6_000,
      actorType: 'peer',
      actorId: 'flock-coordinator',
      actorLabel: 'coordinator',
      sourceService: 'skuld',
      attributes: {},
    },
    {
      id: 'peer-block',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'peer-blocked',
      kind: 'tool.call',
      name: 'Write',
      status: 'cancelled',
      startedAt: '2026-05-23T09:37:20Z',
      endedAt: '2026-05-23T09:37:24Z',
      durationMs: 4_000,
      actorType: 'peer',
      actorId: 'flock-coordinator',
      actorLabel: 'coordinator',
      sourceService: 'skuld',
      attributes: {},
    },
  ],
};

const TOOL_OVERVIEW_TRACE = {
  ...TELEMETRY_TRACE,
  spans: [
    ...TELEMETRY_TRACE.spans,
    {
      id: 'mcp-blocked',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-2',
      kind: 'tool.call',
      name: 'linear.search blocker',
      status: 'cancelled',
      startedAt: '2026-05-23T09:33:20Z',
      endedAt: '2026-05-23T09:33:28Z',
      durationMs: 8_000,
      actorType: 'peer',
      actorId: 'worker',
      actorLabel: 'execution',
      sourceService: 'skuld',
      attributes: { reason: 'blocked by permissions' },
    },
    {
      id: 'write-followup',
      sessionId: RUNNING_SESSION.id,
      traceId: 'trace-1',
      parentSpanId: 'turn-2',
      kind: 'tool.call',
      name: 'Draft handoff note',
      status: 'completed',
      startedAt: '2026-05-23T09:33:28Z',
      endedAt: '2026-05-23T09:33:38Z',
      durationMs: 10_000,
      actorType: 'peer',
      actorId: 'worker',
      actorLabel: 'execution',
      sourceService: 'skuld',
      attributes: {},
    },
  ],
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
    key: 'telemetry',
    label: 'Telemetry',
    icon: '',
    scope: 'session' as const,
    enabled: true,
    defaultEnabled: true,
    adminOnly: false,
    order: 55,
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
    getSessions: vi.fn().mockResolvedValue(
      session
        ? [
            session,
            {
              ...session,
              id: 'recent-trace-peer',
              name: `${session.name}-recent`,
              status: 'stopped',
              lastActive: Date.now() - 120_000,
            },
          ]
        : [],
    ),
    getSession: vi.fn().mockResolvedValue(session),
    getFeatureModules: vi.fn().mockResolvedValue(SESSION_FEATURES),
    getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
    getChronicle: vi.fn().mockResolvedValue(null),
    getSessionTrace: vi.fn().mockResolvedValue(TELEMETRY_TRACE),
    getSessionTraceSummary: vi.fn().mockResolvedValue(TELEMETRY_SUMMARY),
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
            ravnId: 's-4912',
            name: session.name,
            personaName: session.name,
            templateId: 'git-default',
            state: session.status === 'running' ? 'running' : 'terminated',
            clusterId: 'local',
            startedAt: new Date(Date.now() - 9_240_000).toISOString(),
            resources: {
              cpuRequest: 2,
              cpuLimit: 4,
              cpuUsed: 1.5,
              memRequestMi: 4096,
              memLimitMi: 8192,
              memUsedMi: 2048,
              gpuCount: 0,
            },
            env: {},
            files: { added: 1, modified: 2, deleted: 1 },
            events: [
              { ts: new Date().toISOString(), kind: 'message', body: 'started' },
              { ts: new Date().toISOString(), kind: 'file', body: 'updated files' },
            ],
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
    sessionStore?: Partial<ISessionStore>;
  } = {},
) {
  const session = opts.session === undefined ? RUNNING_SESSION : opts.session;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const volundr = { ...buildVolundrService(session), ...opts.volundr } as IVolundrService;
  const sessionStore = { ...buildSessionStore(session), ...opts.sessionStore } as ISessionStore;
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          bifrost: createMockBifrostService(),
          volundr,
          ptyStream: buildPtyStream(),
          filesystem: buildFilesystem(),
          sessionStore,
          metricsStream: createMockMetricsStream(),
        }}
      >
        <LiveSessionDetailPage sessionId={sessionId} readOnly={opts.readOnly} />
      </ServicesProvider>
    </QueryClientProvider>,
  );
}

function mockChatState(overrides: Partial<ReturnType<typeof chatHooks.useSkuldChat>> = {}) {
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
    ...overrides,
  });
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
    mockChatState();

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
    mockChatState();

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

  it('surfaces mesh-driven workflow gates and sends change requests through chat', async () => {
    const sendDirectedMessages = vi.fn();
    mockChatState({
      participants: new Map([
        [
          'workflow-reviewer',
          {
            peerId: 'workflow-reviewer',
            displayName: '',
            persona: 'reviewer',
            participantType: 'ravn',
          },
        ],
      ]),
      meshEvents: [
        {
          id: 'mesh-gate-1',
          participantId: 'workflow-reviewer',
          participant: { color: '#a78bfa' },
          type: 'notification',
          notificationType: 'help_needed',
          summary: 'Please tighten the telemetry review.',
          reason: 'A human review is required.',
          recommendation: 'Request changes until the blocked cases are covered.',
          urgency: 2,
          persona: 'reviewer',
          timestamp: new Date('2026-05-23T09:35:00Z'),
        } as never,
      ],
      sendDirectedMessages,
    });

    wrap('test-session-id-1234');

    await screen.findByTestId('live-session-detail-page');
    expect(await screen.findByText('Human Gate Requested')).toBeInTheDocument();
    expect(screen.getAllByText('reviewer').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Please tighten the telemetry review.').length).toBeGreaterThan(0);
    expect(screen.getByText('A human review is required.')).toBeInTheDocument();
    expect(
      screen.getAllByText('Request changes until the blocked cases are covered.').length,
    ).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('Reply Notes'), {
      target: { value: 'Please cover the blocked timeline state before landing.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Request changes' }));

    await waitFor(() => {
      expect(sendDirectedMessages).toHaveBeenCalledWith(
        [
          {
            peerId: 'workflow-reviewer',
            displayName: '',
            persona: 'reviewer',
            participantType: 'ravn',
          },
        ],
        'CHANGES_REQUESTED\n\nPlease cover the blocked timeline state before landing.',
        [],
      );
    });
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

    it('does not show the model label in the compact header', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.queryByText('Claude Sonnet 4.6')).not.toBeInTheDocument();
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

    it('shows a distinct session handle and linked tracker issue', async () => {
      wrap('test-session-id-1234', {
        session: {
          ...RUNNING_SESSION,
          trackerIssue: {
            url: 'https://linear.app/niuu/issue/OPS-42',
            identifier: 'OPS-42',
            title: 'Telemetry branch gap',
          },
        },
        sessionStore: {
          getSession: vi.fn().mockResolvedValue({
            id: RUNNING_SESSION.id,
            ravnId: 'worker-17',
            name: RUNNING_SESSION.name,
            personaName: RUNNING_SESSION.name,
            templateId: 'git-default',
            state: 'running',
            clusterId: 'local',
            startedAt: new Date(Date.now() - 9_240_000).toISOString(),
            resources: {
              cpuRequest: 2,
              cpuLimit: 4,
              cpuUsed: 1.5,
              memRequestMi: 4096,
              memLimitMi: 8192,
              memUsedMi: 2048,
              gpuCount: 0,
            },
            env: {},
            files: { added: 1, modified: 2, deleted: 1 },
            events: [],
          }),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('worker-17')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /ops-42/i })).toHaveAttribute(
        'href',
        'https://linear.app/niuu/issue/OPS-42',
      );
    });

    it('suppresses the handle when the ravn id matches the session id', async () => {
      wrap('test-session-id-1234', {
        sessionStore: {
          getSession: vi.fn().mockResolvedValue({
            id: RUNNING_SESSION.id,
            ravnId: 'test-session-id-1234',
            name: RUNNING_SESSION.name,
            personaName: RUNNING_SESSION.name,
            templateId: 'git-default',
            state: 'running',
            clusterId: 'local',
            startedAt: new Date(Date.now() - 9_240_000).toISOString(),
            resources: {
              cpuRequest: 2,
              cpuLimit: 4,
              cpuUsed: 1.5,
              memRequestMi: 4096,
              memLimitMi: 8192,
              memUsedMi: 2048,
              gpuCount: 0,
            },
            env: {},
            files: { added: 1, modified: 2, deleted: 1 },
            events: [],
          }),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      const handle = screen.queryByText('test-session-id-1234');
      expect(handle).not.toHaveClass('niuu-live-session__handle');
    });
  });

  describe('status rendering', () => {
    it('renders a disconnected status dot by default', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-live-session__status-dot--disconnected');
      expect(dot).toBeInTheDocument();
    });

    it('renders a connected status dot when chat is live', async () => {
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

      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      const page = screen.getByTestId('live-session-detail-page');
      const dot = page.querySelector('.niuu-live-session__status-dot--connected');
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
      expect(screen.getByRole('tab', { name: /Telemetry/i })).toBeInTheDocument();
      expect(screen.getByRole('tab', { name: /Logs/i })).toBeInTheDocument();
    });

    it('switches to telemetry tab on click', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));
      await waitFor(() => {
        expect(screen.getByTestId('live-telemetry-tab')).toBeInTheDocument();
      });
    });

    it('renders wired telemetry summary metrics', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      await waitFor(() => {
        expect(screen.getByText('Session Duration')).toBeInTheDocument();
      });
      expect(screen.getAllByText('15m 06s').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('9m 16s')).toBeInTheDocument();
      expect(screen.getByText('5m 49s')).toBeInTheDocument();
      expect(screen.getAllByText('21s').length).toBeGreaterThanOrEqual(1);
      expect(screen.getAllByText('execution').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByText('median 15m 06s • n=1')).toBeInTheDocument();
      expect(screen.getByText('Timeline')).toBeInTheDocument();
      expect(screen.getAllByText('workflow').length).toBeGreaterThanOrEqual(1);
      expect(screen.getByTestId('telemetry-breakdown')).toBeInTheDocument();
    });

    it('keeps active child work visible as timeline segments', () => {
      const rows = buildTelemetryTimelineRows(TELEMETRY_TRACE);
      const executionRow = rows.find((row) => row.label === 'execution');

      expect(executionRow?.childSegments).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: 'turn-2-tool',
            tone: 'active',
            durationMs: 38_000,
          }),
        ]),
      );
    });

    it('shows a hover card with span details for a trace row', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      const workflowTrace = await screen.findByRole('button', {
        name: /execution trace details/i,
      });
      fireEvent.mouseEnter(workflowTrace);

      const tooltip = await screen.findByTestId('telemetry-tooltip-workflow');
      expect(tooltip).toHaveTextContent('child spans');
      expect(tooltip).toHaveTextContent('active 8m 16s');
      expect(tooltip).toHaveTextContent('wait 24s');
      expect(tooltip).toHaveTextContent('blocked 0s');
      expect(tooltip).toHaveTextContent('1');
    });

    it('clicking a timeline row selects and expands the matching stage breakdown section', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      fireEvent.click(
        await screen.findByRole('button', {
          name: /execution trace details/i,
        }),
      );

      expect(await screen.findByText(/tool · write/i)).toBeInTheDocument();
    });

    it('renders the turn-by-turn timing shell with all-turns list by default', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      expect(await screen.findByTestId('telemetry-turn-shell')).toBeInTheDocument();
      expect(screen.getByText('Turn-by-turn timing')).toBeInTheDocument();
      expect(screen.getByTestId('telemetry-turn-list')).toBeInTheDocument();
      expect(screen.getByText(/2 turns/i)).toBeInTheDocument();
    });

    it('shows selected turn details when a turn bar is clicked', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      fireEvent.click(await screen.findByTestId('telemetry-turn-bar-2'));

      const detail = await screen.findByTestId('telemetry-turn-detail');
      expect(detail).toHaveTextContent('turn #2');
      expect(detail).toHaveTextContent('tool time');
      expect(detail).toHaveTextContent('operator idle');
      expect(detail).toHaveTextContent('npm test');
    });

    it('surfaces blocked child-state timing for a row with blocked work', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getSessionTrace: vi.fn().mockResolvedValue(BLOCKED_TELEMETRY_TRACE),
        },
      });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      const blockedTrace = await screen.findByRole('button', {
        name: /coordinator trace details/i,
      });
      fireEvent.mouseEnter(blockedTrace);

      const tooltip = await screen.findByTestId('telemetry-tooltip-peer-blocked');
      expect(tooltip).toHaveTextContent('active 7m 50s');
      expect(tooltip).toHaveTextContent('wait 6s');
      expect(tooltip).toHaveTextContent('blocked 4s');
    });

    it('renders tool overview rows with specific MCP detail and blocked note', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getSessionTrace: vi.fn().mockResolvedValue(TOOL_OVERVIEW_TRACE),
        },
      });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      const toolOverview = await screen.findByTestId('telemetry-tools-overview');
      expect(toolOverview).toHaveTextContent('Tool calls');
      expect(toolOverview).toHaveTextContent('linear.search');
      expect(toolOverview).toHaveTextContent('blocked 1×');

      fireEvent.click(screen.getByRole('button', { name: /mcp/i }));
      expect(toolOverview).toHaveTextContent('linear.search');
    });

    it('renders empty telemetry states when no baseline, turns, or tool spans exist', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getSessions: vi.fn().mockResolvedValue([RUNNING_SESSION]),
          getSessionTrace: vi.fn().mockResolvedValue({
            traceId: 'trace-empty',
            sessionId: RUNNING_SESSION.id,
            startedAt: '2026-05-23T09:25:01Z',
            endedAt: '2026-05-23T09:25:31Z',
            durationMs: 30_000,
            spans: [
              {
                id: 'root',
                sessionId: RUNNING_SESSION.id,
                traceId: 'trace-empty',
                parentSpanId: null,
                kind: 'session.lifecycle',
                name: RUNNING_SESSION.name,
                status: 'completed',
                startedAt: '2026-05-23T09:25:01Z',
                endedAt: '2026-05-23T09:25:31Z',
                durationMs: 30_000,
                actorType: 'system',
                actorId: RUNNING_SESSION.id,
                actorLabel: RUNNING_SESSION.name,
                sourceService: 'skuld',
                attributes: {},
              },
            ],
            lanes: [],
          }),
          getSessionTraceSummary: vi.fn().mockResolvedValue({
            totalDurationMs: 30_000,
            provisioningDurationMs: 0,
            setupDurationMs: 0,
            workflowDurationMs: 0,
            publishDurationMs: 0,
            cleanupDurationMs: 0,
            activeExecutionDurationMs: 0,
            waitingDurationMs: 0,
            turnCount: 0,
            toolCallCount: 0,
            longestSpan: null,
          }),
        },
      });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      expect(await screen.findByText('no baseline')).toBeInTheDocument();
      expect(screen.getByText('No tool spans recorded yet.')).toBeInTheDocument();
      expect(screen.queryByTestId('telemetry-turn-shell')).not.toBeInTheDocument();
    });

    it('renders telemetry unavailable when trace loading fails', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getSessionTrace: vi.fn().mockRejectedValue(new Error('trace failed')),
        },
      });
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Telemetry/i }));

      expect(await screen.findByText('Telemetry unavailable')).toBeInTheDocument();
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

    it('shows a transcript error for stopped sessions when replay loading fails', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getConversationHistory: vi.fn().mockRejectedValue(new Error('history failed')),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      await waitFor(() => {
        expect(screen.getByText('Failed to load saved transcript.')).toBeInTheDocument();
      });
    });

    it('shows a transcript loading state for stopped sessions while replay is pending', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getConversationHistory: vi.fn(() => new Promise(() => {})),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Loading saved transcript…')).toBeInTheDocument();
    });

    it('shows an empty replay message for stopped sessions without saved history', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getConversationHistory: vi.fn().mockResolvedValue({ turns: [] }),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      await waitFor(() => {
        expect(screen.getByText('No saved transcript yet.')).toBeInTheDocument();
      });
    });

    it('shows the start-chat empty state for running sessions without a live chat endpoint', async () => {
      wrap('test-session-id-1234', {
        session: {
          ...RUNNING_SESSION,
          chatEndpoint: undefined,
          hostname: undefined,
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Start the session to chat.')).toBeInTheDocument();
    });

    it('renders the files workspace tab', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Files/i }));

      await waitFor(() => {
        expect(screen.getByTestId('session-files-workspace')).toBeInTheDocument();
      });
    });

    it('renders the live terminal when a session is ready', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Terminal/i }));

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /New terminal/i })).toBeInTheDocument();
      });
    });

    it('falls back to the first available tab when chat is hidden', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getFeatureModules: vi
            .fn()
            .mockResolvedValue(SESSION_FEATURES.filter((feature) => feature.key !== 'chat')),
        },
      });
      await screen.findByTestId('live-session-detail-page');

      expect(screen.queryByRole('tab', { name: /Chat/i })).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /New terminal/i })).toBeInTheDocument();
    });

    it('applies user tab visibility and sort preferences', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getUserFeaturePreferences: vi.fn().mockResolvedValue([
            { featureKey: 'terminal', visible: false, sortOrder: 99 },
            { featureKey: 'logs', visible: false, sortOrder: 98 },
            { featureKey: 'files', visible: true, sortOrder: 5 },
          ]),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(screen.queryByRole('tab', { name: /Terminal/i })).not.toBeInTheDocument();
      expect(screen.queryByRole('tab', { name: /Logs/i })).not.toBeInTheDocument();

      const tabLabels = screen
        .getAllByRole('tab')
        .map((tab) => tab.textContent?.replace(/\d+/g, '').trim());
      expect(tabLabels.slice(0, 3)).toEqual(['Files', 'Chat', 'Diffs']);
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
      expect(await screen.findByText('Can you summarize the last run?')).toBeInTheDocument();
      expect(
        await screen.findByText('It finished cleanly and archived the workspace.'),
      ).toBeInTheDocument();
      expect(screen.queryByText('Start the session to chat.')).not.toBeInTheDocument();
    });

    it('rebuilds the outcome sidebar from a stopped session transcript', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getConversationHistory: vi.fn().mockResolvedValue({
            turns: [
              {
                id: 'turn-outcome-1',
                role: 'assistant',
                content: [
                  '```outcome',
                  'verdict: needs_changes',
                  'summary: Review found missing telemetry coverage.',
                  'details: |',
                  '  ## Findings',
                  '  - Add spans around tenant notification delivery.',
                  '```',
                ].join('\n'),
                created_at: '2026-05-15T18:00:05Z',
                participant_meta: {
                  peer_id: 'workflow-reviewer',
                  persona: 'reviewer',
                  display_name: 'Reviewer',
                  participant_type: 'ravn',
                  color: 'red',
                },
              },
            ],
          }),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      expect(await screen.findByTestId('mesh-cascade-panel')).toBeInTheDocument();
      expect(
        screen.getAllByText('Review found missing telemetry coverage.').length,
      ).toBeGreaterThan(1);
      expect(screen.getByText('Changes Requested')).toBeInTheDocument();
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

    it('reloads log content when the session id changes', async () => {
      const service = buildVolundrService();
      vi.mocked(service.getAggregatedLogs).mockImplementation(async (sessionId) => ({
        participants: [{ id: 'skuld', label: 'Skuld', kind: 'broker' }],
        lines: [
          {
            id: `log-${sessionId}`,
            sessionId,
            timestamp: Date.now(),
            level: 'info',
            participant: 'skuld',
            participantLabel: 'Skuld',
            participantKind: 'broker',
            source: 'skuld.broker',
            message: sessionId === 'session-b' ? 'Second session line' : 'First session line',
            sequence: 1,
            stream: '.skuld.log',
          },
        ],
      }));

      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const view = render(
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
            <LiveSessionDetailPage sessionId="session-a" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await screen.findByText('First session line');

      view.rerender(
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
            <LiveSessionDetailPage sessionId="session-b" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await waitFor(() => {
        expect(screen.getByText('Second session line')).toBeInTheDocument();
      });
      expect(screen.queryByText('First session line')).not.toBeInTheDocument();
    });
  });

  describe('chronicles tab', () => {
    it('shows the running empty-state when no live chronicle is available yet', async () => {
      wrap('test-session-id-1234', {
        volundr: {
          getChronicle: vi.fn().mockResolvedValue(null),
          subscribeChronicle: vi.fn().mockReturnValue(() => {}),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Chronicle/i }));
      await waitFor(() => {
        expect(screen.getByText('No chronicle data yet.')).toBeInTheDocument();
      });
    });

    it('shows the stopped empty-state when no saved chronicle exists', async () => {
      wrap('test-session-id-1234', {
        session: STOPPED_SESSION,
        volundr: {
          getChronicle: vi.fn().mockResolvedValue(null),
          subscribeChronicle: vi.fn().mockReturnValue(() => {}),
        },
      });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Chronicle/i }));
      await waitFor(() => {
        expect(screen.getByText('No saved chronicle yet.')).toBeInTheDocument();
      });
    });
  });

  describe('action buttons', () => {
    it('shows Stop button for running session', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /Stop/i })).toBeInTheDocument();
    });

    it('shows Archive button for running session', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /Archive session/i })).toBeInTheDocument();
    });

    it('shows Start button for stopped session', async () => {
      wrap('test-session-id-1234', { session: STOPPED_SESSION });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /Start/i })).toBeInTheDocument();
    });

    it('shows Start button for resumable non-archived sessions', async () => {
      wrap('test-session-id-1234', {
        session: {
          ...STOPPED_SESSION,
          status: 'failed',
        },
      });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByRole('button', { name: /^Start session$/i })).toBeInTheDocument();
    });

    it('resumes a failed session from the resumable fallback action', async () => {
      const failedSession: VolundrSession = {
        ...STOPPED_SESSION,
        status: 'failed',
      };
      const service = buildVolundrService(failedSession);
      service.resumeSession = vi.fn().mockResolvedValue(undefined);
      service.getSession = vi.fn().mockResolvedValue(failedSession);
      wrap('test-session-id-1234', { session: failedSession, volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/^Start session$/i));

      await waitFor(() => {
        expect(service.resumeSession).toHaveBeenCalledWith('test-session-id-1234');
      });
    });

    it('shows delete button', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByTitle(/Delete/i)).toBeInTheDocument();
    });

    it('hides session action buttons in read-only mode', async () => {
      wrap('test-session-id-1234', { readOnly: true });
      await screen.findByTestId('live-session-detail-page');
      expect(screen.queryByTitle(/Stop session/i)).not.toBeInTheDocument();
      expect(screen.queryByTitle(/Archive session/i)).not.toBeInTheDocument();
      expect(screen.queryByTitle(/Delete session/i)).not.toBeInTheDocument();
    });

    it('stops a running session when stop is clicked', async () => {
      const service = buildVolundrService();
      service.stopSession = vi.fn().mockResolvedValue(undefined);
      service.getSession = vi.fn().mockResolvedValue(RUNNING_SESSION);
      wrap('test-session-id-1234', { volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Stop session/i));

      await waitFor(() => {
        expect(service.stopSession).toHaveBeenCalledWith('test-session-id-1234');
      });
    });

    it('resumes a stopped session when start is clicked', async () => {
      const service = buildVolundrService(STOPPED_SESSION);
      service.resumeSession = vi.fn().mockResolvedValue(undefined);
      service.getSession = vi.fn().mockResolvedValue(STOPPED_SESSION);
      wrap('test-session-id-1234', { session: STOPPED_SESSION, volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/^Start session$/i));

      await waitFor(() => {
        expect(service.resumeSession).toHaveBeenCalledWith('test-session-id-1234');
      });
    });

    it('archives a live session when archive is clicked', async () => {
      const service = buildVolundrService();
      service.archiveSession = vi.fn().mockResolvedValue(undefined);
      service.getSession = vi.fn().mockResolvedValue(RUNNING_SESSION);
      wrap('test-session-id-1234', { volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Archive session/i));

      await waitFor(() => {
        expect(service.archiveSession).toHaveBeenCalledWith('test-session-id-1234');
      });
    });

    it('restores an archived session when restore is clicked', async () => {
      const service = buildVolundrService(ARCHIVED_SESSION);
      service.restoreSession = vi.fn().mockResolvedValue(undefined);
      service.getSession = vi.fn().mockResolvedValue(ARCHIVED_SESSION);
      wrap('test-session-id-1234', { session: ARCHIVED_SESSION, volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Restore archived session/i));

      await waitFor(() => {
        expect(service.restoreSession).toHaveBeenCalledWith('test-session-id-1234');
      });
    });

    it('uses tooltip-only eye control without rendering res text', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(
        screen.getByRole('button', { name: /Show tool calls and results/i }),
      ).toBeInTheDocument();
      expect(screen.queryByText(/^res$/i)).not.toBeInTheDocument();
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

    it('closes the delete dialog without deleting when cancel is clicked', async () => {
      const service = buildVolundrService();
      service.deleteSession = vi.fn().mockResolvedValue(undefined);
      wrap('test-session-id-1234', { volundr: service });

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Delete session/i));
      expect(screen.getByRole('dialog')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });
      expect(service.deleteSession).not.toHaveBeenCalled();
    });

    it('resets cleanup checkboxes when a different session is rendered', async () => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const service = buildVolundrService();
      const view = render(
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
            <LiveSessionDetailPage sessionId="session-a" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Delete session/i));
      fireEvent.click(screen.getByTestId('cleanup-workspace_storage'));
      expect(screen.getByTestId('cleanup-workspace_storage')).toBeChecked();

      view.rerender(
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
            <LiveSessionDetailPage sessionId="session-b" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByTitle(/Delete session/i));
      expect(screen.getByTestId('cleanup-workspace_storage')).not.toBeChecked();
      expect(screen.getByTestId('cleanup-chronicles')).not.toBeChecked();
    });

    it('resets back to the chat tab when a different session id is rendered', async () => {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const service = buildVolundrService();
      const view = render(
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
            <LiveSessionDetailPage sessionId="session-a" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await screen.findByTestId('live-session-detail-page');
      fireEvent.click(screen.getByRole('tab', { name: /Logs/i }));
      await screen.findByTestId('live-logs-tab');

      view.rerender(
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
            <LiveSessionDetailPage sessionId="session-b" />
          </ServicesProvider>
        </QueryClientProvider>,
      );

      await waitFor(() => {
        expect(screen.getByRole('tab', { name: /Chat/i })).toHaveAttribute('aria-selected', 'true');
      });
      expect(screen.queryByTestId('live-logs-tab')).not.toBeInTheDocument();
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
    it('shows Uptime metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Uptime')).toBeInTheDocument();
    });

    it('shows Msgs metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Msgs')).toBeInTheDocument();
    });

    it('uses the visible chat count for Msgs once chat history is loaded', async () => {
      vi.spyOn(chatHooks, 'useSkuldChat').mockReturnValue({
        messages: [
          {
            id: 'msg-1',
            role: 'user',
            content: 'Hello',
            createdAt: new Date(),
            status: 'done',
          },
          {
            id: 'msg-2',
            role: 'assistant',
            content: 'Hi there',
            createdAt: new Date(),
            status: 'done',
          },
        ],
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

      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      await waitFor(() => {
        expect(screen.getByTestId('session-stats')).toHaveTextContent('Msgs2');
      });
    });

    it('shows Tokens metric', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Tokens')).toBeInTheDocument();
    });

    it('shows the forge badge with the instance name', async () => {
      wrap('test-session-id-1234');
      await screen.findByTestId('live-session-detail-page');
      expect(screen.getByText('Guild Alpha')).toBeInTheDocument();
      expect(screen.getByText('PRIMARY')).toBeInTheDocument();
    });
  });

  it('renders the error state when session loading fails', async () => {
    const service = buildVolundrService();
    service.getSession = vi.fn().mockRejectedValue(new Error('Session load exploded'));

    wrap('test-session-id-1234', { volundr: service });

    await waitFor(() => {
      expect(screen.getByText('Failed to load session')).toBeInTheDocument();
    });
    expect(screen.getByText('Session load exploded')).toBeInTheDocument();
  });
});
