/**
 * HTTP + SSE adapter factories for the Observatory plugin.
 *
 * - Registry is plain request/response (GET /registry).
 * - Topology is a live SSE stream of full snapshots; the adapter caches the
 *   most recent snapshot and fans it out to multiple subscribers, so the UI
 *   port contract (getSnapshot / subscribe-with-immediate-replay) works the
 *   same way whether the transport is a mock or SSE.
 * - Events is a fire-and-forget SSE broadcast stream.
 *
 * All three are wired by apps/niuu/src/services.ts when the corresponding
 * service's `mode` is set to `http` in runtime config.
 */

import type { ApiClient, EventStreamHandle } from '@niuulabs/query';
import { getAuthHeaders, openEventStream } from '@niuulabs/query';
import type {
  IRegistryRepository,
  ILiveTopologyStream,
  IEventStream,
  TopologyListener,
  ObservatoryEventListener,
  IAgentDirectory,
} from '../ports';
import type {
  AgentDirectoryEntry,
  AgentDirectoryFilters,
  AgentDirectoryPage,
  Registry,
  Topology,
  ObservatoryEvent,
} from '../domain';

function agentDirectoryQuery(filters: AgentDirectoryFilters = {}): string {
  const query = new URLSearchParams();
  const append = (name: string, values: readonly string[] | undefined) => {
    for (const value of values ?? []) query.append(name, value);
  };
  append('skill', filters.skills);
  append('tag', filters.tags);
  append('kind', filters.kinds);
  append('status', filters.statuses);
  append('environmentId', filters.environmentIds);
  append('cluster', filters.clusterIds);
  append('instance', filters.instanceIds);
  const encoded = query.toString();
  return encoded ? `?${encoded}` : '';
}

export function buildObservatoryAgentDirectoryHttpAdapter(client: ApiClient): IAgentDirectory {
  return {
    listAgents(filters): Promise<AgentDirectoryPage> {
      return client.get<AgentDirectoryPage>(`/agents${agentDirectoryQuery(filters)}`);
    },
    getAgent(agentId): Promise<AgentDirectoryEntry> {
      return client.get<AgentDirectoryEntry>(`/agents/${encodeURIComponent(agentId)}`);
    },
  };
}

function toObservatoryEventType(raw: Record<string, unknown>): ObservatoryEvent['type'] {
  const explicitType = typeof raw.type === 'string' ? raw.type.toUpperCase() : '';
  if (explicitType === 'RUN' || explicitType === 'RAVN' || explicitType === 'TING') {
    return explicitType;
  }
  if (explicitType === 'MIMIR' || explicitType === 'BIFROST') {
    return explicitType;
  }

  const service = typeof raw.service === 'string' ? raw.service.toUpperCase() : '';
  if (service === 'RAVN' || service === 'TING' || service === 'MIMIR' || service === 'BIFROST') {
    return service;
  }

  return 'RUN';
}

function toObservatoryEventTime(raw: Record<string, unknown>): string {
  if (typeof raw.time === 'string' && raw.time.trim()) return raw.time;
  if (typeof raw.timestamp === 'string' && raw.timestamp.length >= 19) {
    return raw.timestamp.slice(11, 19);
  }
  return '--:--:--';
}

function toObservatoryEventSubject(raw: Record<string, unknown>): string {
  if (typeof raw.subject === 'string' && raw.subject.trim()) return raw.subject;
  if (typeof raw.service === 'string' && raw.service.trim()) return raw.service;
  if (typeof raw.id === 'string' && raw.id.trim()) return raw.id;
  return 'observatory';
}

function toObservatoryEventBody(raw: Record<string, unknown>): string {
  if (typeof raw.body === 'string') return raw.body;
  if (typeof raw.message === 'string') return raw.message;
  return '';
}

function normalizeObservatoryEvent(raw: unknown): ObservatoryEvent | null {
  if (!raw || typeof raw !== 'object') return null;
  const payload = raw as Record<string, unknown>;
  const time = toObservatoryEventTime(payload);
  const subject = toObservatoryEventSubject(payload);
  const body = toObservatoryEventBody(payload);
  const fallbackId = `${time}:${subject}:${body}`.trim();
  const id =
    typeof payload.id === 'string' && payload.id.trim()
      ? payload.id
      : fallbackId
        ? fallbackId
        : `${Date.now()}`;
  return {
    id,
    time,
    type: toObservatoryEventType(payload),
    subject,
    body,
    level: payload.level === 'warning' ? 'warning' : 'info',
    ...(payload.resolved === true ? { resolved: true } : {}),
  };
}

export function buildObservatoryRegistryHttpAdapter(client: ApiClient): IRegistryRepository {
  return {
    async getRegistry(): Promise<Registry> {
      return client.get<Registry>('/registry');
    },
    async saveRegistry(registry: Registry): Promise<Registry> {
      return client.put<Registry>('/registry', registry);
    },
  };
}

function topologySnapshotUrl(streamUrl: string): string {
  const base = streamUrl.replace(/\/+$/, '').replace(/\/stream$/, '');
  return `${base}/snapshot`;
}

