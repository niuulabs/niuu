import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useVolundr } from './useVolundr';
import { volundrService } from '@/modules/volundr/adapters';
import type {
  VolundrSession,
  VolundrStats,
  VolundrModel,
  VolundrMessage,
  VolundrLog,
  SessionChronicle,
  PullRequest,
  MergeResult,
  VolundrTemplate,
  VolundrPreset,
  TrackerIssue,
} from '@/modules/volundr/models';

vi.mock('@/modules/volundr/adapters', () => ({
  volundrService: {
    getStats: vi.fn(),
    getSessions: vi.fn(),
    getSession: vi.fn(),
    getModels: vi.fn(),
    getRepos: vi.fn(),
    getTemplates: vi.fn(),
    getAvailableMcpServers: vi.fn(),
    getAvailableSecrets: vi.fn(),
    subscribe: vi.fn(() => vi.fn()),
    subscribeStats: vi.fn(() => vi.fn()),
    startSession: vi.fn(),
    updateSession: vi.fn(),
    stopSession: vi.fn(),
    resumeSession: vi.fn(),
    deleteSession: vi.fn(),
    connectSession: vi.fn(),
    archiveSession: vi.fn(),
    archiveStoppedSessions: vi.fn(),
    restoreSession: vi.fn(),
    listArchivedSessions: vi.fn(),
    getMessages: vi.fn(),
    sendMessage: vi.fn(),
    getLogs: vi.fn(),
    getCodeServerUrl: vi.fn(),
    getChronicle: vi.fn(),
    subscribeChronicle: vi.fn(() => vi.fn()),
    getPullRequests: vi.fn(),
    createPullRequest: vi.fn(),
    mergePullRequest: vi.fn(),
    getCIStatus: vi.fn(),
    searchTrackerIssues: vi.fn(),
    updateTrackerIssueStatus: vi.fn(),
    getPresets: vi.fn(),
    getPreset: vi.fn(),
    savePreset: vi.fn(),
    deletePreset: vi.fn(),
    saveTemplate: vi.fn(),
    createSecret: vi.fn(),
  },
}));

const mockStats: VolundrStats = {
  activeSessions: 2,
  totalSessions: 15,
  tokensToday: 125000,
  localTokens: 80000,
  cloudTokens: 45000,
  costToday: 2.34,
};

const mockSessions: VolundrSession[] = [
  {
    id: 'session-001',
    name: 'API Refactor',
    source: { type: 'git', repo: 'odin-core', branch: 'feature/api-v2' },
    status: 'running',
    model: 'claude-sonnet',
    lastActive: Date.now(),
    messageCount: 45,
    tokensUsed: 12000,
    podName: 'volundr-001',
  },
  {
    id: 'session-002',
    name: 'Documentation',
    source: { type: 'git', repo: 'odin-docs', branch: 'main' },
    status: 'stopped',
    model: 'llama-70b',
    lastActive: Date.now() - 3600000,
    messageCount: 23,
    tokensUsed: 8000,
  },
];

const mockModels: Record<string, VolundrModel> = {
  'claude-sonnet': {
    name: 'Claude 3.5 Sonnet',
    provider: 'cloud',
    tier: 'balanced',
    color: 'amber',
    cost: '$3/MTok',
  },
  'llama-70b': {
    name: 'Llama 3.1 70B',
    provider: 'local',
    tier: 'reasoning',
    color: 'purple',
    vram: '40GB',
  },
};

