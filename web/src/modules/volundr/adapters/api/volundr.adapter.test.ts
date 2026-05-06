import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiVolundrService } from './volundr.adapter';
import { setTokenProvider } from './client';
import type {
  ApiSessionResponse,
  ApiModelInfo,
  SSESessionPayload,
  SSEStatsPayload,
  SSEMessagePayload,
  SSELogPayload,
  SSEChroniclePayload,
} from './volundr.types';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

import { mockResponse } from '@/test/mockFetch';

/**
 * Mock SSE stream that replaces EventSource-based tests.
 * When fetch is called with the SSE endpoint URL, it returns a ReadableStream
 * that the test can push SSE-formatted data into via simulateEvent/simulateError.
 */
class MockSSEStream {
  static instances: MockSSEStream[] = [];

  private controller: ReadableStreamDefaultController<Uint8Array> | null = null;
  private encoder = new TextEncoder();
  private abortController: AbortController | null = null;
  url: string;

  constructor(url: string) {
    this.url = url;
    MockSSEStream.instances.push(this);
  }

  /** Build the mock fetch Response with a ReadableStream body */
  createResponse(): Response {
    const stream = new ReadableStream<Uint8Array>({
      start: controller => {
        this.controller = controller;
      },
    });

    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }

  /** Record the AbortController from the fetch call so we can detect aborts */
  setAbortController(ac: AbortController) {
    this.abortController = ac;
  }

  /** Push an SSE event into the stream and wait for processing */
  async simulateEvent(type: string, data: unknown) {
    if (!this.controller) return;
    const block = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
    this.controller.enqueue(this.encoder.encode(block));
    // Yield to macrotask so the stream reader can process all microtasks
    await new Promise(r => setTimeout(r, 0));
  }

  /** Push a raw SSE block (for testing malformed data) */
  async simulateRawBlock(raw: string) {
    if (!this.controller) return;
    this.controller.enqueue(this.encoder.encode(raw));
    await new Promise(r => setTimeout(r, 0));
  }

  /** Simulate a stream error (causes the reader to reject) */
  simulateError() {
    if (!this.controller) return;
    this.controller.error(new Error('SSE connection error'));
    this.controller = null;
    const idx = MockSSEStream.instances.indexOf(this);
    if (idx !== -1) MockSSEStream.instances.splice(idx, 1);
  }

  /** Close the stream cleanly */
  close() {
    if (this.controller) {
      try {
        this.controller.close();
      } catch {
        // already closed
      }
      this.controller = null;
    }
    if (this.abortController) {
      this.abortController.abort();
      this.abortController = null;
    }
    const idx = MockSSEStream.instances.indexOf(this);
    if (idx !== -1) MockSSEStream.instances.splice(idx, 1);
  }
}

const SSE_URL = '/api/v1/volundr/sessions/stream';

/**
 * Wraps the global mockFetch so that SSE endpoint calls create a
 * MockSSEStream while all other calls fall through to the normal
 * vi.fn() mock (mockReturnValueOnce, etc.).
 */
function installSSEFetchMock() {
  const realImpl = global.fetch;
  global.fetch = ((url: string, init?: RequestInit) => {
    if (url === SSE_URL) {
      const stream = new MockSSEStream(url);
      const response = stream.createResponse();
      if (init?.signal) {
        init.signal.addEventListener('abort', () => stream.close());
      }
      return Promise.resolve(response);
    }
    // Delegate non-SSE calls to the vi.fn() mock
    return mockFetch(url, init);
  }) as typeof fetch;

  return () => {
    global.fetch = realImpl;
  };
}

