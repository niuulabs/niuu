/**
 * Streaming adapters for Völundr:
 *
 * - PTY:     WebSocket (bidirectional text frames, one direction per message).
 * - Metrics: SSE (server-push only).
 *
 * Separate from http.ts because the transports, test strategy, and runtime
 * lifecycle are distinct from plain request/response endpoints.
 */

import { openEventStream, type EventStreamHandle } from '@niuulabs/query';
import type { IPtyStream } from '../ports/IPtyStream';
import type { IMetricsStream, MetricPoint } from '../ports/IMetricsStream';

// ---------------------------------------------------------------------------
// PTY — WebSocket adapter
// ---------------------------------------------------------------------------

/**
 * Factory to open a new WebSocket for a given session PTY.
 * Defaults to the native global; tests inject a fake.
 */
type WebSocketFactory = (url: string) => WebSocket;

const defaultWsFactory: WebSocketFactory = (url) => new WebSocket(url);

interface PtyAdapterOptions {
  /**
   * URL template. `{sessionId}` is replaced with the session id, URL-encoded.
   * e.g. `wss://api.niuu.world/volundr/pty?session={sessionId}`.
   */
  urlTemplate: string;
  /** Injectable for tests. Defaults to `new WebSocket(url)`. */
  wsFactory?: WebSocketFactory;
}

/**
 * One WebSocket is opened per session on first subscribe; re-used across
 * subsequent subscribers of the same session; closed when the last subscriber
 * unsubscribes. `send()` forwards text input through the live socket; if the
 * socket isn't open yet, the payload is buffered and flushed on `open`.
 */
export function buildVolundrPtyWsAdapter(options: PtyAdapterOptions): IPtyStream {
  const factory = options.wsFactory ?? defaultWsFactory;

  interface Connection {
    ws: WebSocket;
    subscribers: Set<(chunk: string) => void>;
    pending: string[];
    open: boolean;
  }

  const connections = new Map<string, Connection>();

  function urlFor(sessionId: string): string {
    return options.urlTemplate.replace('{sessionId}', encodeURIComponent(sessionId));
  }

  function ensureConnection(sessionId: string): Connection {
    const existing = connections.get(sessionId);
    if (existing) return existing;

    const ws = factory(urlFor(sessionId));
    const conn: Connection = { ws, subscribers: new Set(), pending: [], open: false };
    connections.set(sessionId, conn);

    ws.addEventListener('open', () => {
      conn.open = true;
      for (const msg of conn.pending) ws.send(msg);
      conn.pending.length = 0;
    });
    ws.addEventListener('message', (ev: MessageEvent) => {
      const data = typeof ev.data === 'string' ? ev.data : '';
      for (const cb of conn.subscribers) cb(data);
    });
    ws.addEventListener('close', () => {
      connections.delete(sessionId);
    });

    return conn;
  }

  function maybeClose(sessionId: string): void {
    const conn = connections.get(sessionId);
    if (!conn) return;
    if (conn.subscribers.size > 0) return;
    conn.ws.close();
    connections.delete(sessionId);
  }

  return {
    subscribe(sessionId, onData) {
      const conn = ensureConnection(sessionId);
      conn.subscribers.add(onData);
      return () => {
        conn.subscribers.delete(onData);
        maybeClose(sessionId);
      };
    },

    send(sessionId, data) {
      const conn = ensureConnection(sessionId);
      if (conn.open && conn.ws.readyState === WebSocket.OPEN) {
        conn.ws.send(data);
      } else {
        conn.pending.push(data);
      }
    },
  };
}

// ---------------------------------------------------------------------------
// Metrics — SSE adapter
// ---------------------------------------------------------------------------

interface MetricsAdapterOptions {
  /** URL template. `{sessionId}` is replaced with the URL-encoded session id. */
  urlTemplate: string;
}

/**
 * One SSE connection per session; fanned out to every subscriber. Each frame is
 * expected to be a JSON-serialised MetricPoint. Malformed frames are dropped.
 */
export function buildVolundrMetricsSseAdapter(options: MetricsAdapterOptions): IMetricsStream {
  interface Connection {
    handle: EventStreamHandle;
    subscribers: Set<(p: MetricPoint) => void>;
  }
  const connections = new Map<string, Connection>();

  function urlFor(sessionId: string): string {
    return options.urlTemplate.replace('{sessionId}', encodeURIComponent(sessionId));
  }

  function ensureOpen(sessionId: string): Connection {
    const existing = connections.get(sessionId);
    if (existing) return existing;

    const subscribers = new Set<(p: MetricPoint) => void>();
    const handle = openEventStream(urlFor(sessionId), {
      onMessage: (raw) => {
        try {
          const point = JSON.parse(raw) as MetricPoint;
          for (const cb of subscribers) cb(point);
        } catch {
          // Drop malformed frames.
        }
      },
    });
    const conn: Connection = { handle, subscribers };
    connections.set(sessionId, conn);
    return conn;
  }

  function maybeClose(sessionId: string): void {
    const conn = connections.get(sessionId);
    if (!conn) return;
    if (conn.subscribers.size > 0) return;
    conn.handle.close();
    connections.delete(sessionId);
  }

  return {
    subscribe(sessionId, onMetrics) {
      const conn = ensureOpen(sessionId);
      conn.subscribers.add(onMetrics);
      return () => {
        conn.subscribers.delete(onMetrics);
        maybeClose(sessionId);
      };
    },
  };
}
