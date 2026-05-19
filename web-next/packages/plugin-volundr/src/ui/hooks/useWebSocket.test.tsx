import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useWebSocket } from './useWebSocket';

const { getAccessTokenMock } = vi.hoisted(() => ({
  getAccessTokenMock: vi.fn(() => null),
}));

vi.mock('@niuulabs/query', () => ({
  getAccessToken: getAccessTokenMock,
  withAuthQuery: (url: string) => {
    const token = getAccessTokenMock();
    if (!token) return url;
    const separator = url.includes('?') ? '&' : '?';
    return `${url}${separator}access_token=${encodeURIComponent(token)}`;
  },
}));

class MockWebSocket {
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = 0;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(_data: string) {}

  close() {
    this.readyState = 3;
  }

  emitOpen() {
    this.readyState = 1;
    this.onopen?.(new Event('open'));
  }

  emitClose(code = 1000, reason = '') {
    this.readyState = 3;
    this.onclose?.({ code, reason } as CloseEvent);
  }

  emitMessage(data: string) {
    this.onmessage?.({ data } as MessageEvent);
  }

  emitError() {
    this.onerror?.(new Event('error'));
  }
}

describe('useWebSocket', () => {
  const originalWebSocket = globalThis.WebSocket;

  afterEach(() => {
    MockWebSocket.instances = [];
    getAccessTokenMock.mockReset();
    getAccessTokenMock.mockReturnValue(null);
    vi.restoreAllMocks();
    vi.useRealTimers();
    globalThis.WebSocket = originalWebSocket;
  });

  it('ignores close events from stale sockets after the url changes', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const onOpen = vi.fn();
    const onClose = vi.fn();

    const { rerender } = renderHook(
      ({ url }) => useWebSocket(url, { onOpen, onClose, reconnect: false }),
      { initialProps: { url: 'ws://localhost:8080/s/one/session' } },
    );

    const first = MockWebSocket.instances[0];
    expect(first).toBeDefined();
    first.emitOpen();
    expect(onOpen).toHaveBeenCalledTimes(1);

    rerender({ url: 'ws://localhost:8080/s/two/session' });

    const second = MockWebSocket.instances[1];
    expect(second).toBeDefined();
    second.emitOpen();
    expect(onOpen).toHaveBeenCalledTimes(2);

    first.emitClose(1006, 'stale');
    expect(onClose).not.toHaveBeenCalledWith(1006, 'stale');

    // The stale close should not tear down or replace the newer socket.
    second.emitClose(1000, 'final');
    expect(onClose).toHaveBeenCalledWith(1000, 'final');
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it('ignores message and error events from stale sockets after the url changes', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const onMessage = vi.fn();
    const onError = vi.fn();

    const { rerender } = renderHook(
      ({ url }) => useWebSocket(url, { onMessage, onError, reconnect: false }),
      { initialProps: { url: 'ws://localhost:8080/s/one/session' } },
    );

    const first = MockWebSocket.instances[0];
    expect(first).toBeDefined();

    rerender({ url: 'ws://localhost:8080/s/two/session' });

    const second = MockWebSocket.instances[1];
    expect(second).toBeDefined();

    first.emitMessage('stale-output');
    first.emitError();
    expect(onMessage).not.toHaveBeenCalledWith('stale-output');
    expect(onError).not.toHaveBeenCalled();

    second.emitMessage('fresh-output');
    expect(onMessage).toHaveBeenCalledWith('fresh-output');
  });

  it('appends the current access token to the websocket url when present', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    getAccessTokenMock.mockReturnValue('secret token');

    renderHook(() => useWebSocket('ws://localhost:8080/s/one/session', { reconnect: false }));

    expect(MockWebSocket.instances[0]?.url).toBe(
      'ws://localhost:8080/s/one/session?access_token=secret%20token',
    );
  });

  it('appends the access token with an ampersand when the url already has query params', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    getAccessTokenMock.mockReturnValue('secret token');

    renderHook(() =>
      useWebSocket('ws://localhost:8080/s/one/session?tail=1', { reconnect: false }),
    );

    expect(MockWebSocket.instances[0]?.url).toBe(
      'ws://localhost:8080/s/one/session?tail=1&access_token=secret%20token',
    );
  });

  it('reports invalid websocket protocols through onError without opening a socket', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const onError = vi.fn();

    renderHook(() => useWebSocket('http://localhost:8080/not-a-websocket', { onError }));

    expect(onError).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it('reconnects after an unexpected close until max attempts are exhausted', async () => {
    vi.useFakeTimers();
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const onClose = vi.fn();

    renderHook(() =>
      useWebSocket('ws://localhost:8080/s/one/session', {
        onClose,
        reconnect: true,
        maxReconnectAttempts: 1,
        reconnectBaseDelay: 5,
        reconnectMaxDelay: 5,
      }),
    );

    const first = MockWebSocket.instances[0];
    expect(first).toBeDefined();
    first.emitClose(1006, 'boom');

    await vi.advanceTimersByTimeAsync(5);
    expect(MockWebSocket.instances).toHaveLength(2);

    const second = MockWebSocket.instances[1];
    second.emitClose(1006, 'still-boom');
    await vi.advanceTimersByTimeAsync(25);

    expect(onClose).toHaveBeenNthCalledWith(1, 1006, 'boom');
    expect(onClose).toHaveBeenNthCalledWith(2, 1006, 'still-boom');
    expect(MockWebSocket.instances).toHaveLength(2);
  });

  it('does not reconnect after the hook unmounts intentionally', async () => {
    vi.useFakeTimers();
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    const { unmount } = renderHook(() =>
      useWebSocket('ws://localhost:8080/s/one/session', {
        reconnect: true,
        reconnectBaseDelay: 5,
        reconnectMaxDelay: 5,
      }),
    );

    const first = MockWebSocket.instances[0];
    expect(first).toBeDefined();

    unmount();
    first.emitClose(1006, 'after-unmount');
    await vi.advanceTimersByTimeAsync(25);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('does not reconnect when reconnect is disabled', async () => {
    vi.useFakeTimers();
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    renderHook(() =>
      useWebSocket('ws://localhost:8080/s/one/session', {
        reconnect: false,
      }),
    );

    const first = MockWebSocket.instances[0];
    first.emitClose(1006, 'done');
    await vi.advanceTimersByTimeAsync(25);

    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('snapshots handlers per connection when requested', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const { rerender } = renderHook(
      ({ onMessage }) =>
        useWebSocket('ws://localhost:8080/s/one/session', {
          onMessage,
          reconnect: false,
          snapshotHandlersPerConnection: true,
        }),
      { initialProps: { onMessage: firstHandler } },
    );

    rerender({ onMessage: secondHandler });

    const first = MockWebSocket.instances[0];
    first.emitMessage('hello');

    expect(firstHandler).toHaveBeenCalledWith('hello');
    expect(secondHandler).not.toHaveBeenCalled();
  });

  it('uses the latest handlers when snapshotting is disabled', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const { rerender } = renderHook(
      ({ onMessage }) =>
        useWebSocket('ws://localhost:8080/s/one/session', {
          onMessage,
          reconnect: false,
          snapshotHandlersPerConnection: false,
        }),
      { initialProps: { onMessage: firstHandler } },
    );

    rerender({ onMessage: secondHandler });

    const first = MockWebSocket.instances[0];
    first.emitMessage('hello');

    expect(firstHandler).not.toHaveBeenCalled();
    expect(secondHandler).toHaveBeenCalledWith('hello');
  });

  it('uses the latest onOpen handler when snapshotting is disabled', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const { rerender } = renderHook(
      ({ onOpen }) =>
        useWebSocket('ws://localhost:8080/s/one/session', {
          onOpen,
          reconnect: false,
          snapshotHandlersPerConnection: false,
        }),
      { initialProps: { onOpen: firstHandler } },
    );

    rerender({ onOpen: secondHandler });

    const first = MockWebSocket.instances[0];
    first.emitOpen();

    expect(firstHandler).not.toHaveBeenCalled();
    expect(secondHandler).toHaveBeenCalledTimes(1);
  });

  it('uses the latest onError handler when snapshotting is disabled', () => {
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    const firstHandler = vi.fn();
    const secondHandler = vi.fn();

    const { rerender } = renderHook(
      ({ onError }) =>
        useWebSocket('ws://localhost:8080/s/one/session', {
          onError,
          reconnect: false,
          snapshotHandlersPerConnection: false,
        }),
      { initialProps: { onError: firstHandler } },
    );

    rerender({ onError: secondHandler });

    const first = MockWebSocket.instances[0];
    first.emitError();

    expect(firstHandler).not.toHaveBeenCalled();
    expect(secondHandler).toHaveBeenCalledTimes(1);
  });
});
