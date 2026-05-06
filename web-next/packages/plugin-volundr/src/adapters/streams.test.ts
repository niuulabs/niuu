import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { buildVolundrPtyWsAdapter, buildVolundrMetricsSseAdapter } from './streams';

/**
 * Minimal WebSocket double compatible with the subset our adapter uses:
 * addEventListener(open/message/close), send, close, readyState.
 */
class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  sent: string[] = [];
  private listeners = new Map<string, Array<(ev: unknown) => void>>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, cb: (ev: unknown) => void): void {
    const arr = this.listeners.get(type) ?? [];
    arr.push(cb);
    this.listeners.set(type, arr);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.fire('close', {});
  }

  // Test hooks
  simulateOpen(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.fire('open', {});
  }

  simulateMessage(data: string): void {
    this.fire('message', { data });
  }

  private fire(type: string, ev: unknown): void {
    for (const cb of this.listeners.get(type) ?? []) cb(ev);
  }
}

describe('buildVolundrPtyWsAdapter', () => {
  const originalWebSocket = globalThis.WebSocket;

  beforeEach(() => {
    // Expose FakeWebSocket as the global so readyState comparisons against
    // WebSocket.OPEN in the adapter resolve to the fake's constant.
    (globalThis as unknown as { WebSocket: typeof FakeWebSocket }).WebSocket = FakeWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
  });

  it('substitutes {sessionId} in the URL template and connects on first subscribe', () => {
    const built: FakeWebSocket[] = [];
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://api/volundr/pty?s={sessionId}',
      wsFactory: (url) => {
        const ws = new FakeWebSocket(url);
        built.push(ws);
        return ws as unknown as WebSocket;
      },
    });
    adapter.subscribe('sess-1', () => {});
    expect(built).toHaveLength(1);
    expect(built[0]!.url).toBe('wss://api/volundr/pty?s=sess-1');
  });

  it('fans incoming messages out to every subscriber for that session', () => {
    let captured: FakeWebSocket | null = null;
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://h/{sessionId}',
      wsFactory: (url) => {
        captured = new FakeWebSocket(url);
        return captured as unknown as WebSocket;
      },
    });
    const a: string[] = [];
    const b: string[] = [];
    adapter.subscribe('s', (c) => a.push(c));
    adapter.subscribe('s', (c) => b.push(c));
    captured!.simulateOpen();
    captured!.simulateMessage('hello\r\n');
    expect(a).toEqual(['hello\r\n']);
    expect(b).toEqual(['hello\r\n']);
  });

  it('buffers send() before open and flushes on open', () => {
    let captured: FakeWebSocket | null = null;
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://h/{sessionId}',
      wsFactory: (url) => {
        captured = new FakeWebSocket(url);
        return captured as unknown as WebSocket;
      },
    });
    adapter.subscribe('s', () => {});
    adapter.send('s', 'ls\n');
    expect(captured!.sent).toEqual([]); // buffered, not sent yet
    captured!.simulateOpen();
    expect(captured!.sent).toEqual(['ls\n']); // flushed
  });

  it('send() after open writes through directly', () => {
    let captured: FakeWebSocket | null = null;
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://h/{sessionId}',
      wsFactory: (url) => {
        captured = new FakeWebSocket(url);
        return captured as unknown as WebSocket;
      },
    });
    adapter.subscribe('s', () => {});
    captured!.simulateOpen();
    adapter.send('s', 'pwd\n');
    expect(captured!.sent).toEqual(['pwd\n']);
  });

  it('closes the socket when the last subscriber unsubscribes', () => {
    let captured: FakeWebSocket | null = null;
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://h/{sessionId}',
      wsFactory: (url) => {
        captured = new FakeWebSocket(url);
        return captured as unknown as WebSocket;
      },
    });
    const closeSpy = vi.spyOn(FakeWebSocket.prototype, 'close');
    const unsub = adapter.subscribe('s', () => {});
    unsub();
    expect(closeSpy).toHaveBeenCalled();
    closeSpy.mockRestore();
    expect(captured).not.toBeNull();
  });

  it('reuses a single socket for two subscribers on the same session', () => {
    let count = 0;
    const adapter = buildVolundrPtyWsAdapter({
      urlTemplate: 'wss://h/{sessionId}',
      wsFactory: (url) => {
        count++;
        return new FakeWebSocket(url) as unknown as WebSocket;
      },
    });
    adapter.subscribe('s', () => {});
    adapter.subscribe('s', () => {});
    expect(count).toBe(1);
  });
});

describe('buildVolundrMetricsSseAdapter', () => {
  const originalFetch = global.fetch;

  function mockSseResponse(chunks: string[]): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    });
  }

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('substitutes {sessionId} in the URL template', async () => {
    const fetchSpy = vi.fn(async () => mockSseResponse([]));
    global.fetch = fetchSpy;

    const adapter = buildVolundrMetricsSseAdapter({
      urlTemplate: '/volundr/metrics?s={sessionId}',
    });
    adapter.subscribe('sess-1', () => {});
    await new Promise((r) => setTimeout(r, 5));

    const [url] = fetchSpy.mock.calls[0]!;
    expect(String(url)).toBe('/volundr/metrics?s=sess-1');
  });

  it('parses JSON frames and delivers MetricPoint to subscribers', async () => {
    const point = { timestamp: 1, cpu: 0.5, memMi: 200, gpu: 0 };
    global.fetch = vi.fn(async () =>
      mockSseResponse([`data: ${JSON.stringify(point)}\n\n`]),
    ) as typeof fetch;

    const adapter = buildVolundrMetricsSseAdapter({ urlTemplate: '/m/{sessionId}' });
    const seen: unknown[] = [];
    adapter.subscribe('s', (p) => seen.push(p));
    await new Promise((r) => setTimeout(r, 20));

    expect(seen).toEqual([point]);
  });

  it('drops malformed frames silently', async () => {
    global.fetch = vi.fn(async () => mockSseResponse(['data: not-json\n\n'])) as typeof fetch;

    const adapter = buildVolundrMetricsSseAdapter({ urlTemplate: '/m/{sessionId}' });
    const seen: unknown[] = [];
    adapter.subscribe('s', (p) => seen.push(p));
    await new Promise((r) => setTimeout(r, 20));

    expect(seen).toEqual([]);
  });
});
