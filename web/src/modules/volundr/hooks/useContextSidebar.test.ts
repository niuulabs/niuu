import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { VolundrSession, SessionChronicle, PullRequest } from '@/modules/volundr/models';
import { useContextSidebar } from './useContextSidebar';

// Track the setter mock so tests can inspect it
const mockSetCollapsed = vi.fn();
let mockCollapsedValue = false;

// Mock useLocalStorage
vi.mock('@/hooks/useLocalStorage', () => ({
  useLocalStorage: () => [mockCollapsedValue, mockSetCollapsed],
}));

// Mock the adapters module
const mockGetSessionMcpServers = vi.fn().mockResolvedValue([
  { name: 'github', status: 'connected', tools: 12 },
  { name: 'filesystem', status: 'disconnected', tools: 8 },
]);

vi.mock('@/modules/volundr/adapters', () => ({
  volundrService: {
    getSessionMcpServers: (...args: unknown[]) => mockGetSessionMcpServers(...args),
  },
}));

const mockSession: VolundrSession = {
  id: 'session-001',
  name: 'test-session',
  source: { type: 'git', repo: 'org/repo', branch: 'feature/test' },
  status: 'running',
  model: 'claude-sonnet',
  lastActive: Date.now(),
  messageCount: 10,
  tokensUsed: 45000,
  taskType: 'skuld-claude',
};

const mockChronicle: SessionChronicle = {
  events: [
    { t: 0, type: 'session', label: 'Session started' },
    { t: 30, type: 'message', label: 'User prompt', tokens: 150 },
    { t: 60, type: 'file', label: 'src/main.ts', action: 'created', ins: 50, del: 0 },
    { t: 90, type: 'session', label: 'Task checkpoint' },
  ],
  files: [{ path: 'src/main.ts', status: 'new', ins: 50, del: 0 }],
  commits: [{ hash: 'abc1234', msg: 'fix: something', time: '2m ago' }],
  tokenBurn: [100, 200, 300, 500, 150],
};

const mockPR: PullRequest = {
  number: 42,
  title: 'Add feature',
  url: 'https://github.com/org/repo/pull/42',
  repoUrl: 'https://github.com/org/repo',
  provider: 'github',
  sourceBranch: 'feature/test',
  targetBranch: 'main',
  status: 'open',
};

