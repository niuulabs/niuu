import type { Session, SessionState } from '../domain/session';

export interface SessionFilters {
  state?: SessionState;
  instanceId?: string;
  archivedOnly?: boolean;
  clusterId?: string;
  ravnId?: string;
}

export interface SessionLookupOptions {
  instanceId?: string;
  signal?: AbortSignal;
}

/** One host's sessions written since a cursor, and every id it lists now. */
export interface SessionInventoryChanges {
  /** Opaque; the next read's cursor. "0" reads every session. */
  cursor: string;
  ids: string[];
  sessions: Session[];
}

/** Incremental reads of one host's inventory, for stores that can do them. */
export interface SessionChangeReader {
  read(
    cursor: string,
    source: { instanceId: string; archivedOnly: boolean },
    signal?: AbortSignal,
  ): Promise<SessionInventoryChanges>;
  /** Longest an inventory goes on incremental reads before a full one. */
  readonly fullResyncMs: number;
}

/** Port for persisting and retrieving domain Sessions. */
export interface ISessionStore {
  getSession(id: string, options?: SessionLookupOptions): Promise<Session | null>;
  listSessions(filters?: SessionFilters, signal?: AbortSignal): Promise<Session[]>;
  /** Registry-backed stores expose independent sources for progressive loading. */
  listSources?(): Promise<Array<{ id: string; name: string }>>;
  readonly listRequestTimeoutMs?: number;
  readonly sessionChanges?: SessionChangeReader;
  createSession(spec: Omit<Session, 'id' | 'events'>): Promise<Session>;
  updateSession(
    id: string,
    updates: Partial<Pick<Session, 'state' | 'readyAt' | 'lastActivityAt' | 'terminatedAt'>>,
  ): Promise<Session>;
  deleteSession(id: string): Promise<void>;
  /** Subscribe to session list changes. Returns an unsubscribe function. */
  subscribe(callback: (sessions: Session[]) => void): () => void;
}
