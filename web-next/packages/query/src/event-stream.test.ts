import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { openEventStream } from './event-stream';
import { setTokenProvider } from './http-client';

/**
 * Build a Response whose body is a ReadableStream yielding the given chunks
 * (as UTF-8 bytes) in sequence, then closing.
 */
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

describe('openEventStream', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    setTokenProvider(null);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    setTokenProvider(null);
    vi.useRealTimers();
  });

  it('parses a single data frame', async () => {
    global.fetch = vi.fn(async () => mockSseResponse(['data: {"hello":1}\n\n']));
    const messages: string[] = [];

    const handle = openEventStream('/stream', { onMessage: (m) => messages.push(m) });

    // Wait a tick for the async pump to drain the (already-closed) stream.
    await new Promise((r) => setTimeout(r, 10));
    handle.close();

    expect(messages).toEqual(['{"hello":1}']);
  });

  it('parses multiple frames and concatenates multi-line data', async () => {
    global.fetch = vi.fn(async () =>
      mockSseResponse(['data: a\ndata: b\n\n', 'data: c\n\n', 'data: d\n\n']),
    );
    const messages: string[] = [];

    const handle = openEventStream('/stream', { onMessage: (m) => messages.push(m) });
    await new Promise((r) => setTimeout(r, 10));
    handle.close();

    expect(messages).toEqual(['a\nb', 'c', 'd']);
  });

  it('passes through SSE event names when provided', async () => {
    global.fetch = vi.fn(async () =>
      mockSseResponse(['event: stats_updated\ndata: {"count":1}\n\n']),
    );
    const seen: Array<{ event?: string; data: string }> = [];

    const handle = openEventStream('/stream', {
      onMessage: () => {},
      onEvent: (frame) => seen.push(frame),
    });
    await new Promise((r) => setTimeout(r, 10));
    handle.close();

    expect(seen).toEqual([{ event: 'stats_updated', data: '{"count":1}' }]);
  });

  it('attaches a Bearer header when a token provider is registered', async () => {
    setTokenProvider(() => 'test-token');
    const fetchSpy = vi.fn(async () => mockSseResponse([]));
    global.fetch = fetchSpy;

    const handle = openEventStream('/stream', { onMessage: () => {} });
    await new Promise((r) => setTimeout(r, 10));
    handle.close();

    const [, init] = fetchSpy.mock.calls[0]!;
    const headers = (init as RequestInit).headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer test-token');
  });

  it('invokes onError when the server responds with a non-OK status', async () => {
    global.fetch = vi.fn(async () => new Response('nope', { status: 500 }));
    const errors: unknown[] = [];

    const handle = openEventStream('/stream', {
      onMessage: () => {},
      onError: (e) => errors.push(e),
    });

    await new Promise((r) => setTimeout(r, 10));
    handle.close();

    expect(errors.length).toBeGreaterThan(0);
    expect(String(errors[0])).toContain('500');
  });

  it('close() stops the stream and aborts in-flight fetches', async () => {
    // A fetch that never resolves — simulates a long-lived SSE connection.
    const abortSpy = vi.fn();
    global.fetch = vi.fn(
      (_url, init) =>
        new Promise((_, reject) => {
          (init as RequestInit).signal?.addEventListener('abort', () => {
            abortSpy();
            reject(new Error('aborted'));
          });
        }),
    ) as typeof fetch;

    const handle = openEventStream('/stream', { onMessage: () => {} });
    await new Promise((r) => setTimeout(r, 10));
    handle.close();
    await new Promise((r) => setTimeout(r, 10));

    expect(abortSpy).toHaveBeenCalled();
  });
});
