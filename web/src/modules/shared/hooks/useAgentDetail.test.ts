import { renderHook } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { useAgentDetail } from './useAgentDetail';

// ── Mock useSkuldChat ──────────────────────────────────────────────────

vi.mock('./useSkuldChat', () => ({
  useSkuldChat: vi.fn(),
  DEFAULT_CAPABILITIES: {
    send_message: true,
    cli_websocket: false,
    session_resume: false,
    interrupt: false,
    set_model: false,
    set_thinking_tokens: false,
    set_permission_mode: false,
    rewind_files: false,
    mcp_set_servers: false,
    permission_requests: false,
    slash_commands: false,
    skills: false,
  },
}));

// Mock chat store (required by useSkuldChat in non-mocked path)
vi.mock('@/modules/shared/store/chat.store', () => ({
  useChatStore: vi.fn(() => ({
    getMessages: vi.fn(() => []),
    setMessages: vi.fn(),
    clearSession: vi.fn(),
  })),
}));

import { useSkuldChat } from './useSkuldChat';

const mockMessages = [
  {
    id: 'msg-1',
    role: 'assistant' as const,
    content: 'Hello',
    createdAt: new Date(),
    status: 'complete' as const,
  },
];

function setupSkuldMock(options: { connected?: boolean; isRunning?: boolean } = {}) {
  vi.mocked(useSkuldChat).mockReturnValue({
    messages: mockMessages,
    connected: options.connected ?? false,
    isRunning: options.isRunning ?? false,
    historyLoaded: true,
    pendingPermissions: [],
    availableCommands: [],
    capabilities: {
      send_message: true,
      cli_websocket: false,
      session_resume: false,
      interrupt: false,
      set_model: false,
      set_thinking_tokens: false,
      set_permission_mode: false,
      rewind_files: false,
      mcp_set_servers: false,
      permission_requests: false,
      slash_commands: false,
      skills: false,
    },
    sendMessage: vi.fn(),
    respondToPermission: vi.fn(),
    sendInterrupt: vi.fn(),
    sendSetModel: vi.fn(),
    sendSetMaxThinkingTokens: vi.fn(),
    sendRewindFiles: vi.fn(),
    clearMessages: vi.fn(),
  });
}

describe('useAgentDetail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('when gatewayUrl is null', () => {
    it('returns empty messages', () => {
      setupSkuldMock();
      const { result } = renderHook(() => useAgentDetail(null));
      expect(result.current.messages).toEqual([]);
    });

    it('returns connected: false', () => {
      setupSkuldMock({ connected: true });
      const { result } = renderHook(() => useAgentDetail(null));
      expect(result.current.connected).toBe(false);
    });

    it('returns isRunning: false', () => {
      setupSkuldMock({ isRunning: true });
      const { result } = renderHook(() => useAgentDetail(null));
      expect(result.current.isRunning).toBe(false);
    });
  });

  describe('WebSocket URL construction', () => {
    it('converts http:// to ws:// and appends /ws', () => {
      setupSkuldMock({ connected: true });
      renderHook(() => useAgentDetail('http://host:8080'));
      expect(vi.mocked(useSkuldChat)).toHaveBeenCalledWith('ws://host:8080/ws');
    });

    it('converts https:// to wss:// and appends /ws', () => {
      setupSkuldMock({ connected: true });
      renderHook(() => useAgentDetail('https://host:8080'));
      expect(vi.mocked(useSkuldChat)).toHaveBeenCalledWith('wss://host:8080/ws');
    });

    it('handles URL with trailing slash', () => {
      setupSkuldMock({ connected: true });
      renderHook(() => useAgentDetail('http://host:8080/'));
      expect(vi.mocked(useSkuldChat)).toHaveBeenCalledWith('ws://host:8080/ws');
    });

    it('handles URL with existing path prefix', () => {
      setupSkuldMock({ connected: true });
      renderHook(() => useAgentDetail('http://host:8080/gateway'));
      expect(vi.mocked(useSkuldChat)).toHaveBeenCalledWith('ws://host:8080/gateway/ws');
    });

    it('passes ws:// URLs through with /ws appended', () => {
      setupSkuldMock({ connected: true });
      renderHook(() => useAgentDetail('ws://host:8080'));
      expect(vi.mocked(useSkuldChat)).toHaveBeenCalledWith('ws://host:8080/ws');
    });
  });

  describe('when gatewayUrl is provided', () => {
    it('returns messages from useSkuldChat', () => {
      setupSkuldMock({ connected: true });
      const { result } = renderHook(() => useAgentDetail('http://host:8080'));
      expect(result.current.messages).toEqual(mockMessages);
    });

    it('returns connected state from useSkuldChat', () => {
      setupSkuldMock({ connected: true });
      const { result } = renderHook(() => useAgentDetail('http://host:8080'));
      expect(result.current.connected).toBe(true);
    });

    it('returns isRunning state from useSkuldChat', () => {
      setupSkuldMock({ isRunning: true });
      const { result } = renderHook(() => useAgentDetail('http://host:8080'));
      expect(result.current.isRunning).toBe(true);
    });
  });
});
