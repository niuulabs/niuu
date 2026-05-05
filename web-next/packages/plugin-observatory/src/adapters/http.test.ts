import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import {
  buildObservatoryRegistryHttpAdapter,
  buildObservatoryTopologySseStream,
  buildObservatoryEventsSseStream,
} from './http';
import type { Registry, Topology, ObservatoryEvent } from '../domain';

const emptyRegistry: Registry = {
  version: 1,
  updatedAt: '2026-01-01T00:00:00Z',
  types: [],
};

const topologyA: Topology = {
  nodes: [{ id: 'n1', typeId: 'realm', label: 'A', parentId: null, status: 'healthy' }],
  edges: [],
  timestamp: '2026-01-01T00:00:00Z',
};

const topologyB: Topology = {
  nodes: [{ id: 'n2', typeId: 'realm', label: 'B', parentId: null, status: 'healthy' }],
  edges: [],
  timestamp: '2026-01-01T00:00:01Z',
};

const event1: ObservatoryEvent = {
  id: 'e1',
  timestamp: '2026-01-01T00:00:00Z',
  severity: 'info',
  sourceId: 'n1',
  message: 'online',
};

function fakeClient(registry: Registry): ApiClient {
  return {
    async get<T>(endpoint: string): Promise<T> {
      if (endpoint === '/registry') return registry as T;
      throw new Error(`unexpected endpoint: ${endpoint}`);
    },
    post: async () => {
      throw new Error('not used');
    },
    put: async () => {
      throw new Error('not used');
    },
    patch: async () => {
      throw new Error('not used');
    },
    delete: async () => {
      throw new Error('not used');
    },
  };
}

/**
 * Stand up a Response whose body feeds the provided SSE frames once, then ends.
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

describe('buildObservatoryRegistryHttpAdapter', () => {
  it('fetches the registry from GET /registry', async () => {
    const adapter = buildObservatoryRegistryHttpAdapter(fakeClient(emptyRegistry));
    const result = await adapter.getRegistry();
    expect(result).toEqual(emptyRegistry);
  });
});

describe('buildObservatoryTopologySseStream', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn(async () =>
      mockSseResponse([
        `data: ${JSON.stringify(topologyA)}\n\n`,
        `data: ${JSON.stringify(topologyB)}\n\n`,
      ]),
    ) as typeof fetch;
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('caches the most recent snapshot and replays it on subscribe', async () => {
    const stream = buildObservatoryTopologySseStream('/topology/stream');
    const received: Topology[] = [];
    const unsub = stream.subscribe((t) => received.push(t));

    // Give the mock stream time to feed both frames to the listener.
    await new Promise((r) => setTimeout(r, 20));
    unsub();

    expect(received).toEqual([topologyA, topologyB]);
    expect(stream.getSnapshot()).toEqual(topologyB);
  });

  it('drops malformed JSON frames without breaking the stream', async () => {
    global.fetch = vi.fn(async () =>
      mockSseResponse([`data: not-json\n\n`, `data: ${JSON.stringify(topologyA)}\n\n`]),
    ) as typeof fetch;

    const stream = buildObservatoryTopologySseStream('/topology/stream');
    const received: Topology[] = [];
    const unsub = stream.subscribe((t) => received.push(t));

    await new Promise((r) => setTimeout(r, 20));
    unsub();

    expect(received).toEqual([topologyA]);
  });
});

describe('buildObservatoryEventsSseStream', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = vi.fn(async () =>
      mockSseResponse([`data: ${JSON.stringify(event1)}\n\n`]),
    ) as typeof fetch;
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('forwards parsed events to every subscriber', async () => {
    const stream = buildObservatoryEventsSseStream('/events/stream');
    const a: ObservatoryEvent[] = [];
    const b: ObservatoryEvent[] = [];
    const u1 = stream.subscribe((e) => a.push(e));
    const u2 = stream.subscribe((e) => b.push(e));

    await new Promise((r) => setTimeout(r, 20));
    u1();
    u2();

    expect(a).toEqual([event1]);
    expect(b).toEqual([event1]);
  });
});