describe('useContextSidebar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCollapsedValue = false;
  });

  it('returns collapsed state from localStorage', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));
    expect(result.current.collapsed).toBe(false);
  });

  it('toggleCollapsed calls setter with inverted value', () => {
    mockCollapsedValue = false;
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    act(() => {
      result.current.toggleCollapsed();
    });

    expect(mockSetCollapsed).toHaveBeenCalledWith(true);
  });

  it('computes token usage from session and chronicle', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    expect(result.current.tokenUsage).toEqual({
      totalTokens: 45000,
      burnRate: [100, 200, 300, 500, 150],
      peakBurn: 500,
      averageBurn: 250,
    });
  });

  it('returns null tokenUsage when session is null', () => {
    const { result } = renderHook(() => useContextSidebar(null, mockChronicle, mockPR));
    expect(result.current.tokenUsage).toBeNull();
  });

  it('returns null tokenUsage when chronicle is null', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, null, mockPR));
    expect(result.current.tokenUsage).toBeNull();
  });

  it('extracts active tasks from chronicle events', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    // Should pick session and message events (not file events)
    expect(result.current.activeTasks).toEqual([
      { label: 'Session started', timestamp: 0 },
      { label: 'User prompt', timestamp: 30 },
      { label: 'Task checkpoint', timestamp: 90 },
    ]);
  });

  it('returns empty activeTasks when chronicle is null', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, null, mockPR));
    expect(result.current.activeTasks).toEqual([]);
  });

  it('passes through pullRequest', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));
    expect(result.current.pullRequest).toBe(mockPR);
  });

  it('passes null pullRequest through', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, null));
    expect(result.current.pullRequest).toBeNull();
  });

  it('builds model config from session', () => {
    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    expect(result.current.modelConfig).toEqual({
      model: 'claude-sonnet',
      taskType: 'Skuld Claude',
      taskDescription: 'Interactive Claude Code CLI session',
      source: { type: 'git', repo: 'org/repo', branch: 'feature/test' },
    });
  });

  it('returns null modelConfig when session is null', () => {
    const { result } = renderHook(() => useContextSidebar(null, mockChronicle, mockPR));
    expect(result.current.modelConfig).toBeNull();
  });

  it('handles unknown task type gracefully', () => {
    const session = { ...mockSession, taskType: undefined };
    const { result } = renderHook(() => useContextSidebar(session, mockChronicle, mockPR));

    expect(result.current.modelConfig?.taskType).toBe('unknown');
  });

  it('fetches MCP servers when session is provided', async () => {
    renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    await waitFor(() => {
      expect(mockGetSessionMcpServers).toHaveBeenCalledWith('session-001');
    });
  });

  it('clears MCP servers when session is null', () => {
    const { result } = renderHook(() => useContextSidebar(null, mockChronicle, mockPR));
    expect(result.current.mcpServers).toEqual([]);
  });

  it('handles MCP server fetch error gracefully', async () => {
    mockGetSessionMcpServers.mockRejectedValueOnce(new Error('Network error'));

    const { result } = renderHook(() => useContextSidebar(mockSession, mockChronicle, mockPR));

    await waitFor(() => {
      expect(result.current.mcpServersLoading).toBe(false);
    });
    expect(result.current.mcpServers).toEqual([]);
  });

  it('limits active tasks to 5', () => {
    const manyEvents: SessionChronicle = {
      ...mockChronicle,
      events: [
        { t: 0, type: 'session', label: 'Task 1' },
        { t: 10, type: 'message', label: 'Task 2' },
        { t: 20, type: 'session', label: 'Task 3' },
        { t: 30, type: 'message', label: 'Task 4' },
        { t: 40, type: 'session', label: 'Task 5' },
        { t: 50, type: 'message', label: 'Task 6' },
        { t: 60, type: 'session', label: 'Task 7' },
      ],
    };

    const { result } = renderHook(() => useContextSidebar(mockSession, manyEvents, mockPR));

    expect(result.current.activeTasks.length).toBe(5);
    // Should keep the last 5
    expect(result.current.activeTasks[0].label).toBe('Task 3');
    expect(result.current.activeTasks[4].label).toBe('Task 7');
  });

  it('computes averageBurn as 0 for empty burnRate', () => {
    const emptyBurn: SessionChronicle = {
      ...mockChronicle,
      tokenBurn: [],
    };

    const { result } = renderHook(() => useContextSidebar(mockSession, emptyBurn, mockPR));

    expect(result.current.tokenUsage?.averageBurn).toBe(0);
    expect(result.current.tokenUsage?.peakBurn).toBe(0);
  });

  it('cancels in-flight MCP fetch when session changes', async () => {
    // Make the first fetch hang
    let resolveFirst: () => void = () => {};
    mockGetSessionMcpServers.mockImplementationOnce(
      () =>
        new Promise<never[]>(resolve => {
          resolveFirst = () => resolve([]);
        })
    );

    const session2: VolundrSession = { ...mockSession, id: 'session-002' };

    const { rerender } = renderHook(
      ({ session }) => useContextSidebar(session, mockChronicle, mockPR),
      { initialProps: { session: mockSession } }
    );

    // First fetch is in flight
    expect(mockGetSessionMcpServers).toHaveBeenCalledWith('session-001');

    // Change session, which cancels the first and starts a new fetch
    rerender({ session: session2 });

    await waitFor(() => {
      expect(mockGetSessionMcpServers).toHaveBeenCalledWith('session-002');
    });

    // Resolve the first (should be ignored because cancelled)
    resolveFirst!();

    // Verify the second fetch completed
    await waitFor(() => {
      expect(mockGetSessionMcpServers).toHaveBeenCalledTimes(2);
    });
  });

  it('returns mcpServersLoading as false when session is null', () => {
    const { result } = renderHook(() => useContextSidebar(null, mockChronicle, mockPR));
    expect(result.current.mcpServersLoading).toBe(false);
  });

  it('handles custom task type not in TASK_TYPES', () => {
    const session = { ...mockSession, taskType: 'custom-task' };
    const { result } = renderHook(() => useContextSidebar(session, mockChronicle, mockPR));

    expect(result.current.modelConfig?.taskType).toBe('custom-task');
    expect(result.current.modelConfig?.taskDescription).toBe('');
  });
});
