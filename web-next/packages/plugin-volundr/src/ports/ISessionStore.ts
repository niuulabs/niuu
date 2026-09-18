import type { Session, SessionState } from '../domain/session';

export interface SessionFilters {
  state?: SessionState;
  instanceId?: string;
  archivedOnly?: boolean;
  clusterId?: string;
  ravnId?: string;
}

/** Port for persisting and retrieving domain Sessions. */
export interface ISessionStore {
  getSession(id: string): Promise<Session | null>;
  listSessions(filters?: SessionFilters, signal?: AbortSignal): Promise<Session[]>;
  /** Registry-backed stores expose independent sources for progressive loading. */
  listSources?(): Promise<Array<{ id: string; name: string }>>;
  readonly listRequestTimeoutMs?: number;
  createSession(spec: Omit<Session, 'id' | 'events'>): Promise<Session>;
  updateSession(
    id: string,
    updates: Partial<Pick<Session, 'state' | 'readyAt' | 'lastActivityAt' | 'terminatedAt'>>,
  ): Promise<Session>;
  deleteSession(id: string): Promise<void>;
  /** Subscribe to session list changes. Returns an unsubscribe function. */
  subscribe(callback: (sessions: Session[]) => void): () => void;
}