describe('useVolundr', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(volundrService.getStats).mockResolvedValue(mockStats);
    vi.mocked(volundrService.getSessions).mockResolvedValue(mockSessions);
    vi.mocked(volundrService.getModels).mockResolvedValue(mockModels);
    vi.mocked(volundrService.getRepos).mockResolvedValue([]);
    vi.mocked(volundrService.getTemplates).mockResolvedValue([]);
    vi.mocked(volundrService.getAvailableMcpServers).mockResolvedValue([]);
    vi.mocked(volundrService.getAvailableSecrets).mockResolvedValue([]);
    vi.mocked(volundrService.getPresets).mockResolvedValue([]);
    vi.mocked(volundrService.listArchivedSessions).mockResolvedValue([]);
    vi.mocked(volundrService.archiveStoppedSessions).mockResolvedValue([]);
    vi.mocked(volundrService.getSession).mockResolvedValue(mockSessions[0]);
  });

  it('should fetch stats, sessions, and models on mount', async () => {
    const { result } = renderHook(() => useVolundr());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.stats).toEqual(mockStats);
    expect(result.current.sessions).toEqual(mockSessions);
    expect(result.current.models).toEqual(mockModels);
    expect(result.current.error).toBeNull();
  });

  it('should filter active sessions', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.activeSessions).toHaveLength(1);
    expect(result.current.activeSessions[0].id).toBe('session-001');
  });

  it('should handle fetch error', async () => {
    vi.mocked(volundrService.getStats).mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error?.message).toBe('Network error');
  });

  it('should subscribe to updates', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(volundrService.subscribe).toHaveBeenCalled();
  });

  it('should get a single session', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const session = await result.current.getSession('session-001');
    expect(session).toEqual(mockSessions[0]);
    expect(volundrService.getSession).toHaveBeenCalledWith('session-001');
  });

  it('should start a new session', async () => {
    const newSession: VolundrSession = {
      id: 'session-003',
      name: 'New Task',
      source: { type: 'git', repo: 'odin-core', branch: 'main' },
      status: 'running',
      model: 'claude-sonnet',
      lastActive: Date.now(),
      messageCount: 0,
      tokensUsed: 0,
    };

    vi.mocked(volundrService.startSession).mockResolvedValue(newSession);

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let returnedSession: VolundrSession | undefined;
    await act(async () => {
      returnedSession = await result.current.startSession({
        name: 'New Task',
        source: { type: 'git', repo: 'odin-core', branch: 'main' },
        model: 'claude-sonnet',
      });
    });

    expect(volundrService.startSession).toHaveBeenCalledWith({
      name: 'New Task',
      source: { type: 'git', repo: 'odin-core', branch: 'main' },
      model: 'claude-sonnet',
    });
    expect(returnedSession).toEqual(newSession);
  });

  it('should stop a session', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.stopSession('session-001');
    });

    expect(volundrService.stopSession).toHaveBeenCalledWith('session-001');
    expect(result.current.sessions[0].status).toBe('stopped');
  });

  it('should resume a session', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.resumeSession('session-002');
    });

    expect(volundrService.resumeSession).toHaveBeenCalledWith('session-002');
    expect(result.current.sessions[1].status).toBe('starting');
  });

  it('should refresh data', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(volundrService.getStats).toHaveBeenCalledTimes(2);
  });

  it('should delete a session', async () => {
    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    await act(async () => {
      await result.current.deleteSession('session-001');
    });

    expect(volundrService.deleteSession).toHaveBeenCalledWith('session-001', undefined);
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.sessions.find(s => s.id === 'session-001')).toBeUndefined();
  });

  describe('messages', () => {
    const mockMessages: VolundrMessage[] = [
      {
        id: 'msg-001',
        sessionId: 'session-001',
        role: 'user',
        content: 'Test message',
        timestamp: Date.now(),
      },
      {
        id: 'msg-002',
        sessionId: 'session-001',
        role: 'assistant',
        content: 'Response',
        timestamp: Date.now(),
        tokensIn: 10,
        tokensOut: 20,
        latency: 100,
      },
    ];

    beforeEach(() => {
      vi.mocked(volundrService.getMessages).mockResolvedValue(mockMessages);
      vi.mocked(volundrService.sendMessage).mockResolvedValue(mockMessages[1]);
    });

    it('should get messages for a session', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getMessages('session-001');
      });

      expect(volundrService.getMessages).toHaveBeenCalledWith('session-001');
      expect(result.current.messages).toEqual(mockMessages);
    });

    it('should set messageLoading while fetching messages', async () => {
      let resolvePromise: (value: VolundrMessage[]) => void;
      vi.mocked(volundrService.getMessages).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.getMessages('session-001');
      });

      expect(result.current.messageLoading).toBe(true);

      await act(async () => {
        resolvePromise!(mockMessages);
      });

      await waitFor(() => {
        expect(result.current.messageLoading).toBe(false);
      });
    });

    it('should send a message and update messages', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.sendMessage('session-001', 'Hello');
      });

      expect(volundrService.sendMessage).toHaveBeenCalledWith('session-001', 'Hello');
      // After sending, it should refresh messages
      expect(volundrService.getMessages).toHaveBeenCalledWith('session-001');
    });
  });

  describe('logs', () => {
    const mockLogs: VolundrLog[] = [
      {
        id: 'log-001',
        sessionId: 'session-001',
        timestamp: Date.now(),
        level: 'info',
        source: 'broker',
        message: 'Session started',
      },
      {
        id: 'log-002',
        sessionId: 'session-001',
        timestamp: Date.now(),
        level: 'warn',
        source: 'k8s',
        message: 'Memory usage high',
      },
    ];

    beforeEach(() => {
      vi.mocked(volundrService.getLogs).mockResolvedValue(mockLogs);
    });

    it('should get logs for a session', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getLogs('session-001');
      });

      expect(volundrService.getLogs).toHaveBeenCalledWith('session-001');
      expect(result.current.logs).toEqual(mockLogs);
    });

    it('should set logLoading while fetching logs', async () => {
      let resolvePromise: (value: VolundrLog[]) => void;
      vi.mocked(volundrService.getLogs).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.getLogs('session-001');
      });

      expect(result.current.logLoading).toBe(true);

      await act(async () => {
        resolvePromise!(mockLogs);
      });

      await waitFor(() => {
        expect(result.current.logLoading).toBe(false);
      });
    });
  });

  describe('openCodeServer', () => {
    beforeEach(() => {
      vi.mocked(volundrService.getCodeServerUrl).mockResolvedValue(
        'https://code.example.com/session-001'
      );
    });

    it('should call getCodeServerUrl and open in new window', async () => {
      const mockOpen = vi.fn();
      vi.stubGlobal('open', mockOpen);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.openCodeServer('session-001');
      });

      expect(volundrService.getCodeServerUrl).toHaveBeenCalledWith('session-001');
      expect(mockOpen).toHaveBeenCalledWith('https://code.example.com/session-001', '_blank');

      vi.unstubAllGlobals();
    });

    it('should not open window if URL is null', async () => {
      vi.mocked(volundrService.getCodeServerUrl).mockResolvedValue(null);

      const mockOpen = vi.fn();
      vi.stubGlobal('open', mockOpen);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.openCodeServer('session-001');
      });

      expect(mockOpen).not.toHaveBeenCalled();

      vi.unstubAllGlobals();
    });
  });

  it('should handle non-Error rejection', async () => {
    vi.mocked(volundrService.getStats).mockRejectedValue('string error');

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.error?.message).toBe('Failed to fetch Völundr data');
  });

  it('should update sessions from subscriber', async () => {
    let subscriberCallback: (sessions: VolundrSession[]) => void = () => {};
    vi.mocked(volundrService.subscribe).mockImplementation(cb => {
      subscriberCallback = cb;
      return vi.fn();
    });

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const updatedSessions = [{ ...mockSessions[0], status: 'stopped' as const }];

    act(() => {
      subscriberCallback(updatedSessions);
    });

    expect(result.current.sessions[0].status).toBe('stopped');
  });

  describe('connectSession', () => {
    it('should connect a session and add to list', async () => {
      const manualSession: VolundrSession = {
        id: 'manual-abc',
        name: 'My Skuld',
        source: { type: 'git', repo: '', branch: '' },
        status: 'running',
        model: 'external',
        lastActive: Date.now(),
        messageCount: 0,
        tokensUsed: 0,
        origin: 'manual',
        hostname: 'skuld-01.local',
      };

      vi.mocked(volundrService.connectSession).mockResolvedValue(manualSession);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      let returnedSession: VolundrSession | undefined;
      await act(async () => {
        returnedSession = await result.current.connectSession({
          name: 'My Skuld',
          hostname: 'skuld-01.local',
        });
      });

      expect(volundrService.connectSession).toHaveBeenCalledWith({
        name: 'My Skuld',
        hostname: 'skuld-01.local',
      });
      expect(returnedSession).toEqual(manualSession);
    });
  });

  describe('refreshSession', () => {
    it('should update session in list when getSession returns a session', async () => {
      const updatedSession = { ...mockSessions[0], messageCount: 99 };
      vi.mocked(volundrService.getSession).mockResolvedValue(updatedSession);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.refreshSession('session-001');
      });

      expect(volundrService.getSession).toHaveBeenCalledWith('session-001');
      expect(result.current.sessions[0].messageCount).toBe(99);
    });

    it('should do nothing when getSession returns null', async () => {
      vi.mocked(volundrService.getSession).mockResolvedValue(null);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.refreshSession('session-nonexistent');
      });

      // Sessions remain unchanged
      expect(result.current.sessions).toEqual(mockSessions);
    });
  });

  describe('markSessionRunning', () => {
    it('should set session status to running', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // session-002 starts as 'stopped'
      expect(result.current.sessions[1].status).toBe('stopped');

      act(() => {
        result.current.markSessionRunning('session-002');
      });

      expect(result.current.sessions[1].status).toBe('running');
    });
  });

  it('should update stats from subscribeStats callback', async () => {
    let statsCallback: (stats: VolundrStats) => void = () => {};
    vi.mocked(volundrService.subscribeStats).mockImplementation(cb => {
      statsCallback = cb;
      return vi.fn();
    });

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const newStats: VolundrStats = {
      ...mockStats,
      activeSessions: 5,
      tokensToday: 999999,
    };

    act(() => {
      statsCallback(newStats);
    });

    expect(result.current.stats?.activeSessions).toBe(5);
    expect(result.current.stats?.tokensToday).toBe(999999);
  });

  it('should recompute stats from session subscriber when stats is null', async () => {
    let subscriberCallback: (sessions: VolundrSession[]) => void = () => {};
    vi.mocked(volundrService.subscribe).mockImplementation(cb => {
      subscriberCallback = cb;
      return vi.fn();
    });
    // Make initial stats fetch fail so stats is null
    vi.mocked(volundrService.getStats).mockResolvedValue(null as unknown as VolundrStats);

    const { result } = renderHook(() => useVolundr());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    act(() => {
      subscriberCallback(mockSessions);
    });

    // Stats should remain null since prev was null
    expect(result.current.stats).toBeNull();
  });

  describe('getCodeServerUrl', () => {
    it('should return URL from service', async () => {
      vi.mocked(volundrService.getCodeServerUrl).mockResolvedValue('https://skuld.local/');

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      const url = await result.current.getCodeServerUrl('session-001');
      expect(url).toBe('https://skuld.local/');
      expect(volundrService.getCodeServerUrl).toHaveBeenCalledWith('session-001');
    });
  });

  describe('chronicle', () => {
    const mockChronicle: SessionChronicle = {
      events: [{ t: 0, type: 'session', label: 'Session started' }],
      files: [{ path: 'src/app.ts', status: 'new', ins: 50, del: 0 }],
      commits: [],
      tokenBurn: [5, 10],
    };

    beforeEach(() => {
      vi.mocked(volundrService.getChronicle).mockResolvedValue(mockChronicle);
      vi.mocked(volundrService.subscribeChronicle).mockReturnValue(vi.fn());
    });

    it('should fetch chronicle and subscribe to updates', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getChronicle('session-001');
      });

      expect(volundrService.getChronicle).toHaveBeenCalledWith('session-001');
      expect(result.current.chronicle).toEqual(mockChronicle);
      expect(volundrService.subscribeChronicle).toHaveBeenCalledWith(
        'session-001',
        expect.any(Function)
      );
    });

    it('should set chronicleLoading while fetching', async () => {
      let resolvePromise: (value: SessionChronicle | null) => void;
      vi.mocked(volundrService.getChronicle).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.getChronicle('session-001');
      });

      expect(result.current.chronicleLoading).toBe(true);

      await act(async () => {
        resolvePromise!(mockChronicle);
      });

      await waitFor(() => {
        expect(result.current.chronicleLoading).toBe(false);
      });
    });

    it('should merge SSE updates into existing chronicle', async () => {
      let sseCallback: (chronicle: SessionChronicle) => void = () => {};
      vi.mocked(volundrService.subscribeChronicle).mockImplementation((_id, cb) => {
        sseCallback = cb;
        return vi.fn();
      });

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getChronicle('session-001');
      });

      // Simulate SSE chronicle update
      const update: SessionChronicle = {
        events: [{ t: 30, type: 'file', label: 'src/new.ts', action: 'created', ins: 10, del: 0 }],
        files: [
          { path: 'src/app.ts', status: 'mod', ins: 55, del: 2 },
          { path: 'src/new.ts', status: 'new', ins: 10, del: 0 },
        ],
        commits: [],
        tokenBurn: [5, 10, 3],
      };

      act(() => {
        sseCallback(update);
      });

      // Events should be appended
      expect(result.current.chronicle!.events).toHaveLength(2);
      expect(result.current.chronicle!.events[1].label).toBe('src/new.ts');
      // Files, commits, tokenBurn should be replaced
      expect(result.current.chronicle!.files).toHaveLength(2);
      expect(result.current.chronicle!.tokenBurn).toEqual([5, 10, 3]);
    });

    it('should use update directly when prev chronicle is null', async () => {
      vi.mocked(volundrService.getChronicle).mockResolvedValue(null);

      let sseCallback: (chronicle: SessionChronicle) => void = () => {};
      vi.mocked(volundrService.subscribeChronicle).mockImplementation((_id, cb) => {
        sseCallback = cb;
        return vi.fn();
      });

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getChronicle('session-001');
      });

      expect(result.current.chronicle).toBeNull();

      const update: SessionChronicle = {
        events: [{ t: 0, type: 'session', label: 'Started' }],
        files: [],
        commits: [],
        tokenBurn: [1],
      };

      act(() => {
        sseCallback(update);
      });

      expect(result.current.chronicle).toEqual(update);
    });

    it('should tear down previous subscription when switching sessions', async () => {
      const unsub1 = vi.fn();
      const unsub2 = vi.fn();
      let callCount = 0;
      vi.mocked(volundrService.subscribeChronicle).mockImplementation(() => {
        callCount++;
        return callCount === 1 ? unsub1 : unsub2;
      });

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getChronicle('session-001');
      });

      expect(unsub1).not.toHaveBeenCalled();

      await act(async () => {
        await result.current.getChronicle('session-002');
      });

      expect(unsub1).toHaveBeenCalled();
    });

    it('should clean up subscription on unmount', async () => {
      const unsub = vi.fn();
      vi.mocked(volundrService.subscribeChronicle).mockReturnValue(unsub);

      const { result, unmount } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.getChronicle('session-001');
      });

      unmount();

      expect(unsub).toHaveBeenCalled();
    });
  });

  describe('pull requests', () => {
    const mockPR: PullRequest = {
      number: 42,
      title: 'Add login feature',
      url: 'https://github.com/org/repo/pull/42',
      repoUrl: 'https://github.com/org/repo',
      provider: 'github',
      sourceBranch: 'feature/login',
      targetBranch: 'main',
      status: 'open',
      ciStatus: 'passed',
    };

    beforeEach(() => {
      vi.mocked(volundrService.getPullRequests).mockResolvedValue([mockPR]);
      vi.mocked(volundrService.createPullRequest).mockResolvedValue(mockPR);
      vi.mocked(volundrService.mergePullRequest).mockResolvedValue({ merged: true });
      vi.mocked(volundrService.getCIStatus).mockResolvedValue('passed');
    });

    it('should fetch pull request matching branch', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.fetchPullRequest('https://github.com/org/repo', 'feature/login');
      });

      expect(volundrService.getPullRequests).toHaveBeenCalledWith(
        'https://github.com/org/repo',
        'open'
      );
      expect(result.current.pullRequest).toEqual(mockPR);
      expect(result.current.prLoading).toBe(false);
    });

    it('should set pullRequest to null when no matching branch found', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.fetchPullRequest('https://github.com/org/repo', 'nonexistent-branch');
      });

      expect(result.current.pullRequest).toBeNull();
    });

    it('should set pullRequest to null on fetch error', async () => {
      vi.mocked(volundrService.getPullRequests).mockRejectedValue(new Error('Network error'));

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.fetchPullRequest('https://github.com/org/repo', 'feature/login');
      });

      expect(result.current.pullRequest).toBeNull();
    });

    it('should set prLoading while fetching PR', async () => {
      let resolvePromise: (value: PullRequest[]) => void;
      vi.mocked(volundrService.getPullRequests).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.fetchPullRequest('https://github.com/org/repo', 'feature/login');
      });

      expect(result.current.prLoading).toBe(true);

      await act(async () => {
        resolvePromise!([mockPR]);
      });

      await waitFor(() => {
        expect(result.current.prLoading).toBe(false);
      });
    });

    it('should create a pull request', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.createPullRequest('session-001', 'My PR', 'main');
      });

      expect(volundrService.createPullRequest).toHaveBeenCalledWith('session-001', 'My PR', 'main');
      expect(result.current.pullRequest).toEqual(mockPR);
      expect(result.current.prCreating).toBe(false);
    });

    it('should set prCreating while creating PR', async () => {
      let resolvePromise: (value: PullRequest) => void;
      vi.mocked(volundrService.createPullRequest).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.createPullRequest('session-001', 'My PR');
      });

      expect(result.current.prCreating).toBe(true);

      await act(async () => {
        resolvePromise!(mockPR);
      });

      await waitFor(() => {
        expect(result.current.prCreating).toBe(false);
      });
    });

    it('should merge a pull request', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // First set a PR
      await act(async () => {
        await result.current.fetchPullRequest('https://github.com/org/repo', 'feature/login');
      });

      await act(async () => {
        await result.current.mergePullRequest(42, 'https://github.com/org/repo');
      });

      expect(volundrService.mergePullRequest).toHaveBeenCalledWith(
        42,
        'https://github.com/org/repo'
      );
      expect(result.current.pullRequest?.status).toBe('merged');
      expect(result.current.prMerging).toBe(false);
    });

    it('should set prMerging while merging PR', async () => {
      let resolvePromise: (value: MergeResult) => void;
      vi.mocked(volundrService.mergePullRequest).mockReturnValue(
        new Promise(resolve => {
          resolvePromise = resolve;
        })
      );

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      act(() => {
        result.current.mergePullRequest(42, 'https://github.com/org/repo');
      });

      expect(result.current.prMerging).toBe(true);

      await act(async () => {
        resolvePromise!({ merged: true });
      });

      await waitFor(() => {
        expect(result.current.prMerging).toBe(false);
      });
    });

    it('should refresh CI status', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // First set a PR
      await act(async () => {
        await result.current.fetchPullRequest('https://github.com/org/repo', 'feature/login');
      });

      vi.mocked(volundrService.getCIStatus).mockResolvedValue('failed');

      await act(async () => {
        await result.current.refreshCIStatus(42, 'https://github.com/org/repo', 'feature/login');
      });

      expect(volundrService.getCIStatus).toHaveBeenCalledWith(
        42,
        'https://github.com/org/repo',
        'feature/login'
      );
      expect(result.current.pullRequest?.ciStatus).toBe('failed');
    });

    it('should silently ignore CI status fetch failures', async () => {
      vi.mocked(volundrService.getCIStatus).mockRejectedValue(new Error('Network error'));

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // Should not throw
      await act(async () => {
        await result.current.refreshCIStatus(42, 'https://github.com/org/repo', 'feature/login');
      });

      // pullRequest remains null since we didn't set one
      expect(result.current.pullRequest).toBeNull();
    });

    it('should handle merge when pullRequest is null', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // Don't set a PR first, merge should still call service
      await act(async () => {
        await result.current.mergePullRequest(42, 'https://github.com/org/repo');
      });

      expect(volundrService.mergePullRequest).toHaveBeenCalled();
      // pullRequest should remain null since prev was null
      expect(result.current.pullRequest).toBeNull();
    });

    it('should handle CI refresh when pullRequest is null', async () => {
      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.refreshCIStatus(42, 'https://github.com/org/repo', 'main');
      });

      // Should not crash, pullRequest stays null
      expect(result.current.pullRequest).toBeNull();
    });
  });

  describe('archive and restore', () => {
    const mockArchivedSession: VolundrSession = {
      id: 'archived-001',
      name: 'Old Session',
      source: { type: 'git', repo: 'odin-core', branch: 'feature/old' },
      status: 'archived',
      model: 'claude-sonnet',
      lastActive: Date.now() - 86400000,
      messageCount: 100,
      tokensUsed: 50000,
      archivedAt: new Date(Date.now() - 86400000),
    };

    it('should archive a session and remove it from active list', async () => {
      vi.mocked(volundrService.listArchivedSessions).mockResolvedValue([mockArchivedSession]);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.sessions).toHaveLength(2);

      await act(async () => {
        await result.current.archiveSession('session-001');
      });

      expect(volundrService.archiveSession).toHaveBeenCalledWith('session-001');
      expect(result.current.sessions).toHaveLength(1);
      expect(result.current.sessions.find(s => s.id === 'session-001')).toBeUndefined();
      expect(result.current.archivedSessions).toHaveLength(1);
    });

    it('should restore a session and refresh lists', async () => {
      vi.mocked(volundrService.listArchivedSessions)
        .mockResolvedValueOnce([mockArchivedSession])
        .mockResolvedValueOnce([]);
      vi.mocked(volundrService.getSessions).mockResolvedValue([
        ...mockSessions,
        { ...mockArchivedSession, status: 'stopped', archivedAt: undefined },
      ]);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.restoreSession('archived-001');
      });

      expect(volundrService.restoreSession).toHaveBeenCalledWith('archived-001');
    });

    it('should archive a selected batch of sessions and refresh the archive list', async () => {
      vi.mocked(volundrService.listArchivedSessions).mockResolvedValue([
        { ...mockSessions[1], status: 'archived', archivedAt: new Date() },
      ]);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.archiveSessions(['session-002']);
      });

      expect(volundrService.archiveSession).toHaveBeenCalledWith('session-002');
      expect(result.current.sessions.find(s => s.id === 'session-002')).toBeUndefined();
      expect(result.current.archivedSessions).toHaveLength(1);
    });

    it('should archive all stopped sessions', async () => {
      vi.mocked(volundrService.archiveStoppedSessions).mockResolvedValue(['session-002']);
      vi.mocked(volundrService.listArchivedSessions).mockResolvedValue([
        { ...mockSessions[1], status: 'archived', archivedAt: new Date() },
      ]);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.archiveAllStopped();
      });

      expect(volundrService.archiveStoppedSessions).toHaveBeenCalled();
      // Only running sessions remain
      expect(result.current.sessions.every(s => s.status !== 'stopped')).toBe(true);
    });

    it('should load archived sessions on mount', async () => {
      vi.mocked(volundrService.listArchivedSessions).mockResolvedValue([mockArchivedSession]);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(volundrService.listArchivedSessions).toHaveBeenCalled();
      expect(result.current.archivedSessions).toHaveLength(1);
      expect(result.current.archivedSessions[0].id).toBe('archived-001');
    });
  });

  describe('saveTemplate', () => {
    it('should add a new template', async () => {
      const template = { name: 'new-tpl' } as VolundrTemplate;
      const saved = { ...template, name: 'new-tpl' } as VolundrTemplate;
      vi.mocked(volundrService.saveTemplate).mockResolvedValue(saved);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      let returned: VolundrTemplate | undefined;
      await act(async () => {
        returned = await result.current.saveTemplate(template);
      });

      expect(volundrService.saveTemplate).toHaveBeenCalledWith(template);
      expect(returned).toEqual(saved);
    });

    it('should update an existing template by name', async () => {
      const existing = { name: 'tpl-1' } as VolundrTemplate;
      vi.mocked(volundrService.getTemplates).mockResolvedValue([existing]);

      const updated = { name: 'tpl-1', description: 'updated' } as unknown as VolundrTemplate;
      vi.mocked(volundrService.saveTemplate).mockResolvedValue(updated);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.saveTemplate(updated);
      });

      expect(result.current.templates[0]).toEqual(updated);
    });
  });

  describe('savePreset', () => {
    it('should add a new preset', async () => {
      const preset = { name: 'new-preset' } as Omit<
        VolundrPreset,
        'id' | 'createdAt' | 'updatedAt'
      >;
      const saved = {
        ...preset,
        id: 'preset-1',
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      } as VolundrPreset;
      vi.mocked(volundrService.savePreset).mockResolvedValue(saved);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      let returned: VolundrPreset | undefined;
      await act(async () => {
        returned = await result.current.savePreset(preset);
      });

      expect(volundrService.savePreset).toHaveBeenCalledWith(preset);
      expect(returned).toEqual(saved);
    });

    it('should update an existing preset by id', async () => {
      const existing = {
        id: 'preset-1',
        name: 'existing',
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      } as VolundrPreset;
      vi.mocked(volundrService.getPresets).mockResolvedValue([existing]);

      const updated = { ...existing, name: 'updated' } as VolundrPreset;
      vi.mocked(volundrService.savePreset).mockResolvedValue(updated);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.savePreset(updated);
      });

      expect(result.current.presets[0].name).toBe('updated');
    });
  });

  describe('deletePreset', () => {
    it('should delete a preset and remove from state', async () => {
      const preset = {
        id: 'preset-1',
        name: 'to-delete',
        createdAt: '2026-01-01',
        updatedAt: '2026-01-01',
      } as VolundrPreset;
      vi.mocked(volundrService.getPresets).mockResolvedValue([preset]);
      vi.mocked(volundrService.deletePreset).mockResolvedValue(undefined);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(result.current.presets).toHaveLength(1);

      await act(async () => {
        await result.current.deletePreset('preset-1');
      });

      expect(volundrService.deletePreset).toHaveBeenCalledWith('preset-1');
      expect(result.current.presets).toHaveLength(0);
    });
  });

  describe('createSecret', () => {
    it('should create a secret and update available secrets', async () => {
      const secretResult = { name: 'my-secret', keys: ['key1'] };
      vi.mocked(volundrService.createSecret).mockResolvedValue(secretResult);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      let returned: { name: string; keys: string[] } | undefined;
      await act(async () => {
        returned = await result.current.createSecret('my-secret', { key1: 'val1' });
      });

      expect(volundrService.createSecret).toHaveBeenCalledWith('my-secret', { key1: 'val1' });
      expect(returned).toEqual(secretResult);
      expect(result.current.availableSecrets).toContain('my-secret');
    });

    it('should not duplicate secret name if already present', async () => {
      vi.mocked(volundrService.getAvailableSecrets).mockResolvedValue(['existing-secret']);
      vi.mocked(volundrService.createSecret).mockResolvedValue({
        name: 'existing-secret',
        keys: ['k'],
      });

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.createSecret('existing-secret', { k: 'v' });
      });

      // Should not have duplicated
      expect(result.current.availableSecrets.filter(s => s === 'existing-secret')).toHaveLength(1);
    });
  });

  describe('searchTrackerIssues', () => {
    it('should return issues from service', async () => {
      const issues: TrackerIssue[] = [
        {
          id: 'issue-1',
          identifier: 'NIU-44',
          title: 'Test issue',
          status: 'todo',
          url: 'https://linear.app/issue/NIU-44',
        },
      ];
      vi.mocked(volundrService.searchTrackerIssues).mockResolvedValue(issues);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      let returned: TrackerIssue[] | undefined;
      await act(async () => {
        returned = await result.current.searchTrackerIssues('NIU-44');
      });

      expect(volundrService.searchTrackerIssues).toHaveBeenCalledWith('NIU-44');
      expect(returned).toEqual(issues);
    });
  });

  describe('updateTrackerIssueStatus', () => {
    it('should update issue status and update matching sessions', async () => {
      const issue: TrackerIssue = {
        id: 'issue-1',
        identifier: 'NIU-44',
        title: 'Test issue',
        status: 'done',
        url: 'https://linear.app/issue/NIU-44',
      };

      const sessionsWithIssue: VolundrSession[] = [
        {
          ...mockSessions[0],
          trackerIssue: { ...issue, status: 'todo' },
        },
        mockSessions[1],
      ];

      vi.mocked(volundrService.getSessions).mockResolvedValue(sessionsWithIssue);
      vi.mocked(volundrService.updateTrackerIssueStatus).mockResolvedValue(issue);

      const { result } = renderHook(() => useVolundr());

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      await act(async () => {
        await result.current.updateTrackerIssueStatus('issue-1', 'done');
      });

      expect(volundrService.updateTrackerIssueStatus).toHaveBeenCalledWith('issue-1', 'done');
      // Session with matching issue should be updated
      expect(result.current.sessions[0].trackerIssue?.status).toBe('done');
      // Session without issue should be unchanged
      expect(result.current.sessions[1].trackerIssue).toBeUndefined();
    });
  });
});