async function loadTopologySnapshot(streamUrl: string): Promise<Topology | null> {
  const response = await fetch(topologySnapshotUrl(streamUrl), {
    headers: getAuthHeaders({ Accept: 'application/json' }),
  });
  if (!response.ok) return null;
  return (await response.json()) as Topology;
}

/** How often the aggregate is re-polled while anything is subscribed. */
const AGGREGATE_POLL_MS = 15_000;

/**
 * Poll the Guild's merged topology and present it as a live stream.
 *
 * The aggregate spans every cluster plus every source that pushes rather than
 * being polled, so it — not one cluster's SSE feed — is what the estate view
 * should render. There is no server-side stream for it, so this polls; the
 * snapshot carries a `revision` that only changes when the graph does, so an
 * unchanged poll is dropped instead of re-rendering the canvas.
 *
 * Polling only runs while something is subscribed.
 */
export function buildObservatoryTopologyAggregateAdapter(
  client: ApiClient,
  { intervalMs = AGGREGATE_POLL_MS }: { intervalMs?: number } = {},
): ILiveTopologyStream {
  let current: Topology | null = null;
  let revision: string | null = null;
  const listeners = new Set<TopologyListener>();
  let timer: ReturnType<typeof setInterval> | null = null;
  let inFlight = false;

  async function poll(): Promise<void> {
    if (inFlight) return;
    inFlight = true;
    try {
      const snapshot = await client.get<Topology>('/snapshot');
      const next = snapshot.revision ?? null;
      // No revision means the producer cannot tell us whether it changed, so
      // publish rather than risk a view that silently stops updating.
      if (next !== null && next === revision) return;
      revision = next;
      current = snapshot;
      for (const listener of listeners) listener(snapshot);
    } catch {
      // A failed poll leaves the last good graph on screen; the snapshot's own
      // `sources` and `partial` are what report per-source trouble.
    } finally {
      inFlight = false;
    }
  }

  return {
    getSnapshot: () => current,
    subscribe(listener) {
      listeners.add(listener);
      if (current) listener(current);
      void poll();
      timer ??= setInterval(() => void poll(), intervalMs);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && timer) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
  };
}

/**
 * Wrap an SSE topology stream so it satisfies the ILiveTopologyStream contract:
 * - `getSnapshot()` returns the most recent snapshot ever received.
 * - `subscribe()` immediately replays the cached snapshot, then forwards each
 *   subsequent message; on unsubscribe, if no listeners remain, the underlying
 *   SSE connection is closed to free resources.
 */
export function buildObservatoryTopologySseStream(url: string): ILiveTopologyStream {
  let current: Topology | null = null;
  const listeners = new Set<TopologyListener>();
  let handle: EventStreamHandle | null = null;
  let snapshotLoad: Promise<void> | null = null;

  function publish(snapshot: Topology): void {
    current = snapshot;
    for (const l of listeners) l(snapshot);
  }

  function ensureSnapshotLoaded(): void {
    if (snapshotLoad || current) return;
    snapshotLoad = loadTopologySnapshot(url)
      .then((snapshot) => {
        if (snapshot) publish(snapshot);
      })
      .catch(() => {
        // The SSE connection remains authoritative; snapshot seeding is a fast first paint.
      })
      .finally(() => {
        snapshotLoad = null;
      });
  }

  function ensureOpen(): void {
    ensureSnapshotLoaded();
    if (handle) return;
    handle = openEventStream(url, {
      onMessage: (raw) => {
        try {
          const snapshot = JSON.parse(raw) as Topology;
          publish(snapshot);
        } catch {
          // Malformed frame — drop it. A future revision can add logging.
        }
      },
    });
  }

  function maybeClose(): void {
    if (listeners.size === 0 && handle) {
      handle.close();
      handle = null;
    }
  }

  return {
    getSnapshot(): Topology | null {
      return current;
    },
    subscribe(listener: TopologyListener): () => void {
      listeners.add(listener);
      ensureOpen();
      if (current) listener(current);
      return () => {
        listeners.delete(listener);
        maybeClose();
      };
    },
  };
}

/**
 * Wrap an SSE event stream so each message is forwarded to every subscriber.
 * No snapshot cache — events are discrete, not reductive state.
 */
export function buildObservatoryEventsSseStream(url: string): IEventStream {
  const listeners = new Set<ObservatoryEventListener>();
  let handle: EventStreamHandle | null = null;

  function ensureOpen(): void {
    if (handle) return;
    handle = openEventStream(url, {
      onMessage: (raw) => {
        try {
          const event = normalizeObservatoryEvent(JSON.parse(raw));
          if (!event) return;
          for (const l of listeners) l(event);
        } catch {
          // Malformed frame — drop it.
        }
      },
    });
  }

  function maybeClose(): void {
    if (listeners.size === 0 && handle) {
      handle.close();
      handle = null;
    }
  }

  return {
    subscribe(listener: ObservatoryEventListener): () => void {
      listeners.add(listener);
      ensureOpen();
      return () => {
        listeners.delete(listener);
        maybeClose();
      };
    },
  };
}
