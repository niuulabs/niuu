import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWebSocket } from './useWebSocket';

const mockGetAccessToken = vi.fn<() => string | null>(() => null);
vi.mock('@/modules/volundr/adapters/api/client', () => ({
  getAccessToken: () => mockGetAccessToken(),
}));

// ---------------------------------------------------------------------------
// MockWebSocket
// ---------------------------------------------------------------------------

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  simulateMessage(data: string) {
    this.onmessage?.(new MessageEvent('message', { data }));
  }

  simulateClose(code = 1000, reason = '') {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.(new CloseEvent('close', { code, reason }));
  }

  simulateError() {
    this.onerror?.(new Event('error'));
  }
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

describe('useWebSocket', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ---- helpers ------------------------------------------------------------

  function latestSocket(): MockWebSocket {
    return MockWebSocket.instances[MockWebSocket.instances.length - 1];
  }

  // ---- 1. Does NOT connect when url is null -------------------------------

  it('should not create a WebSocket when url is null', () => {
    renderHook(() => useWebSocket(null));

    expect(MockWebSocket.instances).toHaveLength(0);
  });

  // ---- 2. Creates WebSocket when url is provided --------------------------

  it('should create a WebSocket when url is provided', () => {
    renderHook(() => useWebSocket('ws://localhost:8080'));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestSocket().url).toBe('ws://localhost:8080');
  });

  // ---- 3. Calls onOpen callback -------------------------------------------

  it('should call onOpen callback when connection opens', () => {
    const onOpen = vi.fn();
    renderHook(() => useWebSocket('ws://localhost:8080', { onOpen }));

    act(() => {
      latestSocket().simulateOpen();
    });

    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  // ---- 4. Calls onMessage callback ----------------------------------------

  it('should call onMessage callback with message data', () => {
    const onMessage = vi.fn();
    renderHook(() => useWebSocket('ws://localhost:8080', { onMessage }));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      latestSocket().simulateMessage('hello world');
    });

    expect(onMessage).toHaveBeenCalledTimes(1);
    expect(onMessage).toHaveBeenCalledWith('hello world');
  });

  // ---- 5. Calls onClose callback ------------------------------------------

  it('should call onClose callback when connection closes', () => {
    const onClose = vi.fn();
    renderHook(() => useWebSocket('ws://localhost:8080', { onClose, reconnect: false }));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      latestSocket().simulateClose(1001, 'going away');
    });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledWith(1001, 'going away');
  });

  // ---- 6. send() sends string data when connected -------------------------

  it('should send string data when connected', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080'));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      result.current.send('test message');
    });

    expect(latestSocket().send).toHaveBeenCalledWith('test message');
  });

  // ---- 7. sendJson() sends JSON-stringified data --------------------------

  it('should send JSON-stringified data when connected', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080'));

    act(() => {
      latestSocket().simulateOpen();
    });

    const payload = { type: 'ping', id: 42 };
    act(() => {
      result.current.sendJson(payload);
    });

    expect(latestSocket().send).toHaveBeenCalledWith(JSON.stringify(payload));
  });

  // ---- 8. send/sendJson are no-ops when not connected ---------------------

  it('should not send when readyState is not OPEN', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080'));

    // Socket is still in CONNECTING state, never opened
    act(() => {
      result.current.send('should not go');
    });

    act(() => {
      result.current.sendJson({ should: 'not go' });
    });

    expect(latestSocket().send).not.toHaveBeenCalled();
  });

  // ---- 9. close() closes the connection -----------------------------------

  it('should close the connection when close() is called', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080'));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      result.current.close();
    });

    expect(latestSocket().close).toHaveBeenCalledTimes(1);
  });

  // ---- 10. Reconnects with exponential backoff ----------------------------

  it('should reconnect with exponential backoff on unexpected close', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8080', {
        reconnect: true,
        reconnectBaseDelay: 1000,
        reconnectMaxDelay: 30000,
      })
    );

    expect(MockWebSocket.instances).toHaveLength(1);

    // First unexpected close: delay = 1000 * 2^0 = 1000ms
    act(() => {
      latestSocket().simulateOpen();
    });
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    // No new socket yet
    expect(MockWebSocket.instances).toHaveLength(1);

    // Advance past the first backoff
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(MockWebSocket.instances).toHaveLength(2);
    expect(latestSocket().url).toBe('ws://localhost:8080');

    // Second unexpected close: delay = 1000 * 2^1 = 2000ms
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    act(() => {
      vi.advanceTimersByTime(1999);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(3);

    // Third unexpected close: delay = 1000 * 2^2 = 4000ms
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    act(() => {
      vi.advanceTimersByTime(3999);
    });
    expect(MockWebSocket.instances).toHaveLength(3);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(4);
  });

  // ---- 11. Does NOT reconnect on intentional close ------------------------

  it('should not reconnect when close() is called intentionally', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080', { reconnect: true }));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      result.current.close();
    });

    // Simulate the server-side close event that follows
    act(() => {
      latestSocket().simulateClose(1000, 'normal');
    });

    // Advance well past any reconnect delay
    act(() => {
      vi.advanceTimersByTime(60000);
    });

    // Only the original connection should exist
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // ---- 12. Does NOT reconnect when reconnect option is false --------------

  it('should not reconnect when reconnect option is false', () => {
    renderHook(() => useWebSocket('ws://localhost:8080', { reconnect: false }));

    act(() => {
      latestSocket().simulateOpen();
    });

    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    act(() => {
      vi.advanceTimersByTime(60000);
    });

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // ---- 13. Stops reconnecting after maxReconnectAttempts ------------------

  it('should stop reconnecting after maxReconnectAttempts', () => {
    const maxAttempts = 3;

    renderHook(() =>
      useWebSocket('ws://localhost:8080', {
        reconnect: true,
        maxReconnectAttempts: maxAttempts,
        reconnectBaseDelay: 100,
        reconnectMaxDelay: 30000,
      })
    );

    expect(MockWebSocket.instances).toHaveLength(1);

    // Perform maxAttempts reconnections
    for (let i = 0; i < maxAttempts; i++) {
      act(() => {
        latestSocket().simulateClose(1006, 'abnormal');
      });

      const delay = 100 * Math.pow(2, i);
      act(() => {
        vi.advanceTimersByTime(delay);
      });

      // Each reconnect should create a new instance
      expect(MockWebSocket.instances).toHaveLength(i + 2);
    }

    // Now we have 1 original + 3 reconnects = 4 instances
    expect(MockWebSocket.instances).toHaveLength(maxAttempts + 1);

    // One more close should NOT trigger another reconnect
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    act(() => {
      vi.advanceTimersByTime(60000);
    });

    expect(MockWebSocket.instances).toHaveLength(maxAttempts + 1);
  });

  // ---- 14. Cleans up WebSocket on unmount ---------------------------------

  it('should clean up WebSocket on unmount', () => {
    const { unmount } = renderHook(() => useWebSocket('ws://localhost:8080'));

    const socket = latestSocket();

    act(() => {
      socket.simulateOpen();
    });

    unmount();

    expect(socket.close).toHaveBeenCalledTimes(1);
  });

  it('should cancel pending reconnect timer on unmount', () => {
    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8080', {
        reconnect: true,
        reconnectBaseDelay: 5000,
      })
    );

    act(() => {
      latestSocket().simulateOpen();
    });

    // Trigger a close so a reconnect timer is started
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    expect(MockWebSocket.instances).toHaveLength(1);

    // Unmount before the timer fires
    unmount();

    // Advance past the reconnect delay
    act(() => {
      vi.advanceTimersByTime(10000);
    });

    // No new socket should have been created
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  // ---- Additional edge cases ----------------------------------------------

  it('should call onError callback on error', () => {
    const onError = vi.fn();
    renderHook(() => useWebSocket('ws://localhost:8080', { onError }));

    act(() => {
      latestSocket().simulateError();
    });

    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledWith(expect.any(Event));
  });

  it('should reset reconnect attempts on successful open', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8080', {
        reconnect: true,
        reconnectBaseDelay: 1000,
        maxReconnectAttempts: 10,
      })
    );

    // First close triggers reconnect at attempt 0 (delay = 1000ms)
    act(() => {
      latestSocket().simulateOpen();
    });
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });
    act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(MockWebSocket.instances).toHaveLength(2);

    // Second socket opens successfully, which resets the counter
    act(() => {
      latestSocket().simulateOpen();
    });

    // Close again -- if counter was reset, delay should be 1000ms (attempt 0) again
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    // Should NOT reconnect at 999ms
    act(() => {
      vi.advanceTimersByTime(999);
    });
    expect(MockWebSocket.instances).toHaveLength(2);

    // Should reconnect at 1000ms (base delay for attempt 0)
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it('should disconnect old socket and create new one when url changes', () => {
    const { rerender } = renderHook(({ url }) => useWebSocket(url), {
      initialProps: { url: 'ws://localhost:8080' as string | null },
    });

    const firstSocket = latestSocket();

    act(() => {
      firstSocket.simulateOpen();
    });

    // Change URL -- should close the old socket and open a new one
    rerender({ url: 'ws://localhost:9090' });

    expect(firstSocket.close).toHaveBeenCalled();
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(latestSocket().url).toBe('ws://localhost:9090');
  });

  it('should close socket when url changes to null', () => {
    const { rerender } = renderHook(({ url }) => useWebSocket(url), {
      initialProps: { url: 'ws://localhost:8080' as string | null },
    });

    const socket = latestSocket();

    act(() => {
      socket.simulateOpen();
    });

    rerender({ url: null });

    expect(socket.close).toHaveBeenCalled();
    // No new socket should be created
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('should expose the socket instance via getSocket()', () => {
    const { result } = renderHook(() => useWebSocket('ws://localhost:8080'));

    const socket = result.current.getSocket();
    expect(socket).toBe(latestSocket());
  });

  it('should return null from getSocket() when url is null', () => {
    const { result } = renderHook(() => useWebSocket(null));

    expect(result.current.getSocket()).toBeNull();
  });

  it('should cap reconnect delay at reconnectMaxDelay', () => {
    renderHook(() =>
      useWebSocket('ws://localhost:8080', {
        reconnect: true,
        reconnectBaseDelay: 1000,
        reconnectMaxDelay: 5000,
        maxReconnectAttempts: 20,
      })
    );

    // Burn through several attempts to exceed the max delay cap
    // Attempt 0: 1000ms, Attempt 1: 2000ms, Attempt 2: 4000ms,
    // Attempt 3: would be 8000ms but capped at 5000ms
    for (let i = 0; i < 3; i++) {
      act(() => {
        latestSocket().simulateClose(1006, 'abnormal');
      });
      const delay = Math.min(1000 * Math.pow(2, i), 5000);
      act(() => {
        vi.advanceTimersByTime(delay);
      });
    }

    expect(MockWebSocket.instances).toHaveLength(4);

    // Attempt 3: delay should be capped at 5000ms (not 8000ms)
    act(() => {
      latestSocket().simulateClose(1006, 'abnormal');
    });

    // At 4999ms it should NOT have reconnected yet
    act(() => {
      vi.advanceTimersByTime(4999);
    });
    expect(MockWebSocket.instances).toHaveLength(4);

    // At 5000ms it should reconnect
    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(MockWebSocket.instances).toHaveLength(5);
  });

  // ---- Token appending -------------------------------------------------

  it('should append access_token query param when token is available', () => {
    mockGetAccessToken.mockReturnValue('test-jwt-token');

    renderHook(() => useWebSocket('ws://localhost:8080'));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestSocket().url).toContain('access_token=test-jwt-token');
  });

  it('should append access_token with & when URL already has query params', () => {
    mockGetAccessToken.mockReturnValue('tok');

    renderHook(() => useWebSocket('ws://localhost:8080?foo=bar'));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestSocket().url).toContain('foo=bar&access_token=tok');
  });

  it('should not append access_token when getAccessToken returns null', () => {
    mockGetAccessToken.mockReturnValue(null);

    renderHook(() => useWebSocket('ws://localhost:8080'));

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(latestSocket().url).toBe('ws://localhost:8080');
  });

  // ---- URL validation ---------------------------------------------------

  it('should reject non-ws protocol and call onError', () => {
    mockGetAccessToken.mockReturnValue(null);
    const onError = vi.fn();

    renderHook(() => useWebSocket('http://localhost:8080', { onError }));

    // WebSocket should NOT be created for http:// URLs
    expect(MockWebSocket.instances).toHaveLength(0);
    expect(onError).toHaveBeenCalledTimes(1);
  });

  // ---- Stale WebSocket close handling -----------------------------------

  it('should not reconnect when stale WS fires onclose after URL change', () => {
    const { rerender } = renderHook(({ url }) => useWebSocket(url, { reconnect: true }), {
      initialProps: { url: 'ws://localhost:8080' as string | null },
    });

    const firstSocket = latestSocket();

    act(() => {
      firstSocket.simulateOpen();
    });

    // Change URL — should close old socket and create new one
    rerender({ url: 'ws://localhost:9090' });

    expect(MockWebSocket.instances).toHaveLength(2);

    // Now the OLD socket fires onclose (as would happen in real life)
    // This should NOT trigger a reconnect because wsRef.current !== firstSocket
    act(() => {
      firstSocket.simulateClose(1006, 'abnormal');
    });

    // Should NOT have created a third socket (no reconnect for stale WS)
    act(() => {
      vi.advanceTimersByTime(60000);
    });

    expect(MockWebSocket.instances).toHaveLength(2);
  });
});
