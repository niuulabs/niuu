import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { useTyrEvents } from './useTyrEvents';

const mockGetAccessToken = vi.fn<() => string | null>(() => null);
const mockApiGet = vi.fn().mockResolvedValue({ events: [], total: 0 });
vi.mock('@/modules/shared/api/client', () => ({
  createApiClient: () => ({
    get: (...args: unknown[]) => mockApiGet(...args),
  }),
  getAccessToken: () => mockGetAccessToken(),
}));

class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  private listeners: Record<string, ((event: MessageEvent) => void)[]> = {};
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (event: MessageEvent) => void) {
    this.listeners[type] = this.listeners[type] || [];
    this.listeners[type].push(handler);
  }

  dispatchEvent(type: string, data: string) {
    const event = new MessageEvent(type, { data });
    for (const handler of this.listeners[type] ?? []) {
      handler(event);
    }
  }

  close() {
    this.closed = true;
  }
}

describe('useTyrEvents', () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (global as any).EventSource = MockEventSource;
    mockApiGet.mockResolvedValue({ events: [], total: 0 });
    mockGetAccessToken.mockReturnValue(null);
  });

  afterEach(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    delete (global as any).EventSource;
    vi.restoreAllMocks();
  });

  it('should start disconnected and create EventSource', async () => {
    const { result } = renderHook(() => useTyrEvents());
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    expect(result.current.connected).toBe(false);
    expect(result.current.events).toHaveLength(0);
  });

  it('should set connected on open', async () => {
    const { result } = renderHook(() => useTyrEvents());
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0].onopen?.();
    });

    expect(result.current.connected).toBe(true);
  });

  it('should receive typed events', async () => {
    const { result } = renderHook(() => useTyrEvents());
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0].dispatchEvent(
        'raid.state_changed',
        JSON.stringify({ status: 'REVIEW' })
      );
    });

    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe('raid.state_changed');
  });

  it('should call onEvent callback', async () => {
    const onEvent = vi.fn();
    renderHook(() => useTyrEvents(onEvent));
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0].dispatchEvent(
        'raid.state_changed',
        JSON.stringify({ status: 'REVIEW' })
      );
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it('should handle generic messages', async () => {
    const { result } = renderHook(() => useTyrEvents());
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    act(() => {
      MockEventSource.instances[0].onmessage?.(new MessageEvent('message', { data: 'hello' }));
    });

    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].type).toBe('message');
  });

  it('should close EventSource on unmount', async () => {
    const { unmount } = renderHook(() => useTyrEvents());
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    const es = MockEventSource.instances[0];
    unmount();
    expect(es.closed).toBe(true);
  });

  it('should seed events from activity log on first connect', async () => {
    mockApiGet.mockResolvedValue({
      events: [
        {
          id: 'log-1',
          event: 'raid.state_changed',
          data: { status: 'CODING' },
          timestamp: '2025-01-15T10:00:00Z',
        },
        {
          id: 'log-2',
          event: 'session.state_changed',
          data: { state: 'running' },
          timestamp: '2025-01-15T10:01:00Z',
        },
      ],
      total: 2,
    });

    const { result } = renderHook(() => useTyrEvents());

    await waitFor(() => {
      expect(result.current.events.length).toBeGreaterThanOrEqual(2);
    });

    const types = result.current.events.map(e => e.type);
    expect(types).toContain('raid.state_changed');
    expect(types).toContain('session.state_changed');
  });

  it('should handle seed fetch failure gracefully', async () => {
    mockApiGet.mockRejectedValue(new Error('Network error'));

    const { result } = renderHook(() => useTyrEvents());

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    // Should still create EventSource and work normally despite seed failure
    expect(result.current.events).toHaveLength(0);
  });

  it('should reconnect on error', async () => {
    vi.useFakeTimers();

    renderHook(() => useTyrEvents());

    await vi.waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    const es = MockEventSource.instances[0];

    act(() => {
      es.onerror?.();
    });

    expect(es.closed).toBe(true);

    // Advance by reconnect delay
    await act(async () => {
      vi.advanceTimersByTime(5000);
    });

    expect(MockEventSource.instances).toHaveLength(2);

    vi.useRealTimers();
  });

  it('should append access token to URL when available', async () => {
    mockGetAccessToken.mockReturnValue('my-token');

    renderHook(() => useTyrEvents());

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));

    expect(MockEventSource.instances[0].url).toContain('token=my-token');
  });
});
