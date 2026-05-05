import { useQuery } from '@tanstack/react-query';
import { useService } from '@niuulabs/plugin-sdk';
import type { ISessionStore, SessionFilters } from '../../ports/ISessionStore';

const LIVE_REFETCH_MS = 5_000;

/** Queries sessions from ISessionStore with optional filters. */
export function useSessionList(filters?: SessionFilters) {
  const store = useService<ISessionStore>('sessionStore');
  return useQuery({
    queryKey: ['volundr', 'domain-sessions', filters ?? null],
    queryFn: () => store.listSessions(filters),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

/** Queries a single session by id from ISessionStore. */
export function useSessionDetail(sessionId: string) {
  const store = useService<ISessionStore>('sessionStore');
  return useQuery({
    queryKey: ['volundr', 'domain-session', sessionId],
    queryFn: () => store.getSession(sessionId),
    refetchInterval: LIVE_REFETCH_MS,
  });
}
