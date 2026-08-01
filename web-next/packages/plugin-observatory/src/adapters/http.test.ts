import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiClient } from '@niuulabs/query';
import {
  buildObservatoryRegistryHttpAdapter,
  buildObservatoryTopologyAggregateAdapter,
  buildObservatoryTopologySseStream,
  buildObservatoryEventsSseStream,
  buildObservatoryAgentDirectoryHttpAdapter,
} from './http';
import type {
  AgentDirectoryEntry,
  AgentDirectoryPage,
  Registry,
  Topology,
  ObservatoryEvent,
} from '../domain';

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
  time: '00:00:00',
  type: 'TING',
  subject: 'n1',
  body: 'online',
};

const liveGuildEvent = {
  id: 'instance:valhalla:up',
  level: 'info',
  service: 'volundr',
  message: 'Valhalla is reachable',
  timestamp: '2026-05-23T20:29:44Z',
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

  it('persists the registry with PUT /registry', async () => {
    const saved: Registry[] = [];
    const client: ApiClient = {
      async get<T>(): Promise<T> {
        throw new Error('not used');
      },
      async post<T>(): Promise<T> {
        throw new Error('not used');
      },
      async put<T>(_endpoint: string, body: unknown): Promise<T> {
        saved.push(body as Registry);
        return body as T;
      },
      async patch<T>(): Promise<T> {
        throw new Error('not used');
      },
      async delete<T>(): Promise<T> {
        throw new Error('not used');
      },
    };
    const adapter = buildObservatoryRegistryHttpAdapter(client);
    const result = await adapter.saveRegistry(emptyRegistry);
    expect(result).toEqual(emptyRegistry);
    expect(saved).toEqual([emptyRegistry]);
  });
});

