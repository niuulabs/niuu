import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ServicesProvider } from '@niuulabs/plugin-sdk';
import { LiveSessionDetailPage } from './LiveSessionDetailPage';
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
  model: 'claude-sonnet',
  lastActive: Date.now() - 60_000,
  messageCount: 10,
  tokensUsed: 5000,
  hostname: 'skuld-test.local',
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
    getModels: vi.fn().mockResolvedValue({
      'claude-sonnet': {
        name: 'Claude Sonnet',
        provider: 'cloud',
        tier: 'balanced',
        color: '#f59e0b',
        cost: '$3/MTok',
      },
    }),
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
  opts: { readOnly?: boolean; session?: VolundrSession | null } = {},
) {
  const session = opts.session === undefined ? RUNNING_SESSION : opts.session;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ServicesProvider
        services={{
          volundr: buildVolundrService(session),
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
      expect(screen.getByText('Claude Sonnet')).toBeInTheDocument();
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
  });
});
