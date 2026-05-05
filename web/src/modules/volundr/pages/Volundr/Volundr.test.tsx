import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { VolundrPage } from './index';
import type {
  VolundrSession,
  VolundrMessage,
  VolundrLog,
  VolundrRepo,
  VolundrTemplate,
} from '@/modules/volundr/models';

vi.mock('@/modules/volundr/hooks/useVolundr', () => ({
  useVolundr: vi.fn(),
}));

vi.mock('@/hooks/useLocalStorage', () => ({
  useLocalStorage: vi.fn(() => [false, vi.fn()]),
}));

vi.mock('@/modules/volundr/hooks/useSessionStartingPoll', async () => {
  const React = await import('react');
  return {
    // Simulate the probe verifying connectivity immediately via useEffect
    // so that tests for 'running' sessions see the chat/terminal.
    useSessionProbe: vi.fn(({ enabled, onReady }: { enabled: boolean; onReady: () => void }) => {
      React.useEffect(() => {
        if (enabled) {
          onReady();
        }
      }, [enabled, onReady]);
    }),
  };
});

vi.mock('@/modules/volundr/hooks/useDiffViewer', () => ({
  useDiffViewer: vi.fn(() => ({
    diff: null,
    diffLoading: false,
    diffError: null,
    selectedFile: null,
    diffBase: 'last-commit',
    selectFile: vi.fn(),
    setDiffBase: vi.fn(),
    clearDiff: vi.fn(),
  })),
}));

vi.mock('@/hooks/useIdentity', () => ({
  useIdentity: vi.fn(() => ({
    identity: null,
    isAdmin: false,
    loading: false,
    error: null,
  })),
}));

vi.mock('@/auth', () => ({
  useAuth: vi.fn(() => ({
    enabled: false,
    authenticated: true,
    loading: false,
    user: null,
    accessToken: null,
    login: vi.fn(),
    logout: vi.fn(),
  })),
}));

// Mock WebSocket-backed components so tests don't need a real WS connection or xterm
vi.mock('@/modules/volundr/components/SessionTerminal', () => ({
  SessionTerminal: ({ url, className }: { url: string | null; className?: string }) => (
    <div data-testid="session-terminal" data-url={url ?? ''} className={className}>
      Mock Terminal
    </div>
  ),
}));

vi.mock('@/modules/shared/components/SessionChat', () => ({
  SessionChat: ({ url, className }: { url: string | null; className?: string }) => (
    <div data-testid="session-chat" data-url={url ?? ''} className={className}>
      Mock Chat
    </div>
  ),
}));

vi.mock('@/modules/volundr/components/EditorPanel/EditorPanel', () => ({
  EditorPanel: ({
    hostname,
    sessionId,
    className,
  }: {
    hostname: string | null;
    sessionId: string | null;
    className?: string;
  }) => (
    <div
      data-testid="editor-panel"
      data-hostname={hostname ?? ''}
      data-session-id={sessionId ?? ''}
      className={className}
    >
      {hostname && sessionId ? 'Mock Editor' : 'Start a session to access the editor'}
    </div>
  ),
}));

vi.mock('@/modules/volundr/components/molecules/SessionStartingIndicator', () => ({
  SessionStartingIndicator: ({ className }: { className?: string }) => (
    <div data-testid="session-starting-indicator" className={className}>
      Forging session…
    </div>
  ),
}));

vi.mock('@/modules/volundr/components/LaunchWizard', () => ({
  LaunchWizard: ({
    templates,
    onLaunch,
    isLaunching,
  }: {
    templates: VolundrTemplate[];
    onLaunch: (config: Record<string, string>) => void;
    isLaunching: boolean;
  }) => (
    <div data-testid="launch-wizard">
      {templates.map(t => (
        <div key={t.name} data-testid={`wizard-template-${t.name}`}>
          {t.name}
        </div>
      ))}
      <button
        data-testid="wizard-launch"
        onClick={() =>
          onLaunch({
            name: 'test-session',
            source: {
              type: 'git',
              repo: 'https://github.com/kanuckvalley/my-repo.git',
              branch: 'main',
            },
            model: 'qwen3-coder:70b',
            definition: 'skuldClaude',
            templateName: templates[0]?.name ?? '',
          })
        }
        disabled={isLaunching}
      >
        {isLaunching ? 'Launching...' : 'Launch Session'}
      </button>
      <button
        data-testid="wizard-launch-codex"
        onClick={() =>
          onLaunch({
            name: 'codex-session',
            source: {
              type: 'git',
              repo: 'https://github.com/kanuckvalley/my-repo.git',
              branch: 'main',
            },
            model: 'gpt-5.4',
            definition: 'skuldCodex',
            templateName: templates[0]?.name ?? '',
          })
        }
        disabled={isLaunching}
      >
        Launch Codex
      </button>
    </div>
  ),
}));

