/**
 * Server-Sent Events helper with Bearer-token support.
 *
 * The native `EventSource` API cannot set custom headers, so it can't inject
 * our Authorization header. This helper uses `fetch()` with a ReadableStream
 * body reader and parses SSE frames manually. It also handles reconnect with
 * exponential backoff when the underlying connection drops.
 *
 * The shape is a simple subscribe/close pair: consumers hand in a message
 * callback and get back a `close()` to tear the connection down.
 */

import { getAccessToken } from './http-client';

export interface EventStreamOptions {
  /** Called for every SSE data frame. `raw` is the concatenated `data:` content. */
  onMessage: (raw: string) => void;
  /** Called with parsed event metadata when the server emits an `event:` name. */
  onEvent?: (frame: { event?: string; data: string }) => void;
  /** Called when a connection attempt fails. The stream will auto-retry. */
  onError?: (err: unknown) => void;
  /** Max retry delay (ms) for exponential backoff. Defaults to 30_000. */
  maxRetryMs?: number;
}

export interface EventStreamHandle {
  close(): void;
}

export function openEventStream(url: string, options: EventStreamOptions): EventStreamHandle {
  const { onMessage, onEvent, onError, maxRetryMs = 30_000 } = options;
  let closed = false;
  let controller: AbortController | null = null;
  let retryMs = 1_000;

  async function connect(): Promise<void> {
    while (!closed) {
      controller = new AbortController();
      try {
        await pump(url, onMessage, onEvent, controller.signal);
        // Clean end: server closed the stream. Retry from the base backoff.
        retryMs = 1_000;
      } catch (err) {
        if (closed) return;
        onError?.(err);
      }
      if (closed) return;
      await sleep(retryMs);
      retryMs = Math.min(retryMs * 2, maxRetryMs);
    }
  }

  void connect();

  return {
    close(): void {
      closed = true;
      controller?.abort();
    },
  };
}

async function pump(
  url: string,
  onMessage: (raw: string) => void,
  onEvent: ((frame: { event?: string; data: string }) => void) | undefined,
  signal: AbortSignal,
): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  const token = getAccessToken();
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const res = await fetch(url, { headers, signal });
  if (!res.ok || !res.body) {
    throw new Error(`SSE connect failed: ${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line (\n\n).
    let idx;
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const parsed = parseFrame(frame);
      if (parsed === null) continue;
      onMessage(parsed.data);
      onEvent?.(parsed);
    }
  }
}

function parseFrame(frame: string): { event?: string; data: string } | null {
  const dataLines: string[] = [];
  let event: string | undefined;
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) {
      const rest = line.slice(6);
      event = rest.startsWith(' ') ? rest.slice(1) : rest;
      continue;
    }
    if (line.startsWith('data:')) {
      // Spec: one optional space after the colon is stripped.
      const rest = line.slice(5);
      dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
    }
  }
  return dataLines.length === 0 ? null : { event, data: dataLines.join('\n') };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
