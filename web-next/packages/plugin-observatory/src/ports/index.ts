import type {
  AgentDirectoryEntry,
  AgentDirectoryFilters,
  AgentDirectoryPage,
  Registry,
  Topology,
  ObservatoryEvent,
} from '../domain';

/** Principal-scoped searchable view of A2A-addressable topology entities. */
export interface IAgentDirectory {
  listAgents(filters?: AgentDirectoryFilters): Promise<AgentDirectoryPage>;
  getAgent(agentId: string): Promise<AgentDirectoryEntry>;
}

/**
 * Provides read access to the versioned entity-type registry.
 * Adapters may fetch from a REST API, a WebSocket, or return in-memory seed data.
 */
export interface IRegistryRepository {
  getRegistry(): Promise<Registry>;
  saveRegistry(registry: Registry): Promise<Registry>;
}

/** Callback signature for topology snapshot updates. */
export type TopologyListener = (topology: Topology) => void;

/**
 * Streams live topology snapshots.
 * subscribe() immediately invokes the listener with the current snapshot (if
 * available) and again on every subsequent update.  Returns an unsubscribe fn.
 */
export interface ILiveTopologyStream {
  getSnapshot(): Topology | null;
  subscribe(listener: TopologyListener): () => void;
}

/** Callback signature for individual observatory events. */
export type ObservatoryEventListener = (event: ObservatoryEvent) => void;

/**
 * Streams observatory events (status changes, alerts, audit log entries).
 * subscribe() returns an unsubscribe fn.
 */
export interface IEventStream {
  subscribe(listener: ObservatoryEventListener): () => void;
}