describe('buildObservatoryAgentDirectoryHttpAdapter', () => {
  it('encodes every repeatable directory filter and loads detail safely', async () => {
    const calls: string[] = [];
    const page = {
      items: [],
      warnings: [],
      sources: [],
      partial: false,
      revision: 'revision-a',
    } satisfies AgentDirectoryPage;
    const detail = { id: 'agent/one' } as AgentDirectoryEntry;
    const client = {
      ...fakeClient(emptyRegistry),
      async get<T>(endpoint: string): Promise<T> {
        calls.push(endpoint);
        return (endpoint.startsWith('/agents?') ? page : detail) as T;
      },
    };
    const adapter = buildObservatoryAgentDirectoryHttpAdapter(client);

    await expect(
      adapter.listAgents({
        skills: ['code', 'review'],
        tags: ['engineering'],
        kinds: ['workflow-session'],
        statuses: ['healthy'],
        environmentIds: ['environment-a'],
        clusterIds: ['noatun'],
        instanceIds: ['observatory-a'],
      }),
    ).resolves.toEqual(page);
    await expect(adapter.getAgent('agent/one')).resolves.toEqual(detail);

    expect(calls[0]).toBe(
      '/agents?skill=code&skill=review&tag=engineering&kind=workflow-session&status=healthy&environmentId=environment-a&cluster=noatun&instance=observatory-a',
    );
    expect(calls[1]).toBe('/agents/agent%2Fone');
  });

  it('propagates transport failures', async () => {
    const client = {
      ...fakeClient(emptyRegistry),
      get: vi.fn().mockRejectedValue(new Error('directory failed')),
    };
    const adapter = buildObservatoryAgentDirectoryHttpAdapter(client);

    await expect(adapter.listAgents()).rejects.toThrow('directory failed');
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

  it('seeds the current topology from the snapshot endpoint before stream frames arrive', async () => {
    global.fetch = vi.fn((input, init) => {
      const url = String(input);
      if (url === '/topology/snapshot') {
        return Promise.resolve(
          new Response(JSON.stringify(topologyA), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        );
      }

      return new Promise<Response>((_, reject) => {
        (init as RequestInit | undefined)?.signal?.addEventListener('abort', () => {
          reject(new Error('aborted'));
        });
      });
    }) as typeof fetch;

    const stream = buildObservatoryTopologySseStream('/topology/stream');
    const received: Topology[] = [];
    const unsub = stream.subscribe((t) => received.push(t));

    await new Promise((r) => setTimeout(r, 20));
    unsub();

    expect(received).toEqual([topologyA]);
    expect(stream.getSnapshot()).toEqual(topologyA);
    expect(global.fetch).toHaveBeenCalledWith(
      '/topology/snapshot',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
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

  it('normalizes live guild snapshot events into observatory UI events', async () => {
    global.fetch = vi.fn(async () =>
      mockSseResponse([`data: ${JSON.stringify(liveGuildEvent)}\n\n`]),
    ) as typeof fetch;

    const stream = buildObservatoryEventsSseStream('/events/stream');
    const received: ObservatoryEvent[] = [];
    const unsubscribe = stream.subscribe((event) => received.push(event));

    await new Promise((r) => setTimeout(r, 20));
    unsubscribe();

    expect(received).toEqual([
      {
        id: 'instance:valhalla:up',
        time: '20:29:44',
        type: 'RUN',
        subject: 'volundr',
        body: 'Valhalla is reachable',
      },
    ]);
  });
});


describe('buildObservatoryTopologyAggregateAdapter', () => {
  /**
   * The aggregate spans every cluster plus every source that pushes rather
   * than being polled, so it — not one cluster's feed — is the estate view.
   */
  function clientReturning(...snapshots: Topology[]): { client: ApiClient; calls: () => number } {
    let index = 0;
    const client = {
      get: vi.fn(async () => {
        const snapshot = snapshots[Math.min(index, snapshots.length - 1)];
        index += 1;
        return snapshot as unknown;
      }),
    } as unknown as ApiClient;
    return { client, calls: () => (client.get as ReturnType<typeof vi.fn>).mock.calls.length };
  }

  it('publishes the merged snapshot to a new subscriber', async () => {
    const { client } = clientReturning(topologyA);
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 10_000 });
    const received: Topology[] = [];

    const unsub = adapter.subscribe((t) => received.push(t));
    await vi.waitFor(() => expect(received).toHaveLength(1));
    unsub();

    expect(received[0]).toEqual(topologyA);
    expect(adapter.getSnapshot()).toEqual(topologyA);
    expect(client.get).toHaveBeenCalledWith('/snapshot');
  });

  it('drops an unchanged poll instead of re-rendering the canvas', async () => {
    const withRevision: Topology = { ...topologyA, revision: 'rev-1' };
    const { client } = clientReturning(withRevision, withRevision);
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 5 });
    const received: Topology[] = [];

    const unsub = adapter.subscribe((t) => received.push(t));
    await vi.waitFor(() =>
      expect(
        (client.get as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThanOrEqual(2),
    );
    unsub();

    expect(received).toHaveLength(1);
  });

  it('publishes again when the revision changes', async () => {
    const { client } = clientReturning(
      { ...topologyA, revision: 'rev-1' },
      { ...topologyB, revision: 'rev-2' },
    );
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 5 });
    const received: Topology[] = [];

    const unsub = adapter.subscribe((t) => received.push(t));
    await vi.waitFor(() => expect(received).toHaveLength(2));
    unsub();

    expect(received[1]?.nodes[0]?.id).toBe('n2');
  });

  it('publishes every poll when the producer sends no revision', async () => {
    // Otherwise a producer that omits it would leave the view frozen.
    const { client } = clientReturning(topologyA, topologyA);
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 5 });
    const received: Topology[] = [];

    const unsub = adapter.subscribe((t) => received.push(t));
    await vi.waitFor(() => expect(received.length).toBeGreaterThanOrEqual(2));
    unsub();
  });

  it('keeps the last good graph when a poll fails', async () => {
    let call = 0;
    const client = {
      get: vi.fn(async () => {
        call += 1;
        if (call === 1) return { ...topologyA, revision: 'rev-1' } as unknown;
        throw new Error('gateway down');
      }),
    } as unknown as ApiClient;
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 5 });

    const unsub = adapter.subscribe(() => {});
    await vi.waitFor(() =>
      expect(
        (client.get as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThanOrEqual(2),
    );
    unsub();

    expect(adapter.getSnapshot()?.nodes[0]?.id).toBe('n1');
  });

  it('stops polling once the last subscriber leaves', async () => {
    const { client } = clientReturning(topologyA);
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 5 });

    const unsub = adapter.subscribe(() => {});
    await vi.waitFor(() => expect(client.get).toHaveBeenCalled());
    unsub();
    const afterUnsub = (client.get as ReturnType<typeof vi.fn>).mock.calls.length;

    await new Promise((r) => setTimeout(r, 20));

    expect((client.get as ReturnType<typeof vi.fn>).mock.calls.length).toBe(afterUnsub);
  });

  it('replays the cached snapshot immediately to a second subscriber', async () => {
    const { client } = clientReturning({ ...topologyA, revision: 'rev-1' });
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 10_000 });

    const first = adapter.subscribe(() => {});
    await vi.waitFor(() => expect(adapter.getSnapshot()).not.toBeNull());
    const received: Topology[] = [];
    const second = adapter.subscribe((t) => received.push(t));

    expect(received).toHaveLength(1);
    first();
    second();
  });

  it('carries source health so a partial estate is visible', async () => {
    const partial: Topology = {
      ...topologyA,
      partial: true,
      sources: [
        { sourceId: 'ymir', status: 'healthy', transport: 'pull' },
        { sourceId: 'spark-1', status: 'stale', transport: 'push', lastSeen: '12:00:00Z' },
      ],
    };
    const { client } = clientReturning(partial);
    const adapter = buildObservatoryTopologyAggregateAdapter(client, { intervalMs: 10_000 });

    const unsub = adapter.subscribe(() => {});
    await vi.waitFor(() => expect(adapter.getSnapshot()).not.toBeNull());
    unsub();

    expect(adapter.getSnapshot()?.partial).toBe(true);
    expect(adapter.getSnapshot()?.sources?.[1]?.status).toBe('stale');
  });
});
