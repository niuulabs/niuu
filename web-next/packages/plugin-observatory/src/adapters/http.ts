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
import { openEventStream } from '@niuulabs/query';
import type {
  IRegistryRepository,
  ILiveTopologyStream,
  IEventStream,
  TopologyListener,
  ObservatoryEventListener,
} from '../ports';
import type { Registry, Topology, ObservatoryEvent } from '../domain';

export function buildObservatoryRegistryHttpAdapter(client: ApiClient): IRegistryRepository {
  return {
    async getRegistry(): Promise<Registry> {
      return client.get<Registry>('/registry');
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

  function ensureOpen(): void {
    if (handle) return;
    handle = openEventStream(url, {
      onMessage: (raw) => {
        try {
          const snapshot = JSON.parse(raw) as Topology;
          current = snapshot;
          for (const l of listeners) l(snapshot);
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
          const event = JSON.parse(raw) as ObservatoryEvent;
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