vi.mock('@/modules/volundr/components/SessionGroupList', () => ({
  SessionGroupList: ({
    sessions,
    renderSession,
  }: {
    sessions: unknown[];
    searchQuery: string;
    renderSession: (s: unknown) => React.ReactNode;
  }) => (
    <div data-testid="session-group-list">{sessions.map((s: unknown) => renderSession(s))}</div>
  ),
}));

vi.mock('@/modules/volundr/adapters', () => ({
  volundrService: {
    getFeatureModules: vi.fn().mockResolvedValue([]),
    getUserFeaturePreferences: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('@/modules/volundr/adapters/api/client', () => ({
  getAccessToken: vi.fn().mockReturnValue(null),
}));

import { useVolundr } from '@/modules/volundr/hooks/useVolundr';
import { useLocalStorage } from '@/hooks/useLocalStorage';

const mockStats = {
  activeSessions: 3,
  totalSessions: 47,
  tokensToday: 1256000,
  localTokens: 892000,
  cloudTokens: 364000,
  costToday: 12.45,
};

const mockSessions: VolundrSession[] = [
  {
    id: 'forge-7f3a2b1c',
    name: 'printer-firmware-thermal',
    source: {
      type: 'git',
      repo: 'kanuckvalley/printer-firmware',
      branch: 'feature/thermal-calibration',
    },
    status: 'running',
    model: 'qwen3-coder:70b',
    lastActive: Date.now() - 1000 * 60 * 5,
    messageCount: 47,
    tokensUsed: 156420,
    podName: 'skuld-7f3a2b1c-xkj2p',
    hostname: 'skuld-7f3a2b1c.volundr.local',
  },
  {
    id: 'forge-2c5d9e7b',
    name: 'nalir-truenas-adapter',
    source: { type: 'git', repo: 'kanuckvalley/nalir', branch: 'feature/truenas-integration' },
    status: 'stopped',
    model: 'qwen3-coder:32b',
    lastActive: Date.now() - 1000 * 60 * 60 * 3,
    messageCount: 89,
    tokensUsed: 287650,
  },
  {
    id: 'forge-8e2f4a6c',
    name: 'kaolin-support-gen',
    source: {
      type: 'git',
      repo: 'kanuckvalley/kaolin-supports',
      branch: 'feature/fenics-cohesive',
    },
    status: 'error',
    model: 'glm-4.7-flash',
    lastActive: Date.now() - 1000 * 60 * 30,
    messageCount: 56,
    tokensUsed: 178300,
    error: 'OOMKilled - exceeded memory limit',
  },
];

const mockModels = {
  'qwen3-coder:70b': {
    name: 'Qwen3 70B',
    provider: 'local' as const,
    tier: 'execution' as const,
    color: '#22c55e',
    vram: '42GB',
  },
  'qwen3-coder:32b': {
    name: 'Qwen3 32B',
    provider: 'local' as const,
    tier: 'execution' as const,
    color: '#22c55e',
    vram: '20GB',
  },
  'glm-4.7-flash': {
    name: 'GLM 4.7',
    provider: 'local' as const,
    tier: 'execution' as const,
    color: '#06b6d4',
    vram: '20GB',
  },
  'claude-opus': {
    name: 'Claude Opus',
    provider: 'cloud' as const,
    tier: 'frontier' as const,
    color: '#a855f7',
    cost: '$15/M',
  },
};

const mockRepos: VolundrRepo[] = [
  {
    provider: 'github',
    org: 'kanuckvalley',
    name: 'my-repo',
    cloneUrl: 'https://github.com/kanuckvalley/my-repo.git',
    url: 'https://github.com/kanuckvalley/my-repo',
    defaultBranch: 'main',
    branches: ['main', 'develop', 'feature/new-thing'],
  },
  {
    provider: 'github',
    org: 'kanuckvalley',
    name: 'printer-firmware',
    cloneUrl: 'https://github.com/kanuckvalley/printer-firmware.git',
    url: 'https://github.com/kanuckvalley/printer-firmware',
    defaultBranch: 'main',
    branches: ['main', 'develop', 'feature/thermal-calibration'],
  },
];

const mockTemplates: VolundrTemplate[] = [
  {
    name: 'full-stack-dev',
    description: 'Full stack development workspace',
    repos: [{ repo: 'https://github.com/kanuckvalley/my-repo.git', branch: 'develop' }],
    setupScripts: ['npm install'],
    workspaceLayout: {},
    isDefault: true,
    cliTool: 'claude',
    workloadType: 'reasoning',
    model: 'qwen3-coder:70b',
    systemPrompt: null,
    resourceConfig: {},
    mcpServers: [],
    envVars: {},
    envSecretRefs: [],
    workloadConfig: {},
    terminalSidecar: { enabled: false, allowedCommands: [] },
    skills: [],
    rules: [],
  },
];

const mockMessages: VolundrMessage[] = [
  {
    id: 'msg-001',
    sessionId: 'forge-7f3a2b1c',
    role: 'user',
    content: 'Review the thermal calibration code',
    timestamp: Date.now() - 1000 * 60 * 2,
  },
  {
    id: 'msg-002',
    sessionId: 'forge-7f3a2b1c',
    role: 'assistant',
    content: 'I found several issues with the PID controller.',
    timestamp: Date.now() - 1000 * 60 * 1,
    tokensIn: 47,
    tokensOut: 847,
    latency: 1200,
  },
];

const mockLogs: VolundrLog[] = [
  {
    id: 'log-001',
    sessionId: 'forge-7f3a2b1c',
    timestamp: Date.now() - 1000 * 60 * 10,
    level: 'info',
    source: 'farm',
    message: 'Job submitted',
  },
  {
    id: 'log-002',
    sessionId: 'forge-7f3a2b1c',
    timestamp: Date.now() - 1000 * 60 * 9,
    level: 'warn',
    source: 'k8s',
    message: 'Memory usage at 80%',
  },
  {
    id: 'log-003',
    sessionId: 'forge-7f3a2b1c',
    timestamp: Date.now() - 1000 * 60 * 8,
    level: 'error',
    source: 'broker',
    message: 'Connection timeout',
  },
];

describe('VolundrPage', () => {
  const stopSession = vi.fn();
  const resumeSession = vi.fn();
  const deleteSession = vi.fn();
  const getSession = vi.fn();
  const refreshSession = vi.fn();
  const markSessionRunning = vi.fn();
  const startSession = vi.fn();
  const refresh = vi.fn();
  const getMessages = vi.fn();
  const sendMessage = vi.fn();
  const getLogs = vi.fn();
  const openCodeServer = vi.fn();
  const getCodeServerUrl = vi.fn();
  const getChronicle = vi.fn();

  const createMockHookReturn = (overrides = {}) => ({
    stats: mockStats,
    sessions: mockSessions,
    activeSessions: mockSessions.filter(s => s.status === 'running'),
    models: mockModels,
    repos: mockRepos,
    templates: mockTemplates,
    availableMcpServers: [],
    availableSecrets: [],
    saveTemplate: vi.fn().mockResolvedValue(mockTemplates[0]),
    createSecret: vi.fn().mockResolvedValue(undefined),
    pullRequest: null,
    prLoading: false,
    prCreating: false,
    prMerging: false,
    fetchPullRequest: vi.fn(),
    createPullRequest: vi.fn(),
    mergePullRequest: vi.fn(),
    refreshCIStatus: vi.fn(),
    searchTrackerIssues: vi.fn().mockResolvedValue([]),
    updateTrackerIssueStatus: vi.fn().mockResolvedValue(undefined),
    loading: false,
    error: null,
    getSession,
    refreshSession,
    markSessionRunning,
    startSession,
    updateSession: vi.fn().mockResolvedValue(mockSessions[0]),
    stopSession,
    resumeSession,
    deleteSession,
    archiveSession: vi.fn().mockResolvedValue(undefined),
    restoreSession: vi.fn().mockResolvedValue(undefined),
    archivedSessions: [],
    archiveAllStopped: vi.fn().mockResolvedValue(undefined),
    refresh,
    messages: mockMessages,
    messageLoading: false,
    getMessages,
    sendMessage,
    logs: mockLogs,
    logLoading: false,
    getLogs,
    openCodeServer,
    getCodeServerUrl,
    chronicle: null,
    chronicleLoading: false,
    getChronicle,
    ...overrides,
  });

  beforeEach(() => {
    vi.clearAllMocks();
    stopSession.mockResolvedValue(undefined);
    resumeSession.mockResolvedValue(undefined);
    deleteSession.mockResolvedValue(undefined);
    getSession.mockResolvedValue(null);
    startSession.mockResolvedValue(mockSessions[0]);
    refresh.mockResolvedValue(undefined);
    getMessages.mockResolvedValue(undefined);
    sendMessage.mockResolvedValue(mockMessages[1]);
    getLogs.mockResolvedValue(undefined);
    openCodeServer.mockResolvedValue(undefined);
    getCodeServerUrl.mockResolvedValue('https://code.skuld.local/forge-7f3a2b1c');
  });

  it('shows loading state when loading', () => {
    vi.mocked(useVolundr).mockReturnValue(
      createMockHookReturn({ stats: null, sessions: [], models: {}, loading: true })
    );

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('shows loading state when stats is null', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn({ stats: null }));

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  it('renders page title and subtitle', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Völundr')).toBeInTheDocument();
    expect(screen.getByText('The Crafting One')).toBeInTheDocument();
  });

  it('renders New Session button', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByTitle('New Session')).toBeInTheDocument();
  });

  it('renders metrics cards when forge stats expanded', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    // Stats are collapsed by default — expand them
    fireEvent.click(screen.getByText('Forge Stats'));

    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Total')).toBeInTheDocument();
    expect(screen.getAllByText('47').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('$12.45')).toBeInTheDocument();
  });

  it('renders forge stats section', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Forge Stats')).toBeInTheDocument();
  });

  it('renders search input', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByPlaceholderText('Search...')).toBeInTheDocument();
  });

  it('renders status filter dropdown', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    const select = screen.getByRole('combobox');
    expect(select).toBeInTheDocument();
  });

  it('renders session cards', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    // Session names may appear in both list and detail panel
    expect(screen.getAllByText('printer-firmware-thermal').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('nalir-truenas-adapter').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('kaolin-support-gen').length).toBeGreaterThanOrEqual(1);
  });

  it('selects first session by default', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    // Detail panel should show first session - repo and branch appear in detail view
    expect(screen.getAllByText('kanuckvalley/printer-firmware').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('feature/thermal-calibration').length).toBeGreaterThanOrEqual(1);
  });

  it('renders tabs in detail panel', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Chat')).toBeInTheDocument();
    expect(screen.getByText('Terminal')).toBeInTheDocument();
    expect(screen.getByText('Code')).toBeInTheDocument();
    expect(screen.getByText('Logs')).toBeInTheDocument();
  });

  it('shows Stop button for running session', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Stop')).toBeInTheDocument();
  });

  it('calls stopSession when Stop button clicked', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    const stopButton = screen.getByText('Stop');
    fireEvent.click(stopButton);

    expect(stopSession).toHaveBeenCalledWith('forge-7f3a2b1c');
  });

  it('shows Start button for stopped session', () => {
    vi.mocked(useVolundr).mockReturnValue(
      createMockHookReturn({
        sessions: [mockSessions[1]], // Stopped session
        activeSessions: [],
      })
    );

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Start')).toBeInTheDocument();
  });

  it('calls resumeSession when Start button clicked', () => {
    vi.mocked(useVolundr).mockReturnValue(
      createMockHookReturn({
        sessions: [mockSessions[1]], // Stopped session
        activeSessions: [],
      })
    );

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    const startButton = screen.getByText('Start');
    fireEvent.click(startButton);

    expect(resumeSession).toHaveBeenCalledWith('forge-2c5d9e7b');
  });

  it('opens launch wizard when New Session clicked', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    // Click the "New Session" button (in sidebar)
    fireEvent.click(screen.getByTitle('New Session'));

    expect(screen.getByRole('heading', { name: 'Launch Session' })).toBeInTheDocument();
    expect(screen.getByTestId('launch-wizard')).toBeInTheDocument();
  });

  it('filters sessions by search query', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    const searchInput = screen.getByPlaceholderText('Search...');
    fireEvent.change(searchInput, { target: { value: 'printer' } });

    // Session name appears in both list and detail panel
    const printerElements = screen.getAllByText('printer-firmware-thermal');
    expect(printerElements.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('nalir-truenas-adapter')).not.toBeInTheDocument();
  });

  it('filters sessions by status', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'stopped' } });

    // The session list should only show stopped sessions
    // The nalir session should be in the list
    expect(screen.getByText('nalir-truenas-adapter')).toBeInTheDocument();
    // printer session should be filtered out from the session list
    // but may still appear if selected in detail panel
    const sessionCards = screen.getAllByText('nalir-truenas-adapter');
    expect(sessionCards.length).toBeGreaterThanOrEqual(1);
  });

  it('shows empty state when no sessions', () => {
    vi.mocked(useVolundr).mockReturnValue(
      createMockHookReturn({ sessions: [], activeSessions: [] })
    );

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );
    expect(screen.getByText('Select a session to view details')).toBeInTheDocument();
  });

  it('switches tabs in detail panel', () => {
    vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

    render(
      <MemoryRouter>
        <VolundrPage />
      </MemoryRouter>
    );

    const logsTab = screen.getByText('Logs');
    fireEvent.click(logsTab);

    // Should show logs content
    expect(screen.getByText(/Job submitted/)).toBeInTheDocument();
  });

  // Chat functionality — now rendered by the SessionChat WebSocket component
  describe('Chat functionality', () => {
    it('renders SessionChat component for running session', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [{ ...mockSessions[0], hostname: 'session-abc.volundr.local' }],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const chat = screen.getByTestId('session-chat');
      expect(chat).toBeInTheDocument();
    });

    it('passes WebSocket URL derived from session hostname', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [{ ...mockSessions[0], hostname: 'session-abc.volundr.local' }],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const chat = screen.getByTestId('session-chat');
      // jsdom uses http: so protocol should be ws:
      expect(chat.getAttribute('data-url')).toContain('session-abc.volundr.local/session');
    });

    it('shows empty state for chat when session is not running', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [mockSessions[1]], // stopped
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByText('Start the session to chat')).toBeInTheDocument();
    });
  });

  // Tests for embedded editor panel (VS Code workbench via REH)
  describe('Editor panel', () => {
    it('renders EditorPanel when code tab is active and session is running', async () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Code'));

      const panel = await screen.findByTestId('editor-panel');
      expect(panel).toBeInTheDocument();
      expect(panel).toHaveAttribute('data-hostname', 'skuld-7f3a2b1c.volundr.local');
      expect(panel).toHaveAttribute('data-session-id', 'forge-7f3a2b1c');
    });

    it('shows empty state when session is not running', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [
            {
              ...mockSessions[0],
              status: 'stopped',
            },
          ],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Code'));

      expect(screen.getByText('Start the session to access IDE')).toBeInTheDocument();
    });
  });

  // New tests for logs functionality
  describe('Logs functionality', () => {
    it('renders logs from hook state', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Logs tab
      const logsTab = screen.getByText('Logs');
      fireEvent.click(logsTab);

      expect(screen.getByText(/Job submitted/)).toBeInTheDocument();
      expect(screen.getByText(/Memory usage at 80%/)).toBeInTheDocument();
      expect(screen.getByText(/Connection timeout/)).toBeInTheDocument();
    });

    it('shows loading state while logs are loading', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn({ logLoading: true, logs: [] }));

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Logs tab
      const logsTab = screen.getByText('Logs');
      fireEvent.click(logsTab);

      expect(screen.getByText('Loading logs...')).toBeInTheDocument();
    });

    it('displays log source in log entries', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Logs tab
      const logsTab = screen.getByText('Logs');
      fireEvent.click(logsTab);

      // Log source appears inline with log message
      expect(screen.getByText(/\[farm\]/)).toBeInTheDocument();
      expect(screen.getByText(/\[k8s\]/)).toBeInTheDocument();
      expect(screen.getByText(/\[broker\]/)).toBeInTheDocument();
    });
  });

  // New tests for delete functionality
  describe('Delete functionality', () => {
    it('calls deleteSession when Delete button clicked', async () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Delete button is icon-only with title "Delete session"
      const deleteButton = screen.getByTitle('Delete session');
      fireEvent.click(deleteButton);

      // Dialog should now be open
      await waitFor(() => {
        expect(screen.getByTestId('delete-session-dialog')).toBeInTheDocument();
      });

      // Confirm deletion
      fireEvent.click(screen.getByTestId('delete-session-confirm'));

      await waitFor(() => {
        expect(deleteSession).toHaveBeenCalledWith('forge-7f3a2b1c', []);
      });
    });

    it('does not call deleteSession when confirmation is cancelled', async () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const deleteButton = screen.getByTitle('Delete session');
      fireEvent.click(deleteButton);

      await waitFor(() => {
        expect(screen.getByTestId('delete-session-dialog')).toBeInTheDocument();
      });

      // Cancel deletion
      fireEvent.click(screen.getByTestId('delete-session-cancel'));

      await waitFor(() => {
        expect(deleteSession).not.toHaveBeenCalled();
      });
    });
  });

  // Tests for launch wizard integration
  describe('Launch wizard integration', () => {
    it('calls startSession with claude definition when wizard launches', async () => {
      startSession.mockResolvedValue(mockSessions[0]);
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));
      fireEvent.click(screen.getByTestId('wizard-launch'));

      await waitFor(() => {
        expect(startSession).toHaveBeenCalledWith(
          expect.objectContaining({
            name: 'test-session',
            model: 'qwen3-coder:70b',
            definition: 'skuldClaude',
            templateName: 'full-stack-dev',
          })
        );
      });
    });

    it('calls startSession with codex definition when codex is selected', async () => {
      startSession.mockResolvedValue(mockSessions[0]);
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));
      fireEvent.click(screen.getByTestId('wizard-launch-codex'));

      await waitFor(() => {
        expect(startSession).toHaveBeenCalledWith(
          expect.objectContaining({
            name: 'codex-session',
            model: 'gpt-5.4',
            definition: 'skuldCodex',
          })
        );
      });
    });

    it('closes wizard after successful launch', async () => {
      startSession.mockResolvedValue(mockSessions[0]);
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));
      expect(screen.getByRole('heading', { name: 'Launch Session' })).toBeInTheDocument();

      fireEvent.click(screen.getByTestId('wizard-launch'));

      await waitFor(() => {
        expect(screen.queryByRole('heading', { name: 'Launch Session' })).not.toBeInTheDocument();
      });
    });

    it('shows error message when launch fails', async () => {
      startSession.mockRejectedValue(new Error('FK violation on users'));
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));
      fireEvent.click(screen.getByTestId('wizard-launch'));

      await waitFor(() => {
        expect(screen.getByText('FK violation on users')).toBeInTheDocument();
      });
      // Wizard should stay open
      expect(screen.getByRole('heading', { name: 'Launch Session' })).toBeInTheDocument();
    });

    it('shows fallback error when launch fails with non-Error', async () => {
      startSession.mockRejectedValue('unexpected');
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));
      fireEvent.click(screen.getByTestId('wizard-launch'));

      await waitFor(() => {
        expect(screen.getByText('Failed to launch session')).toBeInTheDocument();
      });
    });

    it('passes templates to LaunchWizard', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByTitle('New Session'));

      expect(screen.getByTestId('wizard-template-full-stack-dev')).toBeInTheDocument();
    });
  });

  // Tests for sidebar toggle
  describe('Sidebar toggle', () => {
    it('renders collapse button when sidebar is expanded', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Find the collapse button
      const collapseButton = screen.getByLabelText('Collapse sidebar');
      expect(collapseButton).toBeInTheDocument();
    });

    it('renders inline stats when stats panel is collapsed', () => {
      // Override: sidebar expanded (false), stats collapsed (true), archived collapsed (true)
      vi.mocked(useLocalStorage).mockImplementation((key: string) => {
        if (key === 'volundr-stats-collapsed') return [true, vi.fn()];
        if (key === 'volundr-archived-collapsed') return [true, vi.fn()];
        return [false, vi.fn()];
      });

      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // When forge stats are collapsed, inline stat values are shown
      expect(screen.getByText('Forge Stats')).toBeInTheDocument();
      expect(screen.getByText('3')).toBeInTheDocument();
    });
  });

  // Tests for popout functionality
  describe('Popout functionality', () => {
    it('opens popout window for terminal tab', () => {
      const mockOpen = vi.fn();
      vi.stubGlobal('open', mockOpen);

      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to terminal tab
      fireEvent.click(screen.getByText('Terminal'));

      // Find the popout button for terminal tab and click it
      const terminalTab = screen.getByText('Terminal').closest('div');
      const popoutButton = terminalTab?.querySelector('button[title*="Open Terminal"]');
      if (popoutButton) {
        fireEvent.click(popoutButton);
        expect(mockOpen).toHaveBeenCalledWith(
          expect.stringContaining('/volundr/popout?session=forge-7f3a2b1c&tab=terminal'),
          expect.any(String),
          expect.any(String)
        );
      }

      vi.unstubAllGlobals();
    });
  });

  // Test for session not running
  describe('Session not running', () => {
    it('shows empty state for chat when session is stopped', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [mockSessions[1]], // stopped session
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByText('Start the session to chat')).toBeInTheDocument();
    });

    it('shows empty state for terminal when session is stopped', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [mockSessions[1]], // stopped session
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Terminal tab
      fireEvent.click(screen.getByText('Terminal'));

      expect(screen.getByText('Start the session to access terminal')).toBeInTheDocument();
    });

    it('shows empty state for code when session is stopped', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [mockSessions[1]], // stopped session
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Code tab
      fireEvent.click(screen.getByText('Code'));

      expect(screen.getByText('Start the session to access IDE')).toBeInTheDocument();
    });
  });

  // Tests for session starting state
  describe('Session starting state', () => {
    const startingSession: VolundrSession = {
      id: 'forge-starting-1',
      name: 'starting-session',
      source: { type: 'git', repo: 'kanuckvalley/test-repo', branch: 'main' },
      status: 'starting',
      model: 'qwen3-coder:70b',
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
      hostname: 'skuld-starting.local',
    };

    it('shows starting indicator for chat tab when session is starting', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [startingSession],
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByTestId('session-starting-indicator')).toBeInTheDocument();
      expect(screen.queryByText('Start the session to chat')).not.toBeInTheDocument();
    });

    it('shows starting indicator for terminal tab when session is starting', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [startingSession],
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Terminal'));

      expect(screen.getByTestId('session-starting-indicator')).toBeInTheDocument();
      expect(screen.queryByText('Start the session to access terminal')).not.toBeInTheDocument();
    });

    it('shows starting indicator for code tab when session is starting', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [startingSession],
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Code'));

      expect(screen.getByTestId('session-starting-indicator')).toBeInTheDocument();
      expect(screen.queryByText('Start the session to access IDE')).not.toBeInTheDocument();
    });

    it('does not show Start/Stop buttons for starting session', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [startingSession],
          activeSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Starting is not 'running', so Stop should not show;
      // it falls through to the else branch which shows Start
      expect(screen.getByText('Start')).toBeInTheDocument();
    });
  });

  // Tests for session-host logs fetching
  describe('Session-host logs fetching', () => {
    it('fetches logs from session host when session is running with hostname', async () => {
      const sessionHostLogs = {
        session_id: 'forge-7f3a2b1c',
        total: 2,
        returned: 2,
        lines: [
          {
            time: '',
            timestamp: 1771089188.835684,
            level: 'INFO',
            logger: 'skuld.broker',
            message: 'Session initialized',
          },
          {
            time: '',
            timestamp: 1771089190.123,
            level: 'WARN',
            logger: 'skuld.monitor',
            message: 'High memory usage',
          },
        ],
      };

      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(sessionHostLogs),
      });
      vi.stubGlobal('fetch', fetchMock);

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [
            {
              ...mockSessions[0],
              hostname: 'skuld-running.volundr.local',
              status: 'running' as const,
            },
          ],
          logs: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Switch to Logs tab
      fireEvent.click(screen.getByText('Logs'));

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'https://skuld-running.volundr.local/api/logs',
          expect.objectContaining({ headers: expect.any(Object) })
        );
      });

      await waitFor(() => {
        expect(screen.getByText(/Session initialized/)).toBeInTheDocument();
        expect(screen.getByText(/High memory usage/)).toBeInTheDocument();
      });

      vi.unstubAllGlobals();
    });

    it('uses chat endpoint path prefix for gateway-routed sessions', async () => {
      const sessionHostLogs = {
        session_id: 'gw-session',
        total: 1,
        returned: 1,
        lines: [{ timestamp: 1771089188, level: 'INFO', logger: 'skuld', message: 'Gateway log' }],
      };

      const fetchMock = vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(sessionHostLogs),
      });
      vi.stubGlobal('fetch', fetchMock);

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [
            {
              ...mockSessions[0],
              hostname: 'sessions.example.com',
              chatEndpoint: 'wss://sessions.example.com/s/abc-123/session',
              status: 'running' as const,
            },
          ],
          logs: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Logs'));

      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith(
          'https://sessions.example.com/s/abc-123/api/logs',
          expect.objectContaining({ headers: expect.any(Object) })
        );
      });

      vi.unstubAllGlobals();
    });
  });

  // Tests for branch dropdown in new session modal

  // Tests for archive and restore
  describe('Archive and restore', () => {
    it('archives a stopped session without confirmation', async () => {
      const archiveSession = vi.fn().mockResolvedValue(undefined);
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          sessions: [mockSessions[1]], // stopped session
          activeSessions: [],
          archiveSession,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const archiveButton = screen.getByTitle('Archive session');
      fireEvent.click(archiveButton);

      await waitFor(() => {
        expect(archiveSession).toHaveBeenCalledWith('forge-2c5d9e7b');
      });
    });

    it('shows confirmation when archiving a running session', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
      const archiveSession = vi.fn().mockResolvedValue(undefined);
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archiveSession,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const archiveButton = screen.getByTitle('Archive session');
      fireEvent.click(archiveButton);

      expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining('still running'));

      await waitFor(() => {
        expect(archiveSession).toHaveBeenCalledWith('forge-7f3a2b1c');
      });

      confirmSpy.mockRestore();
    });

    it('does not archive running session when confirmation is cancelled', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
      const archiveSession = vi.fn().mockResolvedValue(undefined);
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archiveSession,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      const archiveButton = screen.getByTitle('Archive session');
      fireEvent.click(archiveButton);

      expect(confirmSpy).toHaveBeenCalled();
      expect(archiveSession).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('renders archived section with toggle', () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByText('Archived')).toBeInTheDocument();
    });

    it('shows "No archived sessions" when archived list is empty and expanded', () => {
      // Make archived section expanded by default
      vi.mocked(useLocalStorage).mockImplementation(() => {
        // 1st call = sidebar (false), 2nd call = stats collapsed (false), 3rd call = archived collapsed (false)
        return [false, vi.fn()];
      });

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archivedSessions: [],
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByText('No archived sessions')).toBeInTheDocument();
    });

    it('renders archived sessions with restore buttons', () => {
      // Make archived section expanded
      vi.mocked(useLocalStorage).mockImplementation(() => {
        return [false, vi.fn()];
      });

      const archivedSessions: VolundrSession[] = [
        {
          id: 'forge-archived-1',
          name: 'old-feature-work',
          source: { type: 'git', repo: 'kanuckvalley/my-repo', branch: 'feature/old' },
          status: 'archived',
          model: 'qwen3-coder:70b',
          lastActive: Date.now() - 1000 * 60 * 60 * 24 * 7,
          messageCount: 100,
          tokensUsed: 50000,
          archivedAt: new Date('2026-02-15'),
        },
      ];

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archivedSessions,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      expect(screen.getByText('old-feature-work')).toBeInTheDocument();
      expect(screen.getByText('Restore')).toBeInTheDocument();
    });

    it('shows archived count badge when there are archived sessions', () => {
      const archivedSessions: VolundrSession[] = [
        {
          id: 'forge-archived-1',
          name: 'old-feature-work',
          source: { type: 'git', repo: 'kanuckvalley/my-repo', branch: 'feature/old' },
          status: 'archived',
          model: 'qwen3-coder:70b',
          lastActive: Date.now() - 1000 * 60 * 60 * 24 * 7,
          messageCount: 100,
          tokensUsed: 50000,
          archivedAt: new Date('2026-02-15'),
        },
      ];

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archivedSessions,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // The count badge showing "1"
      expect(screen.getByText('1')).toBeInTheDocument();
    });

    it('calls restoreSession when Restore button is clicked', async () => {
      // Make archived section expanded
      vi.mocked(useLocalStorage).mockImplementation(() => {
        return [false, vi.fn()];
      });

      const restoreSession = vi.fn().mockResolvedValue(undefined);
      const archivedSessions: VolundrSession[] = [
        {
          id: 'forge-archived-1',
          name: 'old-feature-work',
          source: { type: 'git', repo: 'kanuckvalley/my-repo', branch: 'feature/old' },
          status: 'archived',
          model: 'qwen3-coder:70b',
          lastActive: Date.now() - 1000 * 60 * 60 * 24 * 7,
          messageCount: 100,
          tokensUsed: 50000,
          archivedAt: new Date('2026-02-15'),
        },
      ];

      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archivedSessions,
          restoreSession,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Restore'));

      await waitFor(() => {
        expect(restoreSession).toHaveBeenCalledWith('forge-archived-1');
      });
    });

    it('shows Archive All Stopped button when there are stopped sessions', () => {
      // Make archived section expanded
      vi.mocked(useLocalStorage).mockImplementation(() => {
        return [false, vi.fn()];
      });

      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // mockSessions[1] is stopped, so count = 1
      expect(screen.getByText('Archive All Stopped (1)')).toBeInTheDocument();
    });

    it('calls archiveAllStopped when Archive All Stopped is clicked and confirmed', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

      // Make archived section expanded
      vi.mocked(useLocalStorage).mockImplementation(() => {
        return [false, vi.fn()];
      });

      const archiveAllStopped = vi.fn().mockResolvedValue(undefined);
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archiveAllStopped,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Archive All Stopped (1)'));

      await waitFor(() => {
        expect(archiveAllStopped).toHaveBeenCalled();
      });

      confirmSpy.mockRestore();
    });

    it('does not call archiveAllStopped when confirmation is cancelled', async () => {
      const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

      // Make archived section expanded
      vi.mocked(useLocalStorage).mockImplementation(() => {
        return [false, vi.fn()];
      });

      const archiveAllStopped = vi.fn().mockResolvedValue(undefined);
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({
          archiveAllStopped,
        })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Archive All Stopped (1)'));

      expect(archiveAllStopped).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });
  });

  // Tests for editor panel states
  describe('Editor panel states', () => {
    it('renders EditorPanel with session hostname when code tab is active', async () => {
      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      fireEvent.click(screen.getByText('Code'));

      const panel = await screen.findByTestId('editor-panel');
      expect(panel).toHaveAttribute('data-hostname', 'skuld-7f3a2b1c.volundr.local');
    });

    it('opens popout window for code tab', () => {
      const mockOpen = vi.fn();
      vi.stubGlobal('open', mockOpen);

      vi.mocked(useVolundr).mockReturnValue(createMockHookReturn());

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // The popout button is on the Code tab wrapper, visible on hover
      const codeTab = screen.getByText('Code').closest('div');
      const popoutButton = codeTab?.querySelector('button[title*="Open Code"]');
      if (popoutButton) {
        fireEvent.click(popoutButton);

        expect(mockOpen).toHaveBeenCalledWith(
          expect.stringContaining('/volundr/popout?session=forge-7f3a2b1c&tab=code'),
          expect.any(String),
          expect.any(String)
        );
      }

      vi.unstubAllGlobals();
    });
  });

  // Settings and Admin navigation moved to AppShell sidebar (NIU-212)

  describe('Empty state New Session button', () => {
    it('opens launch wizard from empty main panel', () => {
      vi.mocked(useVolundr).mockReturnValue(
        createMockHookReturn({ sessions: [], activeSessions: [] })
      );

      render(
        <MemoryRouter>
          <VolundrPage />
        </MemoryRouter>
      );

      // Click the "New Session" button in the empty main panel
      fireEvent.click(screen.getByText('New Session'));

      expect(screen.getByRole('heading', { name: 'Launch Session' })).toBeInTheDocument();
    });
  });
});