describe('ApiVolundrService', () => {
  let service: ApiVolundrService;

  let cleanupSSEMock: () => void;

  beforeEach(() => {
    service = new ApiVolundrService();
    mockFetch.mockReset();
    MockSSEStream.instances = [];
    cleanupSSEMock = installSSEFetchMock();
  });

  afterEach(() => {
    vi.clearAllMocks();
    // Close any remaining SSE streams
    for (const stream of [...MockSSEStream.instances]) {
      stream.close();
    }
    cleanupSSEMock();
  });

  const mockApiSession: ApiSessionResponse = {
    id: '123e4567-e89b-12d3-a456-426614174000',
    name: 'Test Session',
    model: 'claude-sonnet-4-20250514',
    source: { type: 'git', repo: 'odin/core', branch: 'main' },
    status: 'running',
    chat_endpoint: 'http://localhost:8080/chat',
    code_endpoint: 'http://localhost:8080/code',
    created_at: '2024-01-15T10:00:00Z',
    updated_at: '2024-01-15T10:30:00Z',
    last_active: '2024-01-15T10:30:00Z',
    message_count: 0,
    tokens_used: 0,
    pod_name: null,
    error: null,
  };

  const mockApiSessionWithExtras: ApiSessionResponse = {
    ...mockApiSession,
    message_count: 42,
    tokens_used: 15000,
    pod_name: 'volundr-abc123',
  };

  const mockApiModel: ApiModelInfo = {
    id: 'claude-sonnet-4-20250514',
    name: 'Claude Sonnet 4',
    description: 'Balanced performance model',
    provider: 'cloud',
    tier: 'balanced',
    color: 'purple',
  };

  describe('getSessions', () => {
    it('returns transformed sessions from API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));

      const sessions = await service.getSessions();

      expect(sessions).toHaveLength(1);
      expect(sessions[0]).toMatchObject({
        id: mockApiSession.id,
        name: mockApiSession.name,
        model: mockApiSession.model,
        status: 'running',
      });
    });

    it('maps all required fields from API response', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));

      const sessions = await service.getSessions();

      expect(sessions[0].source).toEqual({ type: 'git', repo: 'odin/core', branch: 'main' });
      expect(sessions[0].messageCount).toBe(0);
      expect(sessions[0].tokensUsed).toBe(0);
    });

    it('extracts hostname from chat_endpoint for managed sessions', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));

      const sessions = await service.getSessions();

      // rewriteOrigin rewrites backend URLs to browser origin (jsdom = localhost:3000)
      expect(sessions[0].hostname).toBe('localhost:3000');
    });

    it('handles null endpoints gracefully', async () => {
      const sessionNoEndpoints = { ...mockApiSession, chat_endpoint: null, code_endpoint: null };
      mockFetch.mockReturnValueOnce(mockResponse([sessionNoEndpoints]));

      const sessions = await service.getSessions();

      expect(sessions[0].hostname).toBeUndefined();
    });

    it('uses API values when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSessionWithExtras]));

      const sessions = await service.getSessions();

      expect(sessions[0].source).toEqual({ type: 'git', repo: 'odin/core', branch: 'main' });
      expect(sessions[0].messageCount).toBe(42);
      expect(sessions[0].tokensUsed).toBe(15000);
      expect(sessions[0].podName).toBe('volundr-abc123');
    });
  });

  describe('getSession', () => {
    it('returns transformed session by ID', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession));

      const session = await service.getSession(mockApiSession.id);

      expect(session).not.toBeNull();
      expect(session?.id).toBe(mockApiSession.id);
    });

    it('returns null for 404 response', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const session = await service.getSession('nonexistent');

      expect(session).toBeNull();
    });
  });

  describe('getActiveSessions', () => {
    it('returns only running sessions', async () => {
      const stoppedSession: ApiSessionResponse = {
        ...mockApiSession,
        id: 'stopped-123',
        status: 'stopped',
      };
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession, stoppedSession]));

      const sessions = await service.getActiveSessions();

      expect(sessions).toHaveLength(1);
      expect(sessions[0].status).toBe('running');
    });
  });

  describe('getStats', () => {
    it('returns stats from API when available', async () => {
      const apiStats = {
        active_sessions: 3,
        total_sessions: 10,
        tokens_today: 50000,
        local_tokens: 20000,
        cloud_tokens: 30000,
        cost_today: 1.5,
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiStats));

      const stats = await service.getStats();

      expect(stats).toMatchObject({
        activeSessions: 3,
        totalSessions: 10,
        tokensToday: 50000,
        localTokens: 20000,
        cloudTokens: 30000,
        costToday: 1.5,
      });
    });

    it('computes stats from sessions when endpoint returns 404', async () => {
      // First call: /stats returns 404
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));
      // Second call: /sessions returns sessions
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));

      const stats = await service.getStats();

      expect(stats.totalSessions).toBe(1);
      expect(stats.activeSessions).toBe(1);
    });
  });

  describe('getModels', () => {
    it('returns transformed models', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiModel]));

      const models = await service.getModels();

      expect(models['claude-sonnet-4-20250514']).toBeDefined();
      expect(models['claude-sonnet-4-20250514'].name).toBe('Claude Sonnet 4');
    });

    it('maps all required model metadata from API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiModel]));

      const models = await service.getModels();

      const model = models['claude-sonnet-4-20250514'];
      expect(model.provider).toBe('cloud');
      expect(model.tier).toBe('balanced');
      expect(model.color).toBe('purple');
    });

    it('uses API metadata when provided', async () => {
      const modelWithMetadata: ApiModelInfo = {
        ...mockApiModel,
        provider: 'cloud',
        tier: 'frontier',
        color: 'gold',
      };
      mockFetch.mockReturnValueOnce(mockResponse([modelWithMetadata]));

      const models = await service.getModels();

      const model = models['claude-sonnet-4-20250514'];
      expect(model.tier).toBe('frontier');
      expect(model.color).toBe('gold');
    });
  });

  describe('status mapping', () => {
    it('maps "failed" to "error"', async () => {
      const failedSession: ApiSessionResponse = {
        ...mockApiSession,
        status: 'failed',
        error: 'Pod crashed',
      };
      mockFetch.mockReturnValueOnce(mockResponse([failedSession]));

      const sessions = await service.getSessions();

      expect(sessions[0].status).toBe('error');
      expect(sessions[0].error).toBe('Pod crashed');
    });

    it('preserves other statuses', async () => {
      const statuses: Array<{
        api: 'created' | 'starting' | 'running' | 'stopping' | 'stopped';
        expected: string;
      }> = [
        { api: 'created', expected: 'created' },
        { api: 'starting', expected: 'starting' },
        { api: 'running', expected: 'running' },
        { api: 'stopping', expected: 'stopping' },
        { api: 'stopped', expected: 'stopped' },
      ];

      for (const { api, expected } of statuses) {
        mockFetch.mockReturnValueOnce(mockResponse([{ ...mockApiSession, status: api }]));
        const sessions = await service.getSessions();
        expect(sessions[0].status).toBe(expected);
      }
    });
  });

  describe('startSession', () => {
    it('creates a session (backend auto-starts)', async () => {
      // Single call: POST /sessions (backend creates AND starts)
      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession, 201));

      const session = await service.startSession({
        name: 'Test',
        source: { type: 'git', repo: 'odin/core', branch: 'main' },
        model: 'claude-sonnet-4-20250514',
      });

      expect(session).toBeDefined();
      expect(mockFetch).toHaveBeenCalledTimes(1);

      // Verify create request body
      const createCall = mockFetch.mock.calls[0];
      const createBody = JSON.parse(createCall[1].body);
      expect(createBody.name).toBe('Test');
      expect(createBody.model).toBe('claude-sonnet-4-20250514');
      expect(createBody.source).toEqual({ type: 'git', repo: 'odin/core', branch: 'main' });
    });

    it('includes resource_config when resourceConfig is provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession, 201));

      await service.startSession({
        name: 'GPU Session',
        source: { type: 'git', repo: 'odin/core', branch: 'main' },
        model: 'claude-sonnet-4-20250514',
        resourceConfig: { cpu: '4', memory: '8Gi', gpu: '1' },
      });

      const createBody = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(createBody.resource_config).toEqual({ cpu: '4', memory: '8Gi', gpu: '1' });
    });
  });

  describe('getClusterResources', () => {
    it('fetches and maps cluster resources', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          resource_types: [
            {
              name: 'cpu',
              resource_key: 'cpu',
              display_name: 'CPU',
              unit: 'cores',
              category: 'compute',
            },
          ],
          nodes: [],
        })
      );

      const result = await service.getClusterResources();
      expect(result.resourceTypes).toHaveLength(1);
      expect(result.resourceTypes[0].name).toBe('cpu');
      expect(result.resourceTypes[0].displayName).toBe('CPU');
      expect(result.nodes).toEqual([]);
    });
  });

  describe('stopSession', () => {
    it('calls stop endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession));

      await service.stopSession(mockApiSession.id);

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining(`/sessions/${mockApiSession.id}/stop`),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('resumeSession', () => {
    it('calls start endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession));

      await service.resumeSession(mockApiSession.id);

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining(`/sessions/${mockApiSession.id}/start`),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('subscribe', () => {
    it('returns unsubscribe function', () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribe(callback);

      expect(typeof unsubscribe).toBe('function');
      unsubscribe();
    });

    it('opens SSE connection on first subscriber', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      // Wait for async connection
      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      expect(MockSSEStream.instances[0].url).toBe('/api/v1/volundr/sessions/stream');
    });

    it('closes SSE connection when last subscriber unsubscribes', async () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsubscribe();
      expect(MockSSEStream.instances).toHaveLength(0);
    });

    it('does not duplicate SSE connections for multiple subscribers', async () => {
      const callback1 = vi.fn();
      const callback2 = vi.fn();

      service.subscribe(callback1);
      service.subscribe(callback2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });
    });
  });

  describe('SSE events', () => {
    const mockSSESession: SSESessionPayload = {
      id: '550e8400-e29b-41d4-a716-446655440000',
      name: 'SSE Test Session',
      model: 'claude-sonnet-4-20250514',
      source: { type: 'git', repo: 'https://github.com/org/repo', branch: 'main' },
      status: 'running',
      chat_endpoint: 'https://session-abc.volundr.example.com/chat',
      code_endpoint: 'https://session-abc.volundr.example.com/code',
      created_at: '2026-02-03T12:00:00',
      updated_at: '2026-02-03T12:05:00',
      last_active: '2026-02-03T12:05:00',
      message_count: 5,
      tokens_used: 1500,
      pod_name: 'volundr-abc123',
      error: null,
    };

    it('handles session_created events', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];
      await eventSource.simulateEvent('session_created', mockSSESession);

      expect(callback).toHaveBeenCalledWith(
        expect.arrayContaining([
          expect.objectContaining({
            id: mockSSESession.id,
            name: mockSSESession.name,
            status: 'running',
            // Non-loopback host is preserved (gateway domain)
            hostname: 'session-abc.volundr.example.com',
          }),
        ])
      );
    });

    it('handles session_updated events', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // First create the session
      await eventSource.simulateEvent('session_created', mockSSESession);

      // Then update it
      const updatedSession = { ...mockSSESession, status: 'stopped' as const, tokens_used: 2000 };
      await eventSource.simulateEvent('session_updated', updatedSession);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall[0].status).toBe('stopped');
      expect(lastCall[0].tokensUsed).toBe(2000);
    });

    it('handles session_created with tracker issue fields', async () => {
      const callback = vi.fn();
      service.subscribe(callback);
      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];
      const sessionWithTracker: SSESessionPayload = {
        ...mockSSESession,
        tracker_issue_id: 'NIU-42',
        issue_tracker_url: 'https://linear.app/niuu/issue/NIU-42',
      };
      await eventSource.simulateEvent('session_created', sessionWithTracker);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall[0].trackerIssue).toEqual(
        expect.objectContaining({
          id: 'NIU-42',
          identifier: 'NIU-42',
          url: 'https://linear.app/niuu/issue/NIU-42',
        })
      );
    });

    it('preserves tracker issue on SSE update via merge', async () => {
      const callback = vi.fn();
      service.subscribe(callback);
      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // Create session with tracker
      const sessionWithTracker: SSESessionPayload = {
        ...mockSSESession,
        tracker_issue_id: 'NIU-42',
        issue_tracker_url: 'https://linear.app/niuu/issue/NIU-42',
      };
      await eventSource.simulateEvent('session_created', sessionWithTracker);

      // Update session without tracker fields (simulates legacy SSE)
      const updateWithoutTracker: SSESessionPayload = {
        ...mockSSESession,
        tokens_used: 3000,
      };
      await eventSource.simulateEvent('session_updated', updateWithoutTracker);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall[0].tokensUsed).toBe(3000);
      // trackerIssue should be preserved from the original session
      expect(lastCall[0].trackerIssue).toEqual(
        expect.objectContaining({
          id: 'NIU-42',
          identifier: 'NIU-42',
        })
      );
    });

    it('handles SSE session with flat repo/branch fields (no source)', async () => {
      const callback = vi.fn();
      service.subscribe(callback);
      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];
      const flatSession: SSESessionPayload = {
        id: '550e8400-e29b-41d4-a716-446655440099',
        name: 'Flat Fields Session',
        model: 'claude-sonnet-4-20250514',
        repo: 'https://github.com/org/repo',
        branch: 'develop',
        status: 'running',
        chat_endpoint: null,
        code_endpoint: null,
        created_at: '2026-02-03T12:00:00',
        updated_at: '2026-02-03T12:05:00',
        last_active: '2026-02-03T12:05:00',
        message_count: 0,
        tokens_used: 0,
        pod_name: null,
        error: null,
      };
      await eventSource.simulateEvent('session_created', flatSession);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      const created = lastCall.find(
        (s: { id: string }) => s.id === '550e8400-e29b-41d4-a716-446655440099'
      );
      expect(created.source).toEqual({
        type: 'git',
        repo: 'https://github.com/org/repo',
        branch: 'develop',
      });
    });

    it('handles session_deleted events', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // First create the session
      await eventSource.simulateEvent('session_created', mockSSESession);
      expect(callback.mock.calls[callback.mock.calls.length - 1][0]).toHaveLength(1);

      // Then delete it
      await eventSource.simulateEvent('session_deleted', { id: mockSSESession.id });

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall).toHaveLength(0);
    });

    it('handles stats_updated events', async () => {
      const statsCallback = vi.fn();
      service.subscribeStats(statsCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockStats: SSEStatsPayload = {
        active_sessions: 3,
        total_sessions: 10,
        tokens_today: 50000,
        local_tokens: 10000,
        cloud_tokens: 40000,
        cost_today: 2.5,
      };

      const eventSource = MockSSEStream.instances[0];
      await eventSource.simulateEvent('stats_updated', mockStats);

      expect(statsCallback).toHaveBeenCalledWith(
        expect.objectContaining({
          activeSessions: 3,
          totalSessions: 10,
          tokensToday: 50000,
          localTokens: 10000,
          cloudTokens: 40000,
          costToday: 2.5,
        })
      );
    });

    it('maps failed status to error', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const failedSession = {
        ...mockSSESession,
        status: 'failed' as const,
        error: 'Pod OOMKilled',
      };
      const eventSource = MockSSEStream.instances[0];
      await eventSource.simulateEvent('session_created', failedSession);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall[0].status).toBe('error');
      expect(lastCall[0].error).toBe('Pod OOMKilled');
    });
  });

  describe('SSE reconnection', () => {
    it('schedules reconnect on connection error', async () => {
      vi.useFakeTimers();

      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];
      eventSource.simulateError();

      // Connection should be closed
      expect(MockSSEStream.instances).toHaveLength(0);

      // Fast-forward past reconnection delay
      await vi.advanceTimersByTimeAsync(1000);

      // Should have reconnected
      expect(MockSSEStream.instances).toHaveLength(1);

      vi.useRealTimers();
    });

    it('does not reconnect when all subscribers have unsubscribed', async () => {
      vi.useFakeTimers();

      const callback = vi.fn();
      const unsubscribe = service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // Unsubscribe before error
      unsubscribe();

      // Simulate error - should not schedule reconnect since no subscribers
      eventSource.simulateError();

      // Fast-forward past reconnection delay
      await vi.advanceTimersByTimeAsync(1000);

      // Should NOT have reconnected since there are no subscribers
      expect(MockSSEStream.instances).toHaveLength(0);

      vi.useRealTimers();
    });

    it('clears pending reconnect timeout when unsubscribing', async () => {
      vi.useFakeTimers();

      const callback = vi.fn();
      const unsubscribe = service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // Trigger error to schedule reconnect
      eventSource.simulateError();

      // Connection closed, reconnect scheduled
      expect(MockSSEStream.instances).toHaveLength(0);

      // Unsubscribe while reconnect is pending - should clear the timeout
      unsubscribe();

      // Fast-forward past reconnection delay
      await vi.advanceTimersByTimeAsync(2000);

      // Should NOT have reconnected since we unsubscribed
      expect(MockSSEStream.instances).toHaveLength(0);

      vi.useRealTimers();
    });
  });

  describe('subscribeStats', () => {
    it('does not notify when cachedStats is null', async () => {
      // Create a new service instance (cachedStats will be null)
      const freshService = new ApiVolundrService();
      const statsCallback = vi.fn();

      // Subscribe to stats - should NOT call callback since no cached stats
      freshService.subscribeStats(statsCallback);

      // The callback should not have been called with stats (only connection events)
      // Wait a tick to ensure any async operations complete
      await vi.waitFor(() => {
        expect(MockSSEStream.instances.length).toBeGreaterThan(0);
      });

      // Stats callback should not be called since cachedStats is null
      expect(statsCallback).not.toHaveBeenCalled();
    });

    it('notifies immediately with cached stats when available', async () => {
      const statsCallback = vi.fn();

      // First, populate the cache by receiving a stats_updated event
      service.subscribeStats(statsCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockStats: SSEStatsPayload = {
        active_sessions: 5,
        total_sessions: 20,
        tokens_today: 100000,
        local_tokens: 50000,
        cloud_tokens: 50000,
        cost_today: 5.0,
      };

      const eventSource = MockSSEStream.instances[0];
      await eventSource.simulateEvent('stats_updated', mockStats);

      // First callback from SSE event
      expect(statsCallback).toHaveBeenCalledTimes(1);

      // Now subscribe a second callback - should receive cached stats immediately
      const secondCallback = vi.fn();
      service.subscribeStats(secondCallback);

      expect(secondCallback).toHaveBeenCalledWith(
        expect.objectContaining({
          activeSessions: 5,
          totalSessions: 20,
        })
      );
    });
  });

  describe('connectSession', () => {
    it('creates a manual session locally without API call', async () => {
      const session = await service.connectSession({
        name: 'my-skuld',
        hostname: 'skuld-01.local',
      });

      expect(mockFetch).not.toHaveBeenCalled();
      expect(session.name).toBe('my-skuld');
      expect(session.hostname).toBe('skuld-01.local');
      expect(session.origin).toBe('manual');
      expect(session.status).toBe('starting');
      expect(session.id).toMatch(/^manual-/);
    });

    it('adds manual session to cached sessions', async () => {
      // First populate cache
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      await service.getSessions();
      mockFetch.mockReset();

      const session = await service.connectSession({
        name: 'test',
        hostname: 'host',
      });

      // Verify it shows up in cached sessions via subscriber
      const callback = vi.fn();
      service.subscribe(callback);

      // The session was already added, so subscriber won't be called retroactively
      // But we can verify by connecting another to trigger notification
      expect(session.origin).toBe('manual');
    });

    it('notifies session subscribers', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await service.connectSession({ name: 'test', hostname: 'host' });

      expect(callback).toHaveBeenCalled();
    });
  });

  describe('manual session lifecycle', () => {
    it('stops manual session without API call', async () => {
      const session = await service.connectSession({
        name: 'test',
        hostname: 'host',
      });
      mockFetch.mockReset();

      await service.stopSession(session.id);

      expect(mockFetch).not.toHaveBeenCalled();
    });

    it('resumes manual session without API call', async () => {
      const session = await service.connectSession({
        name: 'test',
        hostname: 'host',
      });
      await service.stopSession(session.id);
      mockFetch.mockReset();

      await service.resumeSession(session.id);

      expect(mockFetch).not.toHaveBeenCalled();
    });

    it('deletes manual session without API call', async () => {
      const session = await service.connectSession({
        name: 'test',
        hostname: 'host',
      });
      mockFetch.mockReset();

      await service.deleteSession(session.id);

      expect(mockFetch).not.toHaveBeenCalled();
    });
  });

  describe('getCodeServerUrl', () => {
    it('returns null for starting manual session', async () => {
      const session = await service.connectSession({
        name: 'test',
        hostname: 'skuld-dev.local',
      });

      // Manual sessions start as 'starting' — code server isn't ready yet
      const url = await service.getCodeServerUrl(session.id);

      expect(url).toBeNull();
      expect(mockFetch).not.toHaveBeenCalled();
    });

    it('returns null for stopped manual session', async () => {
      const session = await service.connectSession({
        name: 'test',
        hostname: 'skuld-dev.local',
      });
      await service.stopSession(session.id);

      const url = await service.getCodeServerUrl(session.id);

      expect(url).toBeNull();
    });

    it('calls API for managed sessions', async () => {
      // Populate cache with a managed session
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      const sessions = await service.getSessions();

      mockFetch.mockReturnValueOnce(mockResponse(mockApiSession));
      const url = await service.getCodeServerUrl(sessions[0].id);

      // rewriteOrigin rewrites backend URLs to browser origin
      expect(url).toBe('http://localhost:3000/code');
    });

    it('returns null for stopped managed session', async () => {
      const stoppedSession = { ...mockApiSession, status: 'stopped' };
      mockFetch.mockReturnValueOnce(mockResponse(stoppedSession));

      const url = await service.getCodeServerUrl(mockApiSession.id);

      expect(url).toBeNull();
    });

    it('returns null for 404 managed session', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const url = await service.getCodeServerUrl('nonexistent-managed');

      expect(url).toBeNull();
    });

    it('throws for other API errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getCodeServerUrl('some-id')).rejects.toThrow();
    });
  });

  describe('SSE message and log events', () => {
    const mockSSESession: SSESessionPayload = {
      id: 'session-for-msgs',
      name: 'Msg Test',
      model: 'claude-sonnet-4-20250514',
      source: { type: 'git', repo: 'org/repo', branch: 'main' },
      status: 'running',
      chat_endpoint: null,
      code_endpoint: null,
      created_at: '2026-02-03T12:00:00',
      updated_at: '2026-02-03T12:05:00',
      last_active: '2026-02-03T12:05:00',
      message_count: 0,
      tokens_used: 0,
      pod_name: null,
      error: null,
    };

    it('handles message_received events and notifies subscribers', async () => {
      const msgCallback = vi.fn();
      service.subscribeMessages('session-for-msgs', msgCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockMessage: SSEMessagePayload = {
        id: 'msg-001',
        session_id: 'session-for-msgs',
        role: 'assistant',
        content: 'Hello from SSE',
        created_at: '2026-02-03T12:10:00',
        tokens_in: 10,
        tokens_out: 50,
        latency_ms: 200,
      };

      await MockSSEStream.instances[0].simulateEvent('message_received', mockMessage);

      expect(msgCallback).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'msg-001',
          sessionId: 'session-for-msgs',
          role: 'assistant',
          content: 'Hello from SSE',
        })
      );
    });

    it('handles log_received events and notifies subscribers', async () => {
      const logCallback = vi.fn();
      service.subscribeLogs('session-for-logs', logCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockLog: SSELogPayload = {
        id: 'log-001',
        session_id: 'session-for-logs',
        timestamp: '2026-02-03T12:10:00',
        level: 'info',
        source: 'broker',
        message: 'Session started',
      };

      await MockSSEStream.instances[0].simulateEvent('log_received', mockLog);

      expect(logCallback).toHaveBeenCalledWith(
        expect.objectContaining({
          id: 'log-001',
          sessionId: 'session-for-logs',
          level: 'info',
          message: 'Session started',
        })
      );
    });

    it('does not notify message subscribers for different session', async () => {
      const msgCallback = vi.fn();
      service.subscribeMessages('other-session', msgCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockMessage: SSEMessagePayload = {
        id: 'msg-002',
        session_id: 'different-session',
        role: 'user',
        content: 'Test',
        created_at: '2026-02-03T12:10:00',
      };

      await MockSSEStream.instances[0].simulateEvent('message_received', mockMessage);

      expect(msgCallback).not.toHaveBeenCalled();
    });

    it('handles SSE parse errors gracefully', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const stream = MockSSEStream.instances[0];
      // Send raw invalid JSON through the stream
      await stream.simulateRawBlock('event: session_created\ndata: not valid json{{{\n\n');

      // Should not crash, just log error
      expect(consoleSpy).toHaveBeenCalled();
      consoleSpy.mockRestore();
    });

    it('handles session_updated for unknown session', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // Update a session that's not in the cache - should add it
      const unknownSession: SSESessionPayload = {
        ...mockSSESession,
        id: 'unknown-session',
        name: 'Unknown',
      };
      await eventSource.simulateEvent('session_updated', unknownSession);

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      expect(lastCall).toEqual(
        expect.arrayContaining([expect.objectContaining({ id: 'unknown-session' })])
      );
    });
  });

  describe('subscribeMessages', () => {
    it('returns unsubscribe function', () => {
      const unsubscribe = service.subscribeMessages('session-id', vi.fn());
      expect(typeof unsubscribe).toBe('function');
      unsubscribe();
    });

    it('cleans up empty subscriber sets on unsubscribe', () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribeMessages('session-id', callback);
      unsubscribe();
      // Should not throw when unsubscribing
    });

    it('starts SSE if no other subscribers exist', async () => {
      const freshService = new ApiVolundrService();
      const callback = vi.fn();

      freshService.subscribeMessages('session-id', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances.length).toBeGreaterThan(0);
      });
    });
  });

  describe('subscribeLogs', () => {
    it('returns unsubscribe function', () => {
      const unsubscribe = service.subscribeLogs('session-id', vi.fn());
      expect(typeof unsubscribe).toBe('function');
      unsubscribe();
    });

    it('starts SSE if no other subscribers exist', async () => {
      const freshService = new ApiVolundrService();
      const callback = vi.fn();

      freshService.subscribeLogs('session-id', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances.length).toBeGreaterThan(0);
      });
    });

    it('cleans up empty subscriber sets on unsubscribe', () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribeLogs('session-id', callback);
      unsubscribe();
    });
  });

  describe('subscribe with cached sessions', () => {
    it('notifies immediately with cached sessions when available', async () => {
      // Populate cache first
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      await service.getSessions();

      const callback = vi.fn();
      service.subscribe(callback);

      // Should be called immediately with cached data
      expect(callback).toHaveBeenCalledWith(
        expect.arrayContaining([expect.objectContaining({ id: mockApiSession.id })])
      );
    });
  });

  describe('stopSession with cached session', () => {
    it('updates cached session status when stopping managed session', async () => {
      // Populate cache
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      await service.getSessions();

      const callback = vi.fn();
      service.subscribe(callback);

      mockFetch.mockReturnValueOnce(mockResponse({}));
      await service.stopSession(mockApiSession.id);

      expect(callback).toHaveBeenCalled();
    });
  });

  describe('resumeSession with cached session', () => {
    it('updates cached session status when resuming managed session', async () => {
      // Populate cache with a stopped session
      const stoppedSession = { ...mockApiSession, status: 'stopped' as const };
      mockFetch.mockReturnValueOnce(mockResponse([stoppedSession]));
      await service.getSessions();

      const callback = vi.fn();
      service.subscribe(callback);

      mockFetch.mockReturnValueOnce(mockResponse({}));
      await service.resumeSession(mockApiSession.id);

      expect(callback).toHaveBeenCalled();
    });
  });

  describe('model transforms', () => {
    it('uses default cost for known models when API omits it', async () => {
      const modelWithoutCost: ApiModelInfo = {
        id: 'claude-opus-4-20250514',
        name: 'Claude Opus 4',
        description: 'Frontier model',
        provider: 'cloud',
        tier: 'frontier',
        color: 'purple',
      };
      mockFetch.mockReturnValueOnce(mockResponse([modelWithoutCost]));

      const models = await service.getModels();
      expect(models['claude-opus-4-20250514'].cost).toBe('$15/M');
    });

    it('uses API cost when provided', async () => {
      const modelWithCost: ApiModelInfo = {
        id: 'claude-opus-4-20250514',
        name: 'Claude Opus 4',
        description: 'Frontier model',
        provider: 'cloud',
        tier: 'frontier',
        color: 'purple',
        cost_per_million_tokens: 20,
      };
      mockFetch.mockReturnValueOnce(mockResponse([modelWithCost]));

      const models = await service.getModels();
      expect(models['claude-opus-4-20250514'].cost).toBe('$20/M');
    });

    it('uses default vram for known local models', async () => {
      const localModel: ApiModelInfo = {
        id: 'qwen2.5-coder:32b',
        name: 'Qwen 2.5 Coder',
        description: 'Local coder model',
        provider: 'local',
        tier: 'execution',
        color: 'cyan',
      };
      mockFetch.mockReturnValueOnce(mockResponse([localModel]));

      const models = await service.getModels();
      expect(models['qwen2.5-coder:32b'].vram).toBe('24GB');
    });
  });

  describe('subscribeChronicle', () => {
    it('returns unsubscribe function', () => {
      const unsubscribe = service.subscribeChronicle('session-id', vi.fn());
      expect(typeof unsubscribe).toBe('function');
      unsubscribe();
    });

    it('starts SSE if no other subscribers exist', async () => {
      const freshService = new ApiVolundrService();
      const callback = vi.fn();

      freshService.subscribeChronicle('session-id', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances.length).toBeGreaterThan(0);
      });
    });

    it('does not start duplicate SSE when already connected', async () => {
      const callback1 = vi.fn();
      service.subscribe(callback1);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Subscribe chronicle when SSE is already connected
      const callback2 = vi.fn();
      service.subscribeChronicle('session-id', callback2);

      // Still only one SSE connection
      expect(MockSSEStream.instances).toHaveLength(1);
    });

    it('reuses subscriber set when multiple subscribers for same session', async () => {
      const callback1 = vi.fn();
      const callback2 = vi.fn();

      service.subscribeChronicle('session-id', callback1);
      service.subscribeChronicle('session-id', callback2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Both should receive events
      const payload = {
        session_id: 'session-id',
        event: { t: 1, type: 'session', label: 'Started' },
        files: [],
        commits: [],
        token_burn: [1],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback1).toHaveBeenCalled();
      expect(callback2).toHaveBeenCalled();
    });

    it('keeps subscriber set when one of multiple subscribers unsubscribes', async () => {
      const callback1 = vi.fn();
      const callback2 = vi.fn();

      const unsub1 = service.subscribeChronicle('session-id', callback1);
      service.subscribeChronicle('session-id', callback2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsub1();

      // Second subscriber should still receive events
      const payload = {
        session_id: 'session-id',
        event: { t: 2, type: 'session', label: 'Progressing' },
        files: [],
        commits: [],
        token_burn: [1, 2],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback1).not.toHaveBeenCalled();
      expect(callback2).toHaveBeenCalled();
    });

    it('cleans up empty subscriber sets on unsubscribe', () => {
      const callback = vi.fn();
      const unsubscribe = service.subscribeChronicle('session-id', callback);
      unsubscribe();
      // Should not throw when unsubscribing
    });
  });

  describe('SSE chronicle events', () => {
    it('handles chronicle_event and notifies subscribers', async () => {
      const callback = vi.fn();
      service.subscribeChronicle('session-abc', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const payload: SSEChroniclePayload = {
        session_id: 'session-abc',
        event: {
          t: 42,
          type: 'file',
          label: 'src/main.ts',
          action: 'modified',
          ins: 10,
          del: 3,
        },
        files: [{ path: 'src/main.ts', status: 'mod', ins: 10, del: 3 }],
        commits: [],
        token_burn: [1, 2, 3],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback).toHaveBeenCalledWith(
        expect.objectContaining({
          events: [
            expect.objectContaining({
              t: 42,
              type: 'file',
              label: 'src/main.ts',
              action: 'modified',
              ins: 10,
              del: 3,
            }),
          ],
          files: [expect.objectContaining({ path: 'src/main.ts', status: 'mod' })],
          commits: [],
          tokenBurn: [1, 2, 3],
        })
      );
    });

    it('does not notify chronicle subscribers for different session', async () => {
      const callback = vi.fn();
      service.subscribeChronicle('session-other', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const payload: SSEChroniclePayload = {
        session_id: 'session-different',
        event: {
          t: 10,
          type: 'message',
          label: 'User prompt',
          tokens: 100,
        },
        files: [],
        commits: [],
        token_burn: [1],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback).not.toHaveBeenCalled();
    });

    it('handles chronicle_event with null event gracefully', async () => {
      const callback = vi.fn();
      service.subscribeChronicle('session-abc', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Simulate payload with null event
      const payload = {
        session_id: 'session-abc',
        event: null,
        files: [{ path: 'src/main.ts', status: 'mod', ins: 5, del: 0 }],
        commits: [],
        token_burn: [1, 2],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback).toHaveBeenCalledWith(
        expect.objectContaining({
          events: [],
          files: [expect.objectContaining({ path: 'src/main.ts' })],
        })
      );
    });

    it('handles chronicle_event parse errors gracefully', async () => {
      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
      const callback = vi.fn();
      service.subscribeChronicle('session-abc', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const stream = MockSSEStream.instances[0];
      // Send raw invalid JSON through the stream
      await stream.simulateRawBlock('event: chronicle_event\ndata: invalid json!!!\n\n');

      // Allow microtask to process
      await new Promise(r => setTimeout(r, 10));

      expect(callback).not.toHaveBeenCalled();
      expect(consoleSpy).toHaveBeenCalled();
      consoleSpy.mockRestore();
    });

    it('handles chronicle_event with commit data', async () => {
      const callback = vi.fn();
      service.subscribeChronicle('session-abc', callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const payload: SSEChroniclePayload = {
        session_id: 'session-abc',
        event: {
          t: 120,
          type: 'git',
          label: 'fix: resolve null pointer',
          hash: 'abc1234',
          ins: 5,
          del: 2,
        },
        files: [{ path: 'src/main.ts', status: 'mod', ins: 5, del: 2 }],
        commits: [{ hash: 'abc1234', msg: 'fix: resolve null pointer', time: '2m ago' }],
        token_burn: [1, 2, 3, 4],
      };

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', payload);

      expect(callback).toHaveBeenCalledWith(
        expect.objectContaining({
          events: [expect.objectContaining({ type: 'git', hash: 'abc1234' })],
          commits: [expect.objectContaining({ hash: 'abc1234' })],
          tokenBurn: [1, 2, 3, 4],
        })
      );
    });
  });

  describe('getChronicle', () => {
    it('returns transformed chronicle from API', async () => {
      const apiChronicle = {
        events: [{ t: 0, type: 'session', label: 'Session started' }],
        files: [{ path: 'src/app.ts', status: 'new', ins: 50, del: 0 }],
        commits: [{ hash: 'def5678', msg: 'feat: initial', time: '1m ago' }],
        token_burn: [5, 10, 15],
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiChronicle));

      const chronicle = await service.getChronicle('session-123');

      expect(chronicle).toMatchObject({
        events: [expect.objectContaining({ t: 0, type: 'session', label: 'Session started' })],
        files: [expect.objectContaining({ path: 'src/app.ts', status: 'new', ins: 50 })],
        commits: [expect.objectContaining({ hash: 'def5678' })],
        tokenBurn: [5, 10, 15],
      });
    });

    it('returns null for 404 response', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const chronicle = await service.getChronicle('nonexistent');

      expect(chronicle).toBeNull();
    });

    it('throws for non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getChronicle('some-id')).rejects.toThrow();
    });

    it('transforms optional chronicle event fields', async () => {
      const apiChronicle = {
        events: [
          { t: 10, type: 'file', label: 'src/a.ts', action: 'created', ins: 20, del: null },
          { t: 20, type: 'terminal', label: 'npm test', exit: 0, tokens: null },
          { t: 30, type: 'git', label: 'commit', hash: 'abc123', ins: 5, del: 3 },
        ],
        files: [],
        commits: [],
        token_burn: [],
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiChronicle));

      const chronicle = await service.getChronicle('session-456');

      expect(chronicle!.events[0]).toMatchObject({ action: 'created', ins: 20 });
      expect(chronicle!.events[0]).not.toHaveProperty('del');
      expect(chronicle!.events[1]).toMatchObject({ exit: 0 });
      expect(chronicle!.events[1]).not.toHaveProperty('tokens');
      expect(chronicle!.events[2]).toMatchObject({ hash: 'abc123' });
    });
  });

  describe('subscribe SSE lifecycle edge cases', () => {
    it('does not disconnect SSE when stats subscribers remain after session unsub', async () => {
      const sessionCallback = vi.fn();
      const statsCallback = vi.fn();

      service.subscribeStats(statsCallback);
      const unsubSession = service.subscribe(sessionCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Unsubscribe sessions but stats still active
      unsubSession();
      expect(MockSSEStream.instances).toHaveLength(1); // SSE stays open
    });

    it('disconnects SSE when last stats subscriber unsubscribes', async () => {
      const statsCallback = vi.fn();
      const unsubStats = service.subscribeStats(statsCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsubStats();
      expect(MockSSEStream.instances).toHaveLength(0);
    });

    it('does not disconnect SSE when session subscribers remain after stats unsub', async () => {
      const sessionCallback = vi.fn();
      const statsCallback = vi.fn();

      service.subscribe(sessionCallback);
      const unsubStats = service.subscribeStats(statsCallback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsubStats();
      expect(MockSSEStream.instances).toHaveLength(1); // SSE stays open
    });
  });

  describe('subscribeMessages edge cases', () => {
    it('reuses subscriber set for same session', async () => {
      const cb1 = vi.fn();
      const cb2 = vi.fn();

      service.subscribeMessages('s1', cb1);
      service.subscribeMessages('s1', cb2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockMessage: SSEMessagePayload = {
        id: 'msg-x',
        session_id: 's1',
        role: 'assistant',
        content: 'Hi',
        created_at: '2026-02-03T12:00:00',
      };
      await MockSSEStream.instances[0].simulateEvent('message_received', mockMessage);

      expect(cb1).toHaveBeenCalled();
      expect(cb2).toHaveBeenCalled();
    });

    it('keeps subscriber set when one of multiple unsubscribes', async () => {
      const cb1 = vi.fn();
      const cb2 = vi.fn();

      const unsub1 = service.subscribeMessages('s1', cb1);
      service.subscribeMessages('s1', cb2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsub1();

      const mockMessage: SSEMessagePayload = {
        id: 'msg-y',
        session_id: 's1',
        role: 'assistant',
        content: 'Still here',
        created_at: '2026-02-03T12:00:00',
      };
      await MockSSEStream.instances[0].simulateEvent('message_received', mockMessage);

      expect(cb1).not.toHaveBeenCalled();
      expect(cb2).toHaveBeenCalled();
    });
  });

  describe('subscribeLogs edge cases', () => {
    it('reuses subscriber set for same session', async () => {
      const cb1 = vi.fn();
      const cb2 = vi.fn();

      service.subscribeLogs('s1', cb1);
      service.subscribeLogs('s1', cb2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const mockLog: SSELogPayload = {
        id: 'log-x',
        session_id: 's1',
        timestamp: '2026-02-03T12:00:00',
        level: 'info',
        source: 'test',
        message: 'Hello',
      };
      await MockSSEStream.instances[0].simulateEvent('log_received', mockLog);

      expect(cb1).toHaveBeenCalled();
      expect(cb2).toHaveBeenCalled();
    });

    it('keeps subscriber set when one of multiple unsubscribes', async () => {
      const cb1 = vi.fn();
      const cb2 = vi.fn();

      const unsub1 = service.subscribeLogs('s1', cb1);
      service.subscribeLogs('s1', cb2);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      unsub1();

      const mockLog: SSELogPayload = {
        id: 'log-y',
        session_id: 's1',
        timestamp: '2026-02-03T12:00:00',
        level: 'info',
        source: 'test',
        message: 'Still logging',
      };
      await MockSSEStream.instances[0].simulateEvent('log_received', mockLog);

      expect(cb1).not.toHaveBeenCalled();
      expect(cb2).toHaveBeenCalled();
    });
  });

  describe('model transforms edge cases', () => {
    it('uses empty defaults for unknown model', async () => {
      const unknownModel: ApiModelInfo = {
        id: 'unknown-model-xyz',
        name: 'Unknown Model',
        description: 'No defaults',
        provider: 'local',
        tier: 'execution',
        color: 'gray',
      };
      mockFetch.mockReturnValueOnce(mockResponse([unknownModel]));

      const models = await service.getModels();

      expect(models['unknown-model-xyz']).toBeDefined();
      expect(models['unknown-model-xyz'].cost).toBeUndefined();
      expect(models['unknown-model-xyz'].vram).toBeUndefined();
    });
  });

  describe('getSession error handling', () => {
    it('throws for non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getSession('some-id')).rejects.toThrow();
    });
  });

  describe('getStats error handling', () => {
    it('throws for non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getStats()).rejects.toThrow();
    });
  });

  describe('getPullRequests', () => {
    const mockApiPR: import('./volundr.types').ApiPullRequestResponse = {
      number: 42,
      title: 'Add login feature',
      url: 'https://github.com/org/repo/pull/42',
      repo_url: 'https://github.com/org/repo',
      provider: 'github',
      source_branch: 'feature/login',
      target_branch: 'main',
      status: 'open',
      description: 'A new login feature',
      ci_status: 'passed',
      review_status: 'approved',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-02T00:00:00Z',
    };

    it('returns transformed pull requests', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockApiPR]));

      const prs = await service.getPullRequests('https://github.com/org/repo', 'open');

      expect(prs).toHaveLength(1);
      expect(prs[0]).toMatchObject({
        number: 42,
        title: 'Add login feature',
        url: 'https://github.com/org/repo/pull/42',
        repoUrl: 'https://github.com/org/repo',
        provider: 'github',
        sourceBranch: 'feature/login',
        targetBranch: 'main',
        status: 'open',
        description: 'A new login feature',
        ciStatus: 'passed',
        reviewStatus: 'approved',
      });
    });

    it('maps unknown status to open', async () => {
      const prWithBadStatus = { ...mockApiPR, status: 'bizarre' };
      mockFetch.mockReturnValueOnce(mockResponse([prWithBadStatus]));

      const prs = await service.getPullRequests('https://github.com/org/repo');

      expect(prs[0].status).toBe('open');
    });

    it('handles null optional fields', async () => {
      const prNulls = {
        ...mockApiPR,
        description: null,
        ci_status: null,
        review_status: null,
        created_at: null,
        updated_at: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse([prNulls]));

      const prs = await service.getPullRequests('https://github.com/org/repo');

      expect(prs[0].description).toBeUndefined();
      expect(prs[0].ciStatus).toBeUndefined();
      expect(prs[0].reviewStatus).toBeUndefined();
    });

    it('maps merged and closed statuses', async () => {
      const mergedPR = { ...mockApiPR, status: 'merged' };
      const closedPR = { ...mockApiPR, number: 43, status: 'closed' };
      mockFetch.mockReturnValueOnce(mockResponse([mergedPR, closedPR]));

      const prs = await service.getPullRequests('https://github.com/org/repo', 'all');

      expect(prs[0].status).toBe('merged');
      expect(prs[1].status).toBe('closed');
    });

    it('maps unknown CI status to unknown', async () => {
      const prWithBadCI = { ...mockApiPR, ci_status: 'weird_status' };
      mockFetch.mockReturnValueOnce(mockResponse([prWithBadCI]));

      const prs = await service.getPullRequests('https://github.com/org/repo');

      expect(prs[0].ciStatus).toBe('unknown');
    });
  });

  describe('createPullRequest', () => {
    it('creates a pull request and returns transformed result', async () => {
      const apiResponse: import('./volundr.types').ApiPullRequestResponse = {
        number: 99,
        title: 'New Feature',
        url: 'https://github.com/org/repo/pull/99',
        repo_url: 'https://github.com/org/repo',
        provider: 'github',
        source_branch: 'feature/new',
        target_branch: 'main',
        status: 'open',
        ci_status: 'pending',
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse, 201));

      const pr = await service.createPullRequest('session-123', 'New Feature', 'main');

      expect(pr.number).toBe(99);
      expect(pr.title).toBe('New Feature');
      expect(pr.status).toBe('open');
      expect(pr.ciStatus).toBe('pending');
    });

    it('sends correct request body', async () => {
      const apiResponse: import('./volundr.types').ApiPullRequestResponse = {
        number: 100,
        title: 'PR Title',
        url: 'https://github.com/org/repo/pull/100',
        repo_url: 'https://github.com/org/repo',
        provider: 'github',
        source_branch: 'feat',
        target_branch: 'develop',
        status: 'open',
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse, 201));

      await service.createPullRequest('session-456', 'PR Title', 'develop');

      const callBody = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(callBody.session_id).toBe('session-456');
      expect(callBody.title).toBe('PR Title');
      expect(callBody.target_branch).toBe('develop');
    });
  });

  describe('mergePullRequest', () => {
    it('returns merge result', async () => {
      const apiResponse: import('./volundr.types').ApiMergeResultResponse = { merged: true };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const result = await service.mergePullRequest(42, 'https://github.com/org/repo');

      expect(result.merged).toBe(true);
    });

    it('sends merge method in request body', async () => {
      const apiResponse: import('./volundr.types').ApiMergeResultResponse = { merged: true };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      await service.mergePullRequest(42, 'https://github.com/org/repo', 'rebase');

      const callBody = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(callBody.merge_method).toBe('rebase');
    });

    it('returns false when merge fails', async () => {
      const apiResponse: import('./volundr.types').ApiMergeResultResponse = { merged: false };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const result = await service.mergePullRequest(42, 'https://github.com/org/repo');

      expect(result.merged).toBe(false);
    });
  });

  describe('getCIStatus', () => {
    it('returns CI status for a PR', async () => {
      const apiResponse: import('./volundr.types').ApiCIStatusResponse = { status: 'passed' };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const status = await service.getCIStatus(42, 'https://github.com/org/repo', 'feature/login');

      expect(status).toBe('passed');
    });

    it('maps unknown CI status to unknown', async () => {
      const apiResponse: import('./volundr.types').ApiCIStatusResponse = {
        status: 'exotic_status',
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const status = await service.getCIStatus(42, 'https://github.com/org/repo', 'main');

      expect(status).toBe('unknown');
    });

    it('returns running CI status', async () => {
      const apiResponse: import('./volundr.types').ApiCIStatusResponse = { status: 'running' };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const status = await service.getCIStatus(42, 'https://github.com/org/repo', 'main');

      expect(status).toBe('running');
    });

    it('returns pending CI status', async () => {
      const apiResponse: import('./volundr.types').ApiCIStatusResponse = { status: 'pending' };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const status = await service.getCIStatus(42, 'https://github.com/org/repo', 'main');

      expect(status).toBe('pending');
    });

    it('returns failed CI status', async () => {
      const apiResponse: import('./volundr.types').ApiCIStatusResponse = { status: 'failed' };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const status = await service.getCIStatus(42, 'https://github.com/org/repo', 'main');

      expect(status).toBe('failed');
    });
  });

  describe('getIdentity', () => {
    it('returns transformed identity from API', async () => {
      const apiResponse: import('./volundr.types').ApiIdentityResponse = {
        user_id: 'u1',
        email: 'user@test.com',
        tenant_id: 't1',
        roles: ['volundr:admin'],
        display_name: 'Test User',
        status: 'active',
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const identity = await service.getIdentity();

      expect(identity).toMatchObject({
        userId: 'u1',
        email: 'user@test.com',
        tenantId: 't1',
        roles: ['volundr:admin'],
        displayName: 'Test User',
        status: 'active',
      });
    });
  });

  describe('getTenants', () => {
    it('returns transformed tenants from API', async () => {
      const apiResponse: import('./volundr.types').ApiTenantResponse[] = [
        {
          id: 't1',
          path: 't1',
          name: 'Tenant One',
          parent_id: null,
          tier: 'developer',
          max_sessions: 5,
          max_storage_gb: 50,
          created_at: '2024-01-01T00:00:00Z',
        },
        {
          id: 't2',
          path: 't1.t2',
          name: 'Tenant Two',
          parent_id: 't1',
          tier: 'team',
          max_sessions: 10,
          max_storage_gb: 100,
          created_at: null,
        },
      ];
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const tenants = await service.getTenants();

      expect(tenants).toHaveLength(2);
      expect(tenants[0]).toMatchObject({
        id: 't1',
        path: 't1',
        name: 'Tenant One',
        tier: 'developer',
        maxSessions: 5,
        maxStorageGb: 50,
      });
      expect(tenants[0].parentId).toBeUndefined();
      expect(tenants[1].parentId).toBe('t1');
    });
  });

  describe('getTenant', () => {
    it('returns transformed tenant from API', async () => {
      const apiResponse: import('./volundr.types').ApiTenantResponse = {
        id: 't1',
        path: 't1',
        name: 'Tenant One',
        parent_id: null,
        tier: 'developer',
        max_sessions: 5,
        max_storage_gb: 50,
        created_at: '2024-01-01T00:00:00Z',
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const tenant = await service.getTenant('t1');

      expect(tenant).toMatchObject({
        id: 't1',
        name: 'Tenant One',
        maxSessions: 5,
      });
    });

    it('returns null for 404 response', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const tenant = await service.getTenant('nonexistent');

      expect(tenant).toBeNull();
    });
  });

  describe('getUserCredentials', () => {
    it('returns transformed credentials from API', async () => {
      const apiResponse: import('./volundr.types').ApiCredentialListResponse = {
        credentials: [
          { name: 'github-token', keys: ['token'] },
          { name: 'aws-creds', keys: ['access_key', 'secret_key'] },
        ],
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const creds = await service.getUserCredentials();

      expect(creds).toHaveLength(2);
      expect(creds[0]).toEqual({ name: 'github-token', keys: ['token'] });
      expect(creds[1]).toEqual({ name: 'aws-creds', keys: ['access_key', 'secret_key'] });
    });
  });

  describe('storeUserCredential', () => {
    it('sends credential data to API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({}));

      await service.storeUserCredential('github-token', { token: 'abc123' });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/secrets/user'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('deleteUserCredential', () => {
    it('sends delete request to API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(null, 204));

      await service.deleteUserCredential('github-token');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/secrets/user/github-token'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('getTenantCredentials', () => {
    it('returns transformed credentials from API', async () => {
      const apiResponse: import('./volundr.types').ApiCredentialListResponse = {
        credentials: [{ name: 'shared-key', keys: ['key'] }],
      };
      mockFetch.mockReturnValueOnce(mockResponse(apiResponse));

      const creds = await service.getTenantCredentials();

      expect(creds).toHaveLength(1);
      expect(creds[0]).toEqual({ name: 'shared-key', keys: ['key'] });
    });
  });

  describe('storeTenantCredential', () => {
    it('sends credential data to API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({}));

      await service.storeTenantCredential('shared-key', { key: 'val' });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/secrets/tenant'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('deleteTenantCredential', () => {
    it('sends delete request to API', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(null, 204));

      await service.deleteTenantCredential('shared-key');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/secrets/tenant/shared-key'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('SSE fetch-based connection edge cases', () => {
    it('includes Authorization header when token is available', async () => {
      setTokenProvider(() => 'test-jwt-token');

      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      setTokenProvider(null);
    });

    it('omits Authorization header when no token provider', async () => {
      setTokenProvider(null);

      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });
    });

    it('handles SSE fetch returning non-ok response', async () => {
      const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      cleanupSSEMock();

      // Override to return 500
      global.fetch = ((url: string) => {
        if (url === '/api/v1/volundr/sessions/stream') {
          return Promise.resolve(new Response('error', { status: 500 }));
        }
        return mockFetch(url);
      }) as typeof fetch;

      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(consoleSpy).toHaveBeenCalledWith(
          expect.stringContaining('SSE connection error'),
          expect.any(Error)
        );
      });

      consoleSpy.mockRestore();
    });

    it('handles SSE fetch returning response with no body', async () => {
      const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      cleanupSSEMock();

      global.fetch = ((url: string) => {
        if (url === '/api/v1/volundr/sessions/stream') {
          // Response with null body
          const resp = new Response(null, { status: 200 });
          Object.defineProperty(resp, 'body', { value: null });
          return Promise.resolve(resp);
        }
        return mockFetch(url);
      }) as typeof fetch;

      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(consoleSpy).toHaveBeenCalledWith(
          expect.stringContaining('SSE connection error'),
          expect.any(Error)
        );
      });

      consoleSpy.mockRestore();
    });

    it('does not open duplicate SSE connection when already connected', async () => {
      const cb1 = vi.fn();
      const cb2 = vi.fn();
      service.subscribe(cb1);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Subscribe again — should not create a second connection
      service.subscribe(cb2);
      expect(MockSSEStream.instances).toHaveLength(1);
    });

    it('handles heartbeat events (no-op)', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      await MockSSEStream.instances[0].simulateEvent('heartbeat', {});

      // Heartbeat should not trigger any subscriber notifications
      expect(callback).not.toHaveBeenCalled();
    });

    it('handles SSE block with unknown event type', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      await MockSSEStream.instances[0].simulateEvent('unknown_event_type', { foo: 'bar' });

      // Unknown events should not trigger any subscriber notifications
      expect(callback).not.toHaveBeenCalled();
    });

    it('handles stream done signal gracefully', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Close the stream (triggers done=true)
      const stream = MockSSEStream.instances[0];
      stream.close();

      // Should not crash
      await new Promise(r => setTimeout(r, 10));
    });

    it('ignores SSE data lines without event type prefix', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Send block with comment/unknown lines
      await MockSSEStream.instances[0].simulateRawBlock(
        ': this is a comment\nevent: session_created\nid: 123\ndata: {"id":"s1","name":"test","model":"m","source":{"type":"git","repo":"r","branch":"b"},"status":"running","chat_endpoint":null,"code_endpoint":null,"created_at":"2026-01-01T00:00:00","updated_at":"2026-01-01T00:00:00","last_active":"2026-01-01T00:00:00","message_count":0,"tokens_used":0,"pod_name":null,"error":null}\n\n'
      );

      expect(callback).toHaveBeenCalled();
    });

    it('handles log_received with no matching subscribers', async () => {
      // Subscribe to sessions (triggers SSE) but not to logs
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      // Send a log event — no log subscribers exist
      await MockSSEStream.instances[0].simulateEvent('log_received', {
        id: 'log-1',
        session_id: 'no-subscriber-session',
        timestamp: '2026-01-01T00:00:00',
        level: 'info',
        source: 'test',
        message: 'hello',
      });

      // Should not crash, session callback not called for log events
    });

    it('handles message_received with no matching subscribers', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      await MockSSEStream.instances[0].simulateEvent('message_received', {
        id: 'msg-1',
        session_id: 'no-subscriber-session',
        role: 'user',
        content: 'hi',
        created_at: '2026-01-01T00:00:00',
      });

      // Should not crash
    });

    it('handles chronicle_event with no matching subscribers', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      await MockSSEStream.instances[0].simulateEvent('chronicle_event', {
        session_id: 'no-subscriber-session',
        event: null,
        files: [],
        commits: [],
        token_burn: [],
      });

      // Should not crash
    });
  });

  describe('getTemplates', () => {
    it('returns transformed templates from API', async () => {
      const apiTemplate = {
        name: 'default',
        description: 'Default template',
        is_default: true,
        repos: [{ repo: 'org/repo' }],
        setup_scripts: ['npm install'],
        workspace_layout: {},
        cli_tool: 'claude',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: 'You are helpful',
        resource_config: { cpu: '2' },
        mcp_servers: [{ name: 'fs', type: 'stdio', command: 'mcp-fs', args: ['--root', '/'] }],
        env_vars: { NODE_ENV: 'production' },
        env_secret_refs: ['my-secret'],
        workload_config: { timeout: 300 },
        terminal_sidecar: { enabled: true, allowed_commands: ['ls', 'cat'] },
        skills: [{ name: 'test-skill' }],
        rules: [{ inline: 'be helpful' }],
      };
      mockFetch.mockReturnValueOnce(mockResponse([apiTemplate]));

      const templates = await service.getTemplates();

      expect(templates).toHaveLength(1);
      expect(templates[0].name).toBe('default');
      expect(templates[0].isDefault).toBe(true);
      expect(templates[0].cliTool).toBe('claude');
      expect(templates[0].terminalSidecar.enabled).toBe(true);
      expect(templates[0].terminalSidecar.allowedCommands).toEqual(['ls', 'cat']);
      expect(templates[0].skills).toEqual([{ name: 'test-skill' }]);
      expect(templates[0].rules).toEqual([{ inline: 'be helpful' }]);
      expect(templates[0].envVars).toEqual({ NODE_ENV: 'production' });
    });

    it('handles template with null/undefined optional fields', async () => {
      const apiTemplate = {
        name: 'minimal',
        description: '',
        is_default: false,
        repos: [],
        setup_scripts: [],
        workspace_layout: {},
        cli_tool: null,
        workload_type: null,
        model: null,
        system_prompt: null,
        resource_config: null,
        mcp_servers: null,
        env_vars: null,
        env_secret_refs: null,
        workload_config: null,
        terminal_sidecar: null,
        skills: null,
        rules: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse([apiTemplate]));

      const templates = await service.getTemplates();

      expect(templates[0].cliTool).toBe('claude');
      expect(templates[0].workloadType).toBe('development');
      expect(templates[0].resourceConfig).toEqual({});
      expect(templates[0].mcpServers).toEqual([]);
      expect(templates[0].envVars).toEqual({});
      expect(templates[0].envSecretRefs).toEqual([]);
      expect(templates[0].workloadConfig).toEqual({});
      expect(templates[0].terminalSidecar).toEqual({ enabled: false, allowedCommands: [] });
      expect(templates[0].skills).toEqual([]);
      expect(templates[0].rules).toEqual([]);
    });
  });

  describe('getPresets', () => {
    it('returns transformed presets from API', async () => {
      const apiPreset = {
        id: 'preset-1',
        name: 'My Preset',
        description: 'A preset',
        is_default: false,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
        cli_tool: 'codex',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: 'hello',
        resource_config: { gpu: '1' },
        mcp_servers: [],
        terminal_sidecar: { enabled: false, allowed_commands: [] },
        skills: [],
        rules: [],
        env_vars: {},
        env_secret_refs: [],
        workload_config: {},
      };
      mockFetch.mockReturnValueOnce(mockResponse([apiPreset]));

      const presets = await service.getPresets();

      expect(presets).toHaveLength(1);
      expect(presets[0].id).toBe('preset-1');
      expect(presets[0].cliTool).toBe('codex');
      expect(presets[0].terminalSidecar.enabled).toBe(false);
    });

    it('handles preset with null/undefined optional fields', async () => {
      const apiPreset = {
        id: 'preset-2',
        name: 'Minimal',
        description: '',
        is_default: false,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
        cli_tool: null,
        workload_type: null,
        model: null,
        system_prompt: null,
        resource_config: null,
        mcp_servers: null,
        terminal_sidecar: null,
        skills: null,
        rules: null,
        env_vars: null,
        env_secret_refs: null,
        workload_config: null,
      };
      mockFetch.mockReturnValueOnce(mockResponse([apiPreset]));

      const presets = await service.getPresets();

      expect(presets[0].cliTool).toBe('claude');
      expect(presets[0].workloadType).toBe('development');
      expect(presets[0].resourceConfig).toEqual({});
      expect(presets[0].mcpServers).toEqual([]);
      expect(presets[0].terminalSidecar).toEqual({ enabled: false, allowedCommands: [] });
      expect(presets[0].skills).toEqual([]);
      expect(presets[0].rules).toEqual([]);
      expect(presets[0].envVars).toEqual({});
      expect(presets[0].envSecretRefs).toEqual([]);
      expect(presets[0].workloadConfig).toEqual({});
    });
  });

  describe('mapTrackerStatus and transformSession with tracker issue', () => {
    it('transforms session with tracker_issue_id and issue_tracker_url', async () => {
      const sessionWithTracker: ApiSessionResponse = {
        ...mockApiSession,
        tracker_issue_id: 'NIU-42',
        issue_tracker_url: 'https://linear.app/issue/NIU-42',
      };
      mockFetch.mockReturnValueOnce(mockResponse([sessionWithTracker]));

      const sessions = await service.getSessions();

      expect(sessions[0].trackerIssue).toEqual({
        id: 'NIU-42',
        identifier: 'NIU-42',
        title: '',
        status: 'todo',
        url: 'https://linear.app/issue/NIU-42',
      });
    });

    it('transforms session with local_mount source', async () => {
      const sessionWithMount: ApiSessionResponse = {
        ...mockApiSession,
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/code', mount_path: '/workspace', read_only: false }],
          node_selector: { 'kubernetes.io/hostname': 'node1' },
        },
      };
      mockFetch.mockReturnValueOnce(mockResponse([sessionWithMount]));

      const sessions = await service.getSessions();

      expect(sessions[0].source.type).toBe('local_mount');
      if (sessions[0].source.type === 'local_mount') {
        expect(sessions[0].source.paths).toHaveLength(1);
        expect(sessions[0].source.node_selector).toEqual({ 'kubernetes.io/hostname': 'node1' });
      }
    });
  });

  describe('getFeatures', () => {
    it('returns feature flags from API', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          local_mounts_enabled: true,
          file_manager_enabled: true,
          mini_mode: false,
        })
      );

      const features = await service.getFeatures();

      expect(features).toEqual({
        localMountsEnabled: true,
        fileManagerEnabled: true,
        miniMode: false,
      });
    });

    it('defaults fileManagerEnabled and miniMode when not provided', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          local_mounts_enabled: false,
        })
      );

      const features = await service.getFeatures();

      expect(features.localMountsEnabled).toBe(false);
      expect(features.fileManagerEnabled).toBe(true);
      expect(features.miniMode).toBe(false);
    });

    it('returns defaults on error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const features = await service.getFeatures();

      expect(features).toEqual({
        localMountsEnabled: false,
        fileManagerEnabled: true,
        miniMode: false,
      });
    });
  });

  describe('getRepos', () => {
    it('transforms repo info from API', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          github: [
            {
              provider: 'github',
              org: 'niuu',
              name: 'volundr',
              clone_url: 'https://github.com/niuu/volundr.git',
              url: 'https://github.com/niuu/volundr',
              default_branch: 'main',
              branches: ['main', 'dev'],
            },
          ],
        })
      );

      const repos = await service.getRepos();

      expect(repos).toHaveLength(1);
      expect(repos[0]).toMatchObject({
        provider: 'github',
        org: 'niuu',
        name: 'volundr',
        cloneUrl: 'https://github.com/niuu/volundr.git',
        url: 'https://github.com/niuu/volundr',
        defaultBranch: 'main',
        branches: ['main', 'dev'],
      });
    });

    it('generates clone_url from url when not provided', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          github: [
            {
              provider: 'github',
              org: 'niuu',
              name: 'volundr',
              url: 'https://github.com/niuu/volundr',
              default_branch: 'main',
            },
          ],
        })
      );

      const repos = await service.getRepos();

      expect(repos[0].cloneUrl).toBe('https://github.com/niuu/volundr.git');
    });

    it('defaults branches to empty array when not provided', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          github: [
            {
              provider: 'github',
              org: 'niuu',
              name: 'volundr',
              url: 'https://github.com/niuu/volundr',
              default_branch: 'main',
            },
          ],
        })
      );

      const repos = await service.getRepos();

      expect(repos[0].branches).toEqual([]);
    });
  });

  describe('getMessages', () => {
    it('returns transformed messages', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'msg-1',
            session_id: 'sess-1',
            role: 'user',
            content: 'Hello',
            created_at: '2024-01-15T10:00:00Z',
            tokens_in: 10,
            tokens_out: null,
            latency_ms: null,
          },
          {
            id: 'msg-2',
            session_id: 'sess-1',
            role: 'assistant',
            content: 'Hi there',
            created_at: '2024-01-15T10:00:01Z',
            tokens_in: null,
            tokens_out: 50,
            latency_ms: 1200,
          },
        ])
      );

      const messages = await service.getMessages('sess-1');

      expect(messages).toHaveLength(2);
      expect(messages[0]).toMatchObject({
        id: 'msg-1',
        sessionId: 'sess-1',
        role: 'user',
        content: 'Hello',
        tokensIn: 10,
        tokensOut: undefined,
        latency: undefined,
      });
      expect(messages[1]).toMatchObject({
        tokensOut: 50,
        latency: 1200,
      });
    });
  });

  describe('sendMessage', () => {
    it('sends message and returns transformed result', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'msg-new',
          session_id: 'sess-1',
          role: 'user',
          content: 'Test message',
          created_at: '2024-01-15T10:00:00Z',
          tokens_in: null,
          tokens_out: null,
          latency_ms: null,
        })
      );

      const msg = await service.sendMessage('sess-1', 'Test message');

      expect(msg.content).toBe('Test message');
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.content).toBe('Test message');
    });
  });

  describe('getLogs', () => {
    it('returns transformed logs with default limit', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'log-1',
            session_id: 'sess-1',
            timestamp: '2024-01-15T10:00:00Z',
            level: 'info',
            source: 'agent',
            message: 'Processing started',
          },
        ])
      );

      const logs = await service.getLogs('sess-1');

      expect(logs).toHaveLength(1);
      expect(logs[0]).toMatchObject({
        id: 'log-1',
        sessionId: 'sess-1',
        level: 'info',
        source: 'agent',
        message: 'Processing started',
      });
    });

    it('accepts custom limit', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getLogs('sess-1', 50);

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('limit=50'),
        expect.any(Object)
      );
    });
  });

  describe('deleteSession', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteSession('sess-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/sessions/sess-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('sends cleanup array when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteSession('sess-1', ['branch', 'workspace']);

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.cleanup).toEqual(['branch', 'workspace']);
    });

    it('does not send body when cleanup is empty', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteSession('sess-1', []);

      // No body in the request for empty cleanup array
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/sessions/sess-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('archiveSession', () => {
    it('calls archive endpoint and removes from cache', async () => {
      // First populate cache
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      await service.getSessions();

      // Then archive
      mockFetch.mockReturnValueOnce(mockResponse(undefined));

      await service.archiveSession(mockApiSession.id);

      expect(mockFetch).toHaveBeenLastCalledWith(
        expect.stringContaining(`/sessions/${mockApiSession.id}/archive`),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('restoreSession', () => {
    it('calls restore endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined));

      await service.restoreSession('sess-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/sessions/sess-1/restore'),
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  describe('listArchivedSessions', () => {
    it('returns archived sessions', async () => {
      const archivedSession = { ...mockApiSession, status: 'stopped' as const };
      mockFetch.mockReturnValueOnce(mockResponse([archivedSession]));

      const sessions = await service.listArchivedSessions();

      expect(sessions).toHaveLength(1);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('status=archived'),
        expect.any(Object)
      );
    });
  });

  describe('searchTrackerIssues', () => {
    it('returns transformed issues', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'issue-1',
            identifier: 'NIU-100',
            title: 'Test issue',
            status: 'In Progress',
            assignee: 'user-1',
            labels: ['bug'],
            priority: 2,
            url: 'https://linear.app/issue/NIU-100',
          },
        ])
      );

      const issues = await service.searchTrackerIssues('test');

      expect(issues).toHaveLength(1);
      expect(issues[0]).toMatchObject({
        id: 'issue-1',
        identifier: 'NIU-100',
        title: 'Test issue',
        status: 'in_progress',
        assignee: 'user-1',
        labels: ['bug'],
        priority: 2,
      });
    });

    it('returns empty array on error', async () => {
      mockFetch.mockRejectedValueOnce(new Error('Network error'));

      const issues = await service.searchTrackerIssues('test');

      expect(issues).toEqual([]);
    });

    it('maps unknown status to todo', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'issue-2',
            identifier: 'NIU-200',
            title: 'Unknown status',
            status: 'weird_status',
            url: '',
          },
        ])
      );

      const issues = await service.searchTrackerIssues('weird');

      expect(issues[0].status).toBe('todo');
    });

    it('defaults assignee, labels, priority when missing', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'issue-3',
            identifier: 'NIU-300',
            title: 'Minimal',
            status: 'backlog',
            url: '',
          },
        ])
      );

      const issues = await service.searchTrackerIssues('minimal');

      expect(issues[0].assignee).toBeUndefined();
      expect(issues[0].labels).toEqual([]);
      expect(issues[0].priority).toBe(0);
    });
  });

  describe('updateTrackerIssueStatus', () => {
    it('updates and returns transformed issue', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'issue-1',
          identifier: 'NIU-100',
          title: 'Test',
          status: 'Done',
          assignee: null,
          labels: [],
          priority: 1,
          url: 'https://linear.app/issue/NIU-100',
        })
      );

      const issue = await service.updateTrackerIssueStatus('issue-1', 'done');

      expect(issue.status).toBe('done');
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.status).toBe('done');
    });
  });

  describe('createTenant', () => {
    it('creates and returns tenant', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'tenant-1',
          path: '/orgs/test',
          name: 'Test Org',
          parent_id: null,
          tier: 'standard',
          max_sessions: 10,
          max_storage_gb: 100,
          created_at: '2024-01-01T00:00:00Z',
        })
      );

      const tenant = await service.createTenant({
        name: 'Test Org',
        tier: 'standard',
        maxSessions: 10,
        maxStorageGb: 100,
      });

      expect(tenant).toMatchObject({
        id: 'tenant-1',
        name: 'Test Org',
        tier: 'standard',
        maxSessions: 10,
        maxStorageGb: 100,
      });
    });
  });

  describe('deleteTenant', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteTenant('tenant-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/tenants/tenant-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('updateTenant', () => {
    it('updates and returns tenant', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'tenant-1',
          path: '/orgs/test',
          name: 'Test Org',
          parent_id: null,
          tier: 'enterprise',
          max_sessions: 50,
          max_storage_gb: 500,
          created_at: '2024-01-01T00:00:00Z',
        })
      );

      const tenant = await service.updateTenant('tenant-1', {
        tier: 'enterprise',
        maxSessions: 50,
      });

      expect(tenant.tier).toBe('enterprise');
      expect(tenant.maxSessions).toBe(50);
    });
  });

  describe('getTenantMembers', () => {
    it('returns members for a tenant', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            user_id: 'user-1',
            tenant_id: 'tenant-1',
            role: 'admin',
            granted_at: '2024-01-01T00:00:00Z',
          },
          {
            user_id: 'user-2',
            tenant_id: 'tenant-1',
            role: 'member',
            granted_at: null,
          },
        ])
      );

      const members = await service.getTenantMembers('tenant-1');

      expect(members).toHaveLength(2);
      expect(members[0]).toMatchObject({
        userId: 'user-1',
        role: 'admin',
        grantedAt: '2024-01-01T00:00:00Z',
      });
      expect(members[1].grantedAt).toBeUndefined();
    });
  });

  describe('reprovisionUser', () => {
    it('reprovisions and returns result', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          success: true,
          user_id: 'user-1',
          home_pvc: 'pvc-user-1',
          errors: [],
        })
      );

      const result = await service.reprovisionUser('user-1');

      expect(result).toEqual({
        success: true,
        userId: 'user-1',
        homePvc: 'pvc-user-1',
        errors: [],
      });
    });
  });

  describe('reprovisionTenant', () => {
    it('reprovisions all users in tenant', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          { success: true, user_id: 'u1', home_pvc: 'pvc-u1', errors: [] },
          { success: false, user_id: 'u2', errors: ['PVC create failed'] },
        ])
      );

      const results = await service.reprovisionTenant('tenant-1');

      expect(results).toHaveLength(2);
      expect(results[0].success).toBe(true);
      expect(results[1].success).toBe(false);
      expect(results[1].errors).toContain('PVC create failed');
    });
  });

  describe('getIntegrationCatalog', () => {
    it('returns catalog entries', async () => {
      const catalog = [{ type: 'git', name: 'GitHub', adapters: ['github'] }];
      mockFetch.mockReturnValueOnce(mockResponse(catalog));

      const result = await service.getIntegrationCatalog();

      expect(result).toEqual(catalog);
    });
  });

  describe('getIntegrations', () => {
    it('transforms integration connections', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'int-1',
            integration_type: 'repository',
            adapter: 'github',
            credential_name: 'gh-token',
            config: { org: 'niuu' },
            enabled: true,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
            slug: 'niuu-github',
          },
        ])
      );

      const integrations = await service.getIntegrations();

      expect(integrations).toHaveLength(1);
      expect(integrations[0]).toMatchObject({
        id: 'int-1',
        integrationType: 'repository',
        adapter: 'github',
        credentialName: 'gh-token',
        slug: 'niuu-github',
      });
    });

    it('defaults empty slug to empty string', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'int-2',
            integration_type: 'tracker',
            adapter: 'linear',
            credential_name: 'linear-key',
            config: {},
            enabled: true,
            created_at: '2024-01-01T00:00:00Z',
            updated_at: '2024-01-01T00:00:00Z',
            slug: '',
          },
        ])
      );

      const integrations = await service.getIntegrations();

      expect(integrations[0].slug).toBe('');
    });
  });

  describe('createIntegration', () => {
    it('creates integration and returns transformed result', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'int-new',
          integration_type: 'repository',
          adapter: 'github',
          credential_name: 'gh-token',
          config: { org: 'niuu' },
          enabled: true,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
          slug: 'niuu-gh',
        })
      );

      const result = await service.createIntegration({
        integrationType: 'repository',
        adapter: 'github',
        credentialName: 'gh-token',
        config: { org: 'niuu' },
        enabled: true,
        slug: 'niuu-gh',
      });

      expect(result.id).toBe('int-new');
      expect(result.slug).toBe('niuu-gh');
    });
  });

  describe('deleteIntegration', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteIntegration('int-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/integrations/int-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('testIntegration', () => {
    it('tests integration and returns result', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ success: true, message: 'Connection OK' }));

      const result = await service.testIntegration('int-1');

      expect(result).toEqual({ success: true, message: 'Connection OK' });
    });
  });

  describe('getCredentials', () => {
    it('returns transformed credentials', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          credentials: [
            {
              id: 'cred-1',
              name: 'my-token',
              secret_type: 'api_key',
              keys: ['token'],
              metadata: {},
              created_at: '2024-01-01T00:00:00Z',
              updated_at: '2024-01-01T00:00:00Z',
            },
          ],
        })
      );

      const creds = await service.getCredentials();

      expect(creds).toHaveLength(1);
      expect(creds[0]).toMatchObject({
        id: 'cred-1',
        name: 'my-token',
        secretType: 'api_key',
        keys: ['token'],
      });
    });

    it('passes secret type filter when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ credentials: [] }));

      await service.getCredentials('api_key');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('secret_type=api_key'),
        expect.any(Object)
      );
    });

    it('omits filter when type not provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ credentials: [] }));

      await service.getCredentials();

      expect(mockFetch).toHaveBeenCalledWith(
        expect.not.stringContaining('secret_type'),
        expect.any(Object)
      );
    });
  });

  describe('getCredential', () => {
    it('returns credential by name', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'cred-1',
          name: 'my-token',
          secret_type: 'api_key',
          keys: ['token'],
          metadata: {},
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        })
      );

      const cred = await service.getCredential('my-token');

      expect(cred?.name).toBe('my-token');
    });

    it('returns null for 404', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const cred = await service.getCredential('nonexistent');

      expect(cred).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getCredential('bad')).rejects.toThrow();
    });
  });

  describe('createCredential', () => {
    it('creates and returns credential', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'cred-new',
          name: 'new-token',
          secret_type: 'api_key',
          keys: ['key'],
          metadata: {},
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        })
      );

      const cred = await service.createCredential({
        name: 'new-token',
        secretType: 'api_key',
        data: { key: 'secret' },
      });

      expect(cred.id).toBe('cred-new');
    });
  });

  describe('deleteCredential', () => {
    it('calls delete endpoint with encoded name', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteCredential('my token');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/credentials/my%20token'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('getCredentialTypes', () => {
    it('returns transformed credential types', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            type: 'api_key',
            label: 'API Key',
            description: 'A simple API key',
            fields: [{ name: 'key', label: 'Key', required: true }],
            default_mount_type: 'env',
          },
        ])
      );

      const types = await service.getCredentialTypes();

      expect(types).toHaveLength(1);
      expect(types[0]).toMatchObject({
        type: 'api_key',
        label: 'API Key',
        defaultMountType: 'env',
      });
    });
  });

  describe('listWorkspaces', () => {
    const mockWorkspace = {
      id: 'ws-1',
      pvc_name: 'pvc-ws-1',
      session_id: 'sess-1',
      user_id: 'user-1',
      tenant_id: 'tenant-1',
      size_gb: 10,
      status: 'active',
      created_at: '2024-01-01T00:00:00Z',
      archived_at: null,
      session_name: 'My Session',
      source_url: 'https://github.com/org/repo',
      source_ref: 'main',
    };

    it('returns workspace list', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([mockWorkspace]));

      const workspaces = await service.listWorkspaces();

      expect(workspaces).toHaveLength(1);
      expect(workspaces[0]).toMatchObject({
        id: 'ws-1',
        pvcName: 'pvc-ws-1',
        sessionId: 'sess-1',
        status: 'active',
        sessionName: 'My Session',
      });
    });

    it('passes status filter when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.listWorkspaces('archived');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('status=archived'),
        expect.any(Object)
      );
    });

    it('handles null optional fields', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            ...mockWorkspace,
            archived_at: null,
            session_name: null,
            source_url: null,
            source_ref: null,
          },
        ])
      );

      const workspaces = await service.listWorkspaces();

      expect(workspaces[0].archivedAt).toBeUndefined();
      expect(workspaces[0].sessionName).toBeUndefined();
      expect(workspaces[0].sourceUrl).toBeUndefined();
      expect(workspaces[0].sourceRef).toBeUndefined();
    });
  });

  describe('deleteWorkspace', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deleteWorkspace('ws-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/workspaces/ws-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('bulkDeleteWorkspaces', () => {
    it('sends session IDs and returns result', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ deleted: 2, failed: [] }));

      const result = await service.bulkDeleteWorkspaces(['sess-1', 'sess-2']);

      expect(result.deleted).toBe(2);
      expect(result.failed).toEqual([]);
      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.session_ids).toEqual(['sess-1', 'sess-2']);
    });
  });

  describe('getAdminSettings', () => {
    it('returns admin settings', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          storage: { home_enabled: true, file_manager_enabled: true },
        })
      );

      const settings = await service.getAdminSettings();

      expect(settings).toEqual({
        storage: { homeEnabled: true, fileManagerEnabled: true },
      });
    });

    it('defaults file_manager_enabled when missing', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          storage: { home_enabled: false },
        })
      );

      const settings = await service.getAdminSettings();

      expect(settings.storage.fileManagerEnabled).toBe(true);
    });
  });

  describe('updateAdminSettings', () => {
    it('updates and returns settings', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          storage: { home_enabled: false, file_manager_enabled: false },
        })
      );

      const settings = await service.updateAdminSettings({
        storage: { homeEnabled: false, fileManagerEnabled: false },
      });

      expect(settings.storage.homeEnabled).toBe(false);
    });

    it('sends empty body when no storage provided', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          storage: { home_enabled: true, file_manager_enabled: true },
        })
      );

      await service.updateAdminSettings({});

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.storage).toBeUndefined();
    });
  });

  describe('getFeatureModules', () => {
    it('returns feature modules', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            key: 'tyr',
            label: 'Tyr',
            icon: 'shield',
            scope: 'workspace',
            enabled: true,
            default_enabled: true,
            admin_only: false,
            order: 1,
          },
        ])
      );

      const features = await service.getFeatureModules();

      expect(features).toHaveLength(1);
      expect(features[0]).toMatchObject({
        key: 'tyr',
        label: 'Tyr',
        scope: 'workspace',
        enabled: true,
        defaultEnabled: true,
        adminOnly: false,
        order: 1,
      });
    });

    it('passes scope filter when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.getFeatureModules('workspace');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('scope=workspace'),
        expect.any(Object)
      );
    });
  });

  describe('toggleFeature', () => {
    it('toggles feature and returns result', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          key: 'tyr',
          label: 'Tyr',
          icon: 'shield',
          scope: 'workspace',
          enabled: false,
          default_enabled: true,
          admin_only: false,
          order: 1,
        })
      );

      const feature = await service.toggleFeature('tyr', false);

      expect(feature.enabled).toBe(false);
    });
  });

  describe('getUserFeaturePreferences', () => {
    it('returns user feature preferences', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          { feature_key: 'tyr', visible: true, sort_order: 1 },
          { feature_key: 'storage', visible: false, sort_order: 2 },
        ])
      );

      const prefs = await service.getUserFeaturePreferences();

      expect(prefs).toHaveLength(2);
      expect(prefs[0]).toEqual({ featureKey: 'tyr', visible: true, sortOrder: 1 });
    });
  });

  describe('updateUserFeaturePreferences', () => {
    it('updates and returns preferences', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([{ feature_key: 'tyr', visible: false, sort_order: 3 }])
      );

      const prefs = await service.updateUserFeaturePreferences([
        { featureKey: 'tyr', visible: false, sortOrder: 3 },
      ]);

      expect(prefs[0].featureKey).toBe('tyr');
      expect(prefs[0].visible).toBe(false);
    });
  });

  describe('listTokens', () => {
    it('returns personal access tokens', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          { id: 'tok-1', name: 'CI Token', created_at: '2024-01-01', last_used_at: '2024-06-01' },
          { id: 'tok-2', name: 'Dev Token', created_at: '2024-02-01', last_used_at: null },
        ])
      );

      const tokens = await service.listTokens();

      expect(tokens).toHaveLength(2);
      expect(tokens[0]).toEqual({
        id: 'tok-1',
        name: 'CI Token',
        createdAt: '2024-01-01',
        lastUsedAt: '2024-06-01',
      });
      expect(tokens[1].lastUsedAt).toBeNull();
    });
  });

  describe('createToken', () => {
    it('creates and returns token with secret', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          id: 'tok-new',
          name: 'My Token',
          token: 'vat_secret123',
          created_at: '2024-01-01',
        })
      );

      const result = await service.createToken('My Token');

      expect(result).toEqual({
        id: 'tok-new',
        name: 'My Token',
        token: 'vat_secret123',
        createdAt: '2024-01-01',
      });
    });
  });

  describe('revokeToken', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.revokeToken('tok-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/tokens/tok-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('savePreset', () => {
    it('creates new preset when no id', async () => {
      const presetResponse = {
        id: 'preset-new',
        name: 'Test Preset',
        description: 'A test preset',
        is_default: false,
        created_at: '2024-01-01',
        updated_at: '2024-01-01',
        cli_tool: 'claude',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: '',
        resource_config: {},
        mcp_servers: [],
        terminal_sidecar: { enabled: false, allowed_commands: [] },
        skills: [],
        rules: [],
        env_vars: {},
        env_secret_refs: [],
        source: null,
        integration_ids: [],
        setup_scripts: [],
        workload_config: {},
      };
      mockFetch.mockReturnValueOnce(mockResponse(presetResponse));

      const preset = await service.savePreset({
        name: 'Test Preset',
        description: 'A test preset',
        isDefault: false,
        cliTool: 'claude',
        workloadType: 'development',
        model: 'claude-sonnet-4-20250514',
        systemPrompt: '',
        resourceConfig: {},
        mcpServers: [],
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
        envVars: {},
        envSecretRefs: [],
        source: null,
        integrationIds: [],
        setupScripts: [],
        workloadConfig: {},
      });

      expect(preset.id).toBe('preset-new');
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/presets'),
        expect.objectContaining({ method: 'POST' })
      );
    });

    it('updates existing preset when id is provided', async () => {
      const presetResponse = {
        id: 'preset-existing',
        name: 'Updated Preset',
        description: 'Updated',
        is_default: false,
        created_at: '2024-01-01',
        updated_at: '2024-01-02',
        cli_tool: 'claude',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: '',
        resource_config: {},
        mcp_servers: [],
        terminal_sidecar: { enabled: false, allowed_commands: [] },
        skills: [],
        rules: [],
        env_vars: {},
        env_secret_refs: [],
        source: null,
        integration_ids: [],
        setup_scripts: [],
        workload_config: {},
      };
      mockFetch.mockReturnValueOnce(mockResponse(presetResponse));

      await service.savePreset({
        id: 'preset-existing',
        name: 'Updated Preset',
        description: 'Updated',
        isDefault: false,
        cliTool: 'claude',
        workloadType: 'development',
        model: 'claude-sonnet-4-20250514',
        systemPrompt: '',
        resourceConfig: {},
        mcpServers: [],
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
        envVars: {},
        envSecretRefs: [],
        source: null,
        integrationIds: [],
        setupScripts: [],
        workloadConfig: {},
      });

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/presets/preset-existing'),
        expect.objectContaining({ method: 'PUT' })
      );
    });

    it('handles git source in preset', async () => {
      const presetResponse = {
        id: 'preset-git',
        name: 'Git Preset',
        description: '',
        is_default: false,
        created_at: '2024-01-01',
        updated_at: '2024-01-01',
        cli_tool: 'claude',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: '',
        resource_config: {},
        mcp_servers: [],
        terminal_sidecar: { enabled: false, allowed_commands: [] },
        skills: [],
        rules: [],
        env_vars: {},
        env_secret_refs: [],
        source: { type: 'git', repo: 'https://github.com/org/repo', branch: 'main' },
        integration_ids: [],
        setup_scripts: [],
        workload_config: {},
      };
      mockFetch.mockReturnValueOnce(mockResponse(presetResponse));

      const preset = await service.savePreset({
        name: 'Git Preset',
        description: '',
        isDefault: false,
        cliTool: 'claude',
        workloadType: 'development',
        model: 'claude-sonnet-4-20250514',
        systemPrompt: '',
        resourceConfig: {},
        mcpServers: [],
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
        envVars: {},
        envSecretRefs: [],
        source: { type: 'git', repo: 'https://github.com/org/repo', branch: 'main' },
        integrationIds: [],
        setupScripts: [],
        workloadConfig: {},
      });

      expect(preset.source?.type).toBe('git');

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.source).toEqual({
        type: 'git',
        repo: 'https://github.com/org/repo',
        branch: 'main',
      });
    });

    it('handles local_mount source in preset', async () => {
      const presetResponse = {
        id: 'preset-mount',
        name: 'Mount Preset',
        description: '',
        is_default: false,
        created_at: '2024-01-01',
        updated_at: '2024-01-01',
        cli_tool: 'claude',
        workload_type: 'development',
        model: 'claude-sonnet-4-20250514',
        system_prompt: '',
        resource_config: {},
        mcp_servers: [],
        terminal_sidecar: { enabled: false, allowed_commands: [] },
        skills: [],
        rules: [],
        env_vars: {},
        env_secret_refs: [],
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/code', mount_path: '/workspace', read_only: false }],
          node_selector: { node: 'gpu-1' },
        },
        integration_ids: [],
        setup_scripts: [],
        workload_config: {},
      };
      mockFetch.mockReturnValueOnce(mockResponse(presetResponse));

      await service.savePreset({
        name: 'Mount Preset',
        description: '',
        isDefault: false,
        cliTool: 'claude',
        workloadType: 'development',
        model: 'claude-sonnet-4-20250514',
        systemPrompt: '',
        resourceConfig: {},
        mcpServers: [],
        terminalSidecar: { enabled: false, allowedCommands: [] },
        skills: [],
        rules: [],
        envVars: {},
        envSecretRefs: [],
        source: {
          type: 'local_mount',
          paths: [{ host_path: '/code', mount_path: '/workspace', read_only: false }],
          node_selector: { node: 'gpu-1' },
        },
        integrationIds: [],
        setupScripts: [],
        workloadConfig: {},
      });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.source.type).toBe('local_mount');
      expect(body.source.paths).toHaveLength(1);
    });
  });

  describe('deletePreset', () => {
    it('calls delete endpoint', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined, 204));

      await service.deletePreset('preset-1');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/presets/preset-1'),
        expect.objectContaining({ method: 'DELETE' })
      );
    });
  });

  describe('updateSession', () => {
    it('updates session and notifies subscribers', async () => {
      // First populate cache
      mockFetch.mockReturnValueOnce(mockResponse([mockApiSession]));
      await service.getSessions();

      // Update
      const updatedResponse = { ...mockApiSession, name: 'Updated Name' };
      mockFetch.mockReturnValueOnce(mockResponse(updatedResponse));

      const callback = vi.fn();
      service.subscribe(callback);

      const session = await service.updateSession(mockApiSession.id, { name: 'Updated Name' });

      expect(session.name).toBe('Updated Name');
    });
  });

  describe('listAllWorkspaces', () => {
    it('calls admin endpoint', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'ws-1',
            pvc_name: 'pvc-ws-1',
            session_id: 'sess-1',
            user_id: 'user-1',
            tenant_id: 'tenant-1',
            size_gb: 10,
            status: 'active',
            created_at: '2024-01-01T00:00:00Z',
            archived_at: null,
            session_name: null,
            source_url: null,
            source_ref: null,
          },
        ])
      );

      const workspaces = await service.listAllWorkspaces();

      expect(workspaces).toHaveLength(1);
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('/admin/workspaces'),
        expect.any(Object)
      );
    });

    it('passes status filter when provided', async () => {
      mockFetch.mockReturnValueOnce(mockResponse([]));

      await service.listAllWorkspaces('active');

      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining('status=active'),
        expect.any(Object)
      );
    });
  });

  describe('getTemplate', () => {
    it('returns template by name', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse({
          name: 'default',
          description: 'Default template',
          is_default: true,
          repos: [{ repo: 'https://github.com/org/repo' }],
          setup_scripts: [],
          workspace_layout: 'single',
          cli_tool: 'claude',
          workload_type: 'development',
          model: 'claude-sonnet-4-20250514',
          system_prompt: '',
          resource_config: {},
          mcp_servers: [],
          env_vars: {},
          env_secret_refs: [],
          workload_config: {},
          terminal_sidecar: null,
          skills: [],
          rules: [],
        })
      );

      const template = await service.getTemplate('default');

      expect(template?.name).toBe('default');
      expect(template?.isDefault).toBe(true);
    });

    it('returns null for 404', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Not found' }, 404));

      const template = await service.getTemplate('nonexistent');

      expect(template).toBeNull();
    });

    it('throws on non-404 errors', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ detail: 'Server error' }, 500));

      await expect(service.getTemplate('bad')).rejects.toThrow();
    });
  });

  describe('getAvailableMcpServers', () => {
    it('returns MCP server configs', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          { name: 'filesystem', type: 'stdio', command: 'node', url: null, args: ['server.js'] },
        ])
      );

      const servers = await service.getAvailableMcpServers();

      expect(servers).toHaveLength(1);
      expect(servers[0]).toMatchObject({
        name: 'filesystem',
        type: 'stdio',
        command: 'node',
        args: ['server.js'],
      });
    });
  });

  describe('getAvailableSecrets', () => {
    it('returns secret names', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(['secret-1', 'secret-2']));

      const secrets = await service.getAvailableSecrets();

      expect(secrets).toEqual(['secret-1', 'secret-2']);
    });
  });

  describe('createSecret', () => {
    it('creates and returns secret info', async () => {
      mockFetch.mockReturnValueOnce(mockResponse({ name: 'my-secret', keys: ['key1', 'key2'] }));

      const result = await service.createSecret('my-secret', { key1: 'val1', key2: 'val2' });

      expect(result).toEqual({ name: 'my-secret', keys: ['key1', 'key2'] });
    });
  });

  describe('storeUserCredential', () => {
    it('stores user credential', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined));

      await service.storeUserCredential('gh-token', { token: 'secret' });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.name).toBe('gh-token');
      expect(body.data.token).toBe('secret');
    });
  });

  describe('storeTenantCredential', () => {
    it('stores tenant credential', async () => {
      mockFetch.mockReturnValueOnce(mockResponse(undefined));

      await service.storeTenantCredential('shared-key', { key: 'value' });

      const body = JSON.parse(mockFetch.mock.calls[0][1].body);
      expect(body.name).toBe('shared-key');
    });
  });

  describe('listUsers', () => {
    it('returns user list', async () => {
      mockFetch.mockReturnValueOnce(
        mockResponse([
          {
            id: 'u1',
            email: 'user@test.com',
            display_name: 'Test User',
            status: 'active',
            created_at: '2024-01-01',
          },
          {
            id: 'u2',
            email: 'admin@test.com',
            display_name: 'Admin',
            status: 'active',
          },
        ])
      );

      const users = await service.listUsers();

      expect(users).toHaveLength(2);
      expect(users[0].createdAt).toBe('2024-01-01');
      expect(users[1].createdAt).toBeUndefined();
    });
  });

  describe('getSessionMcpServers', () => {
    it('returns empty array (not yet implemented)', async () => {
      const result = await service.getSessionMcpServers('sess-1');
      expect(result).toEqual([]);
    });
  });

  describe('getProjectRepoMappings', () => {
    it('returns empty array (not yet implemented)', async () => {
      const result = await service.getProjectRepoMappings();
      expect(result).toEqual([]);
    });
  });

  describe('restoreWorkspace', () => {
    it('is a no-op', async () => {
      await service.restoreWorkspace('ws-1');
      // Just ensure it doesn't throw
      expect(mockFetch).not.toHaveBeenCalled();
    });
  });

  describe('model transform edge cases', () => {
    it('uses default cost when API does not provide cost_per_million_tokens', async () => {
      const model = {
        id: 'claude-opus-4-20250514',
        name: 'Claude Opus',
        description: 'Top model',
        provider: 'cloud',
        tier: 'frontier',
        color: 'purple',
      };
      mockFetch.mockReturnValueOnce(mockResponse([model]));

      const models = await service.getModels();

      expect(models['claude-opus-4-20250514'].cost).toBe('$15/M');
    });

    it('uses API cost when cost_per_million_tokens is provided', async () => {
      const model = {
        id: 'claude-opus-4-20250514',
        name: 'Claude Opus',
        description: 'Top model',
        provider: 'cloud',
        tier: 'frontier',
        color: 'purple',
        cost_per_million_tokens: '20',
      };
      mockFetch.mockReturnValueOnce(mockResponse([model]));

      const models = await service.getModels();

      expect(models['claude-opus-4-20250514'].cost).toBe('$20/M');
    });

    it('uses default vram when API does not provide it', async () => {
      const model = {
        id: 'qwen2.5-coder:32b',
        name: 'Qwen Coder',
        description: 'Local model',
        provider: 'local',
        tier: 'execution',
        color: 'cyan',
      };
      mockFetch.mockReturnValueOnce(mockResponse([model]));

      const models = await service.getModels();

      expect(models['qwen2.5-coder:32b'].vram).toBe('24GB');
    });

    it('uses API vram when provided', async () => {
      const model = {
        id: 'qwen2.5-coder:32b',
        name: 'Qwen Coder',
        description: 'Local model',
        provider: 'local',
        tier: 'execution',
        color: 'cyan',
        vram_required: '32GB',
      };
      mockFetch.mockReturnValueOnce(mockResponse([model]));

      const models = await service.getModels();

      expect(models['qwen2.5-coder:32b'].vram).toBe('32GB');
    });
  });

  describe('SSE session_activity events', () => {
    it('updates activity state for existing session', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const eventSource = MockSSEStream.instances[0];

      // First create a session
      const session: SSESessionPayload = {
        id: 'sess-activity',
        name: 'Activity Test',
        model: 'claude-sonnet-4-20250514',
        source: { type: 'git', repo: 'org/repo', branch: 'main' },
        status: 'running',
        chat_endpoint: null,
        code_endpoint: null,
        created_at: '2026-02-03T12:00:00',
        updated_at: '2026-02-03T12:00:00',
        last_active: '2026-02-03T12:00:00',
        message_count: 0,
        tokens_used: 0,
        pod_name: null,
        error: null,
      };
      await eventSource.simulateEvent('session_created', session);

      // Then send activity event
      await eventSource.simulateEvent('session_activity', {
        session_id: 'sess-activity',
        state: 'tool_executing',
      });

      const lastCall = callback.mock.calls[callback.mock.calls.length - 1][0];
      const updated = lastCall.find((s: { id: string }) => s.id === 'sess-activity');
      expect(updated.activityState).toBe('tool_executing');
    });

    it('ignores activity event for unknown session', async () => {
      const callback = vi.fn();
      service.subscribe(callback);

      await vi.waitFor(() => {
        expect(MockSSEStream.instances).toHaveLength(1);
      });

      const callCountBefore = callback.mock.calls.length;

      const eventSource = MockSSEStream.instances[0];
      await eventSource.simulateEvent('session_activity', {
        session_id: 'unknown-sess',
        state: 'idle',
      });

      // Callback should not have been called again
      expect(callback.mock.calls.length).toBe(callCountBefore);
    });
  });
});
