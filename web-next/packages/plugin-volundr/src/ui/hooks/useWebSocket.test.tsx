import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useWebSocket } from './useWebSocket';

vi.mock('@niuulabs/query', () => ({
  getAccessToken: () => null,
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
    vi.restoreAllMocks();
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
});
